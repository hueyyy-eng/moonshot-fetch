"""Offline stand-in for every host the job talks to. Shapes mirror the real APIs as documented in the scan prompt.

Usage: tests/run_offline.py patches fetch.common.Http.get / post_json with FakeNet().get / .post_json.
Set FakeNet.block = {"api.geckoterminal.com"} to simulate a Turnstile wall on a host.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from fetch.common import BlockedError

DAY = 86400
NOW = int(time.time())
MID = NOW - NOW % DAY                      # today's UTC midnight

SOL_PAIR = "BdFK8v9bVSe9pSXSdGZfL4pzjRpeSyfrkePGiM1cZEdt"
SOL_TA = "AaEhFTX4naHSWSXz9TVe5QgLbtSLT8ZqYJGZzDDcoroh"
SOL_PAIR2 = "AXTq4JHNYHSnooqjoDmtL9WW5eEgnkkMSWq76Kznidnz"
SOL_TA2 = "JE3HT7SbCgXDQWV6xp3oiiAisDzq4HyZ8wyEVBDCs45Z"
RH_PAIR = "0xf349598d52e023a1b1ed17aa0a52791ac3432158b8471dfe15571866effb4fbf"
RH_TA = "0x3f73365Ea66f44D463bbCab1dB6b553Ae5fbC7D5"
RH_PAIR_UNV = "0xa1a1598d52e023a1b1ed17aa0a52791ac3432158b8471dfe15571866effb4f00"
RH_TA_UNV = "0x1111111111111111111111111111111111111111"
BSC_PAIR = "0x9485Ff32b6b4444C21D5abe4D9a2283d127075a2"
BSC_TA = "0x2222222222222222222222222222222222222222"
BSC_LEADER_PAIR = "0x3333333333333333333333333333333333333333"
BSC_LEADER_TA = "0x4444444444444444444444444444444444444444"
BSC_USDT_PAIR = "0x5555555555555555555555555555555555555555"
BSC_USDT_TA = "0x55d398326f99059fF775485246999027B3197955"


def chart(days: int, base: float, last: float | None = None, tail_partial: bool = True):
    pts = [[MID - (days - i) * DAY, base * (1 + 0.02 * ((i * 7) % 5 - 2))] for i in range(days)]
    if last is not None:
        pts[-1][1] = last
    if tail_partial:
        pts.append([MID, base * 0.3])   # unsettled today bucket — must be dropped
    return pts


def pair(chain, pa, ta, sym, name, mc, liq, v24, v6, c6=5.0, c24=20.0, age_days=12.9, quote="SOL", price=0.00097, tw="jupitercat_st"):
    created = int((datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp() * 1000)
    return {"chainId": chain, "dexId": "raydium", "pairAddress": pa, "baseToken": {"address": ta, "symbol": sym, "name": name},
            "quoteToken": {"symbol": quote}, "priceUsd": str(price), "marketCap": mc, "fdv": mc, "liquidity": {"usd": liq},
            "volume": {"h24": v24, "h6": v6, "h1": v6 / 6}, "txns": {"h24": {"buys": 900, "sells": 700}, "h6": {"buys": 361, "sells": 263}},
            "priceChange": {"h1": -4.77, "h6": c6, "h24": c24}, "pairCreatedAt": created,
            "info": {"socials": [{"type": "twitter", "url": f"https://x.com/{tw}"}], "websites": [{"url": "https://example.org"}]},
            "boosts": {"active": 0}}


PAIRS = {
    SOL_PAIR.lower(): pair("solana", SOL_PAIR, SOL_TA, "JUPCAT", "Jupiter Cat", 970_000, 103_800, 846_276, 364_240),
    SOL_PAIR2.lower(): pair("solana", SOL_PAIR2, SOL_TA2, "PENIS", "Peniscoin", 1_334_900, 124_400, 719_688, 122_762, c6=-33.7, c24=-22.9, age_days=9.4, quote="PUMP", price=0.0013),
    RH_PAIR.lower(): pair("robinhood", RH_PAIR, RH_TA, "RightsLayer", "RightsLayer", 1_723_900, 400_600, 18_422_600, 13_139_000, c6=-8.5, c24=1637, age_days=0.8, quote="WETH", price=0.0017, tw="RightsLayerX"),
    RH_PAIR_UNV.lower(): pair("robinhood", RH_PAIR_UNV, RH_TA_UNV, "URANUS", "Uranus", 900_000, 90_000, 500_000, 100_000, age_days=5, quote="WETH", price=0.02),
    BSC_PAIR.lower(): pair("bsc", BSC_PAIR, BSC_TA, "quq", "quq", 1_410_000, 120_000, 300_000, 50_000, quote="WBNB", price=0.0018),
    BSC_LEADER_PAIR.lower(): pair("bsc", BSC_LEADER_PAIR, BSC_LEADER_TA, "CAKE", "PancakeSwap", 800_000_000, 30_000_000, 90_000_000, 20_000_000, quote="WBNB", price=2.1),
    BSC_USDT_PAIR.lower(): pair("bsc", BSC_USDT_PAIR, BSC_USDT_TA, "USDT", "Tether USD", 90_000_000_000, 500_000_000, 900_000_000, 200_000_000, quote="WBNB", price=1.0),
}


def candles(n=13, close=0.00097, low_factor=0.226, hi_factor=3.6, wrong_side=False):
    """newest-first like the API; latest close ≈ price; low7 gives run≈4.43; high gives retr≈72%."""
    out = []
    for i in range(n):
        ts = MID - (i + 1) * DAY
        c = close * (1 + 0.05 * ((i * 3) % 4 - 1.5))
        if i == 0:
            c = close
        h = c * 1.1
        l = c * 0.9
        if i == 3:
            l = close * low_factor
        if i == 9:
            h = close * hi_factor
        v = 50_000 + 10_000 * ((i * 5) % 7)
        if i == 5:
            v = 400_000
        if wrong_side:
            c, h, l = 1 / c, 1 / l, 1 / h
        out.append([ts, c, h, l, c, v])
    out.insert(0, [MID, close, close, close, close, 999])  # partial today — must be dropped
    return {"data": {"attributes": {"ohlcv_list": out}}}


class FakeNet:
    block: set[str] = set()
    fail_screener = False

    def __init__(self):
        self.log: list[str] = []

    # ------------------------------------------------------------------ dispatch
    def get(self, url, *, params=None, headers=None, retries=2, json_out=True):
        host = url.split("/")[2]
        self.log.append(url)
        if host in self.block:
            if json_out:
                raise BlockedError(f"non-JSON (challenge page?) from {host}")
            return "<html><title>Just a moment...</title>cf-challenge</html>"
        q = params or {}
        path = url.split(host, 1)[1]
        r = self._route(host, path, q, json_out)
        if r is None:
            return None
        return r

    def post_json(self, url, body, *, retries=1):
        self.log.append("POST " + url)
        if "eth-rpc" in url:
            to = (body.get("params") or [{}])[0].get("to", "").lower()
            if to == RH_TA.lower():
                return {"jsonrpc": "2.0", "id": 1, "result": "0x" + "0" * 24 + "cafecafecafecafecafecafecafecafecafecafe"}
            return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "execution reverted"}}
        return {}

    # ------------------------------------------------------------------ routes
    def _route(self, host, path, q, json_out):
        # ---------------- DefiLlama API
        if host == "api.llama.fi":
            if path.startswith("/overview/dexs/"):
                slug = path.split("/")[3].split("?")[0].lower()
                base = {"solana": 2.6e9, "bsc": 1.5e9, "robinhood": 1.5e9, "base": 8e8, "hyperliquid": 3.2e8, "ethereum": 1.5e9, "arbitrum": 2.8e8}.get(slug)
                if base is None:
                    return None
                t24 = base * (1.6 if slug == "bsc" else 1.0)
                return {"total24h": t24, "total7d": base * 7, "total30d": base * 30, "totalDataChart": chart(40, base)}
            if path.startswith("/overview/dexs"):
                return {"protocols": [{"breakdown24h": {"Solana": {"raydium": 2.6e9}, "BSC": {"pancake": 1.5e9}, "Robinhood Chain": {"uni": 1.5e9},
                                                        "Base": {"aero": 8e8}, "Hyperliquid L1": {"hl": 3.2e8}, "Ethereum": {"uni": 1.5e9},
                                                        "Arbitrum": {"uni": 2.8e8}, "Tiny": {"x": 1e5}}}]}
            if path.startswith("/v2/chains"):
                return [{"name": "Solana", "tvl": 5.78e9}, {"name": "BSC", "tvl": 5.5e9}, {"name": "Robinhood Chain", "tvl": 9.3e8},
                        {"name": "Base", "tvl": 5.5e9}, {"name": "Hyperliquid L1", "tvl": 1.3e9}, {"name": "Ethereum", "tvl": 4.9e10},
                        {"name": "Arbitrum", "tvl": 1.37e9}, {"name": "Tiny", "tvl": 1e6}]
            if path.startswith("/v2/historicalChainTvl/"):
                name = path.split("/")[3]
                base = {"Solana": 5.78e9, "BSC": 5.5e9, "Robinhood Chain": 9.3e8, "Base": 5.5e9, "Hyperliquid": 1.3e9, "Ethereum": 4.9e10, "Arbitrum": 1.37e9}.get(name)
                if base is None:
                    return None
                return [{"date": MID - (40 - i) * DAY, "tvl": base * (1 + 0.01 * (i % 3))} for i in range(40)] + [{"date": MID, "tvl": base}]
            if path.startswith("/overview/fees/"):
                return {"total24h": 1.46e7, "total7d": 1.03e8, "total30d": 4.1e8}
            if path.startswith("/overview/derivatives"):
                return {"protocols": [{"breakdown24h": {"Hyperliquid L1": {"hl": 7.18e9}}, "breakdown7d": {"Hyperliquid L1": {"hl": 4.5e10}}, "breakdown30d": {"Hyperliquid L1": {"hl": 2.4e11}}}]}
            return None
        if host == "stablecoins.llama.fi":
            name = path.split("/")[2]
            base = {"Solana": 1.57e10, "BSC": 1.69e10, "Robinhood Chain": 1.04e9, "Base": 4.96e9, "Hyperliquid": 6.8e9, "Ethereum": 1.47e11, "Arbitrum": 3.96e9}.get(name)
            if base is None:
                return None
            return [{"date": MID - (30 - i) * DAY, "totalCirculatingUSD": {"peggedUSD": base * (1 - 0.001 * i)}} for i in range(31)]
        if host == "coins.llama.fi":
            p = q.get("period", "1d")
            k = {"1d": 1.0, "7d": 2.0, "30d": 3.0}[p]
            return {"coins": {"coingecko:bitcoin": 4.21 * k, "coingecko:ethereum": 5.43 * k, "coingecko:solana": 5.46 * k}}
        if host == "defillama.com":
            if path.startswith("/perps/chains"):
                nd = {"props": {"pageProps": {"chains": [
                    {"name": "Hyperliquid L1", "total24h": 7.18e9, "total7d": 4.5e10, "total30d": 2.4e11, "openInterest": 7.14e9},
                    {"name": "Solana", "total24h": 1.72e9, "total7d": 1.16e10, "total30d": 4.7e10, "openInterest": 2.1e8},
                    {"name": "Robinhood Chain", "total24h": 4.5e8, "total7d": 3.3e9, "total30d": 1.16e10, "openInterest": 2.2e8}]}}}
            elif path.startswith("/bridges/chains"):
                nd = {"props": {"pageProps": {
                    "tableData": [{"name": "Solana", "prevDayUsdDeposits": 1.08e8, "prevDayUsdWithdrawals": 1.15e8, "prevDayNetFlow": -6.9e6,
                                   "prevWeekUsdDeposits": 7.3e8, "prevWeekUsdWithdrawals": 7.6e8, "prevWeekNetFlow": -2.8e7, "topTokenDepositedSymbol": "USDC"},
                                  {"name": "BSC", "prevDayUsdDeposits": 3.7e7, "prevDayUsdWithdrawals": 2.8e7, "prevDayNetFlow": 9.5e6,
                                   "prevWeekUsdDeposits": 2.7e8, "prevWeekUsdWithdrawals": 2.3e8, "prevWeekNetFlow": 4.6e7, "topTokenDepositedSymbol": "USDT"},
                                  {"name": "StarkNet", "prevDayUsdDeposits": 0, "prevDayUsdWithdrawals": 6e4, "prevDayNetFlow": -5e4,
                                   "prevWeekUsdDeposits": 7.5e5, "prevWeekUsdWithdrawals": 8e4, "prevWeekNetFlow": 6.7e5}],
                    "filteredBridges": [{"displayName": "Across", "lastDailyVolume": 4.14e8, "weeklyVolume": 1.71e9, "monthlyVolume": 8.2e9, "txsPrevDay": 103171, "change_1d": 45.8, "chains": list(range(25))},
                                        {"displayName": "Relay", "lastDailyVolume": 3.15e8, "weeklyVolume": 2.4e9, "monthlyVolume": 1.25e10, "txsPrevDay": 1785061, "change_1d": -18.9, "chains": list(range(46))}]}}}
            elif path.startswith("/bridges"):
                nd = {"props": {"pageProps": {"netflowsData": {
                    "day": [{"chain": "Hyperliquid", "value": -2.6355e8}, {"chain": "Arbitrum", "value": 3.0871e8}],
                    "week": [{"chain": "Robinhood Chain", "value": 1.549e7}],
                    "month": [{"chain": "BSC", "value": -1.00333e9}]}}}}
            else:
                return "<html>nope</html>"
            return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script></html>'
        # ---------------- DexScreener
        if host == "dexscreener.com":
            if self.fail_screener:
                return "<html><title>Just a moment...</title><div class=cf-challenge></div></html>"
            chain = path.split("?")[0].strip("/")
            mine = [p["pairAddress"] for p in PAIRS.values() if p["chainId"] == chain]
            if "maxMarketCap" in path:
                mine = [p["pairAddress"] for p in PAIRS.values() if p["chainId"] == chain and p["marketCap"] <= 2_000_000]
            else:
                mine = [p["pairAddress"] for p in PAIRS.values() if p["chainId"] == chain and p["marketCap"] >= 2_000_000]
            return "<html>" + "".join(f'<a href="/{chain}/{a}">x</a>' for a in mine) + "</html>"
        if host == "api.dexscreener.com":
            m = re.match(r"/latest/dex/pairs/([^/]+)/(.+)", path)
            chain, addrs = m.group(1), m.group(2).split(",")
            out = [PAIRS[a.lower()] for a in addrs if a.lower() in PAIRS and PAIRS[a.lower()]["chainId"] == chain]
            return {"pairs": out}
        # ---------------- GeckoTerminal
        if host == "api.geckoterminal.com":
            m = re.match(r"/api/v2/networks/([^/]+)/(.+)", path)
            net, rest = m.group(1), m.group(2)
            chain = {"solana": "solana", "bsc": "bsc", "robinhood": "robinhood", "base": "base", "hyperevm": "hyperevm", "hyperliquid": "hyperliquid", "eth": "ethereum", "arbitrum": "arbitrum"}[net]
            mine = [p for p in PAIRS.values() if p["chainId"] == chain]
            if rest.startswith("pools/multi/"):
                if net == "robinhood":
                    raise ConnectionError("Failed to fetch")   # mirrors the real multi-endpoint failure on robinhood
                addrs = rest.split("pools/multi/")[1].split(",")
                return {"data": [{"attributes": self._pool_attr(a)} for a in addrs if a.lower() in PAIRS]}
            if re.match(r"pools/[^/]+/ohlcv/day", rest):
                a = rest.split("/")[1]
                p = PAIRS.get(a.lower())
                if not p:
                    return None
                if p["baseToken"]["symbol"] == "URANUS":
                    return candles(close=float(p["priceUsd"]), wrong_side=True)
                return candles(n=13 if p["chainId"] != "robinhood" else 2, close=float(p["priceUsd"]))
            if rest.startswith("pools/"):
                a = rest.split("/")[1]
                return {"data": {"attributes": self._pool_attr(a)}} if a.lower() in PAIRS else None
            if rest.startswith("pools?") or rest.startswith("trending_pools") or rest.startswith("new_pools"):
                return {"data": [{"attributes": {"address": p["pairAddress"]}} for p in mine]}
            return None
        # ---------------- rug checks
        if host == "api.rugcheck.xyz":
            return {"score_normalised": 1, "risks": [{"name": "Low amount of LP Providers", "level": "warn"}], "lpLockedPct": 96.04}
        if host == "api.gopluslabs.io":
            addr = q["contract_addresses"].lower()
            if addr == RH_TA.lower() or addr == RH_TA_UNV.lower():
                return {"result": {addr: {}}}      # GoPlus lists Robinhood but returns nothing — triggers explorer tier
            return {"result": {addr: {"buy_tax": "0", "sell_tax": "0.02", "holder_count": "3319", "is_mintable": "0", "can_take_back_ownership": "0",
                                      "transfer_pausable": "0", "is_honeypot": "0",
                                      "holders": [{"percent": "0.31"}, {"percent": "0.09"}, {"percent": "0.05"}] + [{"percent": "0.01"}] * 10,
                                      "lp_holders": [{"address": "0x000000000000000000000000000000000000dead", "percent": "0.95", "is_locked": 0}]}}}
        if host == "api.honeypot.is":
            return {"honeypotResult": {"isHoneypot": False}, "simulationSuccess": True, "simulationResult": {"buyTax": 0, "sellTax": 2}}
        # ---------------- Blockscout gateway
        if host == "api.blockscout.com":
            assert q and q.get("apikey"), "explorer call without apikey"
            if re.match(r"/4663/api/v2/tokens/[^/]+/holders", path):
                ta = path.split("/")[5]
                supply = 1e27
                items = [{"address": {"hash": RH_PAIR, "name": "Uniswap V4 PoolManager", "is_contract": True}, "value": str(0.318 * supply)}]
                for i in range(20):
                    items.append({"address": {"hash": f"0x{i:040x}"[:42] if i else "0x000000000000000000000000000000000000dead", "name": "", "is_contract": i % 4 == 0}, "value": str((0.013 if i == 1 else 0.005) * supply)})
                return {"items": items}
            if path.startswith("/4663/api/v2/tokens/"):
                return {"holders_count": "4030", "total_supply": str(1e27)}
            if path.startswith("/4663/api/v2/smart-contracts/"):
                ta = path.split("/")[5].lower()
                if ta == RH_TA_UNV.lower():
                    return {"is_verified": False}
                return {"is_verified": True, "name": "Token", "abi": [{"type": "function", "name": "transfer"}, {"type": "function", "name": "mint"}, {"type": "function", "name": "setMaxTx"}]}
            if path.startswith("/4663/api/v2/stats"):
                return {"ok": True}
            return None
        return None

    def _pool_attr(self, a):
        p = PAIRS[a.lower()]
        return {"address": p["pairAddress"], "transactions": {"h24": {"buyers": 609, "sellers": 487, "buys": 1100, "sells": 990}},
                "locked_liquidity_percentage": "96.04", "pool_created_at": "2026-09-06T00:00:00Z", "base_token_price_usd": p["priceUsd"]}
