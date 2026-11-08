"""Tag tokens with a narrative label, unattended inside GitHub Actions (no model in the loop).

    python scan/auto_nar.py out/nar_todo.json out/nar.json [--offline]

Reads the builder's nar_todo.json (tokens that are new today, plus tokens still tagged "Unclassified")
and writes {"<pair address>": "<label>"}. Labels match the ones the page already uses.

A token can carry a second label when it plainly fits two (OpenAI PreStocks: Tokenised equities + AI).
The first label is the one the page groups and counts by; the second only shows as an extra tag. A token
with two labels is written as ["first", "second"] instead of a plain string.

Passes per token, each only if the one before finds nothing (owner's choice, 8 Nov 2026):
  1. its ticker, name and website address (the original keyword rules, widened);
  2. the description its creator wrote at launch (Solana: read from the token itself on-chain, then the
     launchpad's metadata file on IPFS - this covers pump.fun tokens);
  3. its DexScreener profile description;
  4. its GeckoTerminal profile (description + category tags) and the title / description of its own website.
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
    "BNC",                                    # CEA Industries (Nasdaq: BNC)
}

# well-known projects whose name gives no clue, or whose name points the wrong way: (chain, ticker) -> labels.
# These win over yesterday's label too (build_block applies them to every token, every scan).
KNOWN_PROJECTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("solana", "PUMP"): ("Launchpad / infra", "Classic memes"),   # pump.fun's own token
    ("base", "XDP"): ("Launchpad / infra",),                       # Doppler, a token-launch protocol
    ("solana", "MPLX"): ("Utilities",),                            # Metaplex, NFT / token infrastructure
    ("mantle", "MOE"): ("Utilities",),                             # Merchant Moe, Mantle's main exchange
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
        r"artificial intelligence|machine learning|e/acc|accelerationism|robotics?|super ?intelligence)\b", re.I)),
    ("Launchpad / infra", re.compile(r"launchpad|\blauncher\b|token launch platform|\b[a-z]{3,}pad\b", re.I)),
    ("Gold / BTC proxies", re.compile(r"\b(gold|xau|xaut0?|paxg|btc|bitcoin|zcash|zec|satoshi)\b", re.I)),
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
    ("gaming", "Gaming"), ("gamefi", "Gaming"),      # ahead of Utilities, so a "Gaming | Infrastructure" token reads Gaming
    ("decentralized exchange", "Utilities"), ("dex", "Utilities"), ("lending", "Utilities"), ("yield", "Utilities"),
    ("derivatives", "Utilities"), ("perpetual", "Utilities"), ("oracle", "Utilities"), ("infrastructure", "Utilities"),
    ("meme", "Classic memes"),
]

# pass 2 reads free text, where some pass-1 words are too loose ("worth a dollar", "the next bitcoin")
DESC_OVERRIDES: dict[str, re.Pattern] = {
    "Wrapped assets": re.compile(r"\bwrapped\b|liquid staking|\bstak(ed|ers)\b|\blst\b|bridged (version|token|asset)", re.I),
    "Gold / BTC proxies": re.compile(r"\b(xau|xaut0?|paxg|zcash|zec)\b|\bgold[- ]backed|backed by (gold|btc|bitcoin)|bitcoin (proxy|treasury)", re.I),
    "Tokenised equities": re.compile(r"xstock|tokeni[sz]ed (stock|equit|share)|common stock|tracks the (share|stock) price", re.I),
}

UTILITY_NAME_RE = re.compile(r"\b(protocol|finance|exchange|swap|dex|pools?|bank|lending|yield|vault|oracle|wallet|"
                             r"network|labs|payments?|bridge)\b", re.I)
UTILITY_DESC_RE = re.compile(r"\b(protocol|decentrali[sz]ed exchange|\bdex\b|lending|borrow|yield|vault|oracle|"
                             r"wallet|payments?|infrastructure|platform|marketplace|governance token|utility token|"
                             r"layer[- ]?[12]|sdk|api|app)\b", re.I)


def _base(sym: str) -> str:
    return re.sub(r"(c|x|b|on|x1l|x3l)$", "", sym.upper(), flags=re.I) if len(sym) > 2 else sym.upper()


def known_labels(t: dict) -> tuple[str, ...]:
    return KNOWN_PROJECTS.get(((t.get("c") or "").lower(), (t.get("sym") or "").strip().upper()), ())


def tag_basic(t: dict) -> str:
    """Pass 1: ticker, name and website address only (no network)."""
    return tags_basic(t)[0]


def second_label(t: dict, first: str) -> str | None:
    """A second name-based label that differs from `first`, or None. No network."""
    return next((lbl for lbl in tags_basic(t) if lbl not in (first, "Unclassified")), None)


def tags_basic(t: dict) -> list[str]:
    """Every pass-1 label that fits, best first; ["Unclassified"] when none does."""
    known = known_labels(t)
    found = list(known)
    for lbl in _tags_basic(t):
        if lbl not in found:
            found.append(lbl)
    return found or ["Unclassified"]


def _tags_basic(t: dict):
    sym = (t.get("sym") or "").strip()
    nm = (t.get("nm") or "").strip()
    if nm.lower() in ("none", "null"):
        nm = ""
    web = (t.get("web") or "").lower()
    chain = (t.get("c") or "").lower()
    text = f"{sym} {nm}"
    if any(s in web for s in LAUNCHPAD_SITES):
        yield "Launchpad / infra"
    if (chain, sym.upper()) in CHAIN_LORE:
        yield "Chain-native lore"
    if (t.get("ta") or "").lower().startswith("0xb200000000000000000000"):
        yield "Tokenised equities"          # Coinbase tokenised-stock contracts share this prefix
    if re.search(r"hype$", sym, re.I):
        yield "Chain-native lore"            # kHYPE, vkHYPE: Hyperliquid staking wrappers
    if re.search(r"x\d+[ls]$", sym, re.I):
        yield "Leveraged tokens"
    if sym.upper() in KNOWN_UTILITIES or nm.upper() in KNOWN_UTILITIES or nm.upper().replace(" TOKEN", "") in KNOWN_UTILITIES:
        yield "Utilities"
    for label, rx in RULES:
        if rx.search(text):
            yield label
    if re.search(r"(inu|doge|shib|pepe|dog|cat|frog|pup|paw)", sym, re.I):
        yield "Dog & animal memes"           # tickers glue words together: BASEDOG, CASHCAT, LMCAT
    if sym.upper().endswith("AI") and len(sym) > 3:
        yield "AI"
    if UTILITY_NAME_RE.search(nm):
        yield "Utilities"
    if sym.upper() in EQUITY_TICKERS or _base(sym) in EQUITY_TICKERS:
        yield "Tokenised equities"


def tag_from_profile(categories: list[str], text: str) -> str:
    return tags_from_profile(categories, text)[0]


def tags_from_profile(categories: list[str], text: str) -> list[str]:
    """Pass 2: what the token says about itself. Category tags first (they are curated; up to two labels,
    e.g. "Gaming" + "Classic memes"), then the most frequent keyword family in the description / site text.
    A bare 'meme' mention only counts when nothing more specific does, and generic product words only when
    the text never calls itself a meme."""
    cats = " | ".join(c.lower() for c in categories if c)
    if cats:
        found: list[str] = []
        for key, label in CATEGORY_MAP:
            if key != "meme" and label not in found and re.search(r"\b" + re.escape(key), cats):
                found.append(label)
        if found:
            if re.search(r"\bmeme", cats) and "Classic memes" not in found and not any("memes" in f for f in found):
                found.append("Classic memes")
            return found[:2]
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
            return [sorted(hits.items(), key=lambda kv: (-kv[1], order.index(kv[0])))[0][0]]
    if re.search(r"\bmeme", cats) or (text and RULES[-1][1].search(text)):
        return ["Classic memes"]
    if text and len(UTILITY_DESC_RE.findall(text)) >= 2:
        return ["Utilities"]
    return ["Unclassified"]


# ------------------------------------------------------------------ network (best-effort)
GT = "https://api.geckoterminal.com/api/v2"
GT_HDR = {"Accept": "application/json;version=20230302"}
MAX_LOOKUPS = 90            # tokens looked up per run
TIME_BUDGET_S = 300         # stop looking things up after 5 minutes; the rest wait for tomorrow
SITE_MAX_BYTES = 300_000
# how the lookups went today; written into nar.json under "_stats" so the daily report can show it
STATS = {"gt_ok": 0, "gt_failed": 0, "gt_had_text": 0, "site_tried": 0, "site_ok": 0,
         "launch_tried": 0, "launch_ok": 0, "ds_tried": 0, "ds_ok": 0}
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
# IPFS gateways that answer GitHub's runners (checked 8 Nov 2026; ipfs.io and dweb.link block them)
IPFS_GATEWAYS = ("https://ipfs.filebase.io/ipfs/", "https://gw.ipfs-lens.dev/ipfs/")
DS_DETAILS = "https://io.dexscreener.com/dex/pair-details/v4"

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
    http.pace("api.mainnet-beta.solana.com", 60)
    http.pace("io.dexscreener.com", 40)
    for g in IPFS_GATEWAYS:
        http.pace(g.split("/")[2], 60)
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


def metadata_uri(mint_info: dict) -> str:
    """Solana getAccountInfo (jsonParsed) -> the metadata file address a Token-2022 mint stores about itself."""
    info = ((((mint_info or {}).get("result") or {}).get("value") or {}).get("data") or {})
    info = (info.get("parsed") or {}).get("info") or {} if isinstance(info, dict) else {}
    for ext in info.get("extensions") or []:
        if ext.get("extension") == "tokenMetadata":
            return str((ext.get("state") or {}).get("uri") or "")
    return ""


def ipfs_urls(uri: str) -> list[str]:
    """an ipfs link in any form -> the same file on the gateways that answer GitHub; other links kept as they are"""
    m = re.search(r"/ipfs/([A-Za-z0-9]{40,}[^\s\"']*)", uri or "") or re.match(r"ipfs://([A-Za-z0-9]{40,}\S*)", uri or "")
    if m:
        return [g + m.group(1) for g in IPFS_GATEWAYS]
    return [uri] if re.match(r"^https://", uri or "") else []


def launch_description(http, t: dict) -> str:
    """Pass 2: what the creator wrote when launching the token. Solana only for now: pump.fun and most new Solana
    tokens keep a link to a small metadata file (name, symbol, description) inside the token itself. EVM launchpads
    (four.meme, Clanker, ...) block GitHub's runners or need a paid key, so those go straight to pass 3."""
    if (t.get("c") or "").lower() != "solana" or not t.get("ta"):
        return ""
    STATS["launch_tried"] += 1
    try:
        # plain client name: the public RPC refuses the browser-like identity the scanner uses elsewhere (403)
        import requests
        time.sleep(0.5)
        r = requests.post(SOLANA_RPC, json={"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
                                            "params": [t["ta"], {"encoding": "jsonParsed"}]},
                          headers={"User-Agent": "moonshot-scanner/1.0"}, timeout=12)
        r.raise_for_status()
        uri = metadata_uri(r.json())
    except Exception as e:  # noqa: BLE001 - best-effort
        print(f"  solana rpc {t.get('sym')}: {str(e)[:60]}")
        return ""
    for url in ipfs_urls(uri):
        try:
            j = http.get(url, retries=0)
        except Exception:  # noqa: BLE001 - try the next gateway
            continue
        desc = str((j or {}).get("description") or "").strip() if isinstance(j, dict) else ""
        if desc:
            STATS["launch_ok"] += 1
            print(f"  launch text {t.get('sym')}: {desc[:110]!r}")
        return desc
    return ""


def ds_description(details: dict) -> str:
    """DexScreener pair-details -> the token's profile description (empty when it has no profile)"""
    cms = (details or {}).get("cms") or {}
    return str(cms.get("description") or "").strip() if isinstance(cms, dict) else ""


def dexscreener_description(http, t: dict) -> str:
    """Pass 3: the description on the token's DexScreener profile, if its team set one up."""
    if not t.get("c") or not t.get("ta"):
        return ""
    STATS["ds_tried"] += 1
    try:
        desc = ds_description(http.get(f"{DS_DETAILS}/{t['c']}/{t['ta']}", retries=0))
    except Exception as e:  # noqa: BLE001 - best-effort
        print(f"  dexscreener profile {t.get('sym')}: {str(e)[:60]}")
        return ""
    if desc:
        STATS["ds_ok"] += 1
        print(f"  dexscreener text {t.get('sym')}: {desc[:110]!r}")
    return desc


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
    out: dict[str, str | list[str]] = {}
    how: dict[str, int] = {"name": 0, "launch": 0, "dexscreener": 0, "profile": 0, "unclassified": 0, "skipped": 0}
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
        labels = tags_basic(t)
        if labels[0] != "Unclassified":
            how["name"] += 1
        elif http is not None and looked < MAX_LOOKUPS and time.time() - t0 < TIME_BUDGET_S:
            looked += 1
            src = "launch"
            labels = tags_from_profile([], launch_description(http, t))                 # 2. creator's launch text
            if labels[0] == "Unclassified":
                src = "dexscreener"
                labels = tags_from_profile([], dexscreener_description(http, t))         # 3. DexScreener profile
            if labels[0] == "Unclassified":
                src = "profile"
                cats, desc, webs = gt_profile(http, net_map, t)                          # 4. GeckoTerminal + site
                labels = tags_from_profile(cats, desc)
                if labels[0] == "Unclassified":
                    site = t.get("web") or (webs[0] if webs else "")
                    labels = tags_from_profile([], f"{desc} . {fetch_site(http, site)}")
            how[src if labels[0] != "Unclassified" else "unclassified"] += 1
        else:
            how["unclassified" if http is not None or offline else "skipped"] += 1
        out[key] = labels[:2] if len(labels) > 1 else labels[0]
    counts: dict[str, int] = {}
    for v in out.values():
        v = v[0] if isinstance(v, list) else v
        counts[v] = counts.get(v, 0) + 1
    stats = {"tagged": len(out), "by_name": how["name"],
             "by_profile_or_site": how["launch"] + how["dexscreener"] + how["profile"],
             "by_launch_text": how["launch"], "by_dexscreener": how["dexscreener"], "by_gt_or_site": how["profile"],
             "launch_tried": STATS["launch_tried"], "launch_read": STATS["launch_ok"],
             "dexscreener_tried": STATS["ds_tried"], "dexscreener_read": STATS["ds_ok"],
             "still_unclassified": counts.get("Unclassified", 0), "looked_up": looked,
             "profile_lookups_ok": STATS["gt_ok"], "profile_lookups_failed": STATS["gt_failed"],
             "profiles_with_text": STATS["gt_had_text"], "sites_tried": STATS["site_tried"],
             "sites_read": STATS["site_ok"], "lookup_seconds": round(time.time() - t0),
             "lookups_enabled": http is not None}
    # the builder only looks up pair/token addresses in this file, so an extra "_stats" key is ignored there
    json.dump({**out, "_stats": stats}, open(args[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"auto-tagged {len(out)} tokens ({how['name']} by name, {how['launch']} from launch text, "
          f"{how['dexscreener']} from DexScreener, {how['profile']} from GeckoTerminal/site, "
          f"{how['unclassified']} still unclassified, {looked} looked up in {time.time() - t0:.0f}s): "
          + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
