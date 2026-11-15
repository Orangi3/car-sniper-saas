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


# ---------- vehicle-level facts across postings ------------------------------
# A disclosed problem with the vehicle itself is true of every posting of that
# vehicle; a problem with a posting (scam wording, price, auction format,
# missing description, seller type, buyer reports) is not. Only these facts
# travel, and only between high-confidence postings of one car (same VIN, or
# same_vehicle above): listing flag -> (listings column, derived flag).
VEHICLE_FACTS = {"branded_title": ("is_salvage", "branded_title_other_posting"),
                 "needs_work": ("needs_work", "needs_work_other_posting")}
# A branded title is permanent. A mechanical problem can be repaired, so it
# travels only from a posting seen within the window alerting already treats
# as one offering (saved_search_alerts.CROSS_POST_WINDOW_DAYS).
CONDITION_MAX_AGE_DAYS = 14


def same_vehicle_postings(conn, listing: dict, flagged_only: bool = False) -> list:
    """[(row, basis)] for the other stored postings of this very vehicle:
    same VIN (basis "same_vin"), or the strict no-VIN identity of
    same_vehicle ("same_vehicle_no_vin"). flagged_only: only postings that
    disclose a vehicle-level fact."""
    cid = listing.get("composite_id")
    flagged = " AND (is_salvage OR needs_work)" if flagged_only else ""
    out, seen = [], set()
    if listing.get("vehicle_key"):
        for r in conn.execute("SELECT * FROM listings WHERE vehicle_key = ? "
                              "AND composite_id <> ?" + flagged,
                              (listing["vehicle_key"], cid)).fetchall():
            out.append((dict(r), "same_vin"))
            seen.add(r["composite_id"])
    if not (listing.get("odometer") and listing.get("year") and listing.get("make")
            and listing.get("model") and listing.get("source")):
        return out
    for r in conn.execute(
            "SELECT * FROM listings WHERE year = ? AND odometer = ? "
            "AND LOWER(make) = LOWER(?) AND LOWER(model) = LOWER(?) "
            "AND source <> ? AND composite_id <> ?" + flagged,
            (listing["year"], listing["odometer"], listing["make"], listing["model"],
             listing["source"], cid)).fetchall():
        row = dict(r)
        if row["composite_id"] not in seen and same_vehicle(listing, row)[0]:
            out.append((row, "same_vehicle_no_vin"))
            seen.add(row["composite_id"])
    return out


def _seen_within(row: dict, days: int, now) -> bool:
    from datetime import datetime, timedelta, timezone
    try:
        t = datetime.fromisoformat(str(row.get("last_seen_at") or row.get("first_seen_at"))
                                   .replace("Z", "+00:00"))
    except ValueError:
        return False
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - t <= timedelta(days=days)


def vehicle_risk_elsewhere(conn, listing: dict, now=None) -> list[dict]:
    """Vehicle-level facts disclosed on OTHER postings of this car and not on
    this one: [{"flag": derived flag, "fact": flag, "from": composite_id,
    "basis": "same_vin" | "same_vehicle_no_vin"}], one per fact."""
    out: dict[str, dict] = {}
    for row, basis in same_vehicle_postings(conn, listing, flagged_only=True):
        for fact, (col, derived) in VEHICLE_FACTS.items():
            if listing.get(col) or not row.get(col) or derived in out:
                continue
            if fact == "needs_work" and not _seen_within(row, CONDITION_MAX_AGE_DAYS, now):
                continue
            out[derived] = {"flag": derived, "fact": fact,
                            "from": row["composite_id"], "basis": basis}
    return list(out.values())
