"""Orchestrator. Runs every source best-effort, writes snapshots/<date>/scan.json and latest/scan.json.

The output is everything the dashboard's data block needs EXCEPT the parts that need judgement or the
previous page state (nar tags, HISTORY/REJECTS/NARHIST/HOLDHIST advancement, carry-forward decisions).
Those stay with the cloud scan, which reads latest/scan.json and latest/status.json.
Exit code is always 0 when a file was written — a partial snapshot with an honest status beats no snapshot.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import timezone

from . import chains, discover, candles, safety, reprice
from .common import Http, Status, now_utc, now_sgt_str, drop_none, dump_json, SGT, log

CHAIN_GATE = {"dr": 1.3, "dv": 5}


def passing_chains(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        dv = r.get("dv") or 0
        if dv >= CHAIN_GATE["dv"] and ((r.get("dr") or 0) >= CHAIN_GATE["dr"] or (r.get("dr30") or 0) >= CHAIN_GATE["dr"]):
            out.append(r["s"])
    return out


def clean_token(t: dict) -> dict:
    t = {k: v for k, v in t.items() if not k.startswith("_")}
    order = ["c", "pa", "sym", "nm", "ta", "q", "dex", "mc", "liq", "lp", "v24", "v6", "v1", "b6", "s6", "ub", "us", "ubr", "tpw",
             "turn", "c1", "c6", "c24", "age", "tw", "web", "bo", "band", "px", "run", "retr", "vvp", "nd", "cs", "noCd",
             "sec", "rcs", "lpk", "t10", "hld", "stx", "sstx", "mint", "frz", "tb", "t1", "pool", "cN", "ver", "own", "cnm",
             "honey", "hpOk", "hpInc", "rk", "ran", "par", "wsh"]
    out = {k: t[k] for k in order if k in t and t[k] is not None}
    for k, v in t.items():
        if k not in out and v is not None:
            out[k] = v
    return out


def main(repo_root: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s", stream=sys.stdout)
    t0 = time.time()
    st = Status()
    http = Http()
    stamp = now_sgt_str()
    day = now_utc().astimezone(SGT).strftime("%Y-%m-%d")

    # STEP 1 — chains
    try:
        ch = chains.run(http, st)
    except Exception as e:  # noqa: BLE001
        st.fail("llama.chain_rows", f"chains stage crashed: {e}")
        ch = {"LAST_DAY": None, "DATA": [], "NETFLOW": None, "BRIDGES": None, "MAJORS": None, "perp_slugs_present": []}
    passing = passing_chains(ch["DATA"])
    log.info("passing chains: %s", passing)

    # STEP 2 — discovery + pair rows
    try:
        disc = discover.run(http, st, passing, chains.CORE_CHAINS)
    except Exception as e:  # noqa: BLE001
        st.fail("discovery", f"discovery stage crashed: {e}")
        disc = {"tokens": [], "chains_scanned": [], "NO_DEXSCREENER": []}
    tokens = disc["tokens"]
    log.info("tokens after narrowing: %d (%d m / %d l)", len(tokens), sum(t["band"] == "m" for t in tokens), sum(t["band"] == "l" for t in tokens))

    # STEP 3 — candles/wallets, then safety
    if tokens:
        try:
            candles.run(http, st, tokens)
        except Exception as e:  # noqa: BLE001
            st.fail("geckoterminal", f"candles stage crashed: {e}")
        try:
            safety.run(http, st, tokens)
        except Exception as e:  # noqa: BLE001
            st.fail("safety", f"safety stage crashed: {e}")

    # STEP 4b support — re-price the track record's pairs
    try:
        prices = reprice.run(http, st, repo_root, tokens)
    except Exception as e:  # noqa: BLE001
        st.fail("reprice", f"reprice stage crashed: {e}")
        prices = {}

    status = st.as_dict()
    status["http_calls"] = dict(http.calls)
    status["passing_chains"] = passing
    status["chains_scanned"] = disc.get("chains_scanned", [])
    out = {
        "schema": 1,
        "generated_at_sgt": stamp,
        "day_sgt": day,
        "status": status["sources"],
        "LAST_DAY": ch.get("LAST_DAY"),
        "CORE_CHAINS": chains.CORE_CHAINS,
        "NO_DEXSCREENER": [s for s in disc.get("NO_DEXSCREENER", []) if s in passing],
        "passing_chains": passing,
        "DATA": ch["DATA"],
        "NETFLOW": ch.get("NETFLOW"),
        "BRIDGES": ch.get("BRIDGES"),
        "MAJORS": ch.get("MAJORS"),
        "perp_slugs_present": ch.get("perp_slugs_present", []),
        "TOKENS": [clean_token(t) for t in tokens],
        "PRICES": prices,
    }
    dump_json(os.path.join(repo_root, "snapshots", day, "scan.json"), out)
    dump_json(os.path.join(repo_root, "latest", "scan.json"), out)
    dump_json(os.path.join(repo_root, "latest", "status.json"), status)
    dump_json(os.path.join(repo_root, "snapshots", day, "status.json"), status)
    log.info("wrote snapshot for %s in %ds — %d chains, %d tokens, %d prices", day, int(time.time() - t0),
             len(out["DATA"]), len(out["TOKENS"]), len(prices))
    bad = [k for k, v in status["sources"].items() if v.get("state") == "fail"]
    if bad:
        log.warning("sources that FAILED this run: %s", bad)
    return 0


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    sys.exit(main(root))
