"""
sources/bring_a_trailer.py — BaT live auctions via their RSS feed.

Feed URL: https://bringatrailer.com/feed/
BaT publishes new live auctions and ending-soon auctions in their feed.
"""
from __future__ import annotations

import feedparser
import re
import requests

from .base import (NormalizedListing, extract_int, extract_make_model,
                    extract_miles, extract_year, PRICE_RE)

SOURCE_ID = "bat"
SOURCE_NAME = "Bring a Trailer"
# BaT has multiple feed endpoints — try them in order
FEEDS = [
    "https://bringatrailer.com/feed/",
    "https://bringatrailer.com/auctions/feed/",
]
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
      "Accept": "application/rss+xml, application/xml;q=0.9, text/html;q=0.8, */*;q=0.5",
      "Accept-Language": "en-US,en;q=0.9"}
LAST_RESULTS: dict = {}


def enabled(cfg: dict) -> bool:
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)


def poll(cfg: dict) -> list[NormalizedListing]:
    content = None
    for feed_url in FEEDS:
        try:
            r = requests.get(feed_url, headers=UA, timeout=10)
            LAST_RESULTS["last_url"] = feed_url
            LAST_RESULTS["last_status"] = r.status_code
            if r.status_code == 200 and len(r.content) > 200:
                content = r.content; break
        except requests.RequestException as e:
            LAST_RESULTS["last_error"] = f"{type(e).__name__}"
            continue
    if not content:
        return []
    feed = feedparser.parse(content)
    out: list[NormalizedListing] = []
    for e in feed.entries:
        url = e.get("link") or ""
        if not url or "/listing/" not in url:
            continue
        slug = url.rstrip("/").split("/")[-1]
        title = (e.get("title") or "").strip()
        desc = (e.get("summary") or e.get("description") or "")
        mk, mdl = extract_make_model(title)
        price = extract_int(PRICE_RE, desc)
        # Pull bid count if BaT exposes it
        import re as _re
        bid_m = _re.search(r"(\d+)\s+bids?", desc, _re.IGNORECASE)
        bid_count = int(bid_m.group(1)) if bid_m else None
        out.append(NormalizedListing(
            source=SOURCE_ID, source_id=slug, url=url, title=title,
            price=price, year=extract_year(title), make=mk, model=mdl,
            odometer=extract_miles(desc),
            description=desc, posted_at=e.get("published"),
            is_dealer=False, is_auction=True, bid_count=bid_count,
            is_cash_only=False, accepts_financing=False,
            extras={"auction_site": "bringatrailer"},
        ))
    return out
