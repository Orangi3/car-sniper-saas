"""
sources/marketplace_import.py — pull listings pushed in from a browser extension.

This source does NOT scrape Facebook/OfferUp/etc itself (TOS prohibits it).
Instead it accepts listings POSTed to /api/import by a Chrome extension that
runs in the user's own logged-in browser session — i.e. the user is browsing
Marketplace normally, the extension grabs what's already on screen, and
forwards it here.

That keeps the data flow as "personal use of my own browsing data" rather
than automated scraping. The extension lives in marketplace_extension/.

Listings are stored in marketplace_inbox.sqlite by server.py's /api/import
endpoint. This source just reads them on each poll.
"""
from __future__ import annotations

import json
from typing import Optional

from .base import (NormalizedListing, extract_make_model, extract_miles,
                    extract_year)

SOURCE_ID = "marketplace"
SOURCE_NAME = "Marketplace (browser extension)"
# Imports are kept in the main database (table marketplace_inbox, migration
# 0008) — previously a separate marketplace_inbox.sqlite next to the code,
# invisible to other hosts and outside backups. poll() only re-reads recent
# rows so the table can grow without making every tick slower.
INBOX_LOOKBACK_HOURS = 72


def push(payload: dict) -> Optional[str]:
    """Called by /api/import endpoint to insert a new listing from the extension."""
    import db as _db
    listing_id = payload.get("id") or payload.get("url") or ""
    if not listing_id:
        return None
    platform = str(payload.get("platform", "facebook"))[:40]
    from datetime import datetime, timezone
    key = f"{platform}:{listing_id}"[:500]
    try:
        with _db.session() as c:
            c.execute(
                "INSERT INTO marketplace_inbox(id, platform, payload, received_at) "
                "VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "payload=excluded.payload, received_at=excluded.received_at",
                (key, platform, json.dumps(payload)[:20000],
                 datetime.now(timezone.utc).isoformat()))
        return key
    except Exception:
        return None


# ---------- Email-digest parser -----------------------------------------

import re as _re

_URL_RE = _re.compile(r"https?://[^\s<>\"']+", _re.IGNORECASE)
_PRICE_RE = _re.compile(r"\$\s?([\d,]{3,7})")
_YEAR_RE = _re.compile(r"\b(19[6-9]\d|20[0-3]\d)\b")
_MILES_RE = _re.compile(r"([\d,]+)\s*(?:mi|miles|mileage)\b", _re.IGNORECASE)


def _detect_platform(text: str, hint: Optional[str]) -> str:
    if hint:
        return hint
    t = text.lower()
    if "facebook.com/marketplace" in t or "marketplace.facebook" in t: return "facebook"
    if "nextdoor.com" in t:                                            return "nextdoor"
    if "offerup.com" in t:                                             return "offerup"
    if "cars.com" in t:                                                return "cars_com"
    if "autotrader.com" in t:                                          return "autotrader"
    if "cargurus.com" in t:                                            return "cargurus"
    if "carsandbids.com" in t:                                         return "carsandbids"
    if "ebay.com" in t or "ebay motors" in t:                          return "ebay_email"
    if "craigslist.org" in t:                                          return "craigslist_email"
    return "email_unknown"


def parse_email_digest(text: str, platform_hint: Optional[str] = None) -> list[dict]:
    """Best-effort listing extractor for saved-search emails.

    Most digest emails are HTML where each listing is a stanza with a URL +
    title + price (often within ~200 chars). We pull URL matches, look at
    surrounding text for price/year/miles, and push each as one listing.
    Crude but robust across email formats.
    """
    platform = _detect_platform(text, platform_hint)
    seen_urls = set()
    listings = []

    urls = _URL_RE.findall(text)
    plain = _re.sub(r"<[^>]+>", " ", text)
    plain = _re.sub(r"\s+", " ", plain)

    for url in urls:
        u = url.rstrip('.,;)"\'>')
        if not any(host in u.lower() for host in (
            "facebook.com/marketplace/item", "nextdoor.com",
            "offerup.com/item", "ebay.com/itm", "craigslist.org",
            "marketplace.facebook.com")):
            continue
        if u in seen_urls:
            continue
        seen_urls.add(u)

        idx = plain.find(u)
        if idx == -1:
            idx = max(0, plain.lower().find(u.lower()))
        ctx = plain[max(0, idx - 250):idx + 250]

        price = _PRICE_RE.search(ctx)
        year_m = _YEAR_RE.search(ctx)
        miles_m = _MILES_RE.search(ctx)

        before = plain[max(0, idx - 200):idx].strip()
        title_chunk = before.split("$")[-1] if "$" in before else before
        title = title_chunk.split(".")[-1].strip()[:140] or "(untitled)"

        try:
            price_i = int(price.group(1).replace(",", "")) if price else None
        except ValueError:
            price_i = None

        if not price_i:
            continue

        listing = {
            "id": u, "url": u, "title": title, "price": price_i,
            "year": int(year_m.group(1)) if year_m else None,
            "miles": int(miles_m.group(1).replace(",", "")) if miles_m else None,
            "platform": platform, "description": ctx[:300], "posted_at": None,
        }
        push(listing)
        listings.append(listing)
    return listings


def enabled(cfg: dict) -> bool:
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)


def poll(cfg: dict) -> list[NormalizedListing]:
    import db as _db
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=INBOX_LOOKBACK_HOURS)).isoformat()
    with _db.session() as c:
        rows = [tuple(r) for r in c.execute(
            "SELECT id, platform, payload FROM marketplace_inbox "
            "WHERE received_at >= ?", (cutoff,)).fetchall()]
    # Platforms whose listings are dealer inventory by default. Dealers
    # accept financing and aren't cash-only — opposite of FB/OfferUp/Nextdoor.
    DEALER_PLATFORMS = {"cars_com", "autotrader", "cargurus"}

    out: list[NormalizedListing] = []
    for row_id, platform, payload_json in rows:
        try:
            p = json.loads(payload_json)
        except json.JSONDecodeError:
            continue
        title = p.get("title") or ""
        desc = p.get("description") or ""
        mk, mdl = extract_make_model(title)

        # Trust the extension if it set is_dealer; otherwise infer from platform.
        if "is_dealer" in p:
            is_dealer = bool(p.get("is_dealer"))
        else:
            is_dealer = platform in DEALER_PLATFORMS

        # Cash-only / financing flow from is_dealer unless extension overrode.
        is_cash_only = bool(p["is_cash_only"]) if "is_cash_only" in p else (not is_dealer)
        accepts_fin  = bool(p["accepts_financing"]) if "accepts_financing" in p else is_dealer

        is_auction = bool(p.get("is_auction")) or platform == "carsandbids"

        extras = {"platform": platform, "imported_via": "browser_extension"}
        if p.get("cargurus_deal"):
            extras["cargurus_deal"] = p["cargurus_deal"]

        out.append(NormalizedListing(
            source=SOURCE_ID,
            source_id=row_id.split(":", 1)[-1],
            url=p.get("url") or "",
            title=title,
            price=int(p["price"]) if p.get("price") else None,
            year=int(p["year"]) if p.get("year") else extract_year(title),
            make=p.get("make") or mk,
            model=p.get("model") or mdl,
            odometer=int(p["miles"]) if p.get("miles") else extract_miles(desc),
            location=p.get("location"),
            state=p.get("state"),
            description=desc,
            posted_at=p.get("posted_at"),
            is_dealer=is_dealer,
            is_auction=is_auction,
            auction_end_at=p.get("auction_end_at"),
            is_cash_only=is_cash_only,
            accepts_financing=accepts_fin,
            extras=extras,
        ))
    return out
