"""STEP 3 — rug checks: RugCheck (Solana), GoPlus + Honeypot.is (EVM), Blockscout explorer tier (Robinhood Chain).

Writes sec:"rugcheck"|"goplus"|"explorer"|"nodata" plus the fields the page's safety gate reads.
An unverified contract on the explorer tier stays "nodata" — never a false pass.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from .common import Http, Status, BlockedError, r1, r2, log

RUGCHECK = "https://api.rugcheck.xyz/v1"
GOPLUS = "https://api.gopluslabs.io/api/v1"
HONEYPOT = "https://api.honeypot.is/v2/IsHoneypot"
BLOCKSCOUT = "https://api.blockscout.com/4663"      # Robinhood Chain = 4663 on the unified gateway

GOPLUS_IDS = {"ethereum": "1", "bsc": "56", "base": "8453", "arbitrum": "42161", "optimism": "10", "unichain": "130",
              "robinhood": "4663", "polygon": "137", "avalanche": "43114", "linea": "59144", "scroll": "534352",
              "zksync": "324", "blast": "81457", "sonic": "146", "mantle": "5000", "cronos": "25", "monad": "143",
              "berachain": "80094", "ink": "57073", "plasma": "9745", "hyperevm": "999"}
HONEYPOT_IDS = {"ethereum": "1", "bsc": "56", "base": "8453"}
PERMISSIONED_RE = re.compile(r"^0xb20{20,}", re.I)
POOL_NAME_RE = re.compile(r"pool|manager|uniswap|v4|position", re.I)
DEAD = {"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"}
TB_FUNCS = ("pause", "blacklist", "blocklist", "settax", "setfee", "setmaxtx", "enabletrading", "settrading", "setlimit", "excludefrom", "setswap")
# owner() is read over JSON-RPC. The unified gateway (api.blockscout.com/4663) has NO eth-rpc route
# (404, verified 20 Sep), so try it first for parity but fall back to the chain's own explorer host,
# which does expose /api/eth-rpc. Both best-effort; "?" means we genuinely could not read it.
RPC_HOSTS = ("https://api.blockscout.com/4663", "https://robinhoodchain.blockscout.com")


def _owner(http: Http, ta: str, key: str) -> str:
    """Best-effort owner() read across the RPC hosts. Returns renounced | live | none | ?."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": ta, "data": "0x8da5cb5b"}, "latest"]}
    saw_revert = False
    for host in RPC_HOSTS:
        url = f"{host}/api/eth-rpc" + (f"?apikey={key}" if key else "")
        try:
            rpc = http.post_json(url, payload)
        except Exception:  # noqa: BLE001  (404 on the gateway, 429/timeout on the chain host)
            continue
        if not rpc:
            continue
        res = rpc.get("result")
        if res and isinstance(res, str) and len(res) >= 42:
            return "renounced" if ("0x" + res[-40:]).lower() in DEAD else "live"
        err = rpc.get("error")
        if err:
            msg = str(err).lower()
            if "too many" not in msg and "rate" not in msg:   # a real revert = no owner(); a rate-limit is not
                saw_revert = True
    return "none" if saw_revert else "?"


# ---------------------------------------------------------------- Solana
def rugcheck(http: Http, row: dict) -> bool:
    j = http.get(f"{RUGCHECK}/tokens/{row['ta']}/report/summary", retries=1)
    if not j:
        return False
    row["sec"] = "rugcheck"
    if j.get("score_normalised") is not None:
        row["rcs"] = int(j["score_normalised"])
    elif j.get("score") is not None:
        row["rcs"] = int(j["score"])
    risks = j.get("risks") or []
    rk = []
    for r in risks:
        name, level = r.get("name") or "", (r.get("level") or "").lower()
        rk.append(f"{name}({level})" if level else name)
        n = name.lower()
        if "mint authority" in n:
            row["mint"] = 1
        if "freeze authority" in n:
            row["frz"] = 1
        if "lp unlocked" in n or "low amount of lp" in n:
            pass
    if rk:
        row["rk"] = rk[:6]
    if j.get("lpLockedPct") is not None:
        row["lpk"] = r2(j["lpLockedPct"])
    return True


# ---------------------------------------------------------------- EVM
def goplus(http: Http, row: dict, cid: str) -> bool:
    j = http.get(f"{GOPLUS}/token_security/{cid}", params={"contract_addresses": row["ta"]}, retries=1)
    res = ((j or {}).get("result") or {})
    d = res.get(row["ta"].lower()) or (next(iter(res.values())) if res else None)
    row["sec"] = "goplus"
    if not d:
        return False
    def pct(x):
        try:
            return round(float(x) * 100, 2)
        except Exception:
            return None
    if d.get("buy_tax") not in (None, ""):
        row["stx"] = pct(d["buy_tax"])
    if d.get("sell_tax") not in (None, ""):
        row["sstx"] = pct(d["sell_tax"])
    if d.get("holder_count") not in (None, ""):
        try:
            row["hld"] = int(float(d["holder_count"]))
        except ValueError:
            pass
    holders = d.get("holders") or []
    if holders:
        top = sorted((float(h.get("percent") or 0) for h in holders), reverse=True)[:10]
        row["t10"] = r1(sum(top) * 100)
    lp = d.get("lp_holders") or []
    if lp:
        locked = sum(float(h.get("percent") or 0) for h in lp if str(h.get("is_locked")) == "1" or (h.get("address") or "").lower() in DEAD)
        row["lpk"] = r2(locked * 100)
    if str(d.get("is_mintable")) == "1":
        row["mint"] = 1
    if str(d.get("can_take_back_ownership")) == "1" or str(d.get("transfer_pausable")) == "1":
        row["tb"] = 1
    if str(d.get("is_honeypot")) == "1":
        row.setdefault("rk", []).append("goplus:is_honeypot(warn)")
    return any(k in row for k in ("stx", "sstx", "hld", "t10", "mint", "tb"))


def honeypot(http: Http, row: dict, cid: str) -> None:
    if PERMISSIONED_RE.match(row["ta"] or ""):
        row["hpInc"] = 1
        row.setdefault("rk", []).append("transfer-restricted permissioned token - sell simulation fails, not a rug(warn)")
        return
    try:
        j = http.get(HONEYPOT, params={"address": row["ta"], "chainID": cid}, retries=1)
    except Exception as e:  # noqa: BLE001
        log.warning("honeypot %s: %s", row["sym"], str(e)[:80])
        return
    if not j:
        return
    hr = j.get("honeypotResult") or {}
    sim_ok = j.get("simulationSuccess")
    err = str(j.get("simulationError") or hr.get("honeypotReason") or "")
    if sim_ok is False or "execution reverted" in err.lower() or "STF" in err:
        row["hpInc"] = 1
        return
    if hr.get("isHoneypot") is True:
        row["honey"] = 1
        row.setdefault("rk", []).append("honeypot.is: sell fails(danger)")
    elif hr.get("isHoneypot") is False:
        row["hpOk"] = 1
        sim = j.get("simulationResult") or {}
        if row.get("stx") is None and sim.get("buyTax") is not None:
            row["stx"] = r2(sim["buyTax"])
        if row.get("sstx") is None and sim.get("sellTax") is not None:
            row["sstx"] = r2(sim["sellTax"])


# ---------------------------------------------------------------- Blockscout explorer tier (Robinhood)
def explorer(http: Http, row: dict, key: str) -> None:
    ta = row["ta"]
    q = {"apikey": key} if key else None
    tok = http.get(f"{BLOCKSCOUT}/api/v2/tokens/{ta}", params=q, retries=1)
    if tok is None:
        return
    if tok.get("holders_count") not in (None, ""):
        row["hld"] = int(float(tok["holders_count"]))
    supply = None
    try:
        supply = float(tok.get("total_supply")) if tok.get("total_supply") not in (None, "") else None
    except ValueError:
        pass
    hold = http.get(f"{BLOCKSCOUT}/api/v2/tokens/{ta}/holders", params=q, retries=1) or {}
    items = hold.get("items") or []
    pa = (row.get("pa") or "").lower()
    excluded = ta.lower(), pa
    counted, pool_share, cN = [], 0.0, 0
    for it in items:
        addr = ((it.get("address") or {}).get("hash") or "").lower()
        name = (it.get("address") or {}).get("name") or ""
        is_contract = bool((it.get("address") or {}).get("is_contract"))
        try:
            val = float(it.get("value") or 0)
        except ValueError:
            val = 0.0
        share = (val / supply * 100) if supply else None
        if addr in DEAD:
            continue                        # burned: neither a holder nor a pool
        if addr in excluded or POOL_NAME_RE.search(name):
            if share is not None:
                pool_share += share         # "pool" = the pair, the token's own address, and named pool/manager contracts
            continue
        if len(counted) < 10:
            counted.append(share or 0.0)
            cN += int(is_contract)
    if supply and items:
        row["t10"] = r1(sum(counted))
        row["t1"] = r1(max(counted)) if counted else None
        row["pool"] = r1(pool_share)
        row["cN"] = cN
    sc = http.get(f"{BLOCKSCOUT}/api/v2/smart-contracts/{ta}", params=q, retries=1) or {}
    impl = (sc.get("implementations") or [])
    if impl and isinstance(impl, list) and impl[0].get("address"):
        sc2 = http.get(f"{BLOCKSCOUT}/api/v2/smart-contracts/{impl[0]['address']}", params=q, retries=1)
        if sc2 and sc2.get("is_verified"):
            sc = sc2
    ver = 1 if sc.get("is_verified") else 0
    row["ver"] = ver
    if sc.get("name"):
        row["cnm"] = sc["name"]
    own = _owner(http, ta, key)
    row["own"] = own
    abi = sc.get("abi") or []
    fnames = [(f.get("name") or "").lower() for f in abi if isinstance(f, dict) and f.get("type") == "function"]
    # Flag a live authority capability unless ownership is PROVABLY renounced. When owner() cannot be
    # read (own=="?") we still flag any mint / owner-control function the verified ABI exposes, rather
    # than assume it is dead — assuming dead was the false-pass bug (gateway has no eth-rpc route, so
    # own was always "?" and mint/tb never set, letting a live-mint token pass the authority check).
    if ver and own != "renounced":
        if any(n.startswith("mint") for n in fnames):
            row["mint"] = 1
        if any(any(n.startswith(t) for t in TB_FUNCS) for n in fnames):
            row["tb"] = 1
    row["sec"] = "explorer" if ver else "nodata"


# ---------------------------------------------------------------- orchestration
def run(http: Http, st: Status, tokens: list[dict]) -> None:
    http.pace("api.rugcheck.xyz", 40)
    http.pace("api.gopluslabs.io", 25)
    http.pace("api.honeypot.is", 40)
    http.pace("api.blockscout.com", 120)
    key = os.environ.get("BLOCKSCOUT_API_KEY", "").strip()
    n_rc = n_gp = n_hp = n_ex = n_ex_ok = 0
    err_rc = err_gp = err_ex = 0
    key_rejected = False
    for t in tokens:
        t.setdefault("sec", "nodata")
        c = t["c"]
        try:
            if c == "solana":
                if rugcheck(http, t):
                    n_rc += 1
            elif c in GOPLUS_IDS:
                got = goplus(http, t, GOPLUS_IDS[c])
                n_gp += int(got)
                if c in HONEYPOT_IDS:
                    honeypot(http, t, HONEYPOT_IDS[c])
                    n_hp += 1
                if c == "robinhood" and t.get("band") == "m" and not key_rejected:
                    try:
                        explorer(http, t, key)
                        n_ex += 1
                        n_ex_ok += int(t.get("sec") == "explorer")
                    except Exception as e:  # noqa: BLE001
                        err_ex += 1
                        if "401" in str(e) or "403" in str(e):
                            key_rejected = True
                        log.warning("explorer %s: %s", t["sym"], str(e)[:100])
        except Exception as e:  # noqa: BLE001
            if c == "solana":
                err_rc += 1
            else:
                err_gp += 1
            log.warning("safety %s/%s: %s", c, t["sym"], str(e)[:100])
    sol = sum(1 for t in tokens if t["c"] == "solana")
    evm = sum(1 for t in tokens if t["c"] in GOPLUS_IDS)
    rh = sum(1 for t in tokens if t["c"] == "robinhood" and t.get("band") == "m")
    if sol == 0 or n_rc >= sol * 0.5:
        st.ok("rugcheck", solana=sol, ok=n_rc, errors=err_rc)
    else:
        st.partial("rugcheck", f"{n_rc}/{sol} answered", solana=sol, ok=n_rc, errors=err_rc)
    evm_non_rh = sum(1 for t in tokens if t["c"] in GOPLUS_IDS and t["c"] != "robinhood")
    if evm_non_rh == 0 or n_gp >= evm_non_rh * 0.5:
        st.ok("goplus", evm=evm, with_fields=n_gp, honeypot_sims=n_hp, errors=err_gp)
    else:
        st.partial("goplus", f"{n_gp}/{evm_non_rh} non-Robinhood EVM tokens returned fields", evm=evm, with_fields=n_gp, honeypot_sims=n_hp, errors=err_gp)
    if key_rejected:
        st.fail("blockscout", "key rejected (401/403) — rotate BLOCKSCOUT_API_KEY", robinhood_m=rh)
    elif rh == 0:
        st.ok("blockscout", robinhood_m=0)
    elif n_ex_ok + (n_ex - n_ex_ok) >= rh * 0.5:
        st.ok("blockscout", robinhood_m=rh, explorer=n_ex_ok, unverified=n_ex - n_ex_ok, errors=err_ex, key_present=bool(key))
    else:
        st.partial("blockscout", f"only {n_ex}/{rh} Robinhood tokens read", robinhood_m=rh, explorer=n_ex_ok, errors=err_ex, key_present=bool(key))
