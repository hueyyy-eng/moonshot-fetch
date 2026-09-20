"""Render the rebuilt page headless and check what the scan prompt says to check before publishing.

    python scan/verify_page.py out/page_new.html out/summary.json

Exit 0 when every check passes, 1 otherwise; prints one line per check. Needs playwright (python) with
chromium — present in the cloud sandbox (PLAYWRIGHT_BROWSERS_PATH), otherwise: pip install playwright.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    page_path, summary_path = sys.argv[1], sys.argv[2]
    summary = json.load(open(summary_path, encoding="utf-8"))
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    fails: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        # network failures (Google Fonts is unreachable from the sandbox) are not script errors
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.goto("file://" + page_path)
        pg.wait_for_timeout(800)

        def check(name, ok, detail=""):
            print(f"{'ok  ' if ok else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
            if not ok:
                fails.append(name)

        check("no JS errors", not errors, "; ".join(errors)[:300])
        check("no horizontal overflow", pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"))

        # every table: header cell count == every row's cell count (a renderer emitting fewer cells shifts columns)
        bad = pg.evaluate("""() => {
          const out=[]; for(const t of document.querySelectorAll('table')){
            const h=t.querySelector('thead tr'); if(!h) continue; const n=h.children.length;
            for(const r of t.querySelectorAll('tbody tr')){ if(r.querySelector('td.empty')||r.children.length===1&&r.children[0].colSpan>1) continue;
              if(r.children.length!==n) out.push((t.closest('section')||{}).id+': '+n+' heads vs '+r.children.length+' cells'); }
          } return out; }""")
        check("table cell counts match headers", not bad, "; ".join(bad[:5]))

        syms = lambda sel: pg.evaluate(f"[...document.querySelectorAll('{sel} td.tok')].map(td=>td.childNodes[0].textContent.trim())")  # noqa: E731
        short = syms("#shortBody")
        want = [t["sym"] for t in summary["shortlist"]]
        check("shortlist on page == builder", sorted(short) == sorted(want), f"page {short} vs builder {want}")
        pull = syms("#pullBody")
        wantp = [t["sym"] for t in summary["pullback"]]
        check("pullback on page == builder", sorted(pull) == sorted(wantp), f"page {pull} vs builder {wantp}")
        ign = syms("#ignBody")
        wanti = [t["sym"] for t in summary["igniting"]]
        check("igniting on page == builder", sorted(ign) == sorted(wanti), f"page {ign} vs builder {wanti}")

        # playbook renders in both groupings
        n_chain = pg.evaluate("document.querySelectorAll('#pbWrap .pbgroup').length")
        pg.click("#tabs-pb button[data-v=nar]")
        pg.wait_for_timeout(200)
        n_nar = pg.evaluate("document.querySelectorAll('#pbWrap .pbgroup').length")
        check("playbook renders by chain and by narrative", n_chain > 0 and n_nar > 0, f"{n_chain} chain groups, {n_nar} narrative groups")
        tierA = pg.evaluate("[...document.querySelectorAll('#pbWrap .pbtier')].filter(t=>t.querySelector('.tier.tA')&&t.querySelector('.pbrow')).length")
        print(f"info playbook tier-A groups with rows: {tierA}")

        # NetFlow tab: net-flow bars for all three periods, gross table has rows, bridge table has rows
        pg.click("#tabs-metric button[data-m=bridge]")
        pg.wait_for_timeout(200)
        counts = {}
        for per in ("day", "week", "month"):
            pg.click(f"#tabs-nf button[data-p={per}]")
            pg.wait_for_timeout(120)
            counts[per] = pg.evaluate("document.querySelectorAll('#nfRows .nf-row').length")
        check("net-flow bars for day/week/month", all(v > 0 for v in counts.values()), str(counts))
        gross = pg.evaluate("document.querySelectorAll('#body tr').length")
        check("gross-flow table has rows", gross > 0, f"{gross} rows")
        br = pg.evaluate("document.querySelectorAll('#brBody tr').length")
        check("bridge protocol table has rows", br > 0, f"{br} rows")
        # stamp and staleness banner
        stamp = pg.evaluate("document.getElementById('stampText').textContent")
        print(f"info stamp: {stamp}")
        banner = pg.evaluate("document.getElementById('shortStale').textContent.trim()")
        if banner:
            print(f"info stale banner: {banner[:160]}")
        b.close()
    print("VERIFY " + ("PASSED" if not fails else "FAILED: " + ", ".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
