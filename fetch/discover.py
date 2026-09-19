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
}

MCAP_M = (500.0, 2000.0)      # $k
MCAP_L_MIN = 2000.0
LIQ_PCT, LIQ_MIN_K, LIQ_L_MIN_K = 5.0, 25.0, 100.0
TURN_MIN, DROP_MAX = 1.0, -40.0
CAP_M, CAP_L = 45, 10

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


def gt_pools(http: Http, chain: str) -> tuple[set[str], bool]:
    """Candidate pool addresses from GeckoTerminal: 5 pages by 24h volume + trending (6h and 24h)."""
    net = DS_TO_GT.get(chain)
    if not net:
        return set(), False
    found: set[str] = set()
    worked = False
    # 3 volume pages + 6h trending = 4 calls per chain. First live run (19 Sep) showed 8 calls/chain tripping
    # GeckoTerminal's per-minute limit (429s on the 24h-trending and new_pools calls) and ~75s per chain.
    urls = [f"{GT}/networks/{net}/pools?page={p}&sort=h24_volume_usd_desc" for p in range(1, 4)]
    urls += [f"{GT}/networks/{net}/trending_pools?duration=6h"]
    for u in urls:
        try:
            j = http.get(u, headers={"Accept": "application/json;version=20230302"}, retries=1)
            if not j:
                continue
            for pool in j.get("data", []):
                addr = (pool.get("attributes") or {}).get("address")
                if addr:
                    found.add(addr)
            worked = True
        except Exception as e:  # noqa: BLE001
            log.warning("GeckoTerminal discovery %s: %s", chain, str(e)[:100])
            if isinstance(e, BlockedError):
                break
    return found, worked


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


def narrow_and_band(rows: list[dict]) -> list[dict]:
    """Assign band from mcap, apply the pre-enrichment narrowing, dedupe per token, cap per chain."""
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
            band = "l"
        else:
            continue
        if band == "m":
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
    m = sorted([r for r in keep if r["band"] == "m"], key=lambda r: -(r.get("v24") or 0))[:CAP_M]
    l = sorted([r for r in keep if r["band"] == "l"], key=lambda r: -(r.get("v24") or 0))[:CAP_L]
    return m + l


# ---------------------------------------------------------------- orchestration
def run(http: Http, st: Status, passing_slugs: list[str], core: list[str]) -> dict[str, Any]:
    http.pace("api.dexscreener.com", 200)
    http.pace("dexscreener.com", 30)
    http.pace("api.geckoterminal.com", 25)
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
    for chain in chains:
        a, b, c, ds_worked = ds_screener(http, chain, st)
        g, gt_worked = gt_pools(http, chain)
        ds_ok += int(ds_worked)
        gt_ok += int(gt_worked)
        cands = list(dict.fromkeys(list(a) + list(b) + list(c) + list(g)))
        rows = ds_pairs(http, chain, cands) if cands else []
        kept = narrow_and_band(rows)
        for r in kept:
            r["_from_ds"] = (r["pa"] in a) or (r["pa"] in b) or (r["pa"] in c) or (r["pa"].lower() in {x.lower() for x in a | b | c})
        per_chain[chain] = {"screened": len(cands), "priced": len(rows), "kept_m": sum(r["band"] == "m" for r in kept),
                            "kept_l": sum(r["band"] == "l" for r in kept), "ds": ds_worked, "gt": gt_worked}
        tokens.extend(kept)
        time.sleep(0.5)
    if ds_ok == 0 and gt_ok == 0:
        st.fail("discovery", "neither DexScreener screener pages nor GeckoTerminal pools answered", chains=len(chains))
    elif ds_ok == 0 or gt_ok == 0:
        st.partial("discovery", f"only one route worked (dexscreener={ds_ok}, geckoterminal={gt_ok} of {len(chains)} chains)",
                   tokens=len(tokens), per_chain=per_chain)
    else:
        st.ok("discovery", tokens=len(tokens), per_chain=per_chain)
    return {"tokens": tokens, "chains_scanned": chains, "NO_DEXSCREENER": sorted(no_ds)}
