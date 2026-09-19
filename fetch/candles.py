"""STEP 3 — GeckoTerminal enrichment: wallet counts, LP lock, pool age, daily candles → run/retr/vvp/nd/cs.

The three guards from the scan prompt are applied verbatim:
  1. wrong side of the pair  -> |log10(close/px)| > 0.7  => noCd:1, drop every candle field
  2. too young for the window -> age < 1                  => noCd:1
  3. negative retracement     -> retr < 0                 => omit retr
Partial-day candles (ts >= today's UTC midnight) are dropped before any maths.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .common import Http, Status, BlockedError, r1, r2, safe_div, today_utc_midnight_ts, log10_ratio, log
from .discover import DS_TO_GT, GT

RAN_MULT, RAN_24H, PARAB_6H = 3.0, 200.0, 100.0
TURN_WASH, TURN_WASH_SOFT, TPW_MAX = 200.0, 30.0, 15.0
HDR = {"Accept": "application/json;version=20230302"}


def _pool_attrs(http: Http, net: str, addrs: list[str]) -> dict[str, dict]:
    """address(lower) -> attributes. Multi endpoint first; per-pool fallback for chains where multi fails."""
    out: dict[str, dict] = {}
    for i in range(0, len(addrs), 30):
        batch = addrs[i:i + 30]
        got = False
        try:
            j = http.get(f"{GT}/networks/{net}/pools/multi/{','.join(batch)}", headers=HDR, retries=1)
            for d in (j or {}).get("data") or []:
                a = d.get("attributes") or {}
                if a.get("address"):
                    out[a["address"].lower()] = a
            got = j is not None
        except BlockedError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("GT multi %s: %s — falling back to single calls", net, str(e)[:80])
        if not got:
            for a in batch:
                try:
                    j = http.get(f"{GT}/networks/{net}/pools/{a}", headers=HDR, retries=1)
                    at = ((j or {}).get("data") or {}).get("attributes") or {}
                    if at.get("address"):
                        out[at["address"].lower()] = at
                except BlockedError:
                    raise
                except Exception as e:  # noqa: BLE001
                    log.warning("GT pool %s/%s: %s", net, a[:10], str(e)[:80])
    return out


def _wallets(row: dict, a: dict) -> None:
    tx = (a.get("transactions") or {}).get("h24") or {}
    ub, us = tx.get("buyers"), tx.get("sellers")
    buys, sells = tx.get("buys"), tx.get("sells")
    row["ub"], row["us"] = ub, us
    row["ubr"] = r2(safe_div(ub, (ub or 0) + (us or 0))) if ub is not None and us is not None and (ub + us) > 0 else None
    row["tpw"] = r1(safe_div((buys or 0) + (sells or 0), (ub or 0) + (us or 0))) if ub is not None and us is not None and (ub + us) > 0 else None
    lpk = a.get("locked_liquidity_percentage")
    if lpk not in (None, ""):
        try:
            row["lpk"] = r2(float(lpk))
        except ValueError:
            pass
    if row.get("age") is None and a.get("pool_created_at"):
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00"))
            from .common import now_utc
            row["age"] = r2((now_utc() - dt).total_seconds() / 86400)
        except Exception:  # noqa: BLE001
            pass


def _candles(http: Http, net: str, pool: str) -> Optional[list[list[float]]]:
    j = http.get(f"{GT}/networks/{net}/pools/{pool}/ohlcv/day?limit=30", headers=HDR, retries=1)
    lst = (((j or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list")
    if not lst:
        return None
    cutoff = today_utc_midnight_ts()
    rows = []
    for c in lst:
        try:
            ts, o, h, l, cl, v = int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])
        except Exception:
            continue
        if ts < cutoff and cl > 0:
            rows.append([ts, o, h, l, cl, v])
    rows.sort()  # oldest first
    return rows or None


def _candle_fields(row: dict, cs: list[list[float]]) -> None:
    px = row.get("px")
    close = cs[-1][4]
    lr = log10_ratio(close, px)
    if lr is not None and lr > 0.7:
        row["noCd"] = 1
        row["_cd_note"] = "wrong side of pair"
        return
    if row.get("age") is not None and row["age"] < 1:
        row["noCd"] = 1
        row["_cd_note"] = "too young"
        return
    last7 = cs[-7:]
    low7 = min(c[3] for c in last7 if c[3] > 0) if any(c[3] > 0 for c in last7) else None
    hi = max(c[2] for c in cs)
    vmax = max(c[5] for c in cs)
    row["nd"] = len(cs)
    row["run"] = r2(safe_div(close, low7)) if low7 else None
    retr = (1 - close / hi) * 100 if hi else None
    if retr is not None and retr >= 0:
        row["retr"] = r2(retr)
    v24 = (row.get("v24") or 0) * 1e3
    row["vvp"] = r2(safe_div(v24, vmax) * 100) if vmax else None
    closes = [c[4] for c in cs[-14:]]
    m = max(closes)
    row["cs"] = [int(round(c / m * 100)) for c in closes] if m else None


def flags(row: dict) -> None:
    """ran / par / wsh informational flags (the page recomputes them, these are for the summary)."""
    run_ok = row.get("run") is not None and row["run"] > 0
    ran = (row["run"] >= RAN_MULT) if run_ok else ((row.get("c24") or 0) >= RAN_24H)
    if ran:
        row["ran"] = 1
    if row.get("c6") is not None and row["c6"] > PARAB_6H:
        row["par"] = 1
    turn, tpw = row.get("turn") or 0, row.get("tpw")
    if turn > TURN_WASH or (turn > TURN_WASH_SOFT and tpw is not None and tpw > TPW_MAX):
        row["wsh"] = 1


def run(http: Http, st: Status, tokens: list[dict]) -> None:
    http.pace("api.geckoterminal.com", 25)
    by_chain: dict[str, list[dict]] = {}
    for t in tokens:
        by_chain.setdefault(t["c"], []).append(t)
    got_wallet = got_candle = 0
    blocked = False
    for chain, rows in by_chain.items():
        net = DS_TO_GT.get(chain)
        if not net:
            for r in rows:
                r["noCd"] = 1
            continue
        try:
            attrs = _pool_attrs(http, net, [r["pa"] for r in rows])
        except BlockedError as e:
            st.fail("geckoterminal", f"blocked: {e}")
            blocked = True
            break
        for r in rows:
            a = attrs.get(r["pa"].lower())
            if a:
                _wallets(r, a)
                if r.get("ubr") is not None:
                    got_wallet += 1
            try:
                cs = _candles(http, net, r["pa"])
            except BlockedError as e:
                st.fail("geckoterminal", f"blocked mid-run: {e}")
                blocked = True
                break
            except Exception as e:  # noqa: BLE001
                log.warning("GT candles %s/%s: %s", net, r["sym"], str(e)[:80])
                cs = None
            if cs:
                _candle_fields(r, cs)
                if r.get("run") is not None:
                    got_candle += 1
            else:
                r["noCd"] = 1
        if blocked:
            break
    for r in tokens:
        flags(r)
    m = [t for t in tokens if t.get("band") == "m"]
    cov = sum(1 for t in m if t.get("run") is not None or t.get("ubr") is not None)
    pct = round(100 * cov / len(m)) if m else 0
    if blocked:
        return
    if m and pct < 40:
        st.partial("geckoterminal", f"coverage {pct}% of moonshot band (<40%) — scan must carry candle/wallet fields forward",
                   coverage_pct=pct, wallets=got_wallet, candles=got_candle, band_m=len(m))
    else:
        st.ok("geckoterminal", coverage_pct=pct, wallets=got_wallet, candles=got_candle, band_m=len(m))
