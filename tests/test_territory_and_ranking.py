"""
tests/test_territory_and_ranking.py — ZIP/radius buying territories,
explainable opportunity ranking, and dealer-comparable handling.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import geo
import opportunity
import profit
import valuation as V
from tests.conftest import login
from tests.test_matching_and_history import L, make_user, q, score_all, world  # noqa: F401


def test_zip_centroids_and_distances():
    assert geo.valid_zip("35401") and not geo.valid_zip("00000")
    d_bham = geo.distance_mi("35401", *geo.zip_centroid("35203"))
    d_mobile = geo.distance_mi("35401", *geo.zip_centroid("36602"))
    assert 40 < d_bham < 60 and 150 < d_mobile < 200
    assert geo.distance_mi("35401", None, None) is None


def test_territory_api_validates(client, app):
    make_user(client, "t@example.com", topic=False)
    login(client, "t@example.com")
    assert client.post("/api/me/territory", json={"home_zip": "00000",
                                                  "radius_mi": 50}).status_code == 400
    assert client.post("/api/me/territory", json={"home_zip": "35401",
                                                  "radius_mi": 5000}).status_code == 400
    r = client.post("/api/me/territory", json={"home_zip": "35401", "radius_mi": 75})
    assert r.get_json() == {"home_zip": "35401", "radius_mi": 75}


def test_tuscaloosa_buyer_gets_birmingham_not_mobile(client, world):
    uid = make_user(client, "tusc@example.com", searches=[("c", {"model": "camry"})])
    login(client, "tusc@example.com")
    client.post("/api/me/territory", json={"home_zip": "35401", "radius_mi": 75})
    world.ingest([L("bham", 8100, zip="35203"),          # ~49 mi
                  L("mobile", 8000, zip="36602"),        # ~176 mi, same state
                  L("nowhere", 7900)])                   # no location
    score_all()
    got = {r["composite_id"] for r in q("SELECT composite_id FROM user_matches "
                                         "WHERE user_id = ?", (uid,))}
    assert got == {"craigslist:bham"}
    rows = {r["composite_id"]: r for r in
            q("SELECT composite_id, latitude, location_source FROM listings")}
    assert rows["craigslist:bham"]["location_source"] == "zip_centroid"
    assert rows["craigslist:nowhere"]["latitude"] is None      # never guessed


def test_search_can_override_territory_and_opt_into_unknown(client, world):
    uid = make_user(client, "o@example.com", searches=[
        ("coast", {"model": "camry", "zip": "36602", "radius_mi": 30,
                   "include_unknown_location": True})])
    world.ingest([L("bham", 8100, zip="35203"), L("mobile", 8000, zip="36602"),
                  L("nowhere", 7900)])
    score_all()
    got = {r["composite_id"] for r in q("SELECT composite_id FROM user_matches "
                                         "WHERE user_id = ?", (uid,))}
    assert got == {"craigslist:mobile", "craigslist:nowhere"}


def test_trusted_feed_respects_territory_and_shows_distance(client, world):
    make_user(client, "f@example.com")
    login(client, "f@example.com")
    client.post("/api/me/territory", json={"home_zip": "35401", "radius_mi": 75})
    world.ingest([L("bham", 8100, zip="35203"), L("mobile", 6500, zip="36602")])
    score_all()
    feed = client.get("/api/deals?feed=trusted").get_json()
    assert [d["composite_id"] for d in feed] == ["craigslist:bham"]
    assert 40 < feed[0]["distance_mi"] < 60
    assert feed[0]["opportunity"]["factors"]


# ---------- ranking -------------------------------------------------------

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def _deal(**kw):
    base = {"evidence_class": "asking_comparables", "confidence": "high",
            "risk_flags": [], "comp_n": 12, "price": 8000,
            "first_seen_at": (NOW - timedelta(hours=1)).isoformat()}
    return {**base, **kw}


def test_ranking_is_explainable_and_orders_sensibly():
    fresh_near = opportunity.score(_deal(), 2000, 20, now=NOW)
    old_far = opportunity.score(_deal(first_seen_at=(NOW - timedelta(days=5)).isoformat()),
                                2000, 180, now=NOW)
    risky = opportunity.score(_deal(risk_flags=["needs_work"]), 2000, 20, now=NOW)
    assert fresh_near["risk_adjusted_profit"] > old_far["risk_adjusted_profit"]
    assert fresh_near["risk_adjusted_profit"] > risky["risk_adjusted_profit"]
    names = [f["name"] for f in fresh_near["factors"]]
    assert names == ["evidence", "confidence", "condition", "freshness", "distance", "liquidity"]
    assert all(f["why"] for f in fresh_near["factors"])


def test_scam_zeroes_and_losses_are_not_inflated():
    assert opportunity.score(_deal(risk_flags=["scam_signals"]), 3000, 10,
                             now=NOW)["risk_adjusted_profit"] == 0
    assert opportunity.score(_deal(), -500, 10, now=NOW)["risk_adjusted_profit"] == -500
    assert opportunity.score(_deal(), None)["risk_adjusted_profit"] is None


# ---------- dealer comparables are not private-party proceeds -------------

def test_dealer_share_is_measured_and_discounted():
    now = datetime.now(timezone.utc).isoformat()
    peers = [{"composite_id": f"d{i}", "title": "2015 Toyota Camry", "price": 13000 + i * 50,
              "year": 2015, "make": "Toyota", "model": "Camry", "is_dealer": 1,
              "last_seen_at": now} for i in range(4)]
    peers.append({"composite_id": "p1", "title": "2015 Toyota Camry", "price": 9000,
                  "year": 2015, "make": "Toyota", "model": "Camry", "last_seen_at": now})
    v = V.value_listing({"composite_id": "t", "title": "2015 Toyota Camry", "price": 8000,
                         "year": 2015, "make": "Toyota", "model": "Camry"}, peers)
    assert v.dealer_share >= 0.6
    plain = profit.compute(8000, v.value_low, v.value_mid, v.value_high, v.evidence_class)
    adj = profit.compute(8000, v.value_low, v.value_mid, v.value_high, v.evidence_class,
                         dealer_share=v.dealer_share)
    assert adj["scenarios"]["base"]["resale"] < plain["scenarios"]["base"]["resale"]
    assert any("dealer retail" in x for x in adj["explanation"])
    assert any(a["key"] == "dealer_to_private_pct" for a in adj["assumptions"])


def test_distance_transport_cost_is_opt_in():
    p0 = profit.compute(8000, 9500, 10000, 10500, "confirmed_sales", None, distance_mi=100)
    p1 = profit.compute(8000, 9500, 10000, 10500, "confirmed_sales",
                        {"transport_per_mile": 0.5}, distance_mi=100)
    assert "transport_per_mile" not in p0["costs"]
    assert p1["costs"]["transport_per_mile"] == 100      # 2 × 100 mi × $0.50
