"""
vehicle_identity.py — are two listings the same physical vehicle?

Used to keep one subscriber from being alerted twice about one car posted on
two marketplaces (saved_search_alerts). The answer is "same vehicle" or "not
shown to be", and the bar for "same" is deliberately high: a missed
duplicate costs one extra alert, a false "same" hides a real car.

  both carry a VIN (vehicle_key)   equal → same; different → never the same
  otherwise, ALL of:
    * different sources — same-source reposts are relist detection's job
    * same year, make and model; same trim where both state one
    * same title once prices are stripped (sniper._norm_title, as relist
      detection compares titles)
    * both odometers known and equal
    * both locations known (provider coordinates or ZIP centroid) and within
      SAME_VEHICLE_MAX_MI
    * no conflicting photo evidence
    * one identifying detail: matching photo evidence, or an exact odometer
      reading — a round thousand ("130k") is shared by many cars of a model
Price is not used: one car is often listed at different prices.

Photo evidence is comparable only between two photo fingerprints
(extras["photo_hash"]) or two first-photo URLs on the same host (different
marketplaces host the same photo at different URLs, so cross-host URLs say
nothing either way).
"""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlparse

# Both postings describe one parked car; their locations differ only by
# geocoding precision (a ZIP centroid vs the seller's own coordinates, or a
# neighbouring ZIP in the same town). 25 mi allows that and no more.
SAME_VEHICLE_MAX_MI = 25.0


def _json(v, default):
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default


def _photo(l: dict) -> tuple[Optional[str], Optional[str]]:
    """(fingerprint, first photo URL)."""
    fp = (_json(l.get("extras"), {}) or {}).get("photo_hash")
    urls = _json(l.get("image_urls"), []) or []
    return (str(fp) if fp else None), (str(urls[0]) if urls else None)


def photo_evidence(a: dict, b: dict) -> Optional[bool]:
    """True: same photo; False: different photos; None: not comparable."""
    fa, ua = _photo(a)
    fb, ub = _photo(b)
    if fa and fb:
        return fa == fb
    if ua and ub and urlparse(ua).netloc and urlparse(ua).netloc == urlparse(ub).netloc:
        return ua == ub
    return None


def same_vehicle(a: dict, b: dict) -> tuple[bool, str, dict]:
    """(same?, reason, evidence) for two listings rows (listings columns)."""
    ka, kb = a.get("vehicle_key"), b.get("vehicle_key")
    if ka and kb:
        return (ka == kb), ("same_vin" if ka == kb else "different_vin"), {"vehicle_key": ka}
    if not a.get("source") or a.get("source") == b.get("source"):
        return False, "same_source", {}
    if not (a.get("year") and a.get("year") == b.get("year")):
        return False, "year", {}
    for k in ("make", "model"):
        if not a.get(k) or str(a[k]).lower() != str(b.get(k) or "").lower():
            return False, k, {}
    ta, tb = a.get("trim"), b.get("trim")
    if ta and tb and str(ta).lower() != str(tb).lower():
        return False, "trim", {}
    from sniper import _norm_title
    title = _norm_title(a.get("title") or "")
    if not title or title != _norm_title(b.get("title") or ""):
        return False, "title", {}
    odo = a.get("odometer")
    if not odo or odo != b.get("odometer"):
        return False, "odometer", {}
    ca = (a.get("latitude"), a.get("longitude"))
    cb = (b.get("latitude"), b.get("longitude"))
    if None in ca or None in cb:
        return False, "location_unknown", {}
    from geo import haversine_mi
    dist = haversine_mi((float(ca[0]), float(ca[1])), (float(cb[0]), float(cb[1])))
    if dist > SAME_VEHICLE_MAX_MI:
        return False, "location", {}
    photo = photo_evidence(a, b)
    if photo is False:
        return False, "photo", {}
    if photo:
        identifying = "photo"
    elif int(odo) % 1000:
        identifying = "exact_odometer"
    else:
        return False, "no_identifying_detail", {}
    return True, "same_vehicle_no_vin", {
        "title": title, "year": a.get("year"), "odometer": int(odo),
        "distance_mi": round(dist, 1), "identifying": identifying,
        "sources": sorted([a.get("source"), b.get("source")])}
