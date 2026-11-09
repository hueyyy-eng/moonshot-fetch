"""Quick Solana check between the two daily scans, with Telegram alerts (owner, 9 Nov 2026).

    python -m fetch.quick <repo_root> [--force]

Every 2 hours from 7am to 9pm SGT (.github/workflows/quick.yml) it searches Solana only, with the same code as the
daily scan (discovery, candles / buyers, rug checks), applies the dashboard's own rules (scan/build_block.py) and
sends a Telegram message for each token that:
  - would be on Igniting, Runners or Momentum, AND
  - scores 60 or more, AND
  - is not already on the dashboard's lists and has not been alerted in the last 3 days.
Quiet 11pm-7am SGT: a run that GitHub starts late inside those hours sends nothing (--force overrides, for tests).
The dashboard itself is not changed. Alerted tokens are remembered in .alerts/sent.json (kept between runs by the
workflow's cache, never committed). Nothing here is ever a buy recommendation.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

from . import discover, candles, safety
from .common import Http, Status, now_utc, SGT

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scan"))
from build_block import evaluate, is_ign, is_run, is_mom  # noqa: E402
import telegram  # noqa: E402

BUDGET_MIN = 25                     # stop making calls after 25 minutes; one chain normally takes about 10
SCORE_MIN = 60
QUIET_FROM, QUIET_TO = 23, 7        # SGT hours: no alerts from 11pm to 7am
REMEMBER_DAYS = 3
STATE = os.path.join(".alerts", "sent.json")
PAGE = "https://hueyyy-eng.github.io/moonshot-fetch/site/page.html"
LIST_NAME = {"I": "▲ Igniting", "U": "» Runners", "M": "⚡ Momentum"}


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
    lines = [f"⚡ Moonshot quick check — Solana",
             f"{t['sym']} — {LIST_NAME[lst]} · score {t['_score']}/100",
             f"mcap {k(t.get('mc'))} · liquidity {k(t.get('liq'))} · buyers {round((t.get('ubr') or 0) * 100)}% · "
             f"24h {t.get('c24', '?')}% · 6h {t.get('c6', '?')}% · age {t.get('age', '?')}d",
             f"score points: {parts or 'none'} · safety: {rug}",
             f"https://dexscreener.com/solana/{t['pa']}",
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


def main(repo_root: str, force: bool = False) -> int:
    now = now_utc().astimezone(SGT)
    if quiet(now) and not force:
        print(f"quick check: {now:%H:%M} SGT is inside quiet hours (11pm-7am) - nothing searched or sent")
        return 0
    http, st = Http(budget_s=BUDGET_MIN * 60), Status()
    disc = discover.run(http, st, [], ["solana"])
    tokens = [t for t in disc.get("tokens", []) if t.get("c") == "solana"]
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
    on_lists = sum(1 for t in tokens if which_list(t))
    names = ", ".join("%s %s" % (t["sym"], t["_score"]) for t, _ in picks[:5]) or "none"
    srcs = ", ".join("%s=%s" % (k_, v.get("state")) for k_, v in st.as_dict()["sources"].items())
    print(f"quick check {now:%Y-%m-%d %H:%M} SGT: {len(tokens)} Solana tokens, {on_lists} would be on a fast list, "
          f"{len(picks)} new with score {SCORE_MIN}+, {n_sent} alert(s) sent ({names}); "
          f"{http.elapsed_min():.1f} min; sources: {srcs}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else ".", force="--force" in sys.argv))
