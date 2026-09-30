"""Tag tokens with a narrative label, unattended inside GitHub Actions (no model in the loop).

    python scan/auto_nar.py out/nar_todo.json out/nar.json [--offline]

Reads the builder's nar_todo.json (tokens that are new today, plus tokens still tagged "Unclassified")
and writes {"<pair address>": "<label>"}. Labels match the ones the page already uses.

Two passes per token:
  1. its ticker, name and website address (the original keyword rules, widened);
  2. only if pass 1 finds nothing: what the token says about itself - its GeckoTerminal profile
     (description + category tags) and the title / description of its own website.
When neither pass finds a clear fit the token stays "Unclassified" rather than being guessed, and it
is looked up again on the next day's run (profiles and sites often appear a day or two after launch).

Network use is small and best-effort: at most MAX_LOOKUPS tokens, GeckoTerminal paced well under its
public limit, a hard time budget, and any failure just means pass 2 is skipped for that token.
"""
from __future__ import annotations

import html as htmlmod
import json
import os
import re
import sys
import time

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
    "STON", "STONFI", "STON.FI", "DEDUST", "EVAA", "TONCO", "NAVX", "SCA", "SCALLOP", "BLUE", "HYPERSWAP",
}

# chain mascots and chain in-jokes: (chain, ticker)
CHAIN_LORE = {("hyperevm", "PURR"), ("hyperliquid", "PURR"), ("ton", "UTYA"), ("ton", "DUCK"), ("sui", "HIPPO"),
              ("sui", "BLUB"), ("sui", "LOFI"), ("base", "TOSHI"), ("base", "BRETT"), ("solana", "BONK"),
              ("solana", "WIF"), ("bsc", "BROCCOLI"), ("robinhood", "HOODY")}

KOL = (r"trump|melania|elon|musk|\bcz\b|changpeng|vitalik|saylor|kanye|mrbeast|mr ?beast|jensen|zuck|zuckerberg|bezos|"
       r"altman|\bsbf\b|snoop|\bdrake\b|taylor ?swift|andrew ?tate|pewdiepie|\bansem\b|\btoly\b|\bmilei\b|\bbarron\b|"
       r"\bbiden\b|\bobama\b|\bputin\b|\bxi ?jinping\b|\bmodi\b|\bpope\b|\bbuffett\b|\bmark ?cuban\b|\bkim ?kardashian\b")
BRANDS = (r"mcdonald|\bmcd\b|starbucks|coca ?cola|\bpepsi\b|walmart|\bnike\b|adidas|gucci|chanel|rolex|ferrari|"
          r"lamborghini|\blego\b|pokemon|pok[eé]mon|nintendo|playstation|\bxbox\b|red ?bull|burger ?king|\bkfc\b|"
          r"instagram|\binsta\b|tiktok|youtube|facebook|whatsapp|spotify|airbnb|paypal|netflix|disney|costco|"
          r"ikea|labubu|hello ?kitty|tamagotchi")

RULES: list[tuple[str, re.Pattern]] = [
    ("KOL / personality", re.compile(KOL, re.I)),
    ("Leveraged tokens", re.compile(r"\b\d+(\.\d+)?x\s*(long|short)\b|leveraged token", re.I)),
    ("Tokenised equities", re.compile(
        r"xstock|robinhood token|ondo tokeni[sz]ed|prestocks?|common stock|tokeni[sz]ed (stock|equit|share)|"
        r"\betf\b|\bcorporation\b|\bcorp\b|\binc\b", re.I)),
    ("Stablecoins", re.compile(r"stablecoin|pegged to (the )?(us )?dollar|1:1 (backed|with) (usd|the dollar)", re.I)),
    ("Wrapped assets", re.compile(
        r"\bwrapped\b|\bstak(ed|ers?|ing)\b|liquid staking|\blst\b|^usd|usd$|usd[t₮]|usdc|\bdollar\b|bridged", re.I)),
    ("AI", re.compile(
        r"\b(ai|a\.i\.|gpt|agents?|agentic|openai|anthropic|claude|grok|llm|neural|deepseek|gemini|agi|"
        r"artificial intelligence|machine learning|e/acc|accelerationism)\b", re.I)),
    ("Launchpad / infra", re.compile(r"launchpad|\blauncher\b|token launch platform", re.I)),
    ("Gold / BTC proxies", re.compile(r"\b(gold|xau|btc|bitcoin|zcash|zec|satoshi)\b", re.I)),
    ("RWA", re.compile(r"\brwa\b|real[ -]?world asset|real[ -]?estate|\bestate\b|treasur|\bdeed\b|polymarket|"
                       r"\bbond\b|prediction market", re.I)),
    ("Brand-name memes", re.compile(BRANDS, re.I)),
    ("Dog & animal memes", re.compile(
        r"\b(inu|dog|doge|dogs|shib|shiba|cat|cats|kitty|kitten|pup|puppy|frog|froge|pepe|bull|bear|monkey|ape|fish|"
        r"bird|pigeon|hamster|rabbit|bunny|tiger|leopard|lion|horse|zebra|fly|moth|chameleon|wolf|fox|panda|duck|"
        r"penguin|goat|goose|cow|pig|rat|mouse|hippo|seal|otter|capybara|squirrel|owl|shark|whale|crab|bee|woof|meow|purr|"
        r"dragon|snake|turtle|dolphin|gorilla|chimp|koala|sloth|parrot|chicken|rooster|lobster|puppies|kitties|"
        r"dog-themed|cat-themed|animal)\b"
        r"|shib|doge|pepe|leopard|kitty|puppy"
        r"|猫|狗|蛙|蝇|柴|马|牛|兔|龙|うさぎ|ねこ|いぬ", re.I)),
    ("Classic memes", re.compile(
        r"\b(wojak|chad|fomo|stonks?|wen|when|gme|moon|based|degen|meme|memes|memecoin|meme coin|pump|rekt|cope|"
        r"hodl|lambo|shitcoin|bonk|npcs?|sigma|brainrot|skibidi|67|sixseven|halloween|holdoween|gm|gn|ser|fren)\b",
        re.I)),
]

# GeckoTerminal category tags -> label (matched as substrings of the lower-cased category name / id)
CATEGORY_MAP: list[tuple[str, str]] = [
    ("political", "KOL / personality"), ("celebrity", "KOL / personality"), ("kol", "KOL / personality"),
    ("tokenized stock", "Tokenised equities"), ("tokenized-stock", "Tokenised equities"), ("xstock", "Tokenised equities"),
    ("stablecoin", "Stablecoins"),
    ("liquid staking", "Wrapped assets"), ("liquid-staking", "Wrapped assets"), ("wrapped", "Wrapped assets"),
    ("bridged", "Wrapped assets"),
    ("ai agent", "AI"), ("ai-agent", "AI"), ("artificial intelligence", "AI"), ("artificial-intelligence", "AI"),
    ("ai meme", "AI"), ("ai-meme", "AI"),
    ("launchpad", "Launchpad / infra"),
    ("real world asset", "RWA"), ("real-world-asset", "RWA"), ("rwa", "RWA"), ("prediction market", "RWA"),
    ("dog", "Dog & animal memes"), ("cat-themed", "Dog & animal memes"), ("cat themed", "Dog & animal memes"),
    ("frog", "Dog & animal memes"), ("animal", "Dog & animal memes"),
    ("decentralized exchange", "Utilities"), ("dex", "Utilities"), ("lending", "Utilities"), ("yield", "Utilities"),
    ("derivatives", "Utilities"), ("perpetual", "Utilities"), ("oracle", "Utilities"), ("infrastructure", "Utilities"),
    ("gaming", "Utilities"), ("gamefi", "Utilities"),
    ("meme", "Classic memes"),
]

# pass 2 reads free text, where some pass-1 words are too loose ("worth a dollar", "the next bitcoin")
DESC_OVERRIDES: dict[str, re.Pattern] = {
    "Wrapped assets": re.compile(r"\bwrapped\b|liquid staking|\bstak(ed|ers)\b|\blst\b|bridged (version|token|asset)", re.I),
    "Gold / BTC proxies": re.compile(r"\b(xau|zcash|zec)\b|\bgold[- ]backed|backed by (gold|btc|bitcoin)|bitcoin (proxy|treasury)", re.I),
    "Tokenised equities": re.compile(r"xstock|tokeni[sz]ed (stock|equit|share)|common stock|tracks the (share|stock) price", re.I),
}

UTILITY_NAME_RE = re.compile(r"\b(protocol|finance|exchange|swap|dex|pools?|bank|lending|yield|vault|oracle|wallet|"
                             r"network|labs|payments?|bridge)\b", re.I)
UTILITY_DESC_RE = re.compile(r"\b(protocol|decentrali[sz]ed exchange|\bdex\b|lending|borrow|yield|vault|oracle|"
                             r"wallet|payments?|infrastructure|platform|marketplace|governance token|utility token|"
                             r"layer[- ]?[12]|sdk|api|app)\b", re.I)


def _base(sym: str) -> str:
    return re.sub(r"(c|x|b|on|x1l|x3l)$", "", sym.upper()) if len(sym) > 2 else sym.upper()


def tag_basic(t: dict) -> str:
    """Pass 1: ticker, name and website address only (no network)."""
    sym = (t.get("sym") or "").strip()
    nm = (t.get("nm") or "").strip()
    if nm.lower() in ("none", "null"):
        nm = ""
    web = (t.get("web") or "").lower()
    chain = (t.get("c") or "").lower()
    text = f"{sym} {nm}"
    if any(s in web for s in LAUNCHPAD_SITES):
        return "Launchpad / infra"
    if (chain, sym.upper()) in CHAIN_LORE:
        return "Chain-native lore"
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
    if UTILITY_NAME_RE.search(nm):
        return "Utilities"
    if sym.upper() in EQUITY_TICKERS or _base(sym) in EQUITY_TICKERS:
        return "Tokenised equities"
    return "Unclassified"


def tag_from_profile(categories: list[str], text: str) -> str:
    """Pass 2: what the token says about itself. Category tags first (they are curated), then the most
    frequent keyword family in the description / site text. A bare 'meme' mention only counts when nothing
    more specific does, and generic product words only when the text never calls itself a meme."""
    cats = " | ".join(c.lower() for c in categories if c)
    if cats:
        for key, label in CATEGORY_MAP:
            if key == "meme":
                continue
            if re.search(r"\b" + re.escape(key), cats):
                return label
    text = (text or "")[:4000]
    if text:
        hits: dict[str, int] = {}
        for label, rx in RULES:
            if label == "Classic memes":
                continue
            n = len(DESC_OVERRIDES.get(label, rx).findall(text))
            if n:
                hits[label] = n
        if hits:
            order = [lbl for lbl, _ in RULES]
            return sorted(hits.items(), key=lambda kv: (-kv[1], order.index(kv[0])))[0][0]
    if re.search(r"\bmeme", cats) or (text and RULES[-1][1].search(text)):
        return "Classic memes"
    if text and len(UTILITY_DESC_RE.findall(text)) >= 2:
        return "Utilities"
    return "Unclassified"


# ------------------------------------------------------------------ network (best-effort)
GT = "https://api.geckoterminal.com/api/v2"
GT_HDR = {"Accept": "application/json;version=20230302"}
MAX_LOOKUPS = 90            # tokens looked up per run
TIME_BUDGET_S = 300         # stop looking things up after 5 minutes; the rest wait for tomorrow
SITE_MAX_BYTES = 300_000
# how the lookups went today; written into nar.json under "_stats" so the daily report can show it
STATS = {"gt_ok": 0, "gt_failed": 0, "gt_had_text": 0, "site_tried": 0, "site_ok": 0}

_META_RE = re.compile(r'<meta[^>]+(?:name|property)\s*=\s*["\'](?:description|og:description|twitter:description|'
                      r'og:title|twitter:title)["\'][^>]*>', re.I)
_CONTENT_RE = re.compile(r'content\s*=\s*["\']([^"\']{1,600})["\']', re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.{1,300}?)</title>", re.I | re.S)


def site_text(raw: str) -> str:
    raw = raw[:SITE_MAX_BYTES]
    parts = []
    m = _TITLE_RE.search(raw)
    if m:
        parts.append(m.group(1))
    for tag in _META_RE.findall(raw):
        c = _CONTENT_RE.search(tag)
        if c:
            parts.append(c.group(1))
    txt = htmlmod.unescape(" . ".join(parts))
    return re.sub(r"\s+", " ", txt).strip()


def make_http():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from fetch.common import Http          # the repo's own paced, retrying HTTP client
    from fetch.discover import DS_TO_GT
    http = Http(timeout=12.0)
    http.pace("api.geckoterminal.com", 20)
    return http, DS_TO_GT


def gt_profile(http, net_map: dict, t: dict) -> tuple[list[str], str, list[str]]:
    """-> (categories, description, websites) from GeckoTerminal's token info. Empty on any failure."""
    net, ta = net_map.get((t.get("c") or "").lower()), t.get("ta")
    if not net or not ta:
        return [], "", []
    try:
        j = http.get(f"{GT}/networks/{net}/tokens/{ta}/info", headers=GT_HDR, retries=1)
    except Exception as e:  # noqa: BLE001 - best-effort
        STATS["gt_failed"] += 1
        print(f"  gt info {t.get('sym')}: {str(e)[:80]}")
        return [], "", []
    STATS["gt_ok"] += 1
    a = ((j or {}).get("data") or {}).get("attributes") or {}
    cats = [str(x) for x in (a.get("categories") or []) + (a.get("gt_category_ids") or []) if x]
    desc = a.get("description") or ""
    if isinstance(desc, dict):
        desc = desc.get("en") or ""
    webs = [w for w in (a.get("websites") or []) if isinstance(w, str)]
    if cats or str(desc).strip():
        STATS["gt_had_text"] += 1
    return cats, str(desc), webs


def fetch_site(http, url: str) -> str:
    if not url or not re.match(r"^https?://", url, re.I):
        return ""
    if re.search(r"(x\.com|twitter\.com|t\.me|telegram\.|discord\.|dexscreener\.|pump\.fun|geckoterminal\.)", url, re.I):
        return ""                               # not the token's own site
    STATS["site_tried"] += 1
    try:
        raw = http.get(url, retries=0, json_out=False)
    except Exception as e:  # noqa: BLE001
        print(f"  site {url[:50]}: {str(e)[:60]}")
        return ""
    txt = site_text(raw or "")
    if txt:
        STATS["site_ok"] += 1
    return txt


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    offline = "--offline" in sys.argv
    todo = json.load(open(args[0], encoding="utf-8"))
    out: dict[str, str] = {}
    how: dict[str, int] = {"name": 0, "profile": 0, "unclassified": 0, "skipped": 0}
    http = net_map = None
    if not offline:
        try:
            http, net_map = make_http()
        except Exception as e:  # noqa: BLE001
            print(f"auto_nar: lookups disabled ({e})")
    t0, looked = time.time(), 0
    for t in todo:
        key = t.get("pa") or t.get("ta")
        if not key:
            continue
        label = tag_basic(t)
        if label != "Unclassified":
            how["name"] += 1
        elif http is not None and looked < MAX_LOOKUPS and time.time() - t0 < TIME_BUDGET_S:
            looked += 1
            cats, desc, webs = gt_profile(http, net_map, t)
            label = tag_from_profile(cats, desc)
            if label == "Unclassified":
                site = t.get("web") or (webs[0] if webs else "")
                label = tag_from_profile([], f"{desc} . {fetch_site(http, site)}")
            how["profile" if label != "Unclassified" else "unclassified"] += 1
        else:
            how["unclassified" if http is not None or offline else "skipped"] += 1
        out[key] = label
    counts: dict[str, int] = {}
    for v in out.values():
        counts[v] = counts.get(v, 0) + 1
    stats = {"tagged": len(out), "by_name": how["name"], "by_profile_or_site": how["profile"],
             "still_unclassified": counts.get("Unclassified", 0), "looked_up": looked,
             "profile_lookups_ok": STATS["gt_ok"], "profile_lookups_failed": STATS["gt_failed"],
             "profiles_with_text": STATS["gt_had_text"], "sites_tried": STATS["site_tried"],
             "sites_read": STATS["site_ok"], "lookup_seconds": round(time.time() - t0),
             "lookups_enabled": http is not None}
    # the builder only looks up pair/token addresses in this file, so an extra "_stats" key is ignored there
    json.dump({**out, "_stats": stats}, open(args[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"auto-tagged {len(out)} tokens ({how['name']} by name, {how['profile']} from their profile/site, "
          f"{how['unclassified']} still unclassified, {looked} looked up in {time.time() - t0:.0f}s): "
          + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
