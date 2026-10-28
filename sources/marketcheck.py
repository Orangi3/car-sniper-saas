"""
sources/marketcheck.py — MarketCheck dealer inventory (Inventory Search API).

DEALER listings at dealer RETAIL asking prices. Not a private-party deal
source; useful mainly as clearly-labeled retail comparables. For private
sellers see marketcheck_fsbo.py and DATA_PROVIDER_DECISION.md.

Needs MARKETCHECK_API_KEY. Pricing and terms: see DATA_PROVIDER_DECISION.md
(do not rely on prices quoted in older code comments). Commercial use is
fail-closed until a signed agreement reference is set (same Terms as FSBO).
"""
from __future__ import annotations

import os

import requests

from . import health
from .base import (NormalizedListing, extract_make_model, extract_miles)

SOURCE_ID = "marketcheck"
SOURCE_NAME = "MarketCheck (Cars.com/Autotrader/CarGurus)"
# Data-licensing class (see SOURCES.md): licensed API whose public Terms restrict
# persistent storage; needs a signed agreement covering our use before paid use.
LICENSING = "requires_agreement"
AGREEMENT_ENV = "MARKETCHECK_COMMERCIAL_AGREEMENT"

ACTIVE_URL = "https://api.marketcheck.com/v2/search/car/active"


def enabled(cfg: dict) -> bool:
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)


def config_issue(cfg: dict):
    if not os.environ.get("MARKETCHECK_API_KEY"):
        return "MARKETCHECK_API_KEY not set (paid licensed feed)"
    return None


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
    import provider_budget
    rep = health.report(SOURCE_ID)
    if provider_budget.remaining(SOURCE_ID) <= 0:
        rep.retry_after_s = provider_budget.seconds_to_next_month()
        rep.record("active", health.RATE_LIMITED, detail="monthly call budget exhausted")
        return []
    r = rep.fetch("active", ACTIVE_URL, params=params, timeout=30)
    provider_budget.record(SOURCE_ID)
    if r is None:
        return []
    try:
        data = r.json()
        rep.parsed("active", len(data.get("listings", []) or []))
    except (ValueError, AttributeError):
        rep.malformed("active", "invalid JSON")
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
                "source_site": it.get("source"),
                "price_kind": "dealer_retail_asking",
                "carfax_1_owner": it.get("carfax_1_owner"),
                "carfax_clean_title": it.get("carfax_clean_title"),
            },
        ))
    return out
