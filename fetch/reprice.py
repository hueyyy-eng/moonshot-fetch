"""STEP 4b support — re-price every pair the track record could be following.

The job cannot read the dashboard's HISTORY/REJECTS, so it re-prices the union of:
  - seed_pairs.json (the track record's pairs at the time this repo was set up)
  - every token in the last 60 days of committed snapshots
  - today's tokens (already priced — reused, not refetched)
Output: {"<chainId>:<pairAddress>": priceUsd}, 0 for a pair DexScreener no longer knows (rugged/delisted).
"""
from __future__ import annotations

import glob
import os
from datetime import datetime, timedelta, timezone

from .common import Http, Status, load_json, log
from .discover import DS_API


def collect_pairs(repo_root: str, todays: list[dict]) -> dict[str, set[str]]:
    pairs: dict[str, set[str]] = {}
    seed = load_json(os.path.join(repo_root, "seed_pairs.json"), default=[])
    for p in seed:
        pairs.setdefault(p["c"], set()).add(p["pa"])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    for path in sorted(glob.glob(os.path.join(repo_root, "snapshots", "*", "scan.json"))):
        day = os.path.basename(os.path.dirname(path))
        if day < cutoff:
            continue
        snap = load_json(path, default={}) or {}
        for t in snap.get("TOKENS") or []:
            if t.get("c") and t.get("pa"):
                pairs.setdefault(t["c"], set()).add(t["pa"])
    for t in todays:
        pairs.setdefault(t["c"], set()).add(t["pa"])
    return pairs


def run(http: Http, st: Status, repo_root: str, todays: list[dict]) -> dict[str, float]:
    http.pace("api.dexscreener.com", 200)
    have = {f"{t['c']}:{t['pa']}": t.get("px") for t in todays if t.get("px") is not None}
    prices: dict[str, float] = dict(have)
    pairs = collect_pairs(repo_root, todays)
    n_req = n_ok = n_err = 0
    for chain, addrs in pairs.items():
        todo = [a for a in addrs if f"{chain}:{a}" not in prices]
        for i in range(0, len(todo), 30):
            batch = todo[i:i + 30]
            n_req += 1
            try:
                j = http.get(f"{DS_API}/latest/dex/pairs/{chain}/{','.join(batch)}", retries=2)
                seen = {}
                for p in (j or {}).get("pairs") or []:
                    if p.get("priceUsd") not in (None, ""):
                        seen[p["pairAddress"].lower()] = float(p["priceUsd"])
                for a in batch:
                    prices[f"{chain}:{a}"] = seen.get(a.lower(), 0.0)
                n_ok += 1
            except Exception as e:  # noqa: BLE001
                n_err += 1
                log.warning("reprice %s batch: %s", chain, str(e)[:80])
    total = sum(len(v) for v in pairs.values())
    if n_err and n_ok == 0:
        st.fail("reprice", f"all {n_req} batches failed", pairs=total)
    elif n_err:
        st.partial("reprice", f"{n_err}/{n_req} batches failed — missing pairs are absent, not zero", pairs=total, priced=len(prices))
    else:
        st.ok("reprice", pairs=total, priced=len(prices))
    return prices
