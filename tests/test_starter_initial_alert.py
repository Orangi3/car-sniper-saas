"""
tests/test_starter_initial_alert.py — a subscriber without price-drop alerts
(Lite / "starter") still gets the first-match alert when the price falls
before it was delivered, at the current price (notifications.deliver_job,
saved_search_alerts.rematch_at_current_price).

Before: the queued alert stated the old price, so the send-time check dropped
it, and the plan's one-alert-per-car rule sent nothing at the lower price —
the subscriber heard nothing about a car that had become a better deal.
Now the same job re-runs that subscriber's matching at the current price —
current plan, saved searches, territory, listing freshness (stale, expired
or withdrawn: nothing), risk (price-dependent flags recomputed), duplicate
suppression — and, if the car still qualifies, sends
an ordinary first-match alert at the current price. It never creates a
price-drop event, a second match, or a second delivery after one succeeded;
Pro keeps receiving its own price-drop alerts instead.

The tray shows the price at match time and the current price, and says when
the listing is no longer confirmed by its source.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json
import threading

from tests.conftest import login, set_plan
from tests.test_matching_and_history import (L, _age_listing, make_user, q,  # noqa: F401
                                             score_all, world)
from tests.test_seller_listings import mail  # noqa: F401  (fixture)
from tests.test_send_time_check import _camry_buyer, _deliver, _notify_results, pushes  # noqa: F401

CID = "craigslist:s1"


def _titles(p):
    return [x["title"] for x in p["sent"]]


def _run_now():
    import db
    with db.session() as c:
        c.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00.000000+00:00' "
                  "WHERE status = 'pending'")


def _matched(world, price=8100, **kw):
    world.ingest([L("s1", price, **kw)])
    score_all()                                  # match + notify job queued


def test_starter_gets_the_first_alert_at_the_lower_price(client, world, pushes):
    uid = _camry_buyer(client)
    _matched(world)
    world.ingest([L("s1", 7400)])                # cheaper before delivery
    _deliver()
    assert _titles(pushes) == ["2015 Toyota Camry · $7,400 · 27% below market"]
    (res,) = _notify_results()
    assert res["first_alert_at_current_price"] == {"matched": 8100, "now": 7400}
    # the match event and its price at match time are kept as they were
    assert q("SELECT event, price FROM user_matches WHERE user_id = ?", (uid,)) \
        == [{"event": "new", "price": 8100}]
    score_all()                                  # the re-score at $7,400 ...
    _deliver()
    assert len(pushes["sent"]) == 1              # ... sends nothing more


def test_no_second_alert_after_delivery_when_the_price_drops_later(client, world, pushes):
    _camry_buyer(client)
    _matched(world)
    _deliver()
    assert len(pushes["sent"]) == 1              # delivered at $8,100
    world.ingest([L("s1", 7400)])
    score_all()
    _deliver()
    assert len(pushes["sent"]) == 1              # no price-drop feature on Lite


def test_several_drops_before_delivery_send_one_alert_at_the_latest_price(client, world,
                                                                          pushes):
    _camry_buyer(client)
    _matched(world)
    world.ingest([L("s1", 7800)])
    world.ingest([L("s1", 7400)])
    _deliver()
    score_all()
    _deliver()
    assert _titles(pushes) == ["2015 Toyota Camry · $7,400 · 27% below market"]


def test_pro_still_gets_its_price_drop_alert_not_a_refreshed_first_alert(client, world,
                                                                         pushes):
    _camry_buyer(client, plan="pro")
    _matched(world)
    world.ingest([L("s1", 7400)])
    _deliver()
    score_all()
    _deliver()
    assert _titles(pushes) == ["Price drop · 2015 Toyota Camry $8,100 → $7,400 "
                               "(27% below market)"]


def test_free_and_downgraded_subscribers_get_nothing(client, world, pushes):
    _camry_buyer(client)
    _matched(world)
    set_plan("u@example.com", "free")            # downgraded before delivery
    world.ingest([L("s1", 7400)])
    _deliver()
    score_all()
    _deliver()
    assert pushes["sent"] == []
    assert any("not entitled" in (r or {}).get("skipped", "") for r in _notify_results())


def test_deleted_saved_search_means_no_alert(client, world, pushes):
    import db
    uid = _camry_buyer(client)
    _matched(world)
    with db.session() as c:
        c.execute("DELETE FROM saved_searches WHERE user_id = ?", (uid,))
    world.ingest([L("s1", 7400)])
    _deliver()
    assert pushes["sent"] == []
    assert "no saved search matches" in _notify_results()[0]["skipped"]


def test_search_that_no_longer_matches_at_the_new_price(client, world, pushes):
    """A minimum-price filter the car falls below is respected."""
    _camry_buyer(client, min_price=7600)
    _matched(world)
    world.ingest([L("s1", 7400)])
    _deliver()
    assert pushes["sent"] == []


def test_stale_listing_is_not_sent(client, world, pushes):
    _camry_buyer(client)
    world.ingest([L("e1", 8100, source="ebay")])
    score_all()
    world.ingest([L("e1", 7400, source="ebay")])
    _age_listing("ebay:e1", 7)                   # not re-confirmed for 7 h
    _deliver()
    assert pushes["sent"] == []


def _seller_car_matched(client, mail):
    from tests.test_seller_listings import car, seller
    make_user(client, "buyer@example.com", searches=[("camry", {"max_price": 9000})])
    seller(client, box=mail)
    sid = client.post("/api/seller/listings", json=car()).get_json()["id"]
    score_all()                                  # matched at $6,800; first alert queued
    r = client.patch(f"/api/seller/listings/{sid}", json={"price": 6600})
    assert r.status_code == 200 and r.get_json()["price_changed"]   # cheaper before delivery
    return sid


def test_expired_seller_listing_is_not_sent_at_the_lower_price(client, world, pushes, mail):
    """The re-match itself refuses a listing that is no longer live."""
    from datetime import datetime, timedelta, timezone
    import db
    _seller_car_matched(client, mail)
    with db.session() as c:                      # not re-confirmed in time: expired
        c.execute("UPDATE seller_listings SET last_confirmed_at = ?",
                  ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),))
    _deliver()
    assert pushes["sent"] == []
    (res,) = [x for x in _notify_results() if x]
    assert "not re-sent at the current price: seller listing is not live" in res["skipped"]


def test_withdrawn_seller_listing_is_not_sent(client, world, pushes, mail):
    sid = _seller_car_matched(client, mail)
    assert client.delete(f"/api/seller/listings/{sid}").status_code == 200
    _deliver()
    assert pushes["sent"] == []
    assert q("SELECT 1 FROM jobs WHERE kind = 'notify'") == []    # its queued alert went too


def test_new_salvage_evidence_with_the_drop_is_not_sent(client, world, pushes):
    _camry_buyer(client)
    _matched(world)
    world.ingest([L("s1", 7400, desc="rebuilt title, runs great")])
    _deliver()
    score_all()
    _deliver()
    assert pushes["sent"] == []


def test_two_workers_compete_for_the_job(client, world, pushes):
    import jobqueue
    import notifications
    _camry_buyer(client)
    _matched(world)
    world.ingest([L("s1", 7400)])
    results, barrier = [], threading.Barrier(2)

    def work(name):
        barrier.wait()
        results.append(jobqueue.run_one({"notify": notifications.deliver_job}, name))
    threads = [threading.Thread(target=work, args=(f"worker-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r is None for r in results) == [False, True]   # one claimed it
    assert len(pushes["sent"]) == 1


def test_provider_failure_retries_at_the_current_price(client, world, pushes):
    import requests
    _camry_buyer(client)
    _matched(world)
    world.ingest([L("s1", 7400)])
    pushes["fail_next"].append(requests.ConnectionError("ntfy down"))
    _deliver()                                   # attempt 1 fails
    world.ingest([L("s1", 7200)])                # cheaper again during the outage
    _run_now()
    _deliver()
    assert _titles(pushes) == ["2015 Toyota Camry · $7,200 · 29% below market"]


def test_cross_source_duplicate_suppression_still_holds(client, world, pushes):
    vin = "1HGCM82633A004352"
    _camry_buyer(client)
    _matched(world, desc=f"VIN {vin} clean title")
    world.ingest([L("x9", 8200, source="ebay", odo=129000, desc=f"vin {vin}")])
    score_all()                                  # the same car elsewhere: suppressed
    world.ingest([L("s1", 7400, desc=f"VIN {vin} clean title")])
    _deliver()
    score_all()
    _deliver()
    assert len(pushes["sent"]) == 1 and "$7,400" in pushes["sent"][0]["title"]
    assert [r["reason"] for r in q("SELECT reason FROM alert_suppressions")] == ["same_vin"]


def test_tray_shows_matched_and_current_price(client, world, pushes):
    _camry_buyer(client)
    _matched(world)
    world.ingest([L("s1", 7400)])
    _deliver()
    world.ingest([L("e2", 8000, source="ebay", odo=128000)])
    score_all()
    _age_listing("ebay:e2", 7)
    login(client, "u@example.com")
    tray = {a["composite_id"]: a for a in client.get("/api/me/alerts").get_json()}
    s = tray[CID]
    assert (s["price"], s["matched_price"], s["current_price"], s["current_price_confirmed"]) \
        == (8100, 8100, 7400, True)
    e = tray["ebay:e2"]
    assert e["current_price_confirmed"] is False and e["current_price"] is None


def test_daily_alert_limit_still_applies(client, world, pushes, monkeypatch):
    monkeypatch.setenv("PLAN_LIMITS_JSON", json.dumps({"starter": {"alerts_per_day": 1}}))
    uid = _camry_buyer(client)
    world.ingest([L("s1", 8100, odo=120000), L("s2", 8000, odo=125000)])
    score_all()                                  # s2 over the cap: not notified
    world.ingest([L("s1", 7400, odo=120000), L("s2", 7300, odo=125000)])
    _deliver()
    score_all()
    _deliver()
    assert len(pushes["sent"]) == 1 and "$7,400" in pushes["sent"][0]["title"]
    assert [m["notified"] for m in q("SELECT notified FROM user_matches WHERE user_id = ? "
                                     "ORDER BY id", (uid,))] == [1, 0]
