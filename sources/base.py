"""
sources/base.py — common types and helpers shared by every source.

Every source module must export:
    SOURCE_ID  : str  — short slug ("craigslist", "ebay", "bat", ...)
    SOURCE_NAME: str  — human label
    poll(cfg)  : list[NormalizedListing]
    enabled(cfg): bool — whether this source should run with the given config
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


# ---------- Normalized listing -------------------------------------------

@dataclass
class NormalizedListing:
    """Every source returns these. The sniper dedups by (source, source_id)."""
    source: str                                 # SOURCE_ID
    source_id: str                              # unique within source
    url: str
    title: str
    price: Optional[int] = None                 # ALWAYS the displayed price (current bid for auctions, fixed for BIN)
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    odometer: Optional[int] = None
    location: Optional[str] = None              # human-readable
    state: Optional[str] = None                 # 2-letter
    # Structured location from the provider only (never parsed from text)
    zip: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    description: str = ""
    posted_at: Optional[str] = None             # ISO 8601
    is_dealer: bool = False
    is_salvage: bool = False

    # Auction-specific
    is_auction: bool = False                    # True if this is a bidding listing
    auction_end_at: Optional[str] = None        # ISO 8601 — when bidding closes
    bid_count: Optional[int] = None
    buy_now_price: Optional[int] = None         # if auction has a BIN option

    # Cash-only / payment hints
    is_cash_only: bool = False                  # explicitly cash-only (Craigslist by-owner = True)
    accepts_financing: bool = False             # dealer/MarketCheck = True

    # Photos lifted from the source listing itself (shows actual color + condition)
    image_urls: list[str] = field(default_factory=list)

    extras: dict[str, Any] = field(default_factory=dict)

    def composite_id(self) -> str:
        return f"{self.source}:{self.source_id}"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- Shared parsers (kept identical across sources) ---------------

YEAR_RE = re.compile(r"\b(19[6-9]\d|20[0-3]\d)\b")
# Two-digit year only at the very start of a title ("05 Silverado", "'99 Civic")
LEADING_2DIGIT_YEAR_RE = re.compile(r"^\s*'?(\d{2})\s+[A-Za-z]")
PRICE_RE = re.compile(r"\$\s?([\d,]{3,7})")
# "$27k" / "$27.5k" / "$ 9K"
PRICE_K_RE = re.compile(r"\$\s?(\d{1,3}(?:\.\d{1,2})?)\s?k\b", re.IGNORECASE)
# A price followed by these words is a payment, not the asking price
PAYMENT_SUFFIX_RE = re.compile(
    r"^\s*(?:dollars\s*)?(?:down\b|dwn\b|/\s*mo|/\s*month|/\s*wk|/\s*week|per\s*(?:month|week)|"
    r"a\s*(?:month|week)|monthly|weekly|bi-?weekly)", re.IGNORECASE)
MILES_RE = re.compile(r"(\d{1,3}[,]?\d{3})\s*(?:mi|miles|mileage)\b", re.IGNORECASE)
# "180k miles" / "85k" — but never "$27k" (that's a price)
SHORT_K_RE = re.compile(r"(?<![\$\d.,])(\d{2,3})\s?k\b\s*(?:mi|miles)?", re.IGNORECASE)

MAKES = {
    "acura", "alfa romeo", "audi", "bmw", "buick", "cadillac", "chevrolet",
    "chevy", "chrysler", "dodge", "fiat", "ford", "genesis", "gmc", "honda",
    "hyundai", "infiniti", "jaguar", "jeep", "kia", "land rover", "lexus",
    "lincoln", "mazda", "mercedes", "mercedes-benz", "mini", "mitsubishi",
    "nissan", "porsche", "ram", "saab", "saturn", "scion", "subaru", "suzuki",
    "tesla", "toyota", "volkswagen", "vw", "volvo", "polestar", "rivian", "lucid",
    "pontiac", "hummer", "isuzu", "mercury", "oldsmobile", "plymouth",
}

# Canonical display name for each make. Aliases collapse to one spelling so
# "Chevy Silverado" and "Chevrolet Silverado" share comps and peer estimates.
MAKE_CANON = {
    "chevy": "Chevrolet", "vw": "Volkswagen", "mercedes": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz", "gmc": "GMC", "bmw": "BMW",
    "alfa romeo": "Alfa Romeo", "land rover": "Land Rover",
}

# Whole-word match only — plain substring matching read "Duramax" as Ram and
# "minivan" as Mini.
_MAKE_RES = [(m, re.compile(rf"(?<![a-z0-9]){re.escape(m)}(?![a-z0-9])"))
             for m in sorted(MAKES, key=len, reverse=True)]

# Titles very often skip the make ("2012 F-150 XLT", "05 Silverado 2500HD").
# Model token (lowercase, hyphens stripped) → canonical make.
MODEL_TO_MAKE = {
    **{m: "Ford" for m in ("f150", "f250", "f350", "f450", "ranger", "explorer",
                            "expedition", "escape", "mustang", "fusion", "focus",
                            "edge", "bronco", "taurus", "flex", "econoline",
                            "transit", "excursion", "maverick")},
    **{m: "Chevrolet" for m in ("silverado", "tahoe", "suburban", "camaro",
                                 "corvette", "malibu", "impala", "equinox",
                                 "colorado", "traverse", "avalanche", "cruze",
                                 "blazer", "trailblazer", "s10")},
    **{m: "GMC" for m in ("sierra", "yukon", "acadia", "canyon", "envoy",
                           "terrain")},
    **{m: "Toyota" for m in ("tacoma", "tundra", "camry", "corolla", "4runner",
                              "rav4", "highlander", "prius", "sienna",
                              "sequoia", "avalon", "fj")},
    **{m: "Honda" for m in ("civic", "accord", "crv", "pilot", "odyssey",
                             "ridgeline", "hrv", "element")},
    **{m: "Nissan" for m in ("altima", "maxima", "frontier", "titan", "rogue",
                              "sentra", "pathfinder", "armada", "xterra",
                              "murano", "versa")},
    **{m: "Jeep" for m in ("wrangler", "cherokee", "gladiator", "liberty",
                            "compass", "renegade", "patriot")},
    **{m: "Dodge" for m in ("charger", "challenger", "durango", "dakota",
                             "caravan", "journey")},
    **{m: "Hyundai" for m in ("elantra", "sonata", "tucson", "santa")},
    **{m: "Subaru" for m in ("outback", "forester", "impreza", "wrx",
                              "legacy", "crosstrek")},
    **{m: "Mazda" for m in ("miata", "mx5")},
    **{m: "Volkswagen" for m in ("jetta", "passat", "golf", "gti", "beetle",
                                  "tiguan", "atlas")},
}

# Display spelling for models that title-case badly
MODEL_CANON = {"f150": "F-150", "f250": "F-250", "f350": "F-350", "f450": "F-450",
               "crv": "CR-V", "hrv": "HR-V", "rav4": "RAV4", "mx5": "MX-5",
               "s10": "S-10", "4runner": "4Runner", "wrx": "WRX", "gti": "GTI",
               "fj": "FJ Cruiser"}

# Tokens that only make sense joined to the next one ("Grand Cherokee")
_JOIN_NEXT = {"grand", "land", "town", "model", "alfa", "range", "santa",
              "monte", "park", "e", "c", "s", "cx", "mx"}

DEALER_PATTERNS = [
    r"\bdealer\b", r"\bdealership\b", r"\bfinanc(e|ing)\b",
    r"\bbuy\s*here\s*pay\s*here\b", r"\bbhph\b", r"\bwarranty\s*included\b",
    r"\bcall\s*for\s*price\b", r"\b\$0\s*down\b", r"\bnationwide\s*shipping\b",
    r"\$\s?[\d,]+\s*down\b", r"\bdown\s*payment\b", r"\bper\s*month\b",
]
DEALER_RE = re.compile("|".join(DEALER_PATTERNS), re.IGNORECASE)

CASH_ONLY_PATTERNS = [r"\bcash\s*only\b", r"\bno\s*finance", r"\bno\s*trade", r"\bcash\s*deal"]
CASH_ONLY_RE = re.compile("|".join(CASH_ONLY_PATTERNS), re.IGNORECASE)

# Branded / non-clean titles and parts cars. These must never score against
# clean-title comps — a "rebuilt $6,500" looks like a 40% steal otherwise.
SALVAGE_RE = re.compile(
    r"\b(salvage[d]?|rebuilt|rebuildable|reconstructed|flood(?:ed)?(?:\s*damage)?|"
    r"hail\s*damage|branded\s*title|junk\s*title|lemon\s*(?:law|title)|"
    r"bill\s*of\s*sale\s*only|no\s*title|without\s*(?:a\s*)?title|"
    r"title\s*(?:issue|problem)s?|parts\s*only|for\s*parts|parting\s*out|"
    r"part\s*out|parts\s*car)\b", re.IGNORECASE)
# Runs/drives problems and disclosed damage — still a listing, but not a
# resale-ready "deal"
NEEDS_WORK_RE = re.compile(
    r"\b(not\s*running|non[-\s]?runner|doesn'?t\s*run|does\s*not\s*run|"
    r"won'?t\s*(?:start|crank|run)|needs\s*(?:a\s*)?(?:motor|engine|transmission|trans)|"
    r"blown\s*(?:motor|engine|head\s*gasket)|bad\s*(?:motor|engine|transmission|trans)|"
    r"seized|mechanic'?s?\s*special|project\s*car|as[-\s]?is\s*project|tow\s*(?:it\s*)?away|"
    r"needs\s*(?:some\s*)?(?:work|repairs?)|needs?\s*(?:a\s*)?head\s*gaskets?|"
    r"head\s*gasket\s*(?:leak|issue|problem)s?|"
    r"trans(?:mission)?\s*(?:is\s*)?slip(?:s|ping)?|slipping\s*trans(?:mission)?|"
    r"over[\s-]?heat(?:s|ing|ed)|(?:rod|engine|motor)\s*knock(?:s|ing)?|"
    r"burns?\s*oil|wreck(?:ed)?|"
    r"(?:front|rear|side|body|frame|collision|accident|structural|flood)[\s-]*(?:end\s*)?damaged?|"
    r"air\s*bags?\s*(?:deployed|went\s*off)|deployed\s*air\s*bags?)\b",
    re.IGNORECASE)
_NEGATION_RE = re.compile(r"\b(no|not|never|non|isn'?t|wasn'?t|zero|doesn'?t|don'?t|"
                          r"didn'?t|hasn'?t|haven'?t)\b[\w\s,'-]{0,14}$",
                          re.IGNORECASE)


def extract_int(pat: re.Pattern, text: str) -> Optional[int]:
    if not text:
        return None
    m = pat.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except (ValueError, IndexError):
        return None


def extract_price(text: str) -> Optional[int]:
    """Asking price from free text. Handles "$14,500" and "$27k"; skips
    payment figures ("$2,500 down", "$299/mo") so a teaser number never
    becomes the price."""
    if not text:
        return None
    cands = []
    for m in PRICE_RE.finditer(text):
        if PAYMENT_SUFFIX_RE.match(text[m.end():]):
            continue
        try:
            cands.append((m.start(), int(m.group(1).replace(",", ""))))
        except ValueError:
            continue
    for m in PRICE_K_RE.finditer(text):
        if PAYMENT_SUFFIX_RE.match(text[m.end():]):
            continue
        try:
            cands.append((m.start(), int(round(float(m.group(1)) * 1000))))
        except ValueError:
            continue
    if not cands:
        return None
    return min(cands)[1]   # first price in reading order


def extract_year(text: str) -> Optional[int]:
    if not text:
        return None
    cur = datetime.now().year
    m = YEAR_RE.search(text)
    if m:
        y = int(m.group(1))
        return y if 1960 <= y <= cur + 1 else None
    m = LEADING_2DIGIT_YEAR_RE.match(text)
    if m:
        yy = int(m.group(1))
        y = 2000 + yy if yy <= (cur + 1) % 100 else 1900 + yy
        if 1960 <= y <= cur + 1:
            return y
    return None


# Mileage / price / plain numbers that must never be glued onto a model
# ("9-3 105k" is a Saab 9-3 with 105k miles, not a "9-3 105K").
_NOT_MODEL_TOKEN = re.compile(r"^(\$.*|\d+k|\d{1,3}(,\d{3})+|\d{4,}|\d+mi(les)?)$",
                              re.IGNORECASE)


def _model_key(tok: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tok.lower())


def _canon_model(tokens: list[str]) -> Optional[str]:
    if not tokens:
        return None
    first = tokens[0]
    key = _model_key(first)
    if key in MODEL_CANON:
        return MODEL_CANON[key]
    if len(tokens) > 1 and _model_key(first + tokens[1]) in MODEL_CANON:
        return MODEL_CANON[_model_key(first + tokens[1])]      # "F 150", "CR V"
    if (len(tokens) > 1 and (len(key) <= 2 or key in _JOIN_NEXT)
            and not _NOT_MODEL_TOKEN.match(tokens[1])):
        return f"{first} {tokens[1]}".title()
    return first.title()


def extract_make_model(title: str) -> tuple[Optional[str], Optional[str]]:
    if not title:
        return None, None
    t = title.lower()
    for make, rx in _MAKE_RES:
        m = rx.search(t)
        if not m:
            continue
        after = t[m.end():].strip()
        after = re.split(r"\s[-~|/]\s|\s*\$|\s*\(|,", after, maxsplit=1)[0].strip()
        tokens = after.split()
        canon = MAKE_CANON.get(make, make.title())
        return canon, _canon_model(tokens)
    # No make in the title — infer it from a well-known model name
    tokens = re.split(r"[\s,/]+", t)
    for i, tok in enumerate(tokens):
        key = _model_key(tok)
        if key in MODEL_TO_MAKE:
            return MODEL_TO_MAKE[key], _canon_model(tokens[i:i + 2] if key in _JOIN_NEXT
                                                     else tokens[i:i + 1])
    return None, None


def extract_miles(text: str) -> Optional[int]:
    if not text:
        return None
    m = SHORT_K_RE.search(text)
    if m:
        try:
            return int(m.group(1)) * 1000
        except ValueError:
            pass
    m = MILES_RE.search(text)
    if m:
        try:
            n = int(m.group(1).replace(",", ""))
            if 1000 <= n <= 500_000:
                return n
        except ValueError:
            pass
    return None


def looks_like_dealer(title: str, description: str = "") -> bool:
    blob = f"{title or ''} {description or ''}"
    return bool(DEALER_RE.search(blob))


def looks_cash_only(title: str, description: str = "") -> bool:
    blob = f"{title or ''} {description or ''}"
    return bool(CASH_ONLY_RE.search(blob))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unnegated(rx: re.Pattern, blob: str) -> Optional[str]:
    """First match of rx that isn't preceded by a negation ("no rebuilt
    title", "never been salvaged", "not a parts car")."""
    for m in rx.finditer(blob):
        if not _NEGATION_RE.search(blob[max(0, m.start() - 30):m.start()]):
            return m.group(0)
    return None


def looks_salvage(title: str, description: str = "") -> bool:
    return _unnegated(SALVAGE_RE, f"{title or ''} {description or ''}") is not None


def looks_needs_work(title: str, description: str = "") -> bool:
    return _unnegated(NEEDS_WORK_RE, f"{title or ''} {description or ''}") is not None


def to_iso(ts) -> Optional[str]:
    """Normalize a feed timestamp (RFC 822 from RSS, ISO 8601, or a
    feedparser struct_time) to ISO 8601 UTC. Returns None if unparseable."""
    if not ts:
        return None
    try:
        if hasattr(ts, "tm_year"):
            import calendar
            return datetime.fromtimestamp(calendar.timegm(ts), timezone.utc).isoformat()
        s = str(ts).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None
