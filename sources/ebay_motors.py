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

from .base import (NormalizedListing, extract_make_model, extract_miles,
                    extract_year)

SOURCE_ID = "ebay"
SOURCE_NAME = "eBay Motors"

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_MOTORS_CATEGORY = "6001"  # Cars & Trucks

# Token cache (in-memory)
_TOKEN: dict = {"value": None, "expires_at": 0}


def enabled(cfg: dict) -> bool:
    has_keys = bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))
    src_cfg = cfg.get("sources", {}).get(SOURCE_ID, {})
    return src_cfg.get("enabled", True) and has_keys


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


def poll(cfg: dict) -> list[NormalizedListing]:
    token = _get_token()
    if not token:
        return []

    src_cfg = cfg.get("sources", {}).get(SOURCE_ID, {})
    zip_code = src_cfg.get("zip") or cfg.get("zip", "35401")
    radius = int(src_cfg.get("radius_mi") or cfg.get("radius_mi", 100))
    min_price = int(cfg.get("min_price", 1500))
    max_price = int(cfg.get("max_price", 60000))

    params = {
        "category_ids": EBAY_MOTORS_CATEGORY,
        "limit": 200,
        "sort": "newlyListed",
        "filter": (
            f"price:[{min_price}..{max_price}],"
            "priceCurrency:USD,"
            "itemLocationCountry:US,"
            "buyingOptions:{FIXED_PRICE|AUCTION}"
        ),
        # Local-pickup-style geo filter (eBay uses 'distance' for sellers)
        "buyerPostalCode": zip_code,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-ENDUSERCTX": f"contextualLocation=country=US,zip={zip_code}",
        "Accept": "application/json",
    }
    try:
        r = requests.get(BROWSE_URL, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError):
        return []

    out: list[NormalizedListing] = []
    for it in data.get("itemSummaries", []) or []:
        title = it.get("title") or ""
        # Distance filter (eBay returns distanceFromPickupLocation)
        loc = it.get("itemLocation", {}) or {}
        dist = (it.get("distanceFromPickupLocation") or {}).get("value")
        if dist is not None:
            try:
                if float(dist) > radius:
                    continue
            except (TypeError, ValueError):
                pass
        elif loc.get("stateOrProvince") and loc["stateOrProvince"] not in {
            "AL", "MS", "GA", "TN", "FL"
        }:
            continue  # outside the SE region — skip if no distance returned

        try:
            price = int(float(it.get("price", {}).get("value", 0)))
        except (TypeError, ValueError):
            price = None

        mk, mdl = extract_make_model(title)
        # Pull year/miles from item specifics if eBay provided them
        year = None
        miles = None
        for asp in it.get("itemSpecifics", []) or []:
            name = (asp.get("name") or "").lower()
            vals = ", ".join(asp.get("values") or [])
            if name == "year":
                try: year = int(vals)
                except (ValueError, TypeError): pass
            elif name in ("mileage", "miles"):
                try: miles = int(vals.replace(",", ""))
                except (ValueError, TypeError): pass
        year = year or extract_year(title)
        miles = miles or extract_miles(title)

        buying_options = it.get("buyingOptions") or []
        is_auction = "AUCTION" in buying_options
        # Buy-it-now price if both options exist
        bin_price = None
        if "FIXED_PRICE" in buying_options and is_auction:
            try:
                bin_price = int(float(it.get("buyItNowPrice", {}).get("value", 0))) or None
            except (TypeError, ValueError):
                bin_price = None
        # Auction end if available
        end_at = it.get("itemEndDate") or None
        out.append(NormalizedListing(
            source=SOURCE_ID, source_id=it.get("itemId", ""),
            url=it.get("itemWebUrl", ""), title=title,
            price=price, year=year, make=mk, model=mdl, odometer=miles,
            location=loc.get("city"), state=loc.get("stateOrProvince"),
            description=it.get("shortDescription") or "",
            posted_at=it.get("itemCreationDate"),
            is_dealer=bool(it.get("seller", {}).get("feedbackScore", 0) and
                           int(it.get("seller", {}).get("feedbackScore", 0)) > 500),
            is_auction=is_auction,
            auction_end_at=end_at if is_auction else None,
            buy_now_price=bin_price,
            is_cash_only=not is_auction,  # BIN ≈ cash via PayPal
            accepts_financing=False,
            extras={
                "buying_option": buying_options,
                "condition": it.get("condition"),
                "distance_mi": dist,
                "seller_rating": it.get("seller", {}).get("feedbackPercentage"),
            },
        ))
    return out
