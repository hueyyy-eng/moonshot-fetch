"""STEP 1 — chains (DefiLlama): DEX volume, TVL, stablecoins, perps + OI, net flows, bridges, majors, fees.

Produces the CHAIN ROW shape the dashboard's DATA array uses. Every sub-source is best-effort and
reports into Status so the cloud scan can carry the previous block forward for anything that failed.
"""
from __future__ import annotations

import json
import re
import statistics
from typing import Any, Optional

from .common import Http, Status, BlockedError, r1, r2, safe_div, drop_none, today_utc_midnight_ts, log

LLAMA = "https://api.llama.fi"
COINS = "https://coins.llama.fi"
STAB = "https://stablecoins.llama.fi"
SITE = "https://defillama.com"

CORE_CHAINS = ["solana", "base", "bsc", "hyperliquid", "robinhood"]
DEX_MIN_M = 2.0        # universe: DEX 24h ≥ $2m
TVL_MIN_M = 100.0      # universe: TVL ≥ $100m

# DefiLlama display name -> our slug. Anything not listed becomes name.lower() with spaces→'-'.
ALIASES = {
    "op mainnet": "optimism", "op-mainnet": "optimism", "optimism": "optimism",
    "x layer": "xlayer", "x-layer": "xlayer", "xlayer": "xlayer",
    "avalanche": "avax", "avax": "avax",
    "gnosis": "xdai", "xdai": "xdai",
    "hyperliquid l1": "hyperliquid", "hyperliquid-l1": "hyperliquid", "hyperliquid": "hyperliquid",
    "robinhood chain": "robinhood", "robinhood-chain": "robinhood", "robinhood": "robinhood",
    "binance": "bsc", "bsc": "bsc", "bnb chain": "bsc",
    "core": "native_core", "polygon pos": "polygon", "polygon": "polygon",
    "zklighter": "zklighter", "edgex l1": "edgex", "edgex": "edgex",
}
SKIP_CHAINS = {"off_chain", "off-chain", "offchain", "multi-chain", "multichain"}


def to_slug(name: str) -> Optional[str]:
    if not name:
        return None
    n = name.strip().lower()
    if n in SKIP_CHAINS:
        return None
    if n in ALIASES:
        return ALIASES[n]
    n = re.sub(r"\s+l1$", "", n)
    if n in ALIASES:
        return ALIASES[n]
    return re.sub(r"[^a-z0-9_]+", "-", n).strip("-")


def display_name(name: str) -> str:
    return re.sub(r"\s+L1$", "", name.strip())


def _settled(chart: list, cutoff_ts: int) -> list[tuple[int, float]]:
    """[[ts, value], ...] -> sorted, buckets before today's UTC midnight only."""
    out = []
    for row in chart or []:
        try:
            ts, v = int(row[0]), float(row[1] or 0)
        except Exception:
            continue
        if ts < cutoff_ts:
            out.append((ts, v))
    out.sort()
    return out


def _try(http: Http, urls: list[str], **kw):
    last = None
    for u in urls:
        try:
            r = http.get(u, **kw)
            if r is not None:
                return r
        except BlockedError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    if last:
        raise last
    return None


# ---------------------------------------------------------------- universe
def universe(http: Http, st: Status) -> tuple[dict[str, str], dict[str, float]]:
    """Returns ({slug: display name}, {slug: dex24h $m}) for chains worth scanning."""
    names: dict[str, str] = {}
    dex24: dict[str, float] = {}
    try:
        ov = http.get(f"{LLAMA}/overview/dexs", params={"excludeTotalDataChart": "true",
                                                         "excludeTotalDataChartBreakdown": "true"})
        agg: dict[str, float] = {}
        for p in ov.get("protocols", []):
            for chain, per_proto in (p.get("breakdown24h") or {}).items():
                try:
                    v = sum(float(x or 0) for x in per_proto.values()) if isinstance(per_proto, dict) else float(per_proto or 0)
                except Exception:
                    v = 0.0
                agg[chain] = agg.get(chain, 0.0) + v
        for chain, v in agg.items():
            s = to_slug(chain)
            if not s:
                continue
            dex24[s] = dex24.get(s, 0.0) + v / 1e6
            names.setdefault(s, display_name(chain))
        st.ok("llama.dexs_overview", chains=len(agg))
    except Exception as e:  # noqa: BLE001
        st.fail("llama.dexs_overview", e)

    try:
        ch = http.get(f"{LLAMA}/v2/chains")
        big = 0
        for c in ch or []:
            s = to_slug(c.get("name", ""))
            if not s:
                continue
            # /v2/chains carries the proper display name ("Solana"); the dexs breakdown keys are lowercase slugs
            names[s] = display_name(c.get("name", "")) or names.get(s, s)
            if float(c.get("tvl") or 0) / 1e6 >= TVL_MIN_M:
                big += 1
                dex24.setdefault(s, 0.0)
        st.ok("llama.chains", tvl_ge_100m=big)
    except Exception as e:  # noqa: BLE001
        st.fail("llama.chains", e)

    keep = {s for s, v in dex24.items() if v >= DEX_MIN_M} | set(CORE_CHAINS)
    # TVL-qualified chains were seeded with 0 above; keep them too
    keep |= {s for s in dex24 if s not in keep and dex24[s] == 0.0}
    for s in CORE_CHAINS:
        names.setdefault(s, {"bsc": "BSC", "robinhood": "Robinhood Chain"}.get(s, s.capitalize()))
    return {s: names[s] for s in keep if s in names}, dex24


# ---------------------------------------------------------------- per-chain rows
def chain_row(http: Http, slug: str, name: str, cutoff: int) -> dict[str, Any]:
    row: dict[str, Any] = {"s": slug, "n": name}
    # DEX volume
    try:
        d = _try(http, [f"{LLAMA}/overview/dexs/{slug}?excludeTotalDataChartBreakdown=true",
                        f"{LLAMA}/overview/dexs/{name}?excludeTotalDataChartBreakdown=true"])
        if d:
            settled = _settled(d.get("totalDataChart"), cutoff)
            t24 = float(d.get("total24h") or 0) / 1e6
            last7 = [v / 1e6 for _, v in settled[-7:]]
            row["dv"] = r1(t24)
            row["dr"] = r2(safe_div(t24, statistics.mean(last7))) if last7 else None
            row["d30"] = r1(float(d.get("total30d") or 0) / 1e6) if d.get("total30d") else None
            row["dr30"] = r2(safe_div(t24, (float(d.get("total30d") or 0) / 1e6) / 30)) if d.get("total30d") else None
            row["ds"] = [r1(v / 1e6) for _, v in settled[-14:]]
            row["_lastday"] = settled[-1][0] if settled else None
    except Exception as e:  # noqa: BLE001
        row["_err_dex"] = str(e)[:120]
    # TVL
    try:
        t = _try(http, [f"{LLAMA}/v2/historicalChainTvl/{name}", f"{LLAMA}/v2/historicalChainTvl/{slug}"])
        if t:
            pts = sorted((int(x["date"]), float(x["tvl"] or 0) / 1e6) for x in t if "date" in x)
            pts = [p for p in pts if p[0] < cutoff + 86400]  # today's point is fine for TVL (a level, not a flow)
            if pts:
                tv = pts[-1][1]
                prior7 = [v for _, v in pts[-8:-1]]
                row["tv"] = r1(tv)
                row["tr"] = r2(safe_div(tv, statistics.mean(prior7))) if prior7 else None
                row["t7"] = r2((tv / pts[-8][1] - 1) * 100) if len(pts) >= 8 and pts[-8][1] else None
                row["t30"] = r2((tv / pts[-31][1] - 1) * 100) if len(pts) >= 31 and pts[-31][1] else None
                row["ts"] = [r1(v) for _, v in pts[-14:]]
    except Exception as e:  # noqa: BLE001
        row["_err_tvl"] = str(e)[:120]
    # Stablecoins (chain in the PATH — a ?chain= query silently returns global totals)
    try:
        sc = _try(http, [f"{STAB}/stablecoincharts/{name}", f"{STAB}/stablecoincharts/{slug}"])
        if sc:
            pts = []
            for x in sc:
                try:
                    pts.append((int(x["date"]), float((x.get("totalCirculatingUSD") or {}).get("peggedUSD") or 0) / 1e6))
                except Exception:
                    pass
            pts.sort()
            if pts:
                sb = pts[-1][1]
                row["sb"] = r1(sb)
                if len(pts) >= 2:
                    row["f1"] = r1(sb - pts[-2][1])
                if len(pts) >= 8:
                    row["f7"] = r1(sb - pts[-8][1])
                    diffs = [abs(pts[i][1] - pts[i - 1][1]) for i in range(len(pts) - 8, len(pts) - 1)]
                    m = statistics.mean(diffs) if diffs else 0
                    row["fr"] = r2(safe_div(sb - pts[-2][1], m)) if m else None
    except Exception as e:  # noqa: BLE001
        row["_err_stab"] = str(e)[:120]
    # Fees
    try:
        f = _try(http, [f"{LLAMA}/overview/fees/{slug}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true",
                        f"{LLAMA}/overview/fees/{name}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"])
        if f:
            for k, src in (("fe24", "total24h"), ("fe7", "total7d"), ("fe30", "total30d")):
                if f.get(src) is not None:
                    row[k] = r2(float(f[src]) / 1e6)
    except Exception as e:  # noqa: BLE001
        row["_err_fees"] = str(e)[:120]
    return row


# ---------------------------------------------------------------- perps + OI
def _next_data(http: Http, url: str) -> dict:
    html = http.get(url, json_out=False)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise BlockedError(f"no __NEXT_DATA__ on {url}")
    return json.loads(m.group(1))


def perps(http: Http, st: Status) -> dict[str, dict]:
    """slug -> {pv, pr, p30, pr30, oi}. Page JSON first (has OI per chain); API fallback for volume only."""
    out: dict[str, dict] = {}
    try:
        nd = _next_data(http, f"{SITE}/perps/chains")
        chains = nd["props"]["pageProps"]["chains"]
        for c in chains:
            s = to_slug(c.get("name", ""))
            if not s:
                continue
            t24 = float(c.get("total24h") or 0) / 1e6
            t7 = float(c.get("total7d") or 0) / 1e6
            t30 = float(c.get("total30d") or 0) / 1e6
            oi = c.get("openInterest")
            out[s] = drop_none({"pv": r1(t24), "pr": r2(safe_div(t24, t7 / 7)) if t7 else None,
                                "p30": r1(t30) if t30 else None, "pr30": r2(safe_div(t24, t30 / 30)) if t30 else None,
                                "oi": r1(float(oi) / 1e6) if oi is not None else None})
        st.ok("llama.perps_page", chains=len(out))
        return out
    except Exception as e:  # noqa: BLE001
        st.partial("llama.perps_page", f"page failed ({str(e)[:80]}); trying API")
    try:
        ov = http.get(f"{LLAMA}/overview/derivatives", params={"excludeTotalDataChart": "true",
                                                                "excludeTotalDataChartBreakdown": "true"})
        agg24: dict[str, float] = {}
        agg7: dict[str, float] = {}
        agg30: dict[str, float] = {}
        for p in ov.get("protocols", []):
            for key, agg in (("breakdown24h", agg24), ("breakdown7d", agg7), ("breakdown30d", agg30)):
                for chain, per in (p.get(key) or {}).items():
                    v = sum(float(x or 0) for x in per.values()) if isinstance(per, dict) else float(per or 0)
                    agg[chain] = agg.get(chain, 0.0) + v
        for chain, v in agg24.items():
            s = to_slug(chain)
            if not s:
                continue
            t24, t7, t30 = v / 1e6, agg7.get(chain, 0) / 1e6, agg30.get(chain, 0) / 1e6
            out[s] = drop_none({"pv": r1(t24), "pr": r2(safe_div(t24, t7 / 7)) if t7 else None,
                                "p30": r1(t30) if t30 else None, "pr30": r2(safe_div(t24, t30 / 30)) if t30 else None})
        st.ok("llama.perps_api", chains=len(out), note="no OI from this route")
    except Exception as e:  # noqa: BLE001
        st.fail("llama.perps_api", e)
    return out


# ---------------------------------------------------------------- net flows + bridges
def netflow_and_bridges(http: Http, st: Status) -> tuple[Optional[dict], dict[str, dict], Optional[list]]:
    netflow = None
    gross: dict[str, dict] = {}
    bridges = None
    fb: list = []
    try:
        nd = _next_data(http, f"{SITE}/bridges")
        pp0 = nd["props"]["pageProps"]
        nf = pp0["netflowsData"]
        netflow = {}
        for period in ("day", "week", "month"):
            arr = nf.get(period) or []
            netflow[period] = [[x.get("chain"), r2(float(x.get("value") or 0) / 1e6)] for x in arr if x.get("chain")]
        st.ok("llama.netflow", day=len(netflow.get("day", [])))
        fb = pp0.get("filteredBridges") or []      # confirmed 19 Sep: filteredBridges is on /bridges, not /bridges/chains
    except Exception as e:  # noqa: BLE001
        st.fail("llama.netflow", e)
    try:
        nd = _next_data(http, f"{SITE}/bridges/chains")
        pp = nd["props"]["pageProps"]
        for c in pp.get("tableData") or []:
            s = to_slug(c.get("name", ""))
            if not s:
                continue
            g = {}
            for k, src in (("bd24", "prevDayUsdDeposits"), ("bw24", "prevDayUsdWithdrawals"), ("bn24", "prevDayNetFlow"),
                           ("bd7", "prevWeekUsdDeposits"), ("bw7", "prevWeekUsdWithdrawals"), ("bn7", "prevWeekNetFlow")):
                if c.get(src) is not None:
                    g[k] = r2(float(c[src]) / 1e6)
            if c.get("topTokenDepositedSymbol"):
                g["btop"] = c["topTokenDepositedSymbol"]
            if g:
                gross[s] = g
        st.ok("llama.bridges_chains", chains=len(gross))
        if not fb:
            fb = pp.get("filteredBridges") or []

        def pick(o: dict, *cands):
            for k in cands:
                if k in o and o[k] is not None:
                    return o[k]
            return None
        rows = []
        for b in fb:
            v24 = pick(b, "lastDailyVolume", "volumePrevDay", "dailyVolume", "volume24h")
            rows.append([b.get("displayName") or b.get("name"),
                         r2(float(v24) / 1e6) if v24 is not None else None,
                         r2(float(pick(b, "weeklyVolume", "volumePrevWeek", "volume7d") or 0) / 1e6) if pick(b, "weeklyVolume", "volumePrevWeek", "volume7d") is not None else None,
                         r2(float(pick(b, "monthlyVolume", "volume30d") or 0) / 1e6) if pick(b, "monthlyVolume", "volume30d") is not None else None,
                         int(pick(b, "txsPrevDay", "dailyTxs", "txs24h") or 0) if pick(b, "txsPrevDay", "dailyTxs", "txs24h") is not None else None,
                         r1(pick(b, "change_1d", "change1d", "changeDay")) if pick(b, "change_1d", "change1d", "changeDay") is not None else None,
                         len(b.get("chains") or []) if isinstance(b.get("chains"), list) else None])
        rows = [r for r in rows if r[1] is not None]
        rows.sort(key=lambda r: -r[1])
        bridges = rows[:14]
        if bridges:
            st.ok("llama.bridges_protocols", rows=len(bridges))
        else:
            st.fail("llama.bridges_protocols", "filteredBridges missing or unrecognised keys", sample_keys=sorted(list(fb[0].keys()))[:12] if fb else [])
    except Exception as e:  # noqa: BLE001
        st.fail("llama.bridges_chains", e)
    return netflow, gross, bridges


# ---------------------------------------------------------------- majors
def majors(http: Http, st: Status) -> Optional[dict]:
    ids = "coingecko:bitcoin,coingecko:ethereum,coingecko:solana"
    try:
        out = {"btc": {}, "eth": {}, "sol": {}}
        for key, params in (("d1", {}), ("d7", {"period": "7d"}), ("d30", {"period": "30d"})):
            r = http.get(f"{COINS}/percentage/{ids}", params=params)
            coins = r.get("coins", {})
            out["btc"][key] = r2(coins.get("coingecko:bitcoin"))
            out["eth"][key] = r2(coins.get("coingecko:ethereum"))
            out["sol"][key] = r2(coins.get("coingecko:solana"))
        if out["btc"].get("d7") == out["btc"].get("d30") == out["btc"].get("d1"):
            st.partial("llama.majors", "1d/7d/30d identical — period param ignored?")
        else:
            st.ok("llama.majors")
        return out
    except Exception as e:  # noqa: BLE001
        st.fail("llama.majors", e)
        return None


# ---------------------------------------------------------------- orchestration
def run(http: Http, st: Status) -> dict[str, Any]:
    http.pace("api.llama.fi", 150)
    cutoff = today_utc_midnight_ts()
    names, _ = universe(http, st)
    rows: list[dict] = []
    errs = 0
    for slug, name in sorted(names.items()):
        row = chain_row(http, slug, name, cutoff)
        if any(k.startswith("_err") for k in row):
            errs += 1
        rows.append(row)
    st.ok("llama.chain_rows", chains=len(rows), with_errors=errs) if errs < max(3, len(rows) // 3) \
        else st.partial("llama.chain_rows", f"{errs} of {len(rows)} chains had a failing sub-fetch", chains=len(rows))

    pv = perps(http, st)
    for row in rows:
        row.update(pv.get(row["s"], {}))
    netflow, gross, bridges = netflow_and_bridges(http, st)
    for row in rows:
        row.update(gross.get(row["s"], {}))
    maj = majors(http, st)

    last_day = max((r.get("_lastday") or 0) for r in rows) or None
    for row in rows:
        for k in [k for k in row if k.startswith("_")]:
            row.pop(k)
    rows = [drop_none(r) for r in rows]
    rows.sort(key=lambda r: -(r.get("dv") or 0))
    return {"LAST_DAY": last_day, "DATA": rows, "NETFLOW": netflow, "BRIDGES": bridges, "MAJORS": maj,
            "perp_slugs_present": sorted(pv.keys())}
