"""STEP 2 — token discovery + DexScreener pair rows.

Two independent discovery routes, merged (dedupe by pair address):
  1. DexScreener screener pages (server-rendered HTML, three passes: volume / trending-6h / leaders)
  2. GeckoTerminal pools API (volume pages + trending pools) — the datacenter-safe route
Then one DexScreener pairs-API call per ≤30 pairs builds the numeric TOKEN ROW fields.
Band (m/l) is decided from DexScreener's marketCap, not from which pass surfaced the pair.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from .common import Http, Status, BlockedError, r1, r2, safe_div, drop_none, now_utc, log

DS = "https://dexscreener.com"
DS_API = "https://api.dexscreener.com"
GT = "https://api.geckoterminal.com/api/v2"

# DefiLlama slug -> DexScreener chainId(s)
LLAMA_TO_DS: dict[str, list[str]] = {
    "solana": ["solana"], "ethereum": ["ethereum"], "base": ["base"], "bsc": ["bsc"], "robinhood": ["robinhood"],
    "optimism": ["optimism"], "ink": ["ink"], "near": ["near"], "tron": ["tron"], "unichain": ["unichain"],
    "hyperliquid": ["hyperliquid", "hyperevm"], "arbitrum": ["arbitrum"], "polygon": ["polygon"], "mantle": ["mantle"],
    "plasma": ["plasma"], "monad": ["monad"], "sui": ["sui"], "avax": ["avalanche"], "starknet": ["starknet"],
    "sei": ["sei"], "aptos": ["aptos"], "stacks": ["stacks"], "celo": ["celo"], "flare": ["flare"],
    "cronos": ["cronos"], "ton": ["ton"], "pulsechain": ["pulsechain"], "sonic": ["sonic"], "abstract": ["abstract"],
    "berachain": ["berachain"], "linea": ["linea"], "scroll": ["scroll"], "zksync": ["zksync"], "blast": ["blast"],
    "elysium": ["elysium"],
}
# DexScreener chainId -> GeckoTerminal network id
DS_TO_GT: dict[str, str] = {
    "solana": "solana", "ethereum": "eth", "base": "base", "bsc": "bsc", "robinhood": "robinhood",
    "optimism": "optimism", "arbitrum": "arbitrum", "polygon": "polygon_pos", "avalanche": "avax",
    "hyperevm": "hyperevm", "hyperliquid": "hyperliquid", "unichain": "unichain", "ink": "ink", "sonic": "sonic",
    "monad": "monad", "plasma": "plasma", "mantle": "mantle", "sui": "sui-network", "sei": "sei-evm", "ton": "ton",
    "tron": "tron", "near": "near", "aptos": "aptos", "celo": "celo", "cronos": "cro", "abstract": "abstract",
    "berachain": "berachain", "linea": "linea", "scroll": "scroll", "zksync": "zksync", "blast": "blast",
    "pulsechain": "pulsechain", "starknet": "starknet-alpha", "flare": "flare", "stacks": "stacks",
    "elysium": "elysium",
}

MCAP_M = (500.0, 2000.0)      # $k
MCAP_L_MIN = 2000.0
# --- added 11 Nov 2026 (owner's request; experiments judged at the 22 Nov / 6 Dec reviews) ---
MCAP_E = (200.0, 500.0)       # "Early watch" band: core chains only, its own list, never on the five main lists
MCAP_X = (50.0, 200.0)        # "Micro watch": quick check only (Solana), logged silently, needs a $10k+ pool
LIQ_X_MIN_K = 10.0
CAP_E, CAP_B, CAP_X = 12, 10, 80
LIQ_PCT, LIQ_MIN_K, LIQ_L_MIN_K = 5.0, 25.0, 100.0
TURN_MIN, DROP_MAX = 1.0, -40.0
CAP_M, CAP_L = 45, 6          # leaders capped lower than the prompt's ~10: each one costs GeckoTerminal budget the moonshot band needs more

STABLE_RE = re.compile(r"^(usd|usdc|usdt|usde|usdg|dai|fdusd|pyusd|usd1|usdd|usds|tusd|frax|lusd|gusd|usdb|usdx|usdh|eurc|eurt|xusd|susd|susde|ausd|honey|nusd|usd\+|usdt0|usdc\.e|usdbc|musd|cusd|dusd|ust|usdm)$", re.I)
NATIVE_WRAPPED = {"SOL", "WSOL", "ETH", "WETH", "BNB", "WBNB", "HYPE", "WHYPE", "S", "WS", "POL", "WPOL", "MATIC", "WMATIC", "AVAX", "WAVAX",
                  "TRX", "WTRX", "TON", "WTON", "SUI", "APT", "NEAR", "WNEAR", "MNT", "WMNT", "MON", "WMON", "XPL", "WXPL", "SEI", "WSEI",
                  "CRO", "WCRO", "CELO", "FLR", "WFLR", "STX", "BERA", "WBERA", "PLS", "WPLS", "STRK", "ETHW", "OP", "ARB", "INK", "USDH"}
BRIDGED_MAJORS = {"WBTC", "CBBTC", "TBTC", "BTCB", "WSTETH", "STETH", "CBETH", "WEETH", "RETH", "EZETH", "RSETH", "JITOSOL", "MSOL", "BSOL",
                  "JUPSOL", "INF", "UETH", "UBTC", "USOL", "XAUT", "PAXG", "WHYPE", "KHYPE", "STHYPE", "BEHYPE", "LHYPE"}


def is_unit_not_bet(sym: str, name: str, quote: str) -> bool:
    s = (sym or "").upper().strip()
    if STABLE_RE.match(s) or s in NATIVE_WRAPPED or s in BRIDGED_MAJORS:
        return True
    n = (name or "").lower()
    if any(w in n for w in ("usd coin", "tether", "stablecoin", "wrapped ether", "wrapped sol", "wrapped bnb", "staked eth", "staked sol")):
        return True
    q = (quote or "").upper()
    if STABLE_RE.match(s) and STABLE_RE.match(q):
        return True
    return False


# ---------------------------------------------------------------- discovery routes
_DS_DEAD = {"fails": 0}          # circuit breaker: the screener HTML refused every call from a GitHub runner on 19 Sep


def ds_screener(http: Http, chain: str, st: Status) -> tuple[set[str], set[str], set[str], bool]:
    """Pairs from DexScreener's server-rendered screener pages. Returns (A, B, C, worked).
    After three chains where every pass failed, stops trying (saves ~10s/chain and the log noise)."""
    if _DS_DEAD["fails"] >= 3:
        return set(), set(), set(), False
    passes = {
        "A": f"{DS}/{chain}?rankBy=volume&order=desc&minLiq=50000&minMarketCap=500000&maxMarketCap=2000000",
        "B": f"{DS}/{chain}?rankBy=trendingScoreH6&order=desc&minLiq=50000&minMarketCap=500000&maxMarketCap=2000000",
        "C": f"{DS}/{chain}?rankBy=volume&order=desc&minLiq=100000&minMarketCap=2000000",
    }
    out = {"A": set(), "B": set(), "C": set()}
    worked = False
    rx = re.compile(rf'href="/{re.escape(chain)}/([A-Za-z0-9]{{20,90}})"')
    for k, url in passes.items():
        try:
            html = http.get(url, json_out=False, retries=1)
            found = set(rx.findall(html or ""))
            if not found and ("challenge" in (html or "").lower()[:3000] or "cf-" in (html or "")[:3000]):
                raise BlockedError("cloudflare challenge")
            out[k] = found
            worked = worked or bool(found)
        except Exception as e:  # noqa: BLE001
            log.warning("DexScreener screener %s pass %s: %s", chain, k, str(e)[:100])
    if not worked:
        _DS_DEAD["fails"] += 1
        if _DS_DEAD["fails"] == 3:
            log.warning("DexScreener screener pages refused 3 chains in a row — disabling that route for this run; GeckoTerminal carries discovery")
    return out["A"], out["B"], out["C"], worked


def gt_pools(http: Http, chain: str) -> tuple[set[str], bool, set[str]]:
    """Candidate pool addresses from GeckoTerminal: 8 pages by 24h volume + trending (5m/1h/6h/24h), plus the
    "wider search" (11 Nov 2026): the newest pools and the pools with the most trades. Returns (all, worked,
    found only by the wider search) — the last set is measured so the review can judge whether it is worth its calls."""
    net = DS_TO_GT.get(chain)
    if not net:
        return set(), False, set()
    found: set[str] = set()
    wider: set[str] = set()
    worked = False
    # GeckoTerminal has no market-cap filter, and the $500k-$2m band sits well below the first page of a
    # volume-sorted listing (run #2 on 19 Sep: 1-2 pages/chain -> 14 moonshot tokens vs 76 on the dashboard).
    # So: 8 volume pages + all four trending windows (trending skews small-cap). 12 calls/chain, retried patiently.
    urls = [f"{GT}/networks/{net}/pools?page={p}&sort=h24_volume_usd_desc" for p in range(1, 9)]
    urls += [f"{GT}/networks/{net}/trending_pools?duration={d}" for d in ("5m", "1h", "6h", "24h")]
    n_old = len(urls)
    urls += [f"{GT}/networks/{net}/new_pools?page=1"]
    urls += [f"{GT}/networks/{net}/pools?page={p}&sort=h24_tx_count_desc" for p in (1, 2)]
    for i, u in enumerate(urls):
        try:
            j = http.get(u, headers={"Accept": "application/json;version=20230302"}, retries=3)
            if not j:
                continue
            for pool in j.get("data", []):
                addr = (pool.get("attributes") or {}).get("address")
                if addr:
                    (found if i < n_old else wider).add(addr)
            worked = True
        except Exception as e:  # noqa: BLE001
            log.warning("GeckoTerminal discovery %s: %s", chain, str(e)[-160:])
            if isinstance(e, BlockedError):
                break
    wider_only = wider - found
    return found | wider, worked, wider_only


def ds_promoted(http: Http) -> dict[str, set[str]]:
    """DexScreener's public API (not the challenged screener pages): boosted + profiled tokens, chain-agnostic.
    Paid promotion skews small-cap, so this is a cheap second discovery route. Returns chainId -> token addresses."""
    out: dict[str, set[str]] = {}
    for path in ("token-boosts/top/v1", "token-boosts/latest/v1", "token-profiles/latest/v1"):
        try:
            j = http.get(f"{DS_API}/{path}", retries=2)
            for it in j or []:
                c, ta = it.get("chainId"), it.get("tokenAddress")
                if c and ta:
                    out.setdefault(c, set()).add(ta)
        except Exception as e:  # noqa: BLE001
            log.warning("DexScreener %s: %s", path, str(e)[-120:])
    return out


def ds_tokens_to_pairs(http: Http, chain: str, tas: list[str]) -> list[dict]:
    """Resolve token addresses to their best pair via the public tokens API (30 per call)."""
    rows: list[dict] = []
    for i in range(0, len(tas), 30):
        try:
            j = http.get(f"{DS_API}/tokens/v1/{chain}/{','.join(tas[i:i + 30])}", retries=2)
            for p in j or []:
                r = pair_to_row(p)
                if r:
                    rows.append(r)
        except Exception as e:  # noqa: BLE001
            log.warning("DexScreener tokens %s: %s", chain, str(e)[-120:])
    return rows


# ---------------------------------------------------------------- DexScreener pairs -> rows
def _twitter(info: dict) -> Optional[str]:
    for s in (info or {}).get("socials") or []:
        if (s.get("type") or "").lower() in ("twitter", "x"):
            u = s.get("url") or ""
            m = re.search(r"(?:twitter\.com|x\.com)/([^/?#]+(?:/status/\d+)?)", u)
            return m.group(1) if m else (u.split("/")[-1] or None)
    return None


def pair_to_row(p: dict) -> Optional[dict]:
    try:
        base, quote = p.get("baseToken") or {}, p.get("quoteToken") or {}
        mc = p.get("marketCap") if p.get("marketCap") is not None else p.get("fdv")
        liq = (p.get("liquidity") or {}).get("usd")
        if mc is None or liq is None:
            return None
        mc_k, liq_k = float(mc) / 1e3, float(liq) / 1e3
        vol, tx, pc = p.get("volume") or {}, p.get("txns") or {}, p.get("priceChange") or {}
        created = p.get("pairCreatedAt")
        age = r2((now_utc().timestamp() * 1000 - float(created)) / 86400000) if created else None
        info = p.get("info") or {}
        webs = info.get("websites") or []
        row = {
            "c": p.get("chainId"), "pa": p.get("pairAddress"), "sym": base.get("symbol"), "ta": base.get("address"),
            "q": quote.get("symbol"), "dex": p.get("dexId"),
            "mc": r1(mc_k), "liq": r1(liq_k), "lp": r1(safe_div(liq_k, mc_k) * 100) if mc_k else None,
            "v24": r1(float(vol.get("h24") or 0) / 1e3), "v6": r1(float(vol.get("h6") or 0) / 1e3), "v1": r1(float(vol.get("h1") or 0) / 1e3),
            "b6": (tx.get("h6") or {}).get("buys"), "s6": (tx.get("h6") or {}).get("sells"),
            "turn": r1(safe_div(float(vol.get("h24") or 0) / 1e3, liq_k)) if liq_k else None,
            "c1": r2(pc.get("h1")), "c6": r2(pc.get("h6")), "c24": r2(pc.get("h24")),
            "age": age, "tw": _twitter(info), "web": (webs[0].get("url") if webs and isinstance(webs[0], dict) else None),
            "bo": (p.get("boosts") or {}).get("active"),
            "px": float(p.get("priceUsd")) if p.get("priceUsd") not in (None, "") else None,
            "_b24": (tx.get("h24") or {}).get("buys"), "_s24": (tx.get("h24") or {}).get("sells"),
            "nm": base.get("name") if base.get("name") and base.get("name") != base.get("symbol") else None,
        }
        return row
    except Exception as e:  # noqa: BLE001
        log.warning("pair_to_row: %s", e)
        return None


def ds_pairs(http: Http, chain: str, addrs: list[str]) -> list[dict]:
    rows: list[dict] = []
    for i in range(0, len(addrs), 30):
        batch = addrs[i:i + 30]
        try:
            j = http.get(f"{DS_API}/latest/dex/pairs/{chain}/{','.join(batch)}", retries=2)
            for p in (j or {}).get("pairs") or []:
                r = pair_to_row(p)
                if r:
                    rows.append(r)
        except Exception as e:  # noqa: BLE001
            log.warning("DexScreener pairs %s batch %d: %s", chain, i // 30, str(e)[:100])
    return rows


def narrow_and_band(rows: list[dict], bands: tuple = ("m", "l"), watch: Optional[set] = None) -> list[dict]:
    """Assign band from mcap, apply the pre-enrichment narrowing, dedupe per token, cap per chain.
    Bands: m moonshot $500k-$2m, l leader $2m+, e early $200-500k, x micro $50-200k, b breakout (a token seen in
    the m or e band in the last 7 days that is now above $2m — taken from `watch`, the pair addresses to follow)."""
    watch = {w.lower() for w in (watch or ())}
    by_token: dict[str, dict] = {}
    for r in rows:
        key = (r.get("ta") or "").lower()
        if not key:
            continue
        if key not in by_token or (r.get("v24") or 0) > (by_token[key].get("v24") or 0):
            by_token[key] = r
    keep: list[dict] = []
    for r in by_token.values():
        mc, liq, lp, turn = r.get("mc") or 0, r.get("liq") or 0, r.get("lp") or 0, r.get("turn") or 0
        if MCAP_M[0] <= mc <= MCAP_M[1]:
            band = "m"
        elif mc >= MCAP_L_MIN:
            if is_unit_not_bet(r.get("sym"), r.get("nm"), r.get("q")):
                continue
            followed = (r.get("pa") or "").lower() in watch or (r.get("ta") or "").lower() in watch
            band = "b" if ("b" in bands and followed) else "l"
            if band == "l" and "l" not in bands:
                continue
        elif "e" in bands and MCAP_E[0] <= mc < MCAP_E[1]:
            band = "e"
        elif "x" in bands and MCAP_X[0] <= mc < MCAP_X[1]:
            band = "x"
        else:
            continue
        if band == "x":
            if liq < LIQ_X_MIN_K or is_unit_not_bet(r.get("sym"), r.get("nm"), r.get("q")):
                continue
        elif band == "b":
            if liq < LIQ_MIN_K:
                continue
        elif band in ("m", "e"):
            # pre-enrichment narrowing (the page re-applies these as gates; this just saves API budget)
            if lp < LIQ_PCT or liq < LIQ_MIN_K or turn < TURN_MIN:
                continue
            if (r.get("c6") is not None and r["c6"] <= DROP_MAX) or (r.get("c24") is not None and r["c24"] <= DROP_MAX):
                continue
        else:
            # leaders: only the pass-C liquidity floor. Never drop a leader for lp% or turnover —
            # live data shows category leaders at lp 0.1–0.3% and turnover 0.1×, and that is what they look like.
            if liq < LIQ_L_MIN_K:
                continue
        r["band"] = band
        keep.append(r)
    caps = {"m": CAP_M, "l": CAP_L, "e": CAP_E, "b": CAP_B, "x": CAP_X}
    out: list[dict] = []
    for b in ("m", "b", "e", "l", "x"):
        out += sorted([r for r in keep if r["band"] == b], key=lambda r: -(r.get("v24") or 0))[:caps[b]]
    return out


# ---------------------------------------------------------------- orchestration
def run(http: Http, st: Status, passing_slugs: list[str], core: list[str], watch: Optional[dict] = None,
        early_chains: Optional[list] = None, micro_chains: tuple = (), leaders: bool = True) -> dict[str, Any]:
    """watch: chainId -> {pair address: token address} seen in the m/e band in the last 7 days (for Breakouts).
    early_chains: DexScreener chainIds also searched for the Early band ($200-500k); micro_chains: for the Micro band."""
    watch = watch or {}
    early = set(early_chains or [])
    http.pace("api.dexscreener.com", 200)
    http.pace("dexscreener.com", 30)
    http.pace("api.geckoterminal.com", 12)     # shared runner IP: 25/min tripped 429s on ~half the calls in run #2
    promoted = ds_promoted(http)
    slugs = list(dict.fromkeys(list(core) + list(passing_slugs)))
    chains: list[str] = []
    no_ds: list[str] = []
    for s in slugs:
        ids = LLAMA_TO_DS.get(s)
        if ids:
            chains.extend(ids)
        else:
            no_ds.append(s)
    chains = list(dict.fromkeys(chains))

    tokens: list[dict] = []
    per_chain: dict[str, dict] = {}
    ds_ok = gt_ok = 0
    done = 0
    for chain in chains:
        if http.out_of_time():
            log.warning("discovery: time limit reached after %d of %d chains (%.0f min) — skipping %s",
                        done, len(chains), http.elapsed_min(), ", ".join(chains[done:]))
            break
        a, b, c, ds_worked = ds_screener(http, chain, st)
        g, gt_worked, wider_only = gt_pools(http, chain)
        ds_ok += int(ds_worked)
        gt_ok += int(gt_worked)
        cands = list(dict.fromkeys(list(a) + list(b) + list(c) + list(g)))
        have = {x.lower() for x in cands}
        cands += [w for w in sorted(watch.get(chain, {})) if w.lower() not in have]   # followed for Breakouts
        rows = ds_pairs(http, chain, cands) if cands else []
        promo = sorted(promoted.get(chain, set()))
        if promo:
            seen = {r["pa"].lower() for r in rows}
            rows += [r for r in ds_tokens_to_pairs(http, chain, promo) if r["pa"].lower() not in seen]
        bands = (("m",) + (("l",) if leaders else ()) + (("e",) if chain in early else ())
                 + (("x",) if chain in micro_chains else ()) + (("b",) if watch.get(chain) else ()))
        wl = watch.get(chain) or {}
        kept = narrow_and_band(rows, bands, set(wl) | {v for v in wl.values() if v})
        wider_l = {x.lower() for x in wider_only}
        promo_l = {x.lower() for x in promo}
        for r in kept:
            r["_from_ds"] = (r["pa"] in a) or (r["pa"] in b) or (r["pa"] in c) or (r["pa"].lower() in {x.lower() for x in a | b | c})
            if r["pa"].lower() in wider_l and (r.get("ta") or "").lower() not in promo_l and not r["_from_ds"]:
                r["ws"] = 1                    # found only by the wider search (newest pools / most trades)
        per_chain[chain] = {"screened": len(cands), "promoted": len(promo), "priced": len(rows), "kept_m": sum(r["band"] == "m" for r in kept),
                            "kept_l": sum(r["band"] == "l" for r in kept), "kept_e": sum(r["band"] == "e" for r in kept),
                            "kept_b": sum(r["band"] == "b" for r in kept), "kept_x": sum(r["band"] == "x" for r in kept),
                            "wider_found": len(wider_only), "wider_kept": sum(1 for r in kept if r.get("ws")),
                            "ds": ds_worked, "gt": gt_worked}
        tokens.extend(kept)
        done += 1
        log.info("discovery %s: %d candidates, %d kept (%.0f min, GeckoTerminal rate-limited %d times so far)",
                 chain, len(cands), len(kept), http.elapsed_min(), http.n429.get("api.geckoterminal.com", 0))
        time.sleep(0.5)
    cut = f"; time limit hit — only {done} of {len(chains)} chains scanned" if done < len(chains) else ""
    if ds_ok == 0 and gt_ok == 0:
        st.fail("discovery", "neither DexScreener screener pages nor GeckoTerminal pools answered", chains=len(chains))
    elif ds_ok == 0 or gt_ok == 0:
        st.partial("discovery", f"only one route worked (dexscreener={ds_ok}, geckoterminal={gt_ok} of {len(chains)} chains){cut}",
                   tokens=len(tokens), per_chain=per_chain)
    elif cut:
        st.partial("discovery", cut[2:], tokens=len(tokens), per_chain=per_chain)
    else:
        st.ok("discovery", tokens=len(tokens), per_chain=per_chain)
    # only the chains actually searched: on a busy day the time limit can stop before the last ones
    return {"tokens": tokens, "chains_scanned": chains[:done], "NO_DEXSCREENER": sorted(no_ds)}
