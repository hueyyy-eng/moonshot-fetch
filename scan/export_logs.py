"""Export the dashboard's three running logs as CSV files for Google Drive.

    python scan/export_logs.py site/page.html site/

Reads the built page and writes, next to track-record.csv:
    near-miss-log.csv   the control group (REJECTS): tokens that missed exactly one gate, re-priced daily
    narrative-log.csv   narrative drift (NARHIST): one row per scan day, one column per narrative
    holder-log.csv      holder counts (HOLDHIST) for shortlist / pullback picks, one row per day per token

The page keeps 60 days of each log, so these files hold the same 60-day window; older days stay in
the repo's git history. Standard library only.
"""
from __future__ import annotations

import csv
import json
import os
import sys

GATE_NAMES = {
    "liq": "Liquidity vs size", "turn": "Real turnover", "buyers": "Unique buyers >= sellers",
    "hold": "Not collapsing", "notRan": "Has not already run", "noWash": "Not wash-like",
    "safe": "Rug checks", "social": "Has an X or a site",
}


def read_const(src: str, name: str):
    marker = f"const {name} = "
    i = src.find(marker)
    if i < 0:
        raise SystemExit(f"export_logs: {name} not found in page")
    return json.JSONDecoder().raw_decode(src, i + len(marker))[0]


def ratio(a, b):
    return round(a / b, 3) if a is not None and b else ""


def main() -> int:
    page, out = sys.argv[1], sys.argv[2]
    src = open(page, encoding="utf-8").read()
    hist, rej = read_const(src, "HISTORY"), read_const(src, "REJECTS")
    narhist, holdhist = read_const(src, "NARHIST"), read_const(src, "HOLDHIST")

    with open(os.path.join(out, "near-miss-log.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["logged_date", "symbol", "chain", "missed_gate", "entry_mcap_k", "entry_price_usd",
                    "price_now_usd", "now_vs_entry", "peak_vs_entry", "peak_on_day"])
        for r in sorted(rej, key=lambda r: (r.get("d", ""), r.get("sym", ""))):
            px = r.get("px")
            w.writerow([r.get("d"), r.get("sym"), r.get("c"), GATE_NAMES.get(r.get("why"), r.get("why")), r.get("mc"),
                        px, r.get("pxNow"), ratio(r.get("pxNow"), px), ratio(r.get("pxMax"), px), r.get("dMax")])

    totals: dict[str, int] = {}
    for r in narhist:
        for k, v in (r.get("t") or {}).items():
            totals[k] = totals.get(k, 0) + (v or 0)
    labels = sorted(totals, key=lambda k: (-totals[k], k))
    with open(os.path.join(out, "narrative-log.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scan_date", "tokens_total"] + labels)
        for r in sorted(narhist, key=lambda r: r.get("d", "")):
            t = r.get("t") or {}
            w.writerow([r.get("d"), sum(v or 0 for v in t.values())] + [t.get(k, 0) for k in labels])

    names = {}
    tokens = read_const(src, "TOKENS")
    for r in [t for t in tokens if t.get("pa")] + list(rej) + list(hist):  # later sources win: picks > near-misses > today's tokens
        names[r["pa"].lower()] = (r.get("sym"), r.get("c"))
    with open(os.path.join(out, "holder-log.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "chain", "holders", "pair_address"])
        rows = []
        for r in holdhist:
            sym, c = names.get(r["pa"].lower(), ("", ""))
            sym = sym or "(no longer listed)"
            rows.append([r.get("d"), sym, c, r.get("w"), r["pa"]])
        for row in sorted(rows, key=lambda x: (x[1].startswith("("), x[1].lower(), x[0] or "")):
            w.writerow(row)

    print(f"export_logs: near-miss {len(rej)} rows, narrative {len(narhist)} days x {len(labels)} labels, "
          f"holders {len(holdhist)} rows")
    summary_path = os.path.join(out, "summary.json")
    if os.path.exists(summary_path):
        update_daily_lists(json.load(open(summary_path, encoding="utf-8")), os.path.join(out, "daily-lists.csv"))
    write_price_tracking(src, hist, os.path.join(out, "price-tracking.csv"))
    return 0


# ---------------------------------------------------------------- price-tracking.csv: every listed token since listing
PT_HEADER = ["listed_date", "list", "symbol", "chain", "listed_mcap_k", "listed_price_usd", "price_now_usd",
             "change_since_listed_pct", "best_pct", "worst_pct", "low_tracked_from_listing", "days_since_listed",
             "peak_on_day", "pair_url"]
PT_LIST = {"S": "shortlist", "P": "pullback", "I": "igniting"}


def _pct(v, px):
    return round((v / px - 1) * 100, 1) if px and v is not None else ""


def write_price_tracking(src: str, hist: list, path: str) -> None:
    """Shortlist + pullback (HISTORY) and igniting (IGNHIST), one row per token per list, newest first.
    Same figures as the dashboard's Price tracking tab."""
    marker = "const IGNHIST = "
    ign = read_const(src, "IGNHIST") if marker in src else []
    from datetime import date
    try:
        today = date.fromisoformat(str(read_const(src, "LOG_AT"))[:10])
    except Exception:  # noqa: BLE001
        today = None
    rows = []
    for r in list(hist) + list(ign):
        px = r.get("px")
        if not px:
            continue
        low_known = (r.get("d") or "") >= "2026-10-01"      # lows are recorded from this date on
        lo = r["pxMin"] if r.get("pxMin") is not None else min(px, r["pxNow"] if r.get("pxNow") is not None else px)
        rows.append([r.get("d"), PT_LIST.get(r.get("l"), r.get("l")), r.get("sym"), r.get("c"), r.get("mc"), px,
                     r.get("pxNow"), _pct(r.get("pxNow"), px), _pct(r.get("pxMax"), px), _pct(lo, px),
                     "yes" if low_known else "no (lows recorded from 2026-10-01)",
                     (today - date.fromisoformat(r["d"])).days if today and r.get("d") else "", r.get("dMax"),
                     f"https://dexscreener.com/{r.get('c')}/{r.get('pa')}"])
    rows.sort(key=lambda x: (x[0] or "", x[1] or "", str(x[2]).lower()), reverse=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(PT_HEADER)
        w.writerows(rows)
    print(f"price-tracking: {len(rows)} rows")


# ---------------------------------------------------------------- daily-lists.csv: one row per token per day
# Cumulative: the file in the repo is read, today's rows replace any earlier rows for today (so a re-run of
# the same day never duplicates), and it is written back. A day with all three lists empty gets one "none" row
# so a quiet day is distinguishable from a day the scan did not run.
DAILY_HEADER = ["scan_date", "list", "symbol", "chain", "mcap_k", "narrative", "run_x_off_7d_low", "pct_below_high",
                "vol_6h_pace_x", "change_6h_pct", "age_days", "safety_source", "pair_url", "source"]
LIST_NAMES = (("shortlist", "shortlist"), ("pullback", "pullback"), ("igniting", "igniting"))


def daily_rows(summary: dict, source: str = "daily scan") -> list[list]:
    day = str(summary.get("now", ""))[:10]
    rows = []
    for key, name in LIST_NAMES:
        for t in summary.get(key) or []:
            pa = t.get("pa") or ""
            rows.append([day, name, t.get("sym"), t.get("c"), t.get("mc"), t.get("nar"), t.get("run"), t.get("retr"),
                         t.get("acc"), t.get("c6"), t.get("age"), t.get("sec"),
                         f"https://dexscreener.com/{t.get('c')}/{pa}" if pa else "", source])
    if not rows and day:
        rows.append([day, "none", "", "", "", "", "", "", "", "", "", "", "", source + " (all three lists empty)"])
    return rows


def update_daily_lists(summary: dict, path: str) -> None:
    day = str(summary.get("now", ""))[:10]
    if not day:
        print("daily-lists: summary has no date, skipped")
        return
    old = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            old = [row for row in r if row and row[0] != day]
    rows = sorted(old + [[("" if v is None else v) for v in row] for row in daily_rows(summary)],
                  key=lambda x: (x[0], ["shortlist", "pullback", "igniting", "none"].index(x[1]) if x[1] in
                                 ("shortlist", "pullback", "igniting", "none") else 9, str(x[2]).lower()))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(DAILY_HEADER)
        w.writerows(rows)
    print(f"daily-lists: {day} written, {len(rows)} rows in total")


if __name__ == "__main__":
    sys.exit(main())
