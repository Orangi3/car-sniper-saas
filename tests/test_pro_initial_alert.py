"""
tests/test_pro_initial_alert.py — a subscriber with price-drop alerts (Pro)
no longer loses the first-match alert when the price falls before it was
delivered by less than the price-drop rule ($250 and 3%), or when the
price-drop alert that should replace it never reaches them
(notifications.deliver_job, jobqueue.Defer).

Before: the queued $8,100 alert was dropped as stale and, with no price-drop
event for a $100 drop, the Pro subscriber heard nothing, while a Lite
subscriber in the same situation got the alert at $8,000.

Now the original job decides only when it can. It waits, without spending
an attempt and for at most 30 minutes after it was queued, for the listing's
re-score and for any price-drop alert that re-score actually created for
this subscriber. A delivered price-drop alert covers the car, so the first
alert is not sent. Otherwise the subscriber's matching is re-run at the
current price and, if the car still qualifies, one ordinary first-match
alert goes out at that price. Pro keeps its price-drop alerts and
thresholds; Lite and Free are unchanged.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from tests.conftest import login, set_plan
from tests.test_matching_and_history import (L, _age_listing, make_user, q,  # noqa: F401
                                             score_all, world)
from tests.test_seller_listings import mail  # noqa: F401  (fixture)
from tests.test_send_time_check import _camry_buyer, _deliver, _notify_results, pushes  # noqa: F401

CID = "craigslist:s1"
DROP = "Price drop · 2015 Toyota Camry $8,100 → $7,400 (27% below market)"


def _titles(p):
    return [x["title"] for x in p["sent"]]


def _run_now():
    import db
    with db.session() as c:
        c.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00.000000+00:00' "
                  "WHERE status = 'pending'")


def _settle(rounds=4):
    """Run notifications and re-scores until nothing is left waiting."""
    for _ in range(rounds):
        _deliver()
        score_all()
        _run_now()
    _deliver()


def _pro(client, **f):
    return _camry_buyer(client, plan="pro", **f)


def _matched(world, price=8100, **kw):
    world.ingest([L("s1", price, **kw)])
    score_all()                                  # first match + its notify job queued


def _first_alert_result():
    """Result of the original first-match job (event 'new') for s1."""
    rows = q("SELECT dedupe_key, result FROM jobs WHERE kind = 'notify' ORDER BY id")
    (r,) = [x for x in rows if x["dedupe_key"].startswith(f"notify:{CID}:new:")]
    return json.loads(r["result"] or "null")


def _as_dt(v):
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


# ---------- Pro: the reproduced defect ---------------------------------------------

def test_pro_small_drop_before_delivery_sends_one_first_alert(client, world, pushes):
    uid = _pro(client)
    _matched(world)
    world.ingest([L("s1", 8000)])                # -$100: below the price-drop rule
    _deliver()                                   # the original job runs before the re-score
    _settle()
    assert _titles(pushes) == ["2015 Toyota Camry · $8,000 · 22% below market"]
    assert _first_alert_result()["first_alert_at_current_price"] == {"matched": 8100,
                                                                     "now": 8000}
    assert q("SELECT event, price FROM user_matches WHERE user_id = ?", (uid,)) \
        == [{"event": "new", "price": 8100}]     # history keeps the price at match time


def test_pro_several_small_drops_before_first_delivery(client, world, pushes):
    uid = _pro(client)
    _matched(world)
    for p in (7950, 7800, 7650):                 # -$150 each: no step is a price drop
        world.ingest([L("s1", p)])
    _settle()
    assert len(pushes["sent"]) == 1
    t = pushes["sent"][0]["title"]
    assert "$7,650" in t and "Price drop" not in t
    assert [m["event"] for m in q("SELECT event FROM user_matches WHERE user_id = ?",
                                  (uid,))] == ["new"]


# ---------- Pro: a real price-drop alert covers the car ----------------------------

def test_pro_material_drop_is_covered_by_its_price_drop_alert(client, world, pushes):
    """Original job first, re-score second: the original waits, the price-drop
    alert is delivered, and the first alert is not sent as well."""
    uid = _pro(client)
    _matched(world)
    world.ingest([L("s1", 7400)])
    _deliver()
    assert pushes["sent"] == []
    _settle()
    assert _titles(pushes) == [DROP]
    assert q("SELECT event, price FROM user_matches WHERE user_id = ? ORDER BY id", (uid,)) \
        == [{"event": "new", "price": 8100}, {"event": "price_drop", "price": 7400}]


def test_pro_material_drop_rescored_first_is_still_one_alert(client, world, pushes):
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 7400)])
    score_all()                                  # re-score first: price-drop alert queued
    _settle()
    assert _titles(pushes) == [DROP]


def test_pro_drop_while_the_rescore_is_running_is_not_sent_twice(client, world, pushes):
    """The re-score that may create the price-drop alert is in progress when
    the original job runs: the original must not decide until it has finished."""
    import jobqueue
    import notifications
    import sniper
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 7400)])
    job = jobqueue.claim("rescorer", kinds=["score"])   # claimed, not finished
    assert job and job["payload"]["composite_id"] == CID
    jobqueue.drain({"notify": notifications.deliver_job})
    assert pushes["sent"] == []
    jobqueue.complete(job["id"], "rescorer", sniper.score_job(job))
    _settle()
    assert _titles(pushes) == [DROP]


def test_pro_small_drop_after_delivery_sends_nothing_more(client, world, pushes):
    _pro(client)
    _matched(world)
    _deliver()
    world.ingest([L("s1", 8000)])
    _settle()
    assert _titles(pushes) == ["2015 Toyota Camry · $8,100 · 21% below market"]


def test_pro_material_drop_after_delivery_keeps_its_price_drop_alert(client, world, pushes):
    _pro(client)
    _matched(world)
    _deliver()
    world.ingest([L("s1", 7400)])
    _settle()
    assert _titles(pushes) == ["2015 Toyota Camry · $8,100 · 21% below market", DROP]


# ---------- Pro: the price-drop alert is suppressed or does not get through --------

def test_pro_search_that_excludes_price_drops_still_gets_its_first_alert(client, world,
                                                                         pushes):
    _pro(client, price_drops=False)
    _matched(world)
    world.ingest([L("s1", 7400)])
    _settle()
    assert _titles(pushes) == ["2015 Toyota Camry · $7,400 · 27% below market"]


def test_price_drop_alert_that_goes_stale_does_not_lose_the_car(client, world, pushes):
    """$8,100 -> $7,400 queues a price-drop alert; the price falls by another
    $100 before it is delivered, so that alert is dropped as stale. The car
    must still reach the subscriber, once."""
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 7400)])
    score_all()                                  # price-drop alert queued at $7,400
    world.ingest([L("s1", 7300)])                # then -$100: no new price-drop event
    _settle()
    assert len(pushes["sent"]) == 1
    t = pushes["sent"][0]["title"]
    assert "$7,300" in t and "Price drop" not in t


def test_price_drop_alert_that_fails_once_is_retried_not_duplicated(client, world, pushes):
    import requests
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 7400)])
    score_all()
    pushes["fail_next"].append(requests.ConnectionError("ntfy down"))
    _settle()
    assert _titles(pushes) == [DROP]


def test_daily_limit_reached_by_the_drop_still_delivers_the_counted_first_alert(
        client, world, pushes, monkeypatch):
    """The first match used today's only alert. The price-drop event is
    recorded but not queued (limit reached), so it covers nothing: the first
    alert goes out at the current price. Still one alert today."""
    monkeypatch.setenv("PLAN_LIMITS_JSON", json.dumps({"pro": {"alerts_per_day": 1}}))
    uid = _pro(client)
    _matched(world)
    world.ingest([L("s1", 7400)])
    _settle()
    assert _titles(pushes) == ["2015 Toyota Camry · $7,400 · 27% below market"]
    assert q("SELECT event, notified FROM user_matches WHERE user_id = ? ORDER BY id",
             (uid,)) == [{"event": "new", "notified": 1}, {"event": "price_drop", "notified": 0}]


# ---------- plans, customer settings, freshness, risk -------------------------------

def test_pro_downgraded_to_free_before_delivery_gets_nothing(client, world, pushes):
    _pro(client)
    _matched(world)
    set_plan("u@example.com", "free")
    world.ingest([L("s1", 8000)])
    _settle()
    assert pushes["sent"] == []
    assert any("not entitled" in (r or {}).get("skipped", "") for r in _notify_results())


def test_pro_downgraded_to_lite_gets_one_first_alert_and_no_price_drop(client, world,
                                                                       pushes):
    uid = _pro(client)
    _matched(world)
    set_plan("u@example.com", "starter")
    world.ingest([L("s1", 7400)])
    _settle()
    assert _titles(pushes) == ["2015 Toyota Camry · $7,400 · 27% below market"]
    assert [m["event"] for m in q("SELECT event FROM user_matches WHERE user_id = ?",
                                  (uid,))] == ["new"]


def test_disabled_channel_means_no_alert(client, world, pushes):
    _pro(client)
    _matched(world)
    login(client, "u@example.com")
    assert client.post("/api/me/alert_channels", json={"ntfy_topic": ""}).status_code == 200
    world.ingest([L("s1", 8000)])
    _settle()
    assert pushes["sent"] == []
    assert any("no longer configured" in (r or {}).get("skipped", "") for r in _notify_results())


def test_deleted_saved_search_means_no_alert(client, world, pushes):
    import db
    uid = _pro(client)
    _matched(world)
    with db.session() as c:
        c.execute("DELETE FROM saved_searches WHERE user_id = ?", (uid,))
    world.ingest([L("s1", 8000)])
    _settle()
    assert pushes["sent"] == []
    assert "no saved search matches" in _first_alert_result()["skipped"]


def test_stale_listing_is_not_sent(client, world, pushes):
    _pro(client)
    world.ingest([L("e1", 8100, source="ebay")])
    score_all()
    world.ingest([L("e1", 8000, source="ebay")])
    _age_listing("ebay:e1", 7)                   # not re-confirmed for 7 h
    _settle()
    assert pushes["sent"] == []


def test_withdrawn_seller_listing_is_not_sent(client, world, pushes, mail):
    from tests.test_seller_listings import car, seller
    make_user(client, "buyer@example.com", plan="pro", searches=[("camry", {"max_price": 9000})])
    seller(client, box=mail)
    sid = client.post("/api/seller/listings", json=car()).get_json()["id"]
    score_all()
    assert client.patch(f"/api/seller/listings/{sid}", json={"price": 6600}).status_code == 200
    assert client.delete(f"/api/seller/listings/{sid}").status_code == 200
    _settle()
    assert pushes["sent"] == []


def test_new_salvage_evidence_with_the_drop_is_not_sent(client, world, pushes):
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 8000, desc="rebuilt title, runs great")])
    _settle()
    assert pushes["sent"] == []


# ---------- reliability ------------------------------------------------------------

def test_two_workers_compete_for_the_first_alert(client, world, pushes):
    import jobqueue
    import notifications
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 8000)])
    score_all()                                  # the re-score has run: the job can decide
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


def test_retry_after_a_transient_failure_sends_at_the_current_price(client, world, pushes):
    import requests
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 8000)])
    score_all()
    pushes["fail_next"].append(requests.ConnectionError("ntfy down"))
    _deliver()                                   # attempt 1 fails
    world.ingest([L("s1", 7900)])                # another -$100 during the outage
    _settle()
    assert len(pushes["sent"]) == 1 and "$7,900" in pushes["sent"][0]["title"]


def test_cross_source_duplicate_postings_still_alert_once(client, world, pushes):
    vin = "1HGCM82633A004352"
    _pro(client)
    _matched(world, desc=f"VIN {vin} clean title")
    world.ingest([L("x9", 8200, source="ebay", odo=129000, desc=f"vin {vin}")])
    score_all()                                  # the same car elsewhere: suppressed
    world.ingest([L("s1", 8000, desc=f"VIN {vin} clean title")])
    _settle()
    assert len(pushes["sent"]) == 1 and "$8,000" in pushes["sent"][0]["title"]
    assert [r["reason"] for r in q("SELECT reason FROM alert_suppressions")] == ["same_vin"]


def test_tray_shows_the_price_at_match_and_the_current_price(client, world, pushes):
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 8000)])
    _settle()
    login(client, "u@example.com")
    (a,) = client.get("/api/me/alerts").get_json()
    assert (a["price"], a["matched_price"], a["current_price"], a["current_price_confirmed"]) \
        == (8100, 8100, 8000, True)


# ---------- the wait itself --------------------------------------------------------

def test_waiting_does_not_spend_an_attempt_or_look_like_an_error(client, world, pushes):
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 8000)])
    _deliver()                                   # the re-score has not run: the job waits
    (j,) = q("SELECT status, attempts, last_error, run_after FROM jobs WHERE kind = 'notify'")
    assert (j["status"], j["attempts"], j["last_error"]) == ("pending", 0, None)
    assert _as_dt(j["run_after"]) > datetime.now(timezone.utc)


def test_the_wait_is_bounded(client, world, pushes):
    """If the re-score still has not finished 30 minutes after the alert was
    queued, the original job stops waiting and is not sent, so no duplicate
    can follow; the reason is recorded."""
    import db
    _pro(client)
    _matched(world)
    world.ingest([L("s1", 8000)])
    with db.session() as c:
        c.execute("UPDATE jobs SET created_at = ? WHERE kind = 'notify'",
                  ((datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),))
    _deliver()                                   # the re-score is still pending
    assert pushes["sent"] == []
    assert "still unfinished after 30 min" in _first_alert_result()["skipped"]


def test_defer_is_fenced_to_the_worker_that_owns_the_job(client):
    import db
    import jobqueue
    with db.session() as c:
        jobqueue.enqueue(c, "notify", "defer-fence", {})
    job = jobqueue.claim("w1", kinds=["notify"])
    assert jobqueue.defer(job["id"], "w2", 10) == ""          # not w2's job
    assert jobqueue.defer(job["id"], "w1", 10) == "pending"
    assert q("SELECT status, attempts FROM jobs WHERE id = ?", (job["id"],)) \
        == [{"status": "pending", "attempts": 0}]
