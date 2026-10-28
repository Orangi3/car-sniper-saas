"""
sources/ebay_motors.py — eBay Motors live listings via the official Browse API.

To enable:
  1. Create a free dev account at https://developer.ebay.com/
  2. In your application keys, generate a "Production" Client ID + Secret
  3. Set environment variables:
       export EBAY_CLIENT_ID=...
       export EBAY_CLIENT_SECRET=...
  4. (Optional) Add ebay block to config.json with zip + radius_mi.

Free tier: ~5,000 calls/day, more than enough for 2-min polling.

The Browse API is rate-limited and tokenized. We cache the OAuth app token
for ~2h.
"""
from __future__ import annotations

import base64
import os
import time
from typing import Optional

import requests

from . import health, schema
from .base import (NormalizedListing, extract_make_model, extract_miles,
                    extract_year)

SOURCE_ID = "ebay"
SOURCE_NAME = "eBay Motors"
# Data-licensing class (see SOURCES.md): Official eBay Browse API; use is governed by the eBay API License Agreement.
LICENSING = "licensed_api"

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_MOTORS_CATEGORY = "6001"  # Cars & Trucks

# Token cache (in-memory)
_TOKEN: dict = {"value": None, "expires_at": 0}


def enabled(cfg: dict) -> bool:
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)


def config_issue(cfg: dict) -> Optional[str]:
    if not (os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET")):
        return "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set"
    return None


def _get_token() -> Optional[str]:
    if _TOKEN["value"] and time.time() < _TOKEN["expires_at"] - 60:
        return _TOKEN["value"]
    cid = os.environ.get("EBAY_CLIENT_ID")
    sec = os.environ.get("EBAY_CLIENT_SECRET")
    if not (cid and sec):
        return None
    creds = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    try:
        r = requests.post(
            OAUTH_URL,
            headers={"Authorization": f"Basic {creds}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials",
                  "scope": "https://api.ebay.com/oauth/api_scope"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        return None
    _TOKEN["value"] = data.get("access_token")
    _TOKEN["expires_at"] = time.time() + int(data.get("expires_in", 7200))
    return _TOKEN["value"]


# Fields the adapter relies on (sources/schema.py). Documented Browse
# item_summary fields; verified against fixtures only.
ITEM_SPEC = {
    "itemId": str, "title": str, "itemWebUrl": str, "price.value": schema.NUM,
    "itemLocation.postalCode?": str, "itemLocation.stateOrProvince?": str,
    "itemCreationDate?": str, "seller.sellerAccountType?": str,
    "distanceFromPickupLocation.value?": schema.NUM,
}
LAST_RESULTS: dict = {}


def normalize(it: dict, radius: float | None = None) -> Optional[NormalizedListing]:
    """One Browse item summary -> NormalizedListing (None if unusable or
    beyond `radius` by eBay's own distance)."""
    title = it.get("title") or ""
    url = it.get("itemWebUrl") or ""
    if not it.get("itemId") or not url:
        return None
    loc = it.get("itemLocation", {}) or {}
    dist = (it.get("distanceFromPickupLocation") or {}).get("value")
    if dist is not None and radius is not None:
        try:
            if float(dist) > radius:
                return None
        except (TypeError, ValueError):
            pass
    try:
        price = int(float((it.get("price") or {}).get("value"))) or None
    except (TypeError, ValueError):
        price = None
    mk, mdl = extract_make_model(title)
    year = extract_year(title)
    miles = extract_miles(title)
    buying_options = it.get("buyingOptions") or []
    is_auction = "AUCTION" in buying_options
    bin_price = None
    if "FIXED_PRICE" in buying_options and is_auction:
        try:
            bin_price = int(float((it.get("buyItNowPrice") or {}).get("value"))) or None
        except (TypeError, ValueError):
            bin_price = None
    seller = it.get("seller") or {}
    acct = (seller.get("sellerAccountType") or "").upper()   # BUSINESS | INDIVIDUAL
    postal = str(loc.get("postalCode") or "")[:5]
    return NormalizedListing(
        source=SOURCE_ID, source_id=str(it["itemId"]), url=url, title=title,
        price=price, year=year, make=mk, model=mdl, odometer=miles,
        location=loc.get("city"), state=loc.get("stateOrProvince"),
        zip=postal if postal.isdigit() and len(postal) == 5 else None,
        description=it.get("shortDescription") or "",
        posted_at=it.get("itemCreationDate"),
        # Only eBay's own account type decides; no guessing from feedback.
        is_dealer=acct == "BUSINESS",
        is_auction=is_auction,
        auction_end_at=it.get("itemEndDate") if is_auction else None,
        buy_now_price=bin_price,
        is_cash_only=False, accepts_financing=False,
        extras={"buying_option": buying_options, "condition": it.get("condition"),
                "distance_mi": dist, "seller_type": acct.lower() or "unknown",
                "seller_rating": seller.get("feedbackPercentage")},
    )


def poll(cfg: dict) -> list[NormalizedListing]:
    rep = health.report(SOURCE_ID)
    token = _get_token()
    if not token:
        rep.record("oauth", health.AUTH, detail="could not obtain OAuth token")
        return []

    src_cfg = cfg.get("sources", {}).get(SOURCE_ID, {})
    zip_code = src_cfg.get("zip") or cfg.get("zip", "35401")
    radius = int(src_cfg.get("radius_mi") or cfg.get("radius_mi", 100))
    min_price = int(cfg.get("min_price", 1500))
    max_price = int(cfg.get("max_price", 60000))

    # Browse API geography = the pickup filters (the old `buyerPostalCode`
    # was a parameter of the decommissioned Finding API and was ignored).
    params = {
        "category_ids": EBAY_MOTORS_CATEGORY,
        "limit": 200,
        "sort": "newlyListed",
        "filter": (
            f"price:[{min_price}..{max_price}],"
            "priceCurrency:USD,"
            "itemLocationCountry:US,"
            "buyingOptions:{FIXED_PRICE|AUCTION},"
            "deliveryOptions:{SELLER_ARRANGED_LOCAL_PICKUP},"
            f"pickupCountry:US,pickupPostalCode:{zip_code},"
            f"pickupRadius:{radius},pickupRadiusUnit:mi"
        ),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        # Documented value; a Motors-specific marketplace id was not confirmed.
        "X-EBAY-C-MARKETPLACE-ID": os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US"),
        "X-EBAY-C-ENDUSERCTX": f"contextualLocation=country=US,zip={zip_code}",
        "Accept": "application/json",
    }
    r = rep.fetch("browse", BROWSE_URL, params=params, headers=headers, timeout=20)
    if r is None:
        return []
    try:
        data = r.json()
        items = data.get("itemSummaries", []) or []
    except (ValueError, AttributeError):
        rep.malformed("browse", "invalid JSON")
        return []
    rep.parsed("browse", len(items))

    sc = schema.check_items(items, ITEM_SPEC)
    LAST_RESULTS["schema"] = sc
    if schema.drifted(sc):
        rep.malformed("browse", schema.describe(sc))
    out: list[NormalizedListing] = []
    for it in items:
        n = normalize(it, radius)
        if n:
            out.append(n)
    return out
