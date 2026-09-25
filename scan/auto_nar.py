"""Tag new tokens with a narrative label using plain keyword rules, so the daily build can run
unattended inside GitHub Actions (no model in the loop).

    python scan/auto_nar.py out/nar_todo.json out/nar.json

Reads the builder's nar_todo.json (tokens with no tag yet) and writes {"<pair address>": "<label>"}.
Tokens already on the page keep the tag they have - the builder carries those forward itself; this
only ever sees tokens that are new today. Rules are deliberately conservative: when nothing clearly
fits, the token is tagged "Unclassified" rather than guessed. Labels match the ones the page uses.
"""
from __future__ import annotations

import json
import re
import sys

LAUNCHPAD_SITES = ("odys.fun", "launchpad.meme", "spawn.farm", "knot.fun", "basedpad.fun", "open-set.xyz")

EQUITY_TICKERS = {
    "AAPL", "AMZN", "GOOG", "GOOGL", "META", "MSFT", "NVDA", "TSLA", "NFLX", "AMD", "INTC", "HOOD", "COIN",
    "MSTR", "PLTR", "SPY", "QQQ", "IWM", "DIA", "GME", "AMC", "BABA", "TSM", "SNAP", "RBLX", "UBER", "ORCL",
    "CRCL", "SPCX", "GLXY", "USO", "SNDK", "BRK.B", "BRK", "GOOGLE", "TESLA", "APPLE", "NVIDIA",
}

KNOWN_UTILITIES = {  # established protocols by ticker or name
    "RAY", "RAYDIUM", "JUP", "JUPITER", "JTO", "ORCA", "BP", "BACKPACK", "QI", "BENQI", "CETUS", "JST", "JUST",
    "SUN", "HTX", "BTT", "BITTORRENT", "RHEA", "AERO", "AERODROME", "VELO", "VELODROME", "DEEP", "WAL", "MAGMA",
    "PONS", "DRV", "DERIVE", "VVV", "UNI", "UNISWAP", "CAKE", "MET", "METEORA", "KNTQ", "UPUMP", "NEST",
}

RULES: list[tuple[str, re.Pattern]] = [
    ("KOL / personality", re.compile(r"\b(trump|elon|musk|cz|vitalik|saylor|kanye|melania)\b", re.I)),
    ("Leveraged tokens", re.compile(r"\b\d+(\.\d+)?x\s*(long|short)\b", re.I)),
    ("Tokenised equities", re.compile(
        r"xstock|robinhood token|ondo tokeni[sz]ed|prestocks?|common stock|\betf\b|\bcorporation\b|\bcorp\b|\binc\b", re.I)),
    ("Wrapped assets", re.compile(r"\bwrapped\b|\bstaked\b|^usd|usd$|usd[t₮]|usdc|\bdollar\b|stablecoin", re.I)),
    ("AI", re.compile(r"\b(ai|a\.i\.|gpt|agents?|openai|anthropic|claude|grok|llm|neural|deepseek|gemini|agi)\b", re.I)),
    ("Launchpad / infra", re.compile(r"launchpad|\blauncher\b", re.I)),
    ("Gold / BTC proxies", re.compile(r"\b(gold|xau|btc|bitcoin|zcash|zec|satoshi)\b", re.I)),
    ("RWA", re.compile(r"\brwa\b|real[ -]?estate|\bestate\b|treasur|\bdeed\b|polymarket|\bbond\b", re.I)),
    ("Dog & animal memes", re.compile(
        r"\b(inu|dog|doge|dogs|shib|shiba|cat|cats|kitty|kitten|pup|puppy|frog|froge|pepe|bull|bear|monkey|ape|fish|"
        r"bird|pigeon|hamster|rabbit|bunny|tiger|leopard|lion|horse|zebra|fly|moth|chameleon|wolf|fox|panda|duck|"
        r"penguin|goat|cow|pig|rat|mouse|hippo|seal|otter|capybara|squirrel|owl|shark|whale|crab|bee|woof|meow)\b"
        r"|shib|doge|pepe|leopard|kitty|puppy"
        r"|猫|狗|蛙|蝇|柴|马|牛|兔|うさぎ|ねこ|いぬ", re.I)),
    ("Classic memes", re.compile(
        r"\b(wojak|chad|fomo|stonks?|wen|when|gme|moon|based|degen|meme|pump|rekt|cope|hodl|lambo|shitcoin|bonk)\b", re.I)),
]


def tag(t: dict) -> str:
    sym = (t.get("sym") or "").strip()
    nm = (t.get("nm") or "").strip()
    web = (t.get("web") or "").lower()
    text = f"{sym} {nm}"
    if any(s in web for s in LAUNCHPAD_SITES):
        return "Launchpad / infra"
    base = re.sub(r"(c|x|b|on|x1l|x3l)$", "", sym.upper()) if len(sym) > 2 else sym.upper()
    if (t.get("ta") or "").lower().startswith("0xb200000000000000000000"):
        return "Tokenised equities"          # Coinbase tokenised-stock contracts share this prefix
    if re.search(r"hype$", sym, re.I):
        return "Chain-native lore"            # kHYPE, vkHYPE: Hyperliquid staking wrappers
    if re.search(r"x\d+[ls]$", sym, re.I):
        return "Leveraged tokens"
    if sym.upper() in KNOWN_UTILITIES or nm.upper() in KNOWN_UTILITIES or nm.upper().replace(" TOKEN", "") in KNOWN_UTILITIES:
        return "Utilities"
    for label, rx in RULES:
        if rx.search(text):
            return label
    if re.search(r"(inu|doge|shib|pepe|dog|cat|frog)", sym, re.I):
        return "Dog & animal memes"           # tickers glue words together: BASEDOG, CASHCAT, LMCAT
    if sym.upper().endswith("AI") and len(sym) > 3:
        return "AI"
    if re.search(r"\b(protocol|finance|exchange|swap|dex|pools?|bank|lending|yield|vault|oracle|wallet)\b", nm, re.I):
        return "Utilities"
    if sym.upper() in EQUITY_TICKERS or base in EQUITY_TICKERS:
        return "Tokenised equities"
    return "Unclassified"


def main() -> int:
    todo = json.load(open(sys.argv[1], encoding="utf-8"))
    out = {}
    for t in todo:
        key = t.get("pa") or t.get("ta")
        if key:
            out[key] = tag(t)
    json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    counts: dict[str, int] = {}
    for v in out.values():
        counts[v] = counts.get(v, 0) + 1
    print(f"auto-tagged {len(out)} new tokens: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
