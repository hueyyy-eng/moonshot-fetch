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
    return 0


if __name__ == "__main__":
    sys.exit(main())
