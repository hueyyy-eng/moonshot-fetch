"""Quick Solana + BSC check between the two daily scans, with Telegram alerts (owner, 9 Nov 2026; BSC 11 Nov).

    python -m fetch.quick <repo_root> [--force]

Every 2 hours from 7am to 9pm SGT (.github/workflows/quick.yml) it searches Solana and BSC, with the same code as the
daily scan (discovery, candles / buyers, rug checks), applies the dashboard's own rules (scan/build_block.py) and
sends a Telegram message for each token that:
  - would be on Igniting, Runners or Momentum, AND
  - scores 60 or more, AND
  - is not already on the dashboard's lists and has not been alerted in the last 3 days.
Quiet 11pm-7am SGT: a run that GitHub starts late inside those hours sends nothing (--force overrides, for tests).
The dashboard itself is not changed. Alerted tokens are remembered in .alerts/sent.json (kept between runs by the
workflow's cache, never committed). Nothing here is ever a buy recommendation.

Watch log (owner, 11 Nov 2026 — an experiment, judged at the 22 Nov and 6 Dec reviews): every Solana token first seen
at $50k-$200k ("micro", pool $10k+) or $200k-$500k ("early") is written down silently with its price, then re-priced
at every check. No list, no alert. Kept in .alerts/watch.json (cache) and copied to site/micro-watch.json by the
daily scan, which the Review tab reads.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

from . import discover, candles, safety
from .common import Http, Status, now_utc, SGT

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scan"))
from build_block import evaluate, is_ign, is_run, is_mom  # noqa: E402
import telegram  # noqa: E402

BUDGET_MIN = 32                     # stop making calls after 32 minutes; one chain normally takes about 10
CHAINS = ["solana", "bsc"]
WATCH_CHAINS = ["solana"]           # micro + early watch log: Solana only
WATCH = os.path.join(".alerts", "watch.json")
WATCH_KEEP_DAYS = 45
WATCH_NEW_MAX = {"x": 30, "e": 15}  # new tokens written down per check, by band, most traded first
SCORE_MIN = 60
QUIET_FROM, QUIET_TO = 23, 7        # SGT hours: no alerts from 11pm to 7am
REMEMBER_DAYS = 3
STATE = os.path.join(".alerts", "sent.json")
PAGE = "https://hueyyy-eng.github.io/moonshot-fetch/site/page.html"
LIST_NAME = {"I": "▲ Igniting", "U": "» Runners", "M": "⚡ Momentum"}
CHAIN_NAME = {"solana": "Solana", "bsc": "BSC"}


def quiet(now_sgt) -> bool:
    return now_sgt.hour >= QUIET_FROM or now_sgt.hour < QUIET_TO


def k(n) -> str:
    if n is None:
        return "?"
    return f"${n / 1000:.2f}m" if n >= 1000 else f"${n:.0f}k"


def which_list(t: dict) -> str | None:
    if is_ign(t):
        return "I"
    if is_mom(t):
        return "M"
    if is_run(t):
        return "U"
    return None


def message(t: dict, lst: str) -> str:
    parts = ", ".join(t["_sparts"])
    rug = {"rugcheck": "RugCheck read", "nodata": "NO rug data"}.get(t.get("sec"), t.get("sec") or "?")
    lines = [f"⚡ Moonshot quick check — {CHAIN_NAME.get(t.get('c'), t.get('c') or 'Solana')}",
             f"{t['sym']} — {LIST_NAME[lst]} · score {t['_score']}/100",
             f"mcap {k(t.get('mc'))} · liquidity {k(t.get('liq'))} · buyers {round((t.get('ubr') or 0) * 100)}% · "
             f"24h {t.get('c24', '?')}% · 6h {t.get('c6', '?')}% · age {t.get('age', '?')}d",
             f"score points: {parts or 'none'} · safety: {rug}",
             f"https://dexscreener.com/{t.get('c') or 'solana'}/{t['pa']}",
             f"Not a recommendation. Fast lists move within 1–3 days; decide your exit before buying. Dashboard: {PAGE}"]
    if t.get("_ran"):
        lines.insert(3, "already ran: you would be buying after a big move")
    return "\n".join(lines)


def on_page(repo_root: str) -> set[str]:
    """pair addresses already on the dashboard's lists (the last full scan): no need to alert those"""
    try:
        s = json.load(open(os.path.join(repo_root, "site", "summary.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    out = set()
    for key in ("shortlist", "pullback", "igniting", "momentum", "runners"):
        out |= {(t.get("pa") or "").lower() for t in s.get(key) or []}
    return out


def load_state(now) -> dict:
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    cutoff = (now - timedelta(days=REMEMBER_DAYS)).isoformat()
    return {pa: ts for pa, ts in st.items() if ts >= cutoff}


def load_watch(repo_root: str) -> list:
    for path in (WATCH, os.path.join(repo_root, "site", "micro-watch.json")):
        try:
            rows = json.load(open(path, encoding="utf-8"))
            if isinstance(rows, list):
                return rows
        except (OSError, ValueError):
            continue
    return []


def update_watch(http, repo_root: str, found: list, now) -> dict:
    """Write down new micro/early tokens, then re-price every row of the last 45 days (DexScreener, 30 per call)."""
    rows = load_watch(repo_root)
    cutoff = (now - timedelta(days=WATCH_KEEP_DAYS)).isoformat()
    rows = [r for r in rows if r.get("t0", "") >= cutoff]
    have = {(r["c"], r["pa"].lower()) for r in rows}
    stamp = now.isoformat(timespec="minutes")
    added = {"x": 0, "e": 0}
    for band in ("x", "e"):
        cand = sorted([t for t in found if t.get("band") == band and t.get("c") in WATCH_CHAINS and t.get("px")
                       and (t["c"], t["pa"].lower()) not in have], key=lambda t: -(t.get("v24") or 0))
        for t in cand[:WATCH_NEW_MAX[band]]:
            b, s_ = t.get("_b24") or 0, t.get("_s24") or 0
            rows.append({"t0": stamp, "c": t["c"], "pa": t["pa"], "sym": t.get("sym"), "tier": band, "mc": t.get("mc"),
                         "liq": t.get("liq"), "age": t.get("age"), "c24": t.get("c24"),
                         "bs": round(b / (b + s_), 2) if b + s_ else None, "px": t["px"], "pxNow": t["px"],
                         "pxMax": t["px"], "pxMin": t["px"], "tNow": stamp})
            have.add((t["c"], t["pa"].lower()))
            added[band] += 1
    # re-price (rows written down this run already carry today's price)
    by_chain: dict[str, list] = {}
    for r in rows:
        if r.get("tNow") != stamp:
            by_chain.setdefault(r["c"], []).append(r)
    n_ok = n_gone = 0
    for chain, rs in by_chain.items():
        for i in range(0, len(rs), 30):
            batch = rs[i:i + 30]
            if http.out_of_time():
                break
            try:
                j = http.get(f"{discover.DS_API}/latest/dex/pairs/{chain}/{','.join(r['pa'] for r in batch)}", retries=2)
            except Exception:  # noqa: BLE001 - a failed batch keeps its old prices
                continue
            seen = {}
            for p in (j or {}).get("pairs") or []:
                if p.get("priceUsd") not in (None, ""):
                    seen[p["pairAddress"].lower()] = float(p["priceUsd"])
            for r in batch:
                p = seen.get(r["pa"].lower(), 0.0)      # a pair DexScreener no longer knows counts as gone (0)
                n_gone += int(p == 0)
                n_ok += 1
                r["pxNow"], r["tNow"] = p, stamp
                r["pxMax"] = max(r.get("pxMax") or 0, p)
                r["pxMin"] = min(r.get("pxMin") if r.get("pxMin") is not None else p, p)
                for m in (2, 5, 10):                    # hours from first seen to the first check at 2x / 5x / 10x
                    if r.get(f"h{m}") is None and r["px"] and p >= m * r["px"]:
                        r[f"h{m}"] = round((now - datetime.fromisoformat(r["t0"])).total_seconds() / 3600, 1)
    os.makedirs(os.path.dirname(WATCH), exist_ok=True)
    with open(WATCH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
    return {"rows": len(rows), "added_micro": added["x"], "added_early": added["e"], "repriced": n_ok, "gone": n_gone}


def main(repo_root: str, force: bool = False) -> int:
    now = now_utc().astimezone(SGT)
    if quiet(now) and not force:
        print(f"quick check: {now:%H:%M} SGT is inside quiet hours (11pm-7am) - nothing searched or sent")
        return 0
    http, st = Http(budget_s=BUDGET_MIN * 60), Status()
    disc = discover.run(http, st, [], CHAINS, early_chains=WATCH_CHAINS, micro_chains=tuple(WATCH_CHAINS), leaders=False)
    found = disc.get("tokens", [])
    # only the $500k-$2m band can be alerted; the micro / early bands are only written down (see update_watch)
    tokens = [t for t in found if t.get("c") in CHAINS and t.get("band") == "m"]
    if tokens:
        candles.run(http, st, tokens)
        safety.run(http, st, tokens)
    seen, sent = on_page(repo_root), load_state(now)
    picks = []
    for t in tokens:
        evaluate(t)
        lst = which_list(t)
        if lst and t["_score"] >= SCORE_MIN and t["pa"].lower() not in seen and t["pa"].lower() not in sent:
            picks.append((t, lst))
    picks.sort(key=lambda p: -p[0]["_score"])
    n_sent = 0
    for t, lst in picks[:5]:                       # at most 5 alerts per check
        if quiet(now_utc().astimezone(SGT)) and not force:
            break
        if telegram.send(message(t, lst)):
            n_sent += 1
            sent[t["pa"].lower()] = now.isoformat()
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(sent, open(STATE, "w", encoding="utf-8"), indent=1)
    try:
        wl = update_watch(http, repo_root, found, now)
    except Exception as e:  # noqa: BLE001 - the watch log must never stop the alerts
        wl = {"error": str(e)[:120]}
    on_lists = sum(1 for t in tokens if which_list(t))
    names = ", ".join("%s %s" % (t["sym"], t["_score"]) for t, _ in picks[:5]) or "none"
    srcs = ", ".join("%s=%s" % (k_, v.get("state")) for k_, v in st.as_dict()["sources"].items())
    per = ", ".join("%s %d" % (CHAIN_NAME[c], sum(t["c"] == c for t in tokens)) for c in CHAINS)
    print(f"quick check {now:%Y-%m-%d %H:%M} SGT: {len(tokens)} tokens ({per}), {on_lists} would be on a fast list, "
          f"{len(picks)} new with score {SCORE_MIN}+, {n_sent} alert(s) sent ({names}); "
          f"{http.elapsed_min():.1f} min; sources: {srcs}; watch log: {wl}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else ".", force="--force" in sys.argv))
