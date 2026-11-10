"""
tests/test_cross_source_identity.py — one subscriber, one alert per car, even
when the car is posted on two marketplaces with no usable VIN
(vehicle_identity.same_vehicle, saved_search_alerts.evaluate).

Alert-level only: both listings stay stored, valued and visible; relist
history is untouched. The bar for "same car" without a VIN is deliberately
high — a missed duplicate costs one extra alert, a false match hides a real
car — and every suppression is recorded in alert_suppressions with its
reason and both listing ids.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tests.test_matching_and_history import L, make_user, q, score_all, world  # noqa: F401
from tests.test_send_time_check import _camry_buyer, _deliver, pushes  # noqa: F401

VIN_A = "1HGCM82633A004352"
VIN_B = "1M8GDM9AXKP042788"
EXACT = 131457                     # an exact odometer reading identifies a car
ROUND = 130000                     # "130k" does not


def _car(sid, source, price=8100, odo=EXACT, zip_code="35401",
         title="2015 Toyota Camry SE", desc="clean title, runs great", **kw):
    return L(sid, price, title=title, odo=odo, source=source, desc=desc, zip=zip_code, **kw)


def _post_both(world, a, b):
    world.ingest([a])
    score_all()
    world.ingest([b])
    score_all()
    _deliver()


def _alerts(uid):
    return len(q("SELECT 1 FROM user_matches WHERE user_id = ?", (uid,)))


def _suppressions(uid=None):
    sql = "SELECT user_id, composite_id, duplicate_of, reason, evidence FROM alert_suppressions"
    rows = q(sql + (" WHERE user_id = ?" if uid else "") + " ORDER BY id", (uid,) if uid else ())
    return rows


# ---------- positive ------------------------------------------------------

def test_same_car_on_two_marketplaces_without_vin_alerts_once(client, world, pushes):
    uid = _camry_buyer(client)
    _post_both(world, _car("cl-1", "craigslist"),
               _car("fb-1", "marketcheck_fsbo", price=8300, zip_code="35404"))
    assert _alerts(uid) == 1 and len(pushes["sent"]) == 1
    (s,) = _suppressions(uid)
    assert (s["composite_id"], s["duplicate_of"], s["reason"]) == \
        ("marketcheck_fsbo:fb-1", "craigslist:cl-1", "same_vehicle_no_vin")
    ev = json.loads(s["evidence"])
    assert ev["odometer"] == EXACT and ev["identifying"] == "exact_odometer"
    assert ev["distance_mi"] < 10
    # alert-level only: both listings stay stored, valued and unlinked
    rows = q("SELECT composite_id, relist_of FROM listings WHERE composite_id IN "
             "('craigslist:cl-1', 'marketcheck_fsbo:fb-1') ORDER BY composite_id")
    assert [r["relist_of"] for r in rows] == [None, None]
    assert len(q("SELECT 1 FROM near_misses WHERE composite_id = 'marketcheck_fsbo:fb-1'")) == 1


def test_matching_photo_fingerprint_identifies_a_round_odometer_car(client, world, pushes):
    uid = _camry_buyer(client)
    _post_both(world, _car("cl-2", "craigslist", odo=ROUND, extras={"photo_hash": "ph-77"}),
               _car("fb-2", "marketcheck_fsbo", odo=ROUND, extras={"photo_hash": "ph-77"}))
    assert _alerts(uid) == 1 and len(pushes["sent"]) == 1
    assert json.loads(_suppressions(uid)[0]["evidence"])["identifying"] == "photo"


def test_vin_dedupe_is_unchanged_and_now_recorded(client, world, pushes):
    uid = _camry_buyer(client)
    _post_both(world, _car("cl-3", "craigslist", desc=f"VIN {VIN_A} clean title"),
               _car("eb-3", "ebay", odo=99000, zip_code="35203", desc=f"vin {VIN_A}"))
    assert _alerts(uid) == 1 and len(pushes["sent"]) == 1
    assert _suppressions(uid)[0]["reason"] == "same_vin"


def test_a_buyer_never_alerted_to_the_first_posting_gets_the_second(client, world, pushes):
    first = _camry_buyer(client, "first@example.com")
    late = make_user(client, "late@example.com",
                     searches=[("cheap camry", {"model": "camry", "max_price": 8000})])
    _post_both(world, _car("cl-4", "craigslist", price=8100),
               _car("fb-4", "marketcheck_fsbo", price=7900, zip_code="35404"))
    assert _alerts(first) == 1 and _alerts(late) == 1     # entitlements per buyer
    assert len(pushes["sent"]) == 2
    assert [s["user_id"] for s in _suppressions()] == [first]


# ---------- negative: never suppress on weak evidence ------------------------

def _assert_two_alerts(client, world, pushes, a, b):
    uid = _camry_buyer(client)
    _post_both(world, a, b)
    assert _alerts(uid) == 2 and len(pushes["sent"]) == 2
    assert _suppressions(uid) == []


def test_same_model_different_trim(client, world, pushes):
    # LE comparables, so the LE car is alertable on its own merits
    world.ingest([L(f"le{i}", p, title="2015 Toyota Camry LE", odo=110000 + 9000 * i)
                  for i, p in enumerate((9900, 10100, 10300, 9800, 10000))])
    score_all()
    _assert_two_alerts(client, world, pushes, _car("cl-5", "craigslist"),
                       _car("fb-5", "marketcheck_fsbo", title="2015 Toyota Camry LE"))


def test_same_title_different_mileage(client, world, pushes):
    _assert_two_alerts(client, world, pushes, _car("cl-6", "craigslist"),
                       _car("fb-6", "marketcheck_fsbo", odo=EXACT + 443))


def test_missing_mileage(client, world, pushes):
    _assert_two_alerts(client, world, pushes, _car("cl-7", "craigslist"),
                       _car("fb-7", "marketcheck_fsbo", odo=None))


def test_different_first_photo(client, world, pushes):
    _assert_two_alerts(client, world, pushes,
                       _car("cl-8", "craigslist", extras={"photo_hash": "ph-1"}),
                       _car("fb-8", "marketcheck_fsbo", extras={"photo_hash": "ph-2"}))


def test_different_first_photo_on_the_same_host(client, world, pushes):
    _assert_two_alerts(
        client, world, pushes,
        _car("cl-9", "craigslist", image_urls=["https://img.example.test/a/1.jpg"]),
        _car("fb-9", "marketcheck_fsbo", image_urls=["https://img.example.test/b/7.jpg"]))


def test_conflicting_vins(client, world, pushes):
    _assert_two_alerts(client, world, pushes,
                       _car("cl-10", "craigslist", desc=f"VIN {VIN_A} clean title"),
                       _car("fb-10", "marketcheck_fsbo", desc=f"VIN {VIN_B} clean title"))


def test_two_distinct_cars_with_otherwise_similar_details(client, world, pushes):
    """Same title, same rounded "130k", same town, no photo evidence: two
    sellers with the same popular car is the ordinary case, not a duplicate."""
    _assert_two_alerts(client, world, pushes, _car("cl-11", "craigslist", odo=ROUND),
                       _car("fb-11", "marketcheck_fsbo", odo=ROUND, zip_code="35404"))


def test_far_apart_locations(client, world, pushes):
    _assert_two_alerts(client, world, pushes, _car("cl-12", "craigslist"),
                       _car("fb-12", "marketcheck_fsbo", zip_code="35203"))  # Birmingham


def test_unknown_location(client, world, pushes):
    _assert_two_alerts(client, world, pushes, _car("cl-13", "craigslist"),
                       _car("fb-13", "marketcheck_fsbo", zip_code=None))


def test_same_source_duplicates_keep_their_behavior(client, world, pushes):
    _assert_two_alerts(client, world, pushes, _car("x-14", "craigslist"),
                       _car("y-14", "craigslist"))


def test_suppression_window_is_unchanged(client, world, pushes):
    """An earlier alert older than CROSS_POST_WINDOW_DAYS does not suppress."""
    import db
    uid = _camry_buyer(client)
    world.ingest([_car("cl-15", "craigslist")])
    score_all()
    with db.session() as c:
        c.execute("UPDATE user_matches SET created_at = ? WHERE user_id = ?",
                  ((datetime.now(timezone.utc) - timedelta(days=15)).isoformat(), uid))
    world.ingest([_car("fb-15", "marketcheck_fsbo", zip_code="35404")])
    score_all()
    assert _alerts(uid) == 2 and _suppressions(uid) == []


def test_ops_counts_suppressed_duplicates_by_reason(client, world, pushes):
    import db
    import ops
    _camry_buyer(client)
    _post_both(world, _car("cl-16", "craigslist"),
               _car("fb-16", "marketcheck_fsbo", zip_code="35404"))
    with db.session() as c:
        assert ops.deal_intelligence(c)["duplicates_suppressed"] == {"same_vehicle_no_vin": 1}


# ---------- the policy itself (pure function) ---------------------------------

def _row(**kw):
    base = {"source": "craigslist", "year": 2015, "make": "Toyota", "model": "Camry",
            "trim": "SE", "title": "2015 Toyota Camry SE - $8,100", "odometer": EXACT,
            "latitude": 33.1969, "longitude": -87.5627, "vehicle_key": None,
            "image_urls": "[]", "extras": "{}", "price": 8100}
    return {**base, **kw}


def test_policy_one_sided_vin_falls_back_to_attributes(world):
    import vehicle_identity as vi
    same, reason, _ = vi.same_vehicle(_row(vehicle_key=f"vin:{VIN_A}"),
                                      _row(source="ebay", title="2015 Toyota Camry SE - $7,900"))
    assert (same, reason) == (True, "same_vehicle_no_vin")


def test_policy_ignores_price_and_cross_host_photo_urls(world):
    import vehicle_identity as vi
    a = _row(image_urls=json.dumps(["https://images.craigslist.test/1.jpg"]), price=8100)
    b = _row(source="ebay", price=9400, title="2015 Toyota Camry SE - $9,400",
             image_urls=json.dumps(["https://i.ebayimg.test/zz.jpg"]))
    assert vi.photo_evidence(a, b) is None                 # different hosts: no evidence
    assert vi.same_vehicle(a, b)[:2] == (True, "same_vehicle_no_vin")


def test_policy_reasons_for_refusal(world):
    import vehicle_identity as vi
    cases = {
        "different_vin": (_row(vehicle_key="vin:A"), _row(source="ebay", vehicle_key="vin:B")),
        "same_source": (_row(), _row()),
        "trim": (_row(), _row(source="ebay", trim="LE")),
        "odometer": (_row(), _row(source="ebay", odometer=None)),
        "location_unknown": (_row(), _row(source="ebay", latitude=None)),
        "no_identifying_detail": (_row(odometer=ROUND), _row(source="ebay", odometer=ROUND)),
    }
    for want, (a, b) in cases.items():
        assert vi.same_vehicle(a, b)[:2] == (False, want), want
