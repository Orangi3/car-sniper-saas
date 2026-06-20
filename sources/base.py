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
PRICE_RE = re.compile(r"\$\s?([\d,]{3,7})")
MILES_RE = re.compile(r"(\d{1,3}[,]?\d{3})\s*(?:mi|miles|mileage)\b", re.IGNORECASE)
SHORT_K_RE = re.compile(r"\b(\d{2,3})k\b\s*(?:mi|miles)?", re.IGNORECASE)

MAKES = {
    "acura", "alfa romeo", "audi", "bmw", "buick", "cadillac", "chevrolet",
    "chevy", "chrysler", "dodge", "fiat", "ford", "genesis", "gmc", "honda",
    "hyundai", "infiniti", "jaguar", "jeep", "kia", "land rover", "lexus",
    "lincoln", "mazda", "mercedes", "mercedes-benz", "mini", "mitsubishi",
    "nissan", "porsche", "ram", "saab", "saturn", "scion", "subaru", "suzuki",
    "tesla", "toyota", "volkswagen", "vw", "volvo", "polestar", "rivian", "lucid",
}

DEALER_PATTERNS = [
    r"\bdealer\b", r"\bdealership\b", r"\bfinanc(e|ing)\b",
    r"\bbuy\s*here\s*pay\s*here\b", r"\bbhph\b", r"\bwarranty\s*included\b",
    r"\bcall\s*for\s*price\b", r"\b\$0\s*down\b", r"\bnationwide\s*shipping\b",
]
DEALER_RE = re.compile("|".join(DEALER_PATTERNS), re.IGNORECASE)

CASH_ONLY_PATTERNS = [r"\bcash\s*only\b", r"\bno\s*finance", r"\bno\s*trade", r"\bcash\s*deal"]
CASH_ONLY_RE = re.compile("|".join(CASH_ONLY_PATTERNS), re.IGNORECASE)


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


def extract_year(text: str) -> Optional[int]:
    if not text:
        return None
    m = YEAR_RE.search(text)
    if not m:
        return None
    y = int(m.group(1))
    return y if 1960 <= y <= datetime.now().year + 1 else None


def extract_make_model(title: str) -> tuple[Optional[str], Optional[str]]:
    if not title:
        return None, None
    t = title.lower()
    for make in sorted(MAKES, key=len, reverse=True):
        if make in t:
            after = t.split(make, 1)[1].strip()
            after = re.split(r"\s[-~|/]\s|\s*\$|\s*\(", after, maxsplit=1)[0].strip()
            tokens = after.split()
            if not tokens:
                return make.title(), None
            model = tokens[0]
            if len(tokens) > 1 and (len(tokens[0]) <= 2 or tokens[0].lower() in
                                    {"grand", "land", "town", "model", "alfa"}):
                model = f"{tokens[0]} {tokens[1]}"
            return make.title(), model.title()
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
