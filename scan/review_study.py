"""Review study: the back-tests from the owner's 8 Nov 2026 review, recalculated from all history at every scan.

    python scan/review_study.py <repo_root> <page.html>

Reads every saved snapshot (snapshots/<day>/scan.json and <day>-pm/) and the page's logged lists (HISTORY,
IGNHIST, MOMHIST, RUNHIST, REJECTS), then works out, from scratch:

  lists     how each list did per $1 bought: reached 2x / 5x / 10x, still 2x+, down 50%+, and the result of
            four exit rules (just hold; the owner's rule; owner's rule + stop-loss; the recommended rule)
  timing    how many days after appearing on a list a token reached 2x / 5x / 10x, and how old it was then
  winners   what fast spikes (2x within 3 days) and slow climbers (later) looked like when first seen
  score     2x rates by score band, on the two halves of the history (rules set on the first, checked on the second)
  runners   the Runners rule on all history, by half
  social    tokens that failed only "Has an X or a site"
  robinhood possible extra bars for Robinhood Chain (kept / dropped winners)
  asset     the "tracks an asset" rule: how many tokens it catches and whether any of them doubled
  hits      days to 2x / 5x / 10x for every logged row (shown in Track record and Price tracking)

The result is injected into the page between /*REVIEW-START*/ and /*REVIEW-END*/ and written to site/review.json
(read by the owner's fortnightly review task). On the first scan on or after each review Sunday (every 14 days
from 8 Nov 2026) a copy of the headline numbers is added to site/review-history.json, so the page can show what
changed since the last review. Pure local file work: no network. On any problem the page is left untouched.
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pattern_study import js_const  # noqa: E402
from build_block import evaluate, is_short, is_pull, is_ign, is_mom, is_run, crowded, C  # noqa: E402

SGT = timezone(timedelta(hours=8))
START, END = "/*REVIEW-START*/", "/*REVIEW-END*/"
MAX_SANE = 200.0          # same bad-reading cut as the pattern study
SPIKE = 50.0              # exit tests leave out seconds-long spikes no one could sell into
REVIEW_ANCHOR = date(2026, 11, 8)
LISTS = [("S", "★ Shortlist"), ("P", "◆ Pullback"), ("I", "▲ Igniting"), ("M", "⚡ Momentum"), ("U", "» Runners"),
         ("R", "✕ Near-misses")]


def tday(d: str) -> float:
    """snapshot folder -> day number; the evening folder counts as half a day later."""
    return date.fromisoformat(d[:10]).toordinal() + (0.5 if d.endswith("-pm") else 0.0)


def pct(a, b):
    return round(100 * a / b, 1) if b else None


def med(v):
    v = [x for x in v if x is not None]
    return round(st.median(v), 2) if v else None


def load(root: str):
    snaps = sorted(d for d in os.listdir(os.path.join(root, "snapshots"))
                   if os.path.exists(os.path.join(root, "snapshots", d, "scan.json")))
    price, recs, first = {}, {}, {}
    for d in snaps:
        try:
            s = json.load(open(os.path.join(root, "snapshots", d, "scan.json"), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, v in (s.get("PRICES") or {}).items():
            if v:
                price.setdefault(k.lower(), {})[d] = float(v)
        for t in s.get("TOKENS") or []:
            if not t.get("pa"):
                continue
            k = f"{t['c']}:{t['pa']}".lower()
            if t.get("px"):
                price.setdefault(k, {})[d] = float(t["px"])
            recs.setdefault(k, []).append((d, t))
            first.setdefault(k, (d, t))
    return snaps, price, recs, first


# ---------------------------------------------------------------- exit rules (one $1 position, scan by scan)
def run_exit(path, rule):
    units, cash, stt = 1.0, 0.0, {"peak": 1.0, "i": 0, "done": set()}
    for x in path:
        stt["peak"] = max(stt["peak"], x)
        stt["i"] += 1
        f = rule(x, stt)
        if f > 0:
            cash += units * f * x
            units -= units * f
        if units < 1e-9:
            break
    return cash + units * path[-1]


def r_hold(x, s):
    return 0


def r_owner(x, s):                       # owner's rule: half at 2x (capital back), then 25% of the rest at 3x, 4x, ...
    if x >= 2 and 2 not in s["done"]:
        s["done"].add(2)
        return 0.5
    k = 3
    while k <= x:
        if k not in s["done"]:
            s["done"].add(k)
            return 0.25
        k += 1
    return 0


def with_stop(base, level=0.5):
    return lambda x, s: 1.0 if x <= level else base(x, s)


def r_recommended(fast: bool):
    """half at 2x, rest at 4x (fast lists: everything at 2x), stop-loss at -50%"""
    def r(x, s):
        if x <= 0.5:
            return 1.0
        if fast:
            return 1.0 if x >= 2 else 0
        if x >= 2 and 2 not in s["done"]:
            s["done"].add(2)
            return 0.5
        if x >= 4 and 4 not in s["done"]:
            s["done"].add(4)
            return 1.0
        return 0
    return r


EXITS = [("hold", "Just hold", lambda L: r_hold),
         ("owner", "Owner's rule (½ at 2×, 25% each step)", lambda L: r_owner),
         ("owner_stop", "Owner's rule + stop-loss −50%", lambda L: with_stop(r_owner)),
         ("recommended", "Recommended (½ at 2×, rest at 4× — Igniting/Runners: all at 2× — stop −50%)",
          lambda L: r_recommended(L in ("I", "U")))]


# ---------------------------------------------------------------- per logged row: path, hits, age at listing
def row_path(row, price, snaps_t):
    k = f"{row['c']}:{row['pa']}".lower()
    px = row.get("px") or 0
    if not px:
        return None, None
    start = date.fromisoformat(row["d"]).toordinal()
    pts = sorted((snaps_t[d], v / px) for d, v in price.get(k, {}).items() if d in snaps_t and snaps_t[d] > start)
    if not pts:
        return None, None
    best = (row.get("pxMax") or px) / px
    best = max(best, 1.0)
    path = [(t - start, min(x, best * 1.0001)) for t, x in pts]
    if row.get("pxNow") is not None:
        path[-1] = (path[-1][0], min(path[-1][1], row["pxNow"] / px))
    return path, best


def lists_and_timing(page: str, price, recs, snaps):
    snaps_t = {d: tday(d) for d in snaps}
    rows = []
    for name, lab in (("HISTORY", None), ("IGNHIST", "I"), ("MOMHIST", "M"), ("RUNHIST", "U"), ("REJECTS", "R")):
        for h in js_const(page, name):
            if h.get("pa") and h.get("px") and h.get("d"):
                rows.append(dict(h, l=lab or h.get("l") or "S"))
    hits, per = {}, {L: [] for L, _ in LISTS}
    for r in rows:
        path, best = row_path(r, price, snaps_t)
        if not path:
            continue
        k = f"{r['c']}:{r['pa']}".lower()
        hit = {}
        for m in (2, 5, 10):
            for t, x in path:
                if x >= m:
                    hit[m] = t
                    break
        hits[f"{r['l']}:{r['pa'].lower()}"] = [hit.get(2), hit.get(5), hit.get(10)]
        rec = [t for d, t in recs.get(k, []) if d[:10] >= r["d"]]
        age0 = rec[0].get("age") if rec else None
        per.setdefault(r["l"], []).append(dict(r=r, path=[x for _, x in path], best=best, hit=hit, age0=age0,
                                               now=path[-1][1]))
    out_lists, out_timing = [], []
    for L, nm in LISTS:
        rs = per.get(L) or []
        n = len(rs)
        if not n:
            continue
        clean = [x for x in rs if x["best"] <= SPIKE]
        ex = {}
        for key, _, mk in EXITS:
            rule = mk(L)
            tot = sum(run_exit(x["path"], rule) for x in clean)
            ex[key] = pct(tot - len(clean), len(clean))
        out_lists.append({"l": L, "name": nm, "n": n, "hit2": pct(sum(x["best"] >= 2 for x in rs), n),
                          "hit5": sum(x["best"] >= 5 for x in rs), "hit10": sum(x["best"] >= 10 for x in rs),
                          "still2": sum(x["now"] >= 2 for x in rs), "down50": pct(sum(x["now"] <= 0.5 for x in rs), n),
                          "spikes_left_out": n - len(clean), "exits": ex})
        tm = {"l": L, "name": nm}
        for m in (2, 5, 10):
            h = [x for x in rs if m in x["hit"]]
            dl = [x["hit"][m] for x in h]
            al = [x["age0"] + x["hit"][m] for x in h if x["age0"] is not None]
            tm[f"x{m}"] = {"n": len(h), "days_med": med(dl), "within1": sum(d < 1 for d in dl),
                           "d1_3": sum(1 <= d <= 3 for d in dl), "d3_7": sum(3 < d <= 7 for d in dl),
                           "over7": sum(d > 7 for d in dl), "age_med": med(al)}
        out_timing.append(tm)
    return out_lists, out_timing, hits


# ---------------------------------------------------------------- first-seen tokens: score, runners, social, ...
def first_seen_rows(price, first):
    rows = []
    for k, (d0, t) in first.items():
        px0 = t.get("px")
        later = [v for d, v in sorted(price.get(k, {}).items()) if d > d0]
        if not px0 or not later:
            continue
        pk = max(later) / px0
        if pk > MAX_SANE:
            continue
        x = dict(t)
        evaluate(x)
        rows.append({"d0": d0, "t": t, "x": x, "pk": pk, "now": later[-1] / px0,
                     "lists": is_short(x) or is_pull(x) or is_ign(x) or is_mom(x)})
    return rows


def rates(rs):
    n = len(rs)
    return {"n": n, "hit2": pct(sum(r["pk"] >= 2 for r in rs), n), "hit5": pct(sum(r["pk"] >= 5 for r in rs), n),
            "down50": pct(sum(r["now"] <= 0.5 for r in rs), n)}


def score_block(rows, split):
    a = [r for r in rows if r["d0"][:10] < split]
    b = [r for r in rows if r["d0"][:10] >= split]
    bands = []
    for lo, hi in ((0, 20), (20, 40), (40, 60), (60, 101)):
        f = lambda r, lo=lo, hi=hi: lo <= r["x"]["_score"] < hi  # noqa: E731
        bands.append({"band": f"{lo}–{min(hi, 100)}", "first": rates([r for r in a if f(r)]),
                      "second": rates([r for r in b if f(r)])})
    bs = sorted(b, key=lambda r: -r["x"]["_score"])
    n5 = len(bs) // 5
    traits = []
    for key, label, pts in C.SCORE:
        w = [r for r in rows if key in r["x"]["_sparts"]]
        wo = [r for r in rows if key not in r["x"]["_sparts"]]
        traits.append({"key": key, "label": label, "points": pts, "with": rates(w), "without": rates(wo)})
    return {"bands": bands, "top20": rates(bs[:n5]) if n5 else None, "bottom20": rates(bs[-n5:]) if n5 else None,
            "traits": traits}


def runners_block(rows, split):
    def ok(r):
        x = r["x"]
        return x["_ran"] and is_run(x)
    sel = [r for r in rows if ok(r)]
    return {"first": rates([r for r in sel if r["d0"][:10] < split]),
            "second": rates([r for r in sel if r["d0"][:10] >= split]), "all": rates(sel),
            "all_ran": rates([r for r in rows if r["x"]["_ran"]]),
            "big": sorted([[r["t"]["sym"], r["t"]["c"], round(r["pk"], 1)] for r in sel if r["pk"] >= 5],
                          key=lambda z: -z[2])[:8]}


def social_block(rows):
    only = [r for r in rows if r["x"]["_g"]["social"] is False and r["x"].get("band") != "l" and not crowded(r["x"])
            and sum(1 for k, v in r["x"]["_g"].items() if v is False and k != "social") == 0
            and sum(1 for k, v in r["x"]["_g"].items() if v is None and k != "social") == 0]
    shortl = [r for r in rows if is_short(r["x"]) and r["x"]["_g"]["social"] is not False]
    return {"only_social": rates(only), "shortlist_with_site": rates(shortl),
            "examples": sorted([[r["t"]["sym"], round(r["pk"], 1)] for r in only if r["pk"] >= 2], key=lambda z: -z[1])[:8]}


def robinhood_block(rows):
    rh = [r for r in rows if r["t"].get("c") == "robinhood"]
    bars = [("No extra bar", lambda t, x: True), ("Buyer share 70%+", lambda t, x: (t.get("ubr") or 0) >= .7),
            ("At least 2 days old", lambda t, x: (t.get("age") or 0) >= 2),
            ("Trading 3×+ its liquidity", lambda t, x: (t.get("turn") or 0) >= 3),
            ("Score 30+", lambda t, x: x["_score"] >= 30)]
    out = []
    win = [r for r in rh if r["pk"] >= 2]
    for nm, f in bars:
        keep = [r for r in rh if f(r["t"], r["x"])]
        drop = [r for r in rh if not f(r["t"], r["x"])]
        out.append({"bar": nm, "kept": len(keep), "of": len(rh), "winners_kept": sum(r["pk"] >= 2 for r in keep),
                    "winners": len(win), "hit2_kept": pct(sum(r["pk"] >= 2 for r in keep), len(keep)),
                    "hit2_dropped": pct(sum(r["pk"] >= 2 for r in drop), len(drop))})
    return {"bars": out, "on_lists": sum(r["lists"] for r in rh)}


def asset_block(rows):
    calm = [r for r in rows if r["x"]["_calm"]]
    rest = [r for r in rows if not r["x"]["_calm"]]
    return {"calm": rates(calm), "rest": rates(rest),
            "calm_doubled": [[r["t"]["sym"], r["t"]["c"], round(r["pk"], 1)] for r in calm if r["pk"] >= 2][:10]}


def winners_block(rows):
    fast, slow, never = [], [], []
    for r in rows:
        (never if r["pk"] < 2 else (fast if r.get("d2") is not None and r["d2"] <= 3 else slow)).append(r)

    def prof(rs):
        t = [r["t"] for r in rs]
        n = len(rs)
        ch = {}
        for x in t:
            ch[x.get("c")] = ch.get(x.get("c"), 0) + 1
        return {"n": n, "age": med([x.get("age") for x in t]), "mcap_k": med([x.get("mc") for x in t]),
                "turn": med([x.get("turn") for x in t]), "buyers": med([x.get("ubr") for x in t]),
                "c6": med([x.get("c6") for x in t]), "below_high": med([x.get("retr") for x in t]),
                "holders": med([x.get("hld") for x in t]),
                "has_social": pct(sum(bool(x.get("tw") or x.get("web")) for x in t), n),
                "chains": sorted(([k, pct(v, n)] for k, v in ch.items()), key=lambda z: -z[1])[:4]}
    return {"fast": prof(fast), "slow": prof(slow), "never": prof(never)}


def add_d2(rows, price, first):
    """days from first seen to the first 2x scan, for the fast / slow split"""
    for r in rows:
        k = f"{r['t']['c']}:{r['t']['pa']}".lower()
        d0 = r["d0"]
        px0 = r["t"].get("px")
        r["d2"] = None
        for d, v in sorted(price.get(k, {}).items()):
            if d > d0 and v >= 2 * px0:
                r["d2"] = tday(d) - tday(d0)
                break


def headline(review: dict) -> dict:
    """the few numbers kept at each review, to show what changed since"""
    return {"lists": {x["l"]: {"n": x["n"], "hit2": x["hit2"], "down50": x["down50"],
                               "recommended": x["exits"].get("recommended")} for x in review["lists"]},
            "score": {b["band"]: b["second"]["hit2"] for b in review["score"]["bands"]},
            "runners": review["runners"]["all"]["hit2"], "social": review["social"]["only_social"]["hit2"],
            "asset_doubled": len(review["asset"]["calm_doubled"])}


def study(root: str, page: str) -> dict:
    snaps, price, recs, first = load(root)
    lists, timing, hits = lists_and_timing(page, price, recs, snaps)
    rows = first_seen_rows(price, first)
    add_d2(rows, price, first)
    days = sorted({r["d0"][:10] for r in rows})
    split = days[len(days) // 2] if days else "2026-10-15"
    now = datetime.now(timezone.utc).astimezone(SGT)
    return {"generated": now.strftime("%Y-%m-%d %H:%M SGT"), "first_day": snaps[0][:10] if snaps else None,
            "last_day": snaps[-1] if snaps else None, "tokens": len(rows), "split": split,
            "lists": lists, "timing": timing, "winners": winners_block(rows), "score": score_block(rows, split),
            "runners": runners_block(rows, split), "social": social_block(rows), "robinhood": robinhood_block(rows),
            "asset": asset_block(rows), "hits": hits}


def keep_history(path: str, review: dict) -> list:
    try:
        hist = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        hist = []
    today = datetime.now(timezone.utc).astimezone(SGT).date()
    last_review = REVIEW_ANCHOR + timedelta(days=14 * ((today - REVIEW_ANCHOR).days // 14))
    if today >= REVIEW_ANCHOR and not any(h.get("review") == last_review.isoformat() for h in hist):
        hist.append({"review": last_review.isoformat(), "taken": review["generated"], "k": headline(review)})
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)
    return hist


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: review_study.py <repo_root> <page.html>")
        return 2
    root, page_path = sys.argv[1], sys.argv[2]
    page = open(page_path, encoding="utf-8").read()
    if page.count(START) != 1 or page.count(END) != 1:
        print("review study: markers not found in page — template not updated yet, skipping")
        return 0
    review = study(root, page)
    site = os.path.dirname(page_path)
    hist = keep_history(os.path.join(site, "review-history.json"), review)
    review["history"] = hist[-6:]
    with open(os.path.join(site, "review.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in review.items() if k != "hits"}, f, ensure_ascii=False, indent=1)
    blob = json.dumps(review, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    json.loads(blob.replace("<\\/", "</"))                       # round-trip check before touching the page
    a, b = page.index(START) + len(START), page.index(END)
    with open(page_path + ".tmp", "w", encoding="utf-8") as f:
        f.write(page[:a] + blob + page[b:])
    os.replace(page_path + ".tmp", page_path)
    print(f"review study: {review['tokens']} tokens, {len(review['hits'])} logged rows timed, split {review['split']}, "
          f"history {len(hist)} review(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
