"""
sources/hemmings.py — Hemmings classic-car listings via RSS.

Hemmings exposes RSS feeds per category. We pull the new-listings feed.
"""
from __future__ import annotations

import feedparser
import requests

from . import health
from .base import (NormalizedListing, extract_int, extract_price, extract_make_model,
                    extract_miles, extract_year, PRICE_RE)

SOURCE_ID = "hemmings"
SOURCE_NAME = "Hemmings (classics)"
FEEDS = [
    "https://www.hemmings.com/classifieds/cars-for-sale/feed/",
    "https://www.hemmings.com/feed/cars-for-sale.xml",
]
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
      "Accept": "application/rss+xml, application/xml;q=0.9, text/html;q=0.8, */*;q=0.5",
      "Accept-Language": "en-US,en;q=0.9"}
LAST_RESULTS: dict = {}


def enabled(cfg: dict) -> bool:
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)


def poll(cfg: dict) -> list[NormalizedListing]:
    feed = health.fetch_feed(health.report(SOURCE_ID), SOURCE_ID, FEEDS,
                             headers=UA, timeout=10)
    if feed is None:
        return []
    out: list[NormalizedListing] = []
    for e in feed.entries:
        url = e.get("link") or ""
        if not url:
            continue
        slug = url.rstrip("/").split("/")[-1]
        title = (e.get("title") or "").strip()
        desc = e.get("summary") or e.get("description") or ""
        mk, mdl = extract_make_model(title)
        out.append(NormalizedListing(
            source=SOURCE_ID, source_id=slug, url=url, title=title,
            price=extract_price(title) or extract_price(desc),
            year=extract_year(title), make=mk, model=mdl,
            odometer=extract_miles(desc),
            description=desc, posted_at=e.get("published"),
            is_auction=False,  # Hemmings classifieds are mostly fixed-price
            is_cash_only=False, accepts_financing=True,
            extras={"classic": True},
        ))
    return out
