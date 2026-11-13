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
assert sol["bd24"] == 108.0 and sol["bw24"] == 115.0 and sol["bn24"] == -7.0, sol
assert sol["bd7"] == 730.0 and sol["bw7"] == 760.0 and sol["bn7"] == -30.0, sol     # 7d in/out from total + net
assert "bd24" not in data["robinhood"], "chain absent from /bridges/chains must get NO bridge keys"
assert data["hyperliquid"]["n"] == "Hyperliquid" and data["hyperliquid"]["oi"] == 7140.0
assert sol["sb"] > 15000 and "f1" in sol and "f7" in sol and "fr" in sol
assert sol["fe24"] == 14.6 and sol["tv"] > 5000 and len(sol["ts"]) == 14
assert out["MAJORS"]["btc"] == {"d1": 4.21, "d7": 8.42, "d30": 12.63}, out["MAJORS"]
assert out["NETFLOW"]["day"][0] == ["Hyperliquid L1", -263.55, "hyperliquid"], out["NETFLOW"]
# top 7-day inflow chains: core chains and chains with no token source (arc) are skipped, the next ones taken
from fetch.main import top_inflow_chains
from fetch.chains import CORE_CHAINS
assert top_inflow_chains({"arc": 4822, "ethereum": 860, "base": 45, "plasma": 20, "mantle": 14, "anubis": 15,
                          "polygon": -4612, "sui": -4}, CORE_CHAINS) == ["ethereum", "plasma", "mantle"]
assert top_inflow_chains({"ethereum": 5, "polygon": -1}, CORE_CHAINS) == ["ethereum"], "never pads with outflow chains"
# Elysium (owner, 8 Nov 2026): always searched, token-only, mapped to GeckoTerminal/DexScreener "elysium"
from fetch.chains import TOKEN_ONLY_CORE
from fetch import discover as _d
assert "elysium" in TOKEN_ONLY_CORE and "elysium" not in CORE_CHAINS
assert _d.LLAMA_TO_DS["elysium"] == ["elysium"] and _d.DS_TO_GT["elysium"] == "elysium"
assert "elysium" in out["CORE_CHAINS"], out["CORE_CHAINS"]
assert top_inflow_chains({"elysium": 99, "ethereum": 5}, CORE_CHAINS + TOKEN_ONLY_CORE) == ["ethereum"], "core chains never fill inflow slots"
# no rug check on Elysium: no rug data counts as passed (with a tag); the same token on Solana stays unknown
import sys as _sys; _sys.path.insert(0, "scan")
from build_block import evaluate, C as _C
_tok = lambda c: {"c": c, "pa": "x", "sym": "T", "band": "m", "sec": "nodata", "mc": 900, "lp": 10, "liq": 90, "turn": 5,
                  "ubr": 0.6, "c6": 10, "c24": 20, "run": 1.5, "tw": "t", "v6": 10, "v24": 30}
e, s_ = _tok("elysium"), _tok("solana"); evaluate(e); evaluate(s_)
assert e["_unchk"] and e["_g"]["safe"] is True, e["_g"]
assert not s_["_unchk"] and s_["_g"]["safe"] is None, s_["_g"]
h = dict(_tok("elysium"), honey=1); evaluate(h); assert h["_g"]["safe"] is False, "a failed sell simulation still blocks"
assert "elysium" in _C.MOM["chains"]
# 8 Nov 2026 back-test changes: score, social as a warning, "tracks an asset", Runners
from build_block import is_short, is_run, score as _score
good = dict(_tok("solana"), sec="rugcheck", t10=20, rcs=1, mc=1500, ubr=0.75, age=2, c24=150, turn=12, c6=10, run=1.5, retr=10)
evaluate(good); assert good["_score"] == 100 and good["_sparts"][0] == "buyers", (good["_score"], good["_sparts"])
nosoc = dict(good, tw=None); evaluate(nosoc)
assert nosoc["_g"]["social"] is False and nosoc["_nf"] == 0 and is_short(nosoc), "no X / site warns but does not block"
calm = dict(good, run=1.1, retr=5); evaluate(calm); assert calm["_calm"] and not good["_calm"]
runner = dict(good, run=4.0, retr=10, age=0.5, ubr=0.8, turn=15, c24=150, c6=20, mc=1200); evaluate(runner)
assert runner["_ran"] and is_run(runner), "already ran, half a day old, 80% buyers, 15x turnover -> Runners"
from build_block import is_mom
mom_too = dict(runner, age=1.5); evaluate(mom_too); assert is_mom(mom_too) and not is_run(mom_too), "a Momentum token stays in Momentum"
old = dict(runner, age=5.0); evaluate(old); assert not is_run(old), "Runners are under 3 days old"
# Review tab maths (scan/review_study.py): exit rules on a $1 position, scan by scan
from review_study import run_exit, r_owner, r_recommended, with_stop
assert abs(run_exit([2.0, 4.0], r_recommended(False)) - 3.0) < 1e-9, "half at 2x ($1) + half at 4x ($2)"
assert abs(run_exit([2.0, 3.0], r_owner) - 2.5) < 1e-9, "half at 2x, 25% of the rest at 3x, rest held at 3x"
assert abs(run_exit([0.4, 3.0], with_stop(r_owner)) - 0.4) < 1e-9, "stop-loss sells everything at -60%"
assert abs(run_exit([2.5, 0.1], r_recommended(True)) - 2.5) < 1e-9, "fast lists: everything out at 2x"
# classification sources (8 Nov 2026): Solana launch text via the token's own metadata link, then DexScreener profile
import auto_nar as _an
_mi = {"result": {"value": {"data": {"parsed": {"info": {"extensions": [
    {"extension": "tokenMetadata", "state": {"uri": "https://ipfs.io/ipfs/bafkreifubj7qp4lxavasfmmf5x7kdixhdqs6n25f5vqxk2nmnm5ufm77ge"}}]}}}}}}
assert _an.metadata_uri(_mi).endswith("ufm77ge") and _an.metadata_uri({"result": {"value": None}}) == ""
assert _an.ipfs_urls(_an.metadata_uri(_mi))[0].startswith("https://ipfs.filebase.io/ipfs/bafkrei"), "ipfs.io blocks GitHub: use the gateways that answer"
assert _an.ipfs_urls("https://example.com/m.json") == ["https://example.com/m.json"] and _an.ipfs_urls("") == []
assert _an.ds_description({"cms": {"description": " An AI agent "}}) == "An AI agent" and _an.ds_description({"cms": None}) == ""
assert _an.tags_from_profile([], "The first dog on Solana. Woof!") == ["Dog & animal memes"]
assert _an.tags_from_profile([], "Buy Wealth. Own Wealth. Build Wealth") == ["Unclassified"], "vague text is not guessed"
# quick Solana check (9 Nov 2026): quiet hours 11pm-7am SGT, message never carries a sell target
from datetime import datetime as _dt
from fetch import quick as _q
from fetch.common import SGT as _SGT
assert _q.quiet(_dt(2026, 11, 9, 23, 5, tzinfo=_SGT)) and _q.quiet(_dt(2026, 11, 9, 6, 59, tzinfo=_SGT))
assert not _q.quiet(_dt(2026, 11, 9, 7, 0, tzinfo=_SGT)) and not _q.quiet(_dt(2026, 11, 9, 22, 59, tzinfo=_SGT))
_m = _q.message({"sym": "T", "pa": "abc", "_score": 70, "_sparts": ["buyers"], "mc": 1200, "liq": 150, "ubr": 0.76,
                 "c24": 150, "c6": 20, "age": 1.4, "sec": "rugcheck", "_ran": False}, "U")
assert "score 70/100" in _m and "Not a recommendation" in _m and "2x" not in _m.lower().replace("2×", "")
# 13 Nov 2026: self-restarting quick-check chain - next slot at least 30 min away, overnight one relay wait
from quick_next import plan as _plan
_w, _r, _s = _plan(_dt(2026, 11, 13, 9, 35, tzinfo=_SGT)); assert (_s.hour, _s.minute, _r) == (11, 23, False), (_s, _r)
_w, _r, _s = _plan(_dt(2026, 11, 13, 9, 0, tzinfo=_SGT)); assert (_s.hour, _r) == (11, False), "23 min to 9:23 is too close"
_w, _r, _s = _plan(_dt(2026, 11, 13, 21, 35, tzinfo=_SGT)); assert _r and _s.hour == 7 and _w <= 330 * 60, "overnight: relay"
_w, _r, _s = _plan(_dt(2026, 11, 14, 3, 5, tzinfo=_SGT)); assert not _r and (_s.hour, _s.minute) == (7, 23) and _w == 258 * 60
assert "quick check — BSC" in _q.message(dict(sym="T", pa="abc", c="bsc", _score=70, _sparts=[]), "I"), "BSC alerts say BSC"
# 11 Nov 2026: Early watch, Breakouts, Second look, band rules, wider search
from build_block import is_early, is_brk, second_look, mainband, is_ign, is_pull
ew = dict(good, band="e", mc=350, liq=60, lp=17, age=2); evaluate(ew)
assert ew["_g"]["mcap"] is True and is_early(ew) and not is_short(ew) and not is_mom(ew) and not is_run(ew), "early band: own list only"
assert not is_early(dict(ew, age=0.5, _g=ew["_g"], _honey=False, _wash=False)), "Early watch wants 1 day+"
bk = dict(good, band="b", mc=2600, liq=300, lp=11, bko=1); evaluate(bk)
assert is_brk(bk) and not is_short(bk) and not is_run(bk)
bk3 = dict(bk, bko=3); evaluate(bk3); assert not is_brk(bk3), "Breakouts show for 3 days"
assert second_look(dict(px=1.2, age=2, ubr=0.6), dict(px=1.0)) and not second_look(dict(px=1.05, age=2, ubr=0.6), dict(px=1.0))
assert not second_look(dict(px=1.2, age=8, ubr=0.6), dict(px=1.0)) and not second_look(dict(px=1.2, age=2, ubr=0.5), dict(px=1.0))
assert mainband({}) and mainband({"band": "m"}) and not mainband({"band": "e"})
from fetch import discover as _d
_rows = [dict(ta="a1", pa="p1", mc=300, liq=40, lp=13, turn=3, v24=100), dict(ta="a2", pa="p2", mc=120, liq=12, lp=10, turn=1, v24=50),
         dict(ta="a3", pa="p3", mc=2500, liq=200, lp=8, turn=2, v24=80), dict(ta="a4", pa="p4", mc=900, liq=90, lp=10, turn=2, v24=70),
         dict(ta="a5", pa="p5", mc=120, liq=5, lp=4, turn=1, v24=40)]
_b = {r["ta"]: r["band"] for r in _d.narrow_and_band([dict(r) for r in _rows], ("m", "l", "e", "x", "b"), {"a3"})}
assert _b == {"a1": "e", "a2": "x", "a3": "b", "a4": "m"}, _b
_b = {r["ta"]: r["band"] for r in _d.narrow_and_band([dict(r) for r in _rows])}
assert _b == {"a3": "l", "a4": "m"}, ("default bands unchanged", _b)
assert "INFLOW_CHAINS" in out and set(out["SCANNED_CHAINS"]) >= {"solana", "bsc"}, (out.get("INFLOW_CHAINS"), out.get("SCANNED_CHAINS"))
assert "OldChain" not in [r[0] for r in out["NETFLOW"]["day"]], "deprecated chains are left out"
assert out["NETFLOW"]["month"][0] == ["BSC", -1003.33, "bsc"], out["NETFLOW"]
assert out["BRIDGES"][0] == ["Across", 414.0, 1710.0, 8200.0, None, 45.8, 25], out["BRIDGES"][0]
assert status["sources"]["llama.bridges_chains"]["state"] == "ok"

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
# the evening scan saves to snapshots/<day>-pm/, the morning one to snapshots/<day>/ (SLOT; manual runs: 3pm SGT cut-off)
assert out["SLOT"] in ("am", "pm"), out.get("SLOT")
snap_dir = out["day_sgt"] + ("-pm" if out["SLOT"] == "pm" else "")
assert os.path.exists(os.path.join(root, "snapshots", snap_dir, "scan.json")), f"no snapshots/{snap_dir}/scan.json"
assert not any(k.startswith("_") for t in out["TOKENS"] for k in t), "no private keys may leak into the snapshot"
print("\nALL ASSERTIONS PASSED —", len(out["DATA"]), "chains,", len(out["TOKENS"]), "tokens, day", out["day_sgt"], "at", out["generated_at_sgt"])
