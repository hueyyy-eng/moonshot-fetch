"""Cloud-only daily scan: rebuild the Moonshot Scanner data block from latest/scan.json.

    python scan/build_block.py --page page.html --scan scan.json --status status.json --out out/ [--nar nar.json]

Reads the current dashboard HTML (from the Artifact tool), replaces everything between /*DATA-START*/ and
/*DATA-END*/, and writes:
    out/page_new.html         the page to republish (every other byte unchanged)
    out/track-record.csv      STEP 4b export for Google Drive
    out/summary.json          what changed, the lists, the stale parts, the message draft, and nar_todo
    out/nar_todo.json         tokens that still need a narrative tag (exit code 3 until it is empty)

Every deterministic rule of the scan prompt lives here: carry-forward per source status, the three
candle guards' outputs are trusted from the fetch job, the nine gates + pullback + igniting exactly as
the page's own JS evaluates them (minliq 25, allowUnk off), and the four logs (HISTORY, REJECTS,
NARHIST, HOLDHIST). The model's remaining job is judgement: narrative tags for new tokens, the
verification render, the publish, and the report.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
KEEP_DAYS = 60
REJECT_CAP = 30
MINLIQ = 25.0
CANDLE_KEYS = ("run", "retr", "vvp", "nd", "cs", "noCd", "ub", "us", "ubr", "tpw", "lpk")
SAFETY_KEYS = ("sec", "rcs", "lpk", "t10", "hld", "stx", "sstx", "mint", "frz", "tb", "t1", "pool", "cN", "ver",
               "own", "cnm", "honey", "hpOk", "hpInc", "rk")
NAMES = {"solana": "Solana", "ethereum": "Ethereum", "robinhood": "Robinhood Chain", "optimism": "Optimism", "ink": "Ink", "near": "Near",
         "tron": "Tron", "unichain": "Unichain", "base": "Base", "bsc": "BSC", "arbitrum": "Arbitrum", "pulsechain": "PulseChain",
         "xrpl": "XRP Ledger", "plasma": "Plasma", "polygon": "Polygon", "avax": "Avalanche", "hyperliquid": "Hyperliquid",
         "sui": "Sui", "xlayer": "X Layer", "edgex": "edgeX", "zklighter": "zkLighter", "native_core": "Core", "xdai": "Gnosis",
         "ton": "TON", "sei": "Sei", "aptos": "Aptos", "monad": "Monad", "mantle": "Mantle", "starknet": "Starknet", "sonic": "Sonic"}
GATE_KEYS = ["mcap", "liq", "turn", "buyers", "hold", "notRan", "noWash", "safe", "social"]
ORDER = ["LAST_DAY", "SNAP_AT", "TOK_AT", "CHAIN_GATE", "CORE_CHAINS", "MCAP", "LIQ_PCT", "TURN_MIN", "RAN_MULT",
         "DROP_MAX", "PULL", "IGN", "MCAP_L", "TYPE", "CAND_AT", "NO_DEXSCREENER", "DATA", "NETFLOW_AT", "NETFLOW",
         "BRIDGES_AT", "BRIDGES", "REGIME_AT", "MAJORS", "LOG_AT", "REJECTS", "NARHIST", "HOLDHIST", "TOKENS",
         "HISTORY", "KOLS", "CHAIN_ACCTS"]


# ------------------------------------------------------------------ block parsing
def split_page(html: str):
    a, b = "/*DATA-START*/", "/*DATA-END*/"
    i, j = html.index(a) + len(a), html.index(b)
    return html[:i], html[i:j], html[j:]


def parse_block(block: str) -> dict:
    """const NAME = <value>;  — one per statement; multi-const lines (LIQ_PCT = 5, LIQ_MIN = 25) are kept verbatim."""
    out: dict[str, dict] = {}
    for m in re.finditer(r"^const ([A-Z_]+)\s*=\s*(.*?);[ \t]*(//[^\n]*)?$", block, re.M | re.S):
        name, raw, comment = m.group(1), m.group(2), m.group(3) or ""
        # the value may span lines (arrays): the lazy .*? stops at the first ";" at end of a line
        try:
            val = json.loads(raw)
        except ValueError:
            val = None
        out[name] = {"raw": raw, "val": val, "comment": comment.strip(), "line": m.group(0)}
    return out


def jsdump(v) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def rows_dump(name: str, rows: list) -> str:
    if not rows:
        return f"const {name} = [];\n"
    body = ",\n".join(jsdump(r) for r in rows)
    return f"const {name} = [\n{body}\n];\n"


# ------------------------------------------------------------------ page logic, ported verbatim
class Consts:
    MCAP = (500, 2000); MCAP_L = (2000, None); LIQ_PCT = 5; LIQ_MIN = 25
    TURN_MIN = 1; TURN_WASH = 200; TURN_WASH_SOFT = 30; TPW_MAX = 15
    RAN_MULT = 3; RAN_24H = 200; PARAB_6H = 100; CHASE_1H = 50
    DROP_MAX = -40; TOP10_MAX = 50; TAX_MAX = 10; BUYER_SHARE = 0.5
    PULL = {"run": 3, "retrLo": 40, "retrHi": 70, "volVsPeak": 30}
    IGN = {"runLo": 1.3, "runHi": 3, "accel": 1.5, "c24Max": 200}
    CHAIN_GATE = {"dr": 1.3, "dv": 5}


C = Consts()


def derive(t: dict) -> None:
    run_ok = t.get("run") is not None and t["run"] > 0
    t["_runOk"] = run_ok
    t["_ran"] = (t["run"] >= C.RAN_MULT) if run_ok else ((t.get("c24") is not None and t["c24"] >= C.RAN_24H))
    t["_parab"] = t.get("c6") is not None and t["c6"] > C.PARAB_6H
    turn = t.get("turn") or 0
    t["_wash"] = turn > C.TURN_WASH or (turn > C.TURN_WASH_SOFT and t.get("tpw") is not None and t["tpw"] > C.TPW_MAX)
    t["_honey"] = t.get("honey") == 1
    t["_partial"] = t.get("sec") == "explorer"
    sec = t.get("sec")
    t["_secKnown"] = ((sec in ("goplus", "rugcheck")) and any(t.get(k) is not None for k in ("t10", "stx", "sstx", "rcs", "mint", "frz", "tb")) or (sec in ("goplus", "rugcheck") and bool(t.get("hpOk")))) \
        or (t["_partial"] and t.get("t10") is not None and t.get("ver") == 1)
    t["_top10ok"] = (t["t10"] < C.TOP10_MAX) if t.get("t10") is not None else None
    tax = max(t.get("stx") or 0, t.get("sstx") or 0)
    t["_taxok"] = None if (t.get("stx") is None and t.get("sstx") is None) else tax <= C.TAX_MAX
    if sec == "rugcheck":
        t["_authok"] = not (t.get("mint") or t.get("frz"))
    elif sec == "goplus":
        t["_authok"] = not (t.get("mint") or t.get("tb"))
    elif t["_partial"]:
        t["_authok"] = (not (t.get("mint") or t.get("tb"))) if t.get("ver") == 1 else None
    else:
        t["_authok"] = None
    v6, v24 = t.get("v6"), t.get("v24")
    t["_acc"] = round((v6 / 6) / (v24 / 24) * 100) / 100 if (v6 is not None and v24 and v24 > 0) else None


def gate(t: dict, k: str):
    if k == "mcap":
        B = C.MCAP_L if t.get("band") == "l" else C.MCAP
        mc = t.get("mc")
        return mc is not None and mc >= B[0] and (B[1] is None or mc <= B[1])
    if k == "liq":
        return (t.get("lp") or 0) >= C.LIQ_PCT and (t.get("liq") or 0) >= max(C.LIQ_MIN, MINLIQ)
    if k == "turn":
        return (t.get("turn") or 0) >= C.TURN_MIN
    if k == "buyers":
        return None if t.get("ubr") is None else t["ubr"] >= C.BUYER_SHARE
    if k == "hold":
        return (t.get("c6") is None or t["c6"] > C.DROP_MAX) and (t.get("c24") is None or t["c24"] > C.DROP_MAX)
    if k == "notRan":
        return not t["_ran"] and not t["_parab"]
    if k == "noWash":
        return not t["_wash"]
    if k == "safe":
        if t["_honey"]:
            return False
        if not t["_secKnown"]:
            return None
        return t["_top10ok"] is not False and t["_taxok"] is not False and t["_authok"] is not False
    if k == "social":
        return bool(t.get("tw") or t.get("web"))
    raise KeyError(k)


def evaluate(t: dict) -> None:
    derive(t)
    g = {k: gate(t, k) for k in GATE_KEYS}
    t["_g"] = g
    t["_nf"] = sum(1 for v in g.values() if v is False)
    t["_nu"] = sum(1 for v in g.values() if v is None)


def is_short(t):  return t.get("band") != "l" and t["_nf"] == 0 and t["_nu"] == 0
def is_pull(t):
    g = t["_g"]
    return (t.get("band") != "l" and t["_runOk"] and t.get("retr") is not None and t["run"] >= C.PULL["run"]
            and C.PULL["retrLo"] <= t["retr"] <= C.PULL["retrHi"] and t.get("vvp") is not None and t["vvp"] >= C.PULL["volVsPeak"]
            and t.get("ubr") is not None and t["ubr"] >= C.BUYER_SHARE and not t["_honey"] and not t["_wash"]
            and g["safe"] is True and g["liq"] is True and g["mcap"] is True)
def is_ign(t):
    g = t["_g"]
    return (t.get("band") != "l" and t["_acc"] is not None and t["_acc"] >= C.IGN["accel"]
            and t.get("c6") is not None and 0 < t["c6"] <= C.PARAB_6H
            and (t.get("c24") is None or t["c24"] < C.IGN["c24Max"])
            and (t.get("run") is None or C.IGN["runLo"] <= t["run"] < C.IGN["runHi"])
            and t.get("ubr") is not None and t["ubr"] >= C.BUYER_SHARE and not t["_wash"] and not t["_honey"]
            and g["safe"] is True and g["liq"] is True and g["mcap"] is True and g["turn"] is True)


def chain_hot(r: dict) -> bool:
    if r.get("dr") is None or r.get("dv") is None:
        return False
    fast, slow = r["dr"] >= C.CHAIN_GATE["dr"], r.get("dr30") is not None and r["dr30"] >= C.CHAIN_GATE["dr"]
    if not fast and not slow or r["dv"] < C.CHAIN_GATE["dv"]:
        return False
    if r.get("sb") is not None:
        return r.get("f7") is not None and r["f7"] >= 0
    return r.get("t7") is not None and r["t7"] > -1


def flags(t: dict) -> None:
    for k in ("ran", "par", "wsh"):
        t.pop(k, None)
    if t["_ran"]:
        t["ran"] = 1
    if t["_parab"]:
        t["par"] = 1
    if t["_wash"]:
        t["wsh"] = 1


def public(t: dict) -> dict:
    return {k: v for k, v in t.items() if not k.startswith("_") and v is not None}


# ------------------------------------------------------------------ build
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--scan", required=True)
    ap.add_argument("--status", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--nar", help="JSON {pairAddress|tokenAddress: narrative}")
    ap.add_argument("--now", help='override "YYYY-MM-DD HH:MM SGT"')
    ap.add_argument("--allow-unclassified", action="store_true", help='tag leftovers "Unclassified" instead of stopping')
    a = ap.parse_args()

    now_dt = datetime.now(SGT)
    now = a.now or now_dt.strftime("%Y-%m-%d %H:%M SGT")
    today = now[:10]
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")

    html = open(a.page, encoding="utf-8").read()
    head, block, tail = split_page(html)
    old = parse_block(block)
    scan = json.load(open(a.scan, encoding="utf-8"))
    status = json.load(open(a.status, encoding="utf-8"))
    src = status.get("sources") or scan.get("status") or {}
    state = lambda k: (src.get(k) or {}).get("state", "missing")  # noqa: E731
    nar_map = json.load(open(a.nar, encoding="utf-8")) if a.nar else {}
    nar_map = {k.lower(): v for k, v in nar_map.items()}

    def oldv(name, default):
        v = old.get(name, {}).get("val")
        return default if v is None else v

    stale: list[str] = []
    notes: list[str] = []
    new: dict = {}

    # ---- chains
    old_data = oldv("DATA", [])
    old_by_slug = {r["s"]: r for r in old_data}
    if state("llama.chain_rows") == "ok" and scan.get("DATA"):
        data = [dict(r) for r in scan["DATA"]]
        for r in data:
            o = old_by_slug.get(r["s"])
            if o and o.get("n") and r.get("n", "").lower() == r["s"]:
                r["n"] = o["n"]                       # keep the display name the page already knows
            elif r.get("n", "").lower() == r["s"]:
                r["n"] = NAMES.get(r["s"], r["s"].replace("_", " ").title())
            if r.get("pv") is None and o and o.get("pv") is not None:
                for k in ("pv", "pr", "p30", "pr30"):
                    if o.get(k) is not None:
                        r[k] = o[k]
                r["pStale"] = 1
        new["LAST_DAY"] = scan.get("LAST_DAY") or oldv("LAST_DAY", 0)
        new["SNAP_AT"] = now
        new["NO_DEXSCREENER"] = scan.get("NO_DEXSCREENER", oldv("NO_DEXSCREENER", []))
        if state("llama.perps_page") != "ok":
            stale.append("perps (carried, pStale)")
    else:
        data = old_data
        new["LAST_DAY"] = oldv("LAST_DAY", 0)
        new["SNAP_AT"] = oldv("SNAP_AT", now)
        new["NO_DEXSCREENER"] = oldv("NO_DEXSCREENER", [])
        stale.append("chain rows (DefiLlama failed — previous DATA kept)")
    new["DATA"] = data

    for key, st_key, at_key, label in (("NETFLOW", "llama.netflow", "NETFLOW_AT", "net flows"),
                                       ("BRIDGES", "llama.bridges_protocols", "BRIDGES_AT", "bridge protocol stats"),
                                       ("MAJORS", "llama.majors", "REGIME_AT", "majors")):
        v = scan.get(key)
        ok = state(st_key) == "ok" and v
        if key == "MAJORS" and ok:
            b = v.get("btc") or {}
            if b.get("d7") is not None and b.get("d7") == b.get("d30"):
                ok = False
                notes.append("MAJORS d7 == d30 — DefiLlama returned one window under two labels; previous MAJORS kept")
        if ok:
            new[key], new[at_key] = v, now
        else:
            new[key], new[at_key] = oldv(key, {} if key != "BRIDGES" else []), oldv(at_key, now)
            stale.append(label + " (carried)")

    # ---- tokens
    old_tokens = oldv("TOKENS", [])
    old_by_pa = {t["pa"].lower(): t for t in old_tokens if t.get("pa")}
    old_by_ta = {t["ta"].lower(): t for t in old_tokens if t.get("ta")}
    tokens_carried = False
    if state("discovery") in ("ok", "partial") and scan.get("TOKENS"):
        tokens = [dict(t) for t in scan["TOKENS"]]
        new["TOK_AT"] = now
        if state("discovery") == "partial":
            notes.append("discovery partial: " + str((src.get("discovery") or {}).get("note", "")))
    else:
        tokens = [dict(t) for t in old_tokens]
        tokens_carried = True
        new["TOK_AT"] = oldv("TOK_AT", now)
        stale.append("tokens (discovery failed — previous TOKENS kept)")

    cand_at = now
    gt = state("geckoterminal")
    if not tokens_carried and gt != "ok":
        n = 0
        for t in tokens:
            o = old_by_pa.get((t.get("pa") or "").lower())
            if o:
                for k in CANDLE_KEYS:
                    t.pop(k, None)
                    if o.get(k) is not None:
                        t[k] = o[k]
                n += 1
        cand_at = oldv("CAND_AT", now)
        stale.append(f"candles/wallets ({gt}: {(src.get('geckoterminal') or {}).get('note') or (src.get('geckoterminal') or {}).get('error') or ''} — carried from {cand_at} for {n} tokens)")
    safety_bad = [k for k in ("rugcheck", "goplus", "blockscout") if state(k) == "fail"]
    if not tokens_carried and safety_bad:
        n = 0
        for t in tokens:
            o = old_by_pa.get((t.get("pa") or "").lower())
            if o and t.get("sec") in (None, "nodata") and o.get("sec") not in (None, "nodata"):
                for k in SAFETY_KEYS:
                    if o.get(k) is not None:
                        t[k] = o[k]
                n += 1
        if n:
            cand_at = oldv("CAND_AT", now)
        stale.append(f"safety ({', '.join(safety_bad)} failed — {n} tokens carried)")
    if tokens_carried:
        cand_at = oldv("CAND_AT", now)
    new["CAND_AT"] = cand_at

    # ---- narrative tags
    todo = []
    for t in tokens:
        if t.get("nar"):
            continue
        o = old_by_pa.get((t.get("pa") or "").lower()) or old_by_ta.get((t.get("ta") or "").lower())
        n = (o or {}).get("nar") or nar_map.get((t.get("pa") or "").lower()) or nar_map.get((t.get("ta") or "").lower())
        if n:
            t["nar"] = n
        elif a.allow_unclassified:
            t["nar"] = "Unclassified"
        else:
            todo.append({"pa": t.get("pa"), "ta": t.get("ta"), "c": t.get("c"), "sym": t.get("sym"), "nm": t.get("nm"),
                         "q": t.get("q"), "web": t.get("web"), "tw": t.get("tw"), "band": t.get("band"), "mc": t.get("mc")})
    os.makedirs(a.out, exist_ok=True)
    json.dump(todo, open(os.path.join(a.out, "nar_todo.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if todo:
        labels: dict[str, int] = {}
        for t in old_tokens:
            if t.get("nar"):
                labels[t["nar"]] = labels.get(t["nar"], 0) + 1
        print(f"{len(todo)} tokens need a narrative tag — see {os.path.join(a.out, 'nar_todo.json')}; labels in use: "
              + ", ".join(f"{k} ({v})" for k, v in sorted(labels.items(), key=lambda x: -x[1])))
        return 3

    # ---- gates
    for t in tokens:
        evaluate(t)
        flags(t)
    for t in old_tokens:
        evaluate(t)
    short = [t for t in tokens if is_short(t)]
    pull = [t for t in tokens if is_pull(t) and not is_short(t)]
    ign = sorted([t for t in tokens if is_ign(t)], key=lambda t: -t["_acc"])
    old_short = {t["sym"] for t in old_tokens if is_short(t)}
    old_pull = {t["sym"] for t in old_tokens if is_pull(t) and not is_short(t)}
    hot = [r["s"] for r in data if chain_hot(r)]
    old_hot = [r["s"] for r in old_data if chain_hot(r)]

    # ---- logs
    prices = scan.get("PRICES") or {}
    reprice_ok = state("reprice") in ("ok", "partial")
    hist = [dict(r) for r in oldv("HISTORY", [])]
    rej = [dict(r) for r in oldv("REJECTS", []) if r.get("d", "") >= cutoff]
    hit2x, tozero = [], []

    def reprice(row):
        if not reprice_ok or row.get("d", "") < cutoff:
            return
        key = f"{row['c']}:{row['pa']}"
        if key not in prices:
            return
        p = float(prices[key])
        was = row.get("pxNow")
        row["pxNow"] = p
        if p > (row.get("pxMax") or 0):
            row["pxMax"] = p
            row["dMax"] = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(row["d"], "%Y-%m-%d")).days
        px = row.get("px") or 0
        if px and (was or 0) / px < 2 <= p / px:
            hit2x.append(row)
        if p == 0 and (was or 0) > 0:
            tozero.append(row)

    for r in hist:
        reprice(r)
    for r in rej:
        reprice(r)
    have = {(r["pa"].lower(), r["l"]) for r in hist}
    new_hist = []
    if not tokens_carried:
        for lst, rows in (("S", short), ("P", pull)):
            for t in rows:
                if (t["pa"].lower(), lst) in have or t.get("px") is None:
                    continue
                row = {"d": today, "sym": t["sym"], "c": t["c"], "pa": t["pa"], "l": lst, "mc": t.get("mc"),
                       "px": t["px"], "pxNow": t["px"], "pxMax": t["px"], "dMax": 0}
                hist.append(row); new_hist.append(row); have.add((t["pa"].lower(), lst))
        have_r = {r["pa"].lower() for r in rej}
        near = sorted([t for t in tokens if t["_nf"] == 1 and t["pa"].lower() not in have_r and t.get("px") is not None],
                      key=lambda t: -(t.get("mc") or 0))[:REJECT_CAP]
        for t in near:
            why = next(k for k, v in t["_g"].items() if v is False)
            rej.append({"d": today, "sym": t["sym"], "c": t["c"], "pa": t["pa"], "mc": t.get("mc"), "px": t["px"],
                        "pxNow": t["px"], "pxMax": t["px"], "dMax": 0, "why": why})
    narhist = [r for r in oldv("NARHIST", []) if r.get("d", "") >= cutoff]
    holdhist = [r for r in oldv("HOLDHIST", []) if r.get("d", "") >= cutoff]
    if not tokens_carried:
        if not any(r.get("d") == today for r in narhist):
            counts: dict[str, int] = {}
            for t in tokens:
                counts[t["nar"]] = counts.get(t["nar"], 0) + 1
            narhist.append({"d": today, "t": counts})
        seen_h = {(r["d"], r["pa"].lower()) for r in holdhist}
        for t in short + pull:
            if t.get("hld") is not None and (today, t["pa"].lower()) not in seen_h:
                holdhist.append({"d": today, "pa": t["pa"], "w": t["hld"]})
    new["HISTORY"], new["REJECTS"], new["NARHIST"], new["HOLDHIST"] = hist, rej, narhist, holdhist
    new["LOG_AT"] = now
    new["TOKENS"] = [public(t) for t in tokens]

    # ---- write the block, constants and KOLS/CHAIN_ACCTS verbatim
    lines = []
    for name in ORDER:
        if name in ("CHAIN_GATE", "CORE_CHAINS", "MCAP", "LIQ_PCT", "TURN_MIN", "RAN_MULT", "DROP_MAX", "PULL", "IGN",
                    "MCAP_L", "TYPE", "KOLS", "CHAIN_ACCTS"):
            lines.append(old[name]["line"] + "\n")
        elif name in ("DATA", "REJECTS", "NARHIST", "HOLDHIST", "TOKENS", "HISTORY", "BRIDGES"):
            lines.append(rows_dump(name, new[name]))
        elif name == "CAND_AT":
            lines.append(f'const CAND_AT = {jsdump(new[name])};   // {"carried" if new[name] != now else "fresh candles + rug checks this run"}\n')
        elif name in ("SNAP_AT",):
            lines.append(f'const SNAP_AT = {jsdump(new[name])};   // chain scan time\n')
        elif name == "TOK_AT":
            lines.append(f'const TOK_AT  = {jsdump(new[name])};   // token scan time\n')
        elif name in ("NETFLOW_AT", "BRIDGES_AT", "REGIME_AT", "LOG_AT"):
            lines.append(f"const {name} = {jsdump(new[name])};\n")
        else:
            lines.append(f"const {name} = {jsdump(new[name])};\n")
    block_new = "\n" + "".join(lines)
    open(os.path.join(a.out, "page_new.html"), "w", encoding="utf-8").write(head + block_new + tail)

    # ---- track-record.csv
    with open(os.path.join(a.out, "track-record.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["logged_date", "symbol", "chain", "list", "entry_mcap_k", "entry_price_usd", "price_now_usd",
                    "now_vs_entry", "peak_vs_entry", "peak_on_day", "pair_url"])
        for r in sorted(hist, key=lambda r: (r["d"], r["sym"])):
            px = r.get("px") or 0
            w.writerow([r["d"], r["sym"], r["c"], "shortlist" if r["l"] == "S" else "pullback", r.get("mc"), r.get("px"),
                        r.get("pxNow"), round(r["pxNow"] / px, 3) if px and r.get("pxNow") is not None else "",
                        round(r["pxMax"] / px, 3) if px and r.get("pxMax") is not None else "", r.get("dMax"),
                        f"https://dexscreener.com/{r['c']}/{r['pa']}"])

    # ---- summary for the report
    def brief(t):
        return {"sym": t["sym"], "c": t["c"], "mc": t.get("mc"), "nar": t.get("nar"), "run": t.get("run"), "retr": t.get("retr"),
                "acc": t.get("_acc"), "c6": t.get("c6"), "age": t.get("age"), "sec": t.get("sec"), "pa": t["pa"]}
    mult = lambda r: (r["pxMax"] / r["px"]) if r.get("px") else None  # noqa: E731
    pick_m = [m for m in map(mult, hist) if m]
    rej_m = [m for m in map(mult, rej) if m]
    hottest = max((r for r in data if (r.get("dv") or 0) >= C.CHAIN_GATE["dv"] and r.get("dr") is not None), key=lambda r: r["dr"], default=None)
    honey_new = [t["sym"] for t in tokens if t.get("honey") == 1 and (old_by_pa.get(t["pa"].lower()) or {}).get("honey") != 1]
    band_m = [t for t in tokens if t.get("band") == "m"]
    cov = sum(1 for t in band_m if t.get("run") is not None or t.get("ubr") is not None)
    summary = {
        "now": now, "cand_at": cand_at, "tokens_carried": tokens_carried, "stale": stale, "notes": notes,
        "sources": {k: v.get("state") for k, v in src.items()},
        "chains": {"hot": hot, "new": sorted(set(hot) - set(old_hot)), "dropped": sorted(set(old_hot) - set(hot)),
                   "hottest": {"s": hottest["s"], "n": hottest.get("n"), "dr": hottest["dr"], "dr30": hottest.get("dr30")} if hottest else None},
        "tokens": {"total": len(tokens), "band_m": len(band_m), "candle_or_wallet_coverage_pct": round(100 * cov / len(band_m)) if band_m else 0,
                   "nodata": sum(1 for t in tokens if t.get("sec") in (None, "nodata"))},
        "shortlist": [brief(t) for t in short], "shortlist_new": sorted({t["sym"] for t in short} - old_short),
        "shortlist_dropped": sorted(old_short - {t["sym"] for t in short}),
        "pullback": [brief(t) for t in pull], "pullback_new": sorted({t["sym"] for t in pull} - old_pull),
        "pullback_dropped": sorted(old_pull - {t["sym"] for t in pull}),
        "igniting": [brief(t) for t in ign],
        "honeypots_new": honey_new,
        "track": {"logged": len(hist), "new_rows": [(r["sym"], r["l"]) for r in new_hist],
                  "best_x": round(max(pick_m), 2) if pick_m else None,
                  "median_peak_x": round(statistics.median(pick_m), 2) if pick_m else None,
                  "hit_2x_today": [r["sym"] for r in hit2x], "to_zero_today": [r["sym"] for r in tozero],
                  "repriced": reprice_ok},
        "control": {"rows": len(rej), "median_peak_x": round(statistics.median(rej_m), 2) if rej_m else None},
        "changed": bool(({t["sym"] for t in short} != old_short) or ({t["sym"] for t in pull} != old_pull) or (set(hot) != set(old_hot))),
    }
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: summary[k] for k in ("now", "cand_at", "stale", "chains", "tokens", "shortlist_new", "shortlist_dropped",
                                               "pullback_new", "pullback_dropped", "changed")}, ensure_ascii=False))
    print(f"shortlist {len(short)} · pullback {len(pull)} · igniting {len(ign)} · history {len(hist)} (+{len(new_hist)}) · rejects {len(rej)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
