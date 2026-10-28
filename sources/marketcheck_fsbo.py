"""
sources/marketcheck_fsbo.py — MarketCheck private-party (FSBO) listings.

Endpoint (documented): GET https://api.marketcheck.com/v2/search/car/fsbo/active
Detail (documented):   GET https://api.marketcheck.com/v2/listing/car/fsbo/{id}
Returns private-seller listings only (seller_type = fsbo). Seller contact
details are not provided — only the source marketplace and listing URL.
See DATA_PROVIDER_DECISION.md.

Commercial status: FAIL-CLOSED. MarketCheck's public Terms restrict
persistent datasets and caching outside "normal application use"; Car
Sniper's model (central polling, stored history, pushed alerts) needs
written confirmation. In commercial mode this source runs only when
MARKETCHECK_COMMERCIAL_AGREEMENT holds the reference of a signed agreement
that covers this use. Setting it without such an agreement misrepresents
the license — don't.

Cost control:
  * ONE central query per poll for the configured territory (zip + radius,
    $2k–$15k used cars by default) — subscribers never trigger calls
  * newest-first; a second page is fetched only if every listing on the
    first page is new to us (otherwise we're caught up)
  * hard monthly call budget (provider_budget.py); when exhausted, or when
    MarketCheck reports the monthly quota exhausted, the source backs off
    until the 1st of next month

Field mapping follows MarketCheck's documented listing schema (FSBO detail is
documented as identical to Car Listing Details). It has NOT been verified
against a live FSBO response — tests use fixtures shaped from the docs.
Location coordinates/ZIP are taken only from structured fields.
"""
from __future__ import annotations

import os
from typing import Optional

from . import health
from .base import NormalizedListing, extract_make_model, extract_miles, extract_year

SOURCE_ID = "marketcheck_fsbo"
SOURCE_NAME = "MarketCheck private sellers (FSBO)"
# Data-licensing class (see SOURCES.md): licensed API whose public Terms don't
# clearly cover stored alerts; needs a signed agreement before paid use.
LICENSING = "requires_agreement"
AGREEMENT_ENV = "MARKETCHECK_COMMERCIAL_AGREEMENT"

SEARCH_URL = "https://api.marketcheck.com/v2/search/car/fsbo/active"
ROWS = 50                      # per-request cap reported for this endpoint
MAX_PAGES = int(os.environ.get("MARKETCHECK_FSBO_MAX_PAGES", "2"))
# Newest-first sort. Parameter values follow the Inventory Search docs and are
# configurable because they were not live-verified for the FSBO endpoint.
SORT_BY = os.environ.get("MARKETCHECK_FSBO_SORT_BY", "dom")
SORT_ORDER = os.environ.get("MARKETCHECK_FSBO_SORT_ORDER", "asc")

LAST_RESULTS: dict = {}


def enabled(cfg: dict) -> bool:
    return cfg.get("sources", {}).get(SOURCE_ID, {}).get("enabled", True)


def config_issue(cfg: dict) -> Optional[str]:
    if not os.environ.get("MARKETCHECK_API_KEY"):
        return "MARKETCHECK_API_KEY not set (Basic plan or higher needed)"
    return None


def _territory(cfg: dict) -> tuple[str, int, int, int]:
    src = cfg.get("sources", {}).get(SOURCE_ID, {})
    zip_code = str(src.get("zip") or cfg.get("zip") or "35401")
    radius = int(src.get("radius_mi") or cfg.get("radius_mi") or 100)
    lo = int(src.get("min_price") or 2000)
    hi = int(src.get("max_price") or 15000)
    return zip_code, radius, lo, hi


def _num(v, cast=float):
    try:
        return cast(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize(it: dict) -> Optional[NormalizedListing]:
    """One FSBO record -> NormalizedListing (None if unusable)."""
    lid = it.get("id")
    url = it.get("vdp_url") or ""
    if not lid or not url:
        return None
    build = it.get("build") or {}
    loc = it.get("dealer") or it.get("seller") or it.get("location") or {}
    heading = it.get("heading") or " ".join(
        str(x) for x in (build.get("year"), build.get("make"), build.get("model"),
                         build.get("trim")) if x)
    mk, md = extract_make_model(heading)
    lat = _num(loc.get("latitude", it.get("latitude")))
    lon = _num(loc.get("longitude", it.get("longitude")))
    if lat is not None and not (-90 <= lat <= 90):
        lat = None
    if lon is not None and not (-180 <= lon <= 180):
        lon = None
    zip_code = loc.get("zip") or it.get("zip")
    photos = ((it.get("media") or {}).get("photo_links") or [])[:8]
    return NormalizedListing(
        source=SOURCE_ID, source_id=str(lid), url=url, title=heading,
        price=_num(it.get("price"), int),
        year=_num(build.get("year"), int) or extract_year(heading),
        make=build.get("make") or mk, model=build.get("model") or md,
        odometer=_num(it.get("miles"), int) or extract_miles(heading),
        location=loc.get("city"), state=loc.get("state"),
        zip=str(zip_code)[:5] if zip_code else None, latitude=lat, longitude=lon,
        description=it.get("seller_comments") or "",
        posted_at=it.get("first_seen_at_date") or it.get("first_seen_at"),
        is_dealer=False,                       # endpoint returns private sellers only
        is_auction=False, is_cash_only=True, accepts_financing=False,
        image_urls=photos,
        extras={"provider": "marketcheck", "seller_type": it.get("seller_type") or "fsbo",
                "origin_marketplace": it.get("source"), "vin": it.get("vin"),
                "trim": build.get("trim"), "body_type": build.get("body_type"),
                "drivetrain": build.get("drivetrain"),
                "transmission": build.get("transmission"),
                "provider_ref_price": it.get("ref_price"),
                "provider_dom": it.get("dom"),
                "provider_last_seen": it.get("last_seen_at_date") or it.get("last_seen_at")},
    )


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
    import provider_budget
    rep = health.report(SOURCE_ID)
    key = os.environ.get("MARKETCHECK_API_KEY")
    if not key:
        rep.record("config", health.AUTH, detail="MARKETCHECK_API_KEY not set")
        return []
    zip_code, radius, lo, hi = _territory(cfg)
    out: list[NormalizedListing] = []
    for page in range(MAX_PAGES):
        if provider_budget.remaining(SOURCE_ID) <= 0:
            rep.retry_after_s = provider_budget.seconds_to_next_month()
            rep.record(f"page{page}", health.RATE_LIMITED,
                       detail="monthly call budget exhausted")
            break
        params = {"api_key": key, "zip": zip_code, "radius": radius, "car_type": "used",
                  "price_range": f"{lo}-{hi}", "rows": ROWS, "start": page * ROWS,
                  "sort_by": SORT_BY, "sort_order": SORT_ORDER}
        r = rep.fetch(f"page{page}", SEARCH_URL, params=params, timeout=30)
        provider_budget.record(SOURCE_ID)
        if r is None:
            last = rep.attempts[-1]
            if last["outcome"] == health.RATE_LIMITED and "quota" in str(last.get("detail", "")):
                rep.retry_after_s = provider_budget.seconds_to_next_month()
            break
        try:
            data = r.json()
            items = data.get("listings")
            if not isinstance(items, list):
                raise ValueError("no listings array")
        except (ValueError, AttributeError) as e:
            rep.malformed(f"page{page}", f"unexpected response: {e}")
            break
        rep.parsed(f"page{page}", len(items))
        batch = [n for n in (normalize(it) for it in items if isinstance(it, dict)) if n]
        out.extend(batch)
        LAST_RESULTS.update({"num_found": data.get("num_found"), "pages": page + 1,
                             "territory": f"{zip_code}+{radius}mi"})
        if len(items) < ROWS:
            break
        if _known_ids([b.source_id for b in batch]):
            break                          # caught up with what we already have
    return out
