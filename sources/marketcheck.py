"""
sources/marketcheck.py — MarketCheck API (covers Cars.com / Autotrader / CarGurus).

This is the LEGAL way to get listings from sites that prohibit scraping.
MarketCheck licenses dealer inventory feeds from Cars.com, Autotrader,
CarGurus, and thousands of independent dealer sites — and exposes them via
a single REST API.

To enable:
  1. Sign up at https://www.marketcheck.com/automotive/cars-api
     (free trial ~250 calls; paid plans from ~$50/mo for personal use)
  2. Set environment variable:
       export MARKETCHECK_API_KEY=...
  3. (Optional) Add marketcheck block to config with zip + radius_mi.

If the env var isn't set, this source disables itself silently.
"""
from __future__ import annotations

import os

import requests

from .base import (NormalizedListing, extract_make_model, extract_miles)

SOURCE_ID = "marketcheck"
SOURCE_NAME = "MarketCheck (Cars.com/Autotrader/CarGurus)"

ACTIVE_URL = "https://api.marketcheck.com/v2/search/car/active"


def enabled(cfg: dict) -> bool:
    has_key = bool(os.environ.get("MARKETCHECK_API_KEY"))
    src_cfg = cfg.get("sources", {}).get(SOURCE_ID, {})
    return src_cfg.get("enabled", True) and has_key


def poll(cfg: dict) -> list[NormalizedListing]:
    key = os.environ.get("MARKETCHECK_API_KEY")
    if not key:
        return []
    src_cfg = cfg.get("sources", {}).get(SOURCE_ID, {})
    zip_code = src_cfg.get("zip") or cfg.get("zip", "35401")
    radius = int(src_cfg.get("radius_mi") or cfg.get("radius_mi", 100))

    params = {
        "api_key": key,
        "zip": zip_code,
        "radius": radius,
        "rows": 100,
        "sort_by": "list_date",
        "sort_order": "desc",
        "include_relevant_links": "true",
        "price_range": f"{cfg.get('min_price', 1500)}-{cfg.get('max_price', 60000)}",
    }
    try:
        r = requests.get(ACTIVE_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return []

    out: list[NormalizedListing] = []
    for it in data.get("listings", []) or []:
        build = it.get("build", {}) or {}
        dealer = it.get("dealer", {}) or {}
        out.append(NormalizedListing(
            source=SOURCE_ID, source_id=str(it.get("id") or it.get("vin") or it.get("vdp_url")),
            url=it.get("vdp_url") or "", title=it.get("heading") or "",
            price=int(it.get("price")) if it.get("price") else None,
            year=int(build.get("year")) if build.get("year") else None,
            make=build.get("make"), model=build.get("model"),
            odometer=int(it.get("miles")) if it.get("miles") else None,
            location=dealer.get("city"), state=dealer.get("state"),
            description=it.get("seller_comments") or "",
            posted_at=it.get("first_seen_date_mc"),
            is_dealer=True,  # MarketCheck is dealer inventory by definition
            is_auction=False,
            is_cash_only=False, accepts_financing=True,
            extras={
                "vin": it.get("vin"), "trim": build.get("trim"),
                "transmission": build.get("transmission"),
                "drivetrain": build.get("drivetrain"),
                "exterior_color": it.get("exterior_color"),
                "dealer_name": dealer.get("name"),
                "source_site": it.get("source"),  # cars.com / autotrader / etc
                "carfax_1_owner": it.get("carfax_1_owner"),
                "carfax_clean_title": it.get("carfax_clean_title"),
            },
        ))
    return out
