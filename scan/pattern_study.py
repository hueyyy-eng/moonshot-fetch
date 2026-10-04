"""Pattern study: which characteristics go with a token reaching 2x?

Reads every saved daily snapshot (snapshots/<day>/scan.json), takes each token on the first day the scanner saw it,
records its characteristics that day, and follows its price at every later daily scan. Then it groups tokens by
list, chain, age, market cap, narrative and trading activity and counts, per group, how many reached 2x.

Two 2x counts are kept because many one-day spikes are bad readings or pump-and-dumps that could not be caught:
  hit_all  = price was >= 2x the first-seen price at ANY later daily scan
  hit_conf = price was >= 2x at TWO OR MORE later daily scans (confirmed)

The result is injected into the built page between /*PATTERN-START*/ and /*PATTERN-END*/ (the page renders it in
the "Pattern study" section). Narrative tags are only known for tokens on the page on a given day, so a cumulative
map is kept in site/pattern-nar.json and grows every day.

Usage: python scan/pattern_study.py <repo_root> <page.html>
Pure local file work: no network. On any problem the page is left untouched.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))
MAX_SANE_PEAK = 200.0          # a peak above 200x in a day or two is treated as a bad price reading and dropped
START, END = "/*PATTERN-START*/", "/*PATTERN-END*/"


def js_const(page: str, name: str):
    m = re.search(r"const " + name + r"\s*=\s*(\[.*?\]);\n", page, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except ValueError:
        return []


def key(c, pa) -> str:
    return f"{c}:{pa}".lower()


def bucket(x, edges, labels):
    if x is None:
        return None
    for e, lab in zip(edges, labels):
        if x < e:
            return lab
    return labels[-1]


GROUPS = {
    # name: (title, function(row) -> label, ordered labels or None, note)
    "list": ("Which list", None, ["★ Shortlist", "◆ Pullback", "▲ Igniting", "✕ Near-miss", "Not listed"],
             "Ever on that list. Measured from the day the scanner first saw the token, which can be before it was listed. Saved daily scans start on 19 Sep 2026, so picks from 12-18 Sep (Stonks, pill, IF and others) are not in this study; see Track record and Price tracking for those."),
    "chain": ("Chain", None, None, ""),
    "age": ("Token age when first seen", None, ["under 1 day", "1–3 days", "3–7 days", "7–30 days", "30–90 days", "90+ days"], ""),
    "mcap": ("Market cap when first seen", None, ["under $500k", "$500k–1m", "$1–2m", "$2–5m", "$5m+"], ""),
    "nar": ("Narrative", None, None, "Narrative tags come from the dashboard's rules. Tags are kept from 4 Oct 2026 for every scanned token (earlier: list tokens from 26 Sep only)."),
    "turn": ("24h volume ÷ liquidity", None, ["under 1×", "1–3×", "3–10×", "10–30×", "30×+"], "How hard the pool is being traded."),
    "ubr": ("Buyer share of traders (6h)", None, ["under 45%", "45–55%", "55–70%", "70–90%", "90%+"],
            "90%+ buyers with almost no sellers is often a honeypot (you can buy but not sell), not strength."),
    "c24": ("24h price move when first seen", None, ["below −30%", "−30% to 0", "0 to +30%", "+30% to +100%", "+100% to +300%", "+300%+"], ""),
    "run": ("Run off 7-day low", None, ["under 1.5×", "1.5–3×", "3–5×", "5×+"], "Needs daily candles; tokens without them are left out."),
}
TABS = [("list", "List", ["list"]), ("cas", "Chain, age & size", ["chain", "age", "mcap"]),
        ("nar", "Narrative", ["nar"]), ("act", "Trading activity", ["turn", "ubr", "c24", "run"])]


def label_for(g: str, r: dict):
    if g == "list":
        return {"S": "★ Shortlist", "P": "◆ Pullback", "I": "▲ Igniting", "R": "✕ Near-miss"}.get(r["lst"], "Not listed")
    if g == "chain":
        return r["c"]
    if g == "age":
        return bucket(r["age"], [1, 3, 7, 30, 90], GROUPS["age"][2])
    if g == "mcap":
        return bucket(r["mc"], [500, 1000, 2000, 5000], GROUPS["mcap"][2])
    if g == "nar":
        return r["nar"]
    if g == "turn":
        return bucket(r["turn"], [1, 3, 10, 30], GROUPS["turn"][2])
    if g == "ubr":
        return bucket(r["ubr"], [0.45, 0.55, 0.70, 0.90], GROUPS["ubr"][2])
    if g == "c24":
        return bucket(r["c24"], [-30, 0, 30, 100, 300], GROUPS["c24"][2])
    if g == "run":
        return bucket(r["run"], [1.5, 3, 5], GROUPS["run"][2])
    return None


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    pk = [r["pk"] for r in rows]
    return {"n": n,
            "hit_all": sum(r["hit_all"] for r in rows),
            "hit_conf": sum(r["hit_conf"] for r in rows),
            "still2": sum(r["now"] >= 2 for r in rows),
            "down50": sum(r["now"] <= 0.5 for r in rows),
            "med_peak": round(statistics.median(pk), 2) if pk else None}


def study(root: str, page: str, nar_path: str) -> dict:
    snaps = {}
    for f in sorted(glob.glob(os.path.join(root, "snapshots", "*", "scan.json"))):
        try:
            s = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d = s.get("day_sgt") or os.path.basename(os.path.dirname(f))
        snaps[d] = s
    days = sorted(snaps)
    if len(days) < 2:
        raise RuntimeError("need at least two daily snapshots")

    # narrative tags: cumulative map, today's page tokens, and the list tokens in daily-lists.csv
    nar = {}
    try:
        nar = json.load(open(nar_path, encoding="utf-8"))
    except (OSError, ValueError):
        nar = {}
    for t in js_const(page, "TOKENS"):
        if t.get("nar") and t.get("pa"):
            nar[key(t["c"], t["pa"])] = t["nar"]
    dl = os.path.join(os.path.dirname(nar_path), "daily-lists.csv")
    if os.path.exists(dl):
        for r in csv.DictReader(open(dl, encoding="utf-8")):
            if r.get("narrative") and r.get("pair_url"):
                nar.setdefault(key(r["chain"], r["pair_url"].rstrip("/").rsplit("/", 1)[-1]), r["narrative"])

    lists = {}
    for name, lab in (("HISTORY", None), ("IGNHIST", "I"), ("REJECTS", "R")):
        for h in js_const(page, name):
            if h.get("pa"):
                k = key(h["c"], h["pa"])
                L = lab or h.get("l") or "S"
                # a token on a pick list beats near-miss; first seen label otherwise
                if k not in lists or lists[k] == "R":
                    lists[k] = L

    first: dict[str, tuple[str, dict]] = {}
    price: dict[str, dict[str, float]] = {}
    for d in days:
        s = snaps[d]
        for k, v in (s.get("PRICES") or {}).items():
            if v is not None:
                price.setdefault(k.lower(), {})[d] = float(v)
        for t in s.get("TOKENS") or []:
            if not t.get("pa"):
                continue
            k = key(t["c"], t["pa"])
            if t.get("px"):
                price.setdefault(k, {})[d] = float(t["px"])
            first.setdefault(k, (d, t))

    rows = []
    dropped = 0
    for k, (d0, t) in first.items():
        px0 = t.get("px")
        if not px0:
            continue
        later = [v for d, v in price.get(k, {}).items() if d > d0]
        if not later:
            continue
        pk = max(later) / px0
        if pk > MAX_SANE_PEAK:
            dropped += 1
            continue
        last_day = max(d for d in price[k])
        rows.append({
            "c": t["c"], "pk": pk, "now": price[k][last_day] / px0,
            "hit_all": pk >= 2, "hit_conf": sum(v >= 2 * px0 for v in later) >= 2,
            "mc": t.get("mc"), "age": t.get("age"), "turn": t.get("turn"), "ubr": t.get("ubr"),
            "c24": t.get("c24"), "run": t.get("run"), "nar": nar.get(k), "lst": lists.get(k, "-"),
        })

    out_groups = {}
    for g, (title, _, order, note) in GROUPS.items():
        buckets: dict[str, list] = {}
        for r in rows:
            lab = label_for(g, r)
            if lab is not None:
                buckets.setdefault(lab, []).append(r)
        labs = [l for l in order if l in buckets] if order else sorted(buckets, key=lambda l: -len(buckets[l]))
        out_groups[g] = {"title": title, "note": note, "rows": [{"label": l, **summarise(buckets[l])} for l in labs]}

    now = datetime.now(timezone.utc).astimezone(SGT).strftime("%Y-%m-%d %H:%M SGT")
    return {"generated": now, "first_day": days[0], "last_day": days[-1], "days": len(days),
            "dropped_bad_readings": dropped, "all": summarise(rows), "groups": out_groups,
            "tabs": [{"id": i, "name": n, "groups": gs} for i, n, gs in TABS]}, nar


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: pattern_study.py <repo_root> <page.html>")
        return 2
    root, page_path = sys.argv[1], sys.argv[2]
    nar_path = os.path.join(os.path.dirname(page_path), "pattern-nar.json")
    page = open(page_path, encoding="utf-8").read()
    if page.count(START) != 1 or page.count(END) != 1:
        print("pattern study: markers not found in page — template not updated yet, skipping")
        return 0
    data, nar = study(root, page, nar_path)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    json.loads(blob.replace("<\\/", "</"))                       # round-trip check before touching the page
    a, b = page.index(START) + len(START), page.index(END)
    new = page[:a] + blob + page[b:]
    with open(page_path + ".tmp", "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(page_path + ".tmp", page_path)
    with open(nar_path, "w", encoding="utf-8") as f:
        json.dump(nar, f, ensure_ascii=False, separators=(",", ":"))
    a_ = data["all"]
    print(f"pattern study: {a_['n']} tokens over {data['days']} days — 2x any {a_['hit_all']}, confirmed {a_['hit_conf']}; "
          f"{len(nar)} narrative tags kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
