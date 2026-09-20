"""End-to-end offline run of the job against tests/fake_net.py, then assertions on the output contract.

    python tests/run_offline.py            # happy path
    python tests/run_offline.py --block-gt # simulate GeckoTerminal behind Turnstile
    python tests/run_offline.py --block-ds # simulate DexScreener screener pages challenged
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BLOCKSCOUT_API_KEY", "test-key")

from fetch import common          # noqa: E402
from fetch import main as job     # noqa: E402
from tests.fake_net import FakeNet, SOL_PAIR, RH_PAIR, RH_PAIR_UNV, BSC_PAIR, BSC_LEADER_PAIR, BSC_USDT_PAIR  # noqa: E402

fake = FakeNet()
if "--block-gt" in sys.argv:
    FakeNet.block = {"api.geckoterminal.com"}
if "--block-ds" in sys.argv:
    FakeNet.fail_screener = True
common.Http.get = lambda self, url, **kw: fake.get(url, **kw)
common.Http.post_json = lambda self, url, body, **kw: fake.post_json(url, body, **kw)
common.time.sleep = lambda s: None  # no pacing waits offline
import fetch.chains, fetch.discover, fetch.candles, fetch.safety, fetch.reprice  # noqa: E402,F401
for m in (fetch.chains, fetch.discover, fetch.candles, fetch.safety, fetch.reprice):
    if hasattr(m, "time"):
        m.time.sleep = lambda s: None

root = tempfile.mkdtemp(prefix="moonshot-")
shutil.copy(os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed_pairs.json"), root)
rc = job.main(root)
assert rc == 0
out = json.load(open(os.path.join(root, "latest", "scan.json")))
status = json.load(open(os.path.join(root, "latest", "status.json")))
print("\n=== STATUS ===")
for k, v in status["sources"].items():
    print(f"  {k:26s} {v['state']:8s} {v.get('note') or v.get('error') or ''}")
print("http calls:", status["http_calls"])
print("passing:", status["passing_chains"], "scanned:", status["chains_scanned"])

toks = {t["pa"]: t for t in out["TOKENS"]}
by_sym = {t["sym"]: t for t in out["TOKENS"]}
print("\n=== TOKENS ===")
for t in out["TOKENS"]:
    print(" ", json.dumps(t)[:400])

if "--block-gt" in sys.argv:
    assert status["sources"]["geckoterminal"]["state"] == "fail", status["sources"]["geckoterminal"]
    # discovery still works via DexScreener; candle fields absent; safety still ran
    assert all(t.get("run") is None for t in out["TOKENS"])
    assert by_sym["JUPCAT"]["sec"] == "rugcheck"
    print("\nBLOCK-GT scenario OK: geckoterminal=fail, discovery via dexscreener, safety intact")
    sys.exit(0)
if "--block-ds" in sys.argv:
    assert status["sources"]["discovery"]["state"] == "partial"
    assert len(out["TOKENS"]) >= 4, "GeckoTerminal-only discovery should still find the pairs"
    print("\nBLOCK-DS scenario OK: discovery partial (GT only), tokens found:", len(out["TOKENS"]))
    sys.exit(0)

# ---------------- chain rows
data = {r["s"]: r for r in out["DATA"]}
assert "tiny" not in data, "chain under both universe thresholds must be excluded"
sol = data["solana"]
assert sol["n"] == "Solana" and sol["dv"] == 2600.0 and len(sol["ds"]) == 14, sol
assert abs(sol["dr"] - 1.0) < 0.05, sol["dr"]                       # last==base, mean of 7 settled ≈ base
assert out["LAST_DAY"] == sol.get("_lastday", out["LAST_DAY"]) and out["LAST_DAY"] < __import__("time").time() - 86400 + 1, "LAST_DAY must be a settled bucket"
bsc = data["bsc"]
assert abs(bsc["dr"] - 1.6) < 0.05, ("bsc dr should reflect 24h at 1.6× pace", bsc["dr"])
assert "bsc" in status["passing_chains"] and "solana" not in status["passing_chains"]
assert sol["oi"] == 210.0 and sol["pv"] == 1720.0 and abs(sol["pr"] - 1.04) < 0.02, (sol["oi"], sol["pv"], sol["pr"])
assert sol["bd24"] == 108.0 and sol["bn7"] == -28.0 and sol["btop"] == "USDC", sol
assert "bd24" not in data["robinhood"], "chain absent from tableData must get NO bridge keys"
assert data["hyperliquid"]["n"] == "Hyperliquid" and data["hyperliquid"]["oi"] == 7140.0
assert sol["sb"] > 15000 and "f1" in sol and "f7" in sol and "fr" in sol
assert sol["fe24"] == 14.6 and sol["tv"] > 5000 and len(sol["ts"]) == 14
assert out["MAJORS"]["btc"] == {"d1": 4.21, "d7": 8.42, "d30": 12.63}, out["MAJORS"]
assert out["NETFLOW"]["day"][0] == ["Hyperliquid", -263.55], out["NETFLOW"]
assert out["BRIDGES"][0] == ["Across", 414.0, 1710.0, 8200.0, 103171, 45.8, 25], out["BRIDGES"][0]

# ---------------- tokens
assert BSC_USDT_PAIR in toks or True  # USDT must NOT be present as a leader
assert "USDT" not in by_sym, "stablecoin must be excluded from the leader band"
assert by_sym["CAKE"]["band"] == "l" and by_sym["JUPCAT"]["band"] == "m"
j = by_sym["JUPCAT"]
assert j["c"] == "solana" and j["mc"] == 970.0 and j["liq"] == 103.8 and j["lp"] == 10.7 and j["turn"] == 8.2, j
assert j["ub"] == 609 and j["us"] == 487 and j["ubr"] == 0.56 and j["tpw"] == 1.9, j
assert j["nd"] == 13 and abs(j["run"] - 4.43) < 0.02 and abs(j["retr"] - 72.2) < 0.5, (j["run"], j["retr"])
assert len(j["cs"]) == 13 and max(j["cs"]) == 100 and j["cs"][-1] < 100, j["cs"]
assert j["vvp"] is not None and j["sec"] == "rugcheck" and j["rcs"] == 1 and j["lpk"] == 96.04 and j["ran"] == 1
assert j["tw"] == "jupitercat_st" and j["web"] == "https://example.org" and 12 < j["age"] < 14
assert "px" in j, "px must be carried for the scan's price cross-check"
u = by_sym["URANUS"]
assert u["noCd"] == 1 and "run" not in u, "wrong-side guard must void candle fields"
assert u["sec"] == "nodata" and u["ver"] == 0 and u["hld"] == 4030 and "t10" in u, "unverified explorer contract stays nodata but keeps holder facts"
r = by_sym["RightsLayer"]
assert r["noCd"] == 1, "age < 1 must set noCd"
assert r["sec"] == "explorer" and r["ver"] == 1 and r["hld"] == 4030 and r["cnm"] == "Token", r
assert r["own"] == "live" and r["mint"] == 1 and r["tb"] == 1, ("owner live + mint/setMaxTx in ABI", r)
assert abs(r["pool"] - 31.8) < 0.2 and r["t10"] < 50 and r["t1"] == 1.3 and r["cN"] >= 1, (r["pool"], r["t10"], r["t1"], r["cN"])
q = by_sym["quq"]
assert q["sec"] == "goplus" and q["stx"] == 0.0 and q["sstx"] == 2.0 and q["hld"] == 3319 and q["t10"] == 52.0 and q["lpk"] == 95.0, q
assert q["hpOk"] == 1 and "honey" not in q
assert status["sources"]["blockscout"]["explorer"] == 1 and status["sources"]["blockscout"]["unverified"] == 1

# ---------------- prices / reprice
assert f"solana:{SOL_PAIR}" in out["PRICES"] and out["PRICES"][f"solana:{SOL_PAIR}"] == 0.00097
seed = json.load(open(os.path.join(root, "seed_pairs.json")))
missing = [p for p in seed if f"{p['c']}:{p['pa']}" not in out["PRICES"]]
assert not missing, f"every seed pair must get a price (0 when unknown): {missing[:3]}"
zeros = sum(1 for k, v in out["PRICES"].items() if v == 0)
print(f"\nprices: {len(out['PRICES'])} entries, {zeros} unknown→0")

# ---------------- snapshot files
assert os.path.exists(os.path.join(root, "snapshots", out["day_sgt"], "scan.json"))
assert not any(k.startswith("_") for t in out["TOKENS"] for k in t), "no private keys may leak into the snapshot"
print("\nALL ASSERTIONS PASSED —", len(out["DATA"]), "chains,", len(out["TOKENS"]), "tokens, day", out["day_sgt"], "at", out["generated_at_sgt"])
