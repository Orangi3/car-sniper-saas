"""
sources/ebay_motors.py — eBay Motors live listings via the official Browse API.

Access (owner steps: EBAY_SETUP.md):
  * Sandbox keys work at once and return eBay's TEST data (never real cars).
  * Production use of the Buy APIs (Browse included) is for eBay partners:
    apply through the eBay Partner Network, get eBay's approval and sign its
    contracts; approval is not guaranteed (eBay "Buy APIs requirements",
    read via search 2026-10-30). The API License Agreement also bars using
    eBay content to model prices of eBay items without written permission,
    which FlipFirst's valuation does. Until the owner records written
    approval of both in EBAY_COMMERCIAL_AGREEMENT, commercial mode keeps
    this source off.
  * Credentials: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET in the server env file.

Default limit 5,000 Browse calls/day (Application Growth Check for more);
one territory polled every 5 min with <= 2 pages is <= 576 calls/day.

The Browse API is rate-limited and tokenized. We cache the OAuth app token
for ~2h and drop it as soon as eBay rejects it (401), so an expired or
revoked token is replaced on the next poll instead of failing until expiry.

Known API limits (eBay docs, via search 2026-10-28; not live-verified):
  * `seller.sellerAccountType` is only returned on some European
    marketplaces (EBAY_DE, EBAY_GB, ...). On EBAY_US it is absent, so
    US sellers are recorded as seller_type "unknown" — never as private.
  * item summaries carry no structured year/make/model/mileage; those are
    parsed from the title and marked as such.
  * the `distanceFromPickupLocation` container is returned only when the
    local-pickup filters are sent (they are, together, as required).
"""
from __future__ import annotations

import base64
import os
import time
from typing import Optional
from urllib.parse import quote

import requests

from . import health, schema
from .base import (NormalizedListing, extract_make_model, extract_miles,
                    extract_year)

SOURCE_ID = "ebay"
SOURCE_NAME = "eBay Motors"
# Data-licensing class (see SOURCES.md): official Browse API, but production
# use needs eBay's Buy API approval and contracts — fail-closed until the
# owner records the approval reference.
LICENSING = "requires_agreement"
AGREEMENT_ENV = "EBAY_COMMERCIAL_AGREEMENT"

def _api_base() -> str:
    """https://api.ebay.com, or eBay's Sandbox (EBAY_ENVIRONMENT=sandbox,
    for testing keys before production access), or EBAY_API_BASE for a
    staging stand-in. Plain http is accepted only for a local address."""
    base = (os.environ.get("EBAY_API_BASE") or "").rstrip("/")
    if base:
        if not (base.startswith("https://") or base.startswith("http://127.0.0.1")
                or base.startswith("http://localhost")):
            raise ValueError("EBAY_API_BASE must be https (or a local http address)")
        return base
    if os.environ.get("EBAY_ENVIRONMENT", "").lower() == "sandbox":
        return "https://api.sandbox.ebay.com"
    return "https://api.ebay.com"


OAUTH_URL = _api_base() + "/identity/v1/oauth2/token"
BROWSE_URL = _api_base() + "/buy/browse/v1/item_summary/search"
EBAY_MOTORS_CATEGORY = "6001"  # Cars & Trucks
PAGE_LIMIT = 200               # documented max per search call
# A further page is fetched only when the newest page is full and entirely
# new to us; bounded to keep within the daily call quota.
MAX_PAGES = int(os.environ.get("EBAY_MAX_PAGES", "2"))
# eBay API License Agreement: displayed listing data may be at most 6 hours
# older than eBay's site. Listings not re-confirmed by a poll within this
# window are hidden from customer views (server._displayable).
DISPLAY_MAX_AGE_HOURS = 6

# Token cache (in-memory)
_TOKEN: dict = {"value": None, "expires_at": 0}


def enabled(cfg: dict) -> bool:
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)


def config_issue(cfg: dict) -> Optional[str]:
    if not (os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET")):
        return "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set"
    return None


def _get_token() -> tuple[Optional[str], Optional[tuple[str, Optional[int], str]]]:
    """(token, None) or (None, (health outcome, http status, detail)).
    Rejected credentials (400/401 from the token endpoint) are AUTH;
    everything else is a transient network/HTTP problem."""
    if _TOKEN["value"] and time.time() < _TOKEN["expires_at"] - 60:
        return _TOKEN["value"], None
    cid = os.environ.get("EBAY_CLIENT_ID")
    sec = os.environ.get("EBAY_CLIENT_SECRET")
    if not (cid and sec):
        return None, (health.AUTH, None, "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set")
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
    except requests.Timeout:
        return None, (health.TIMEOUT, None, "token request timed out")
    except requests.RequestException as e:
        return None, (health.NETWORK, None, f"token request failed: {type(e).__name__}")
    if r.status_code in (400, 401):
        return None, (health.AUTH, r.status_code, "eBay rejected the client credentials")
    if r.status_code != 200:
        return None, (health.HTTP_ERROR, r.status_code, "token endpoint error")
    try:
        data = r.json()
        token = data["access_token"]
    except (ValueError, KeyError, TypeError):
        return None, (health.MALFORMED, 200, "token response without access_token")
    _TOKEN["value"] = token
    _TOKEN["expires_at"] = time.time() + int(data.get("expires_in", 7200))
    return token, None


def _drop_token() -> None:
    _TOKEN["value"], _TOKEN["expires_at"] = None, 0


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
    # eBay Partner Network tracking (a Buy API requirement): when a campaign
    # id is configured eBay returns itemAffiliateWebUrl — link to that.
    url = ((it.get("itemAffiliateWebUrl") if os.environ.get("EBAY_EPN_CAMPAIGN_ID") else None)
           or it.get("itemWebUrl") or "")
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
    pr = it.get("price") or {}
    currency = (pr.get("currency") or "USD").upper()
    price = None
    if currency == "USD":                 # only USD is comparable with our market data
        try:
            price = int(float(pr.get("value"))) or None
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
    # eBay's own condition "For parts or not working" (id 7000)
    for_parts = (str(it.get("conditionId") or "") == "7000"
                 or "not working" in str(it.get("condition") or "").lower())
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
                "seller_rating": seller.get("feedbackPercentage"),
                "currency": currency,
                # no structured vehicle fields in item summaries
                "vehicle_fields_from": "title",
                **({"needs_work": True} if for_parts else {})},
    )


def _enduser_ctx(zip_code: str) -> str:
    parts = []
    camp = (os.environ.get("EBAY_EPN_CAMPAIGN_ID") or "").strip()
    if camp.isdigit():                      # EPN campaign ids are numeric
        parts.append(f"affiliateCampaignId={camp}")
    parts.append("contextualLocation=" + quote(f"country=US,zip={zip_code}", safe=""))
    return ",".join(parts)


def _known_ids(ids: list[str]) -> set[str]:
    import db as _db
    if not ids:
        return set()
    with _db.session() as conn:
        rows = conn.execute(
            f"SELECT source_id FROM listings WHERE source = ? AND source_id IN "
            f"({','.join('?' for _ in ids)})", (SOURCE_ID, *ids)).fetchall()
    return {r[0] for r in rows}


def poll(cfg: dict) -> list[NormalizedListing]:
    rep = health.report(SOURCE_ID)
    token, err = _get_token()
    if not token:
        outcome, status, detail = err
        rep.record("oauth", outcome, status, detail=detail)
        return []

    src_cfg = cfg.get("sources", {}).get(SOURCE_ID, {})
    zip_code = src_cfg.get("zip") or cfg.get("zip", "35401")
    radius = int(src_cfg.get("radius_mi") or cfg.get("radius_mi", 100))
    min_price = int(cfg.get("min_price", 1500))
    max_price = int(cfg.get("max_price", 60000))

    # Browse API geography = the pickup filters (the old `buyerPostalCode`
    # was a parameter of the decommissioned Finding API and was ignored).
    # The pickup filters only work together.
    params = {
        "category_ids": EBAY_MOTORS_CATEGORY,
        "limit": PAGE_LIMIT,
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
        # documented as URL-encoded: contextualLocation=country%3DUS%2Czip%3D...
        "X-EBAY-C-ENDUSERCTX": _enduser_ctx(zip_code),
        "Accept": "application/json",
    }
    out: list[NormalizedListing] = []
    seen: set[str] = set()
    all_items: list = []
    for page in range(max(1, MAX_PAGES)):
        label = f"browse{page}"
        r = rep.fetch(label, BROWSE_URL, params={**params, "offset": page * PAGE_LIMIT},
                      headers=headers, timeout=20)
        if r is None:
            last = rep.attempts[-1] if rep.attempts else {}
            if last.get("status") == 401:
                _drop_token()             # expired/revoked: fetch a new one next poll
            elif last.get("status") == 403 and (
                    "1100" in last.get("detail", "") or
                    "Insufficient permissions" in last.get("detail", "")):
                # eBay errorId 1100: the keyset has no Buy API (Browse) access.
                # Production Buy APIs are limited release (eBay Partner Network
                # application + growth check) — a permission problem, not a block.
                rep.reclassify_last(health.AUTH, "eBay keyset lacks Browse API access "
                                    "(errorId 1100): production Buy APIs need eBay "
                                    "Partner Network approval; Sandbox keys work for tests")
            break
        try:
            data = r.json()
            items = data.get("itemSummaries", []) or []
            if not isinstance(items, list):
                raise ValueError("itemSummaries is not a list")
        except (ValueError, AttributeError) as e:
            rep.malformed(label, f"invalid response: {e}")
            break
        rep.parsed(label, len(items))
        items = [it for it in items if isinstance(it, dict)]
        all_items.extend(items)
        batch = []
        for it in items:
            n = normalize(it, radius)
            if n and n.source_id not in seen:   # eBay can repeat items across pages
                seen.add(n.source_id)
                batch.append(n)
        out.extend(batch)
        LAST_RESULTS.update({"total": data.get("total"), "pages": page + 1})
        if len(items) < PAGE_LIMIT or not data.get("next"):
            break
        if _known_ids([b.source_id for b in batch]):
            break                          # caught up with what we already have
    sc = schema.check_items(all_items, ITEM_SPEC)
    LAST_RESULTS["schema"] = sc
    if schema.drifted(sc):
        rep.malformed("schema", schema.describe(sc))
    return out
