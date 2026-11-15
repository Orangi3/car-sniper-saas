"""
tests/test_send_time_check.py — a queued deal alert must still be true when
it is sent (notifications.stale_reason, called by deliver_job).

A queued alert is a claim about one listing at one asking price. Jobs can
wait: owner quiet hours, retries after a provider outage, a worker that was
down, an operator re-queueing dead jobs — and notify jobs run before score
jobs. At send time the alert is dropped (job done, result "skipped: stale:
...") when the listing is gone, may no longer be shown (eBay: not
re-confirmed within 6 h; ended auction; seller listing not live), its asking
price changed (the re-score at the new price decides what to send next), it
now carries a blocking risk flag it was not based on, or a valuation at this
price no longer rates it trusted.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json

import pytest

from tests.test_matching_and_history import (L, _age_listing, make_user, q,  # noqa: F401
                                             score_all, user_jobs, world)
from tests.test_seller_listings import mail  # noqa: F401  (fixture)


def _deliver():
    import jobqueue
    import notifications
    return jobqueue.drain({"notify": notifications.deliver_job})


def _notify_results():
    return [json.loads(r["result"] or "null") for r in
            q("SELECT result FROM jobs WHERE kind = 'notify' ORDER BY id")]


@pytest.fixture()
def pushes(monkeypatch):
    """Every outbound HTTP push is recorded instead of sent. Put an exception
    in `fail_next` to make the next send fail like a provider outage."""
    import notifications

    class R:
        status_code = 200
    state = {"sent": [], "fail_next": []}

    def post(url, **kw):
        if state["fail_next"]:
            raise state["fail_next"].pop(0)
        state["sent"].append(kw.get("json"))
        return R()
    monkeypatch.setattr(notifications.requests, "post", post)
    return state


def _camry_buyer(client, email="u@example.com", plan="starter", **f):
    return make_user(client, email, plan=plan,
                     searches=[("camry", {"model": "camry", **f})])




def test_unchanged_listing_is_delivered_once(client, world, pushes):
    uid = _camry_buyer(client)
    world.ingest([L("ok", 8100)])
    score_all()
    assert len(user_jobs(uid)) == 1
    _deliver()
    assert len(pushes["sent"]) == 1
    assert "$8,100" in pushes["sent"][0]["title"]
    assert _notify_results() == [{"sent": "ntfy 200"}]


def test_price_raised_after_alert_queued_is_not_sent(client, world, pushes):
    uid = _camry_buyer(client)
    world.ingest([L("up", 8100)])
    score_all()                                   # match + notify job queued
    assert len(user_jobs(uid)) == 1
    world.ingest([L("up", 9900)])                 # seller raised the price first
    _deliver()                                    # notify runs before the re-score
    assert pushes["sent"] == []
    (res,) = _notify_results()
    assert res["skipped"].startswith("stale:") and "$8,100" in res["skipped"] \
        and "$9,900" in res["skipped"]


def test_price_lowered_after_alert_queued_sends_the_fresh_alert_instead(
        client, world, pushes):
    """The stale $8,100 claim is dropped; the re-score at $7,400 decides what
    goes out — for a plan with price-drop alerts, a price-drop alert."""
    uid = _camry_buyer(client, plan="pro")
    world.ingest([L("down", 8100)])
    score_all()
    world.ingest([L("down", 7400)])
    _deliver()
    assert pushes["sent"] == []                   # the $8,100 claim is gone
    score_all()                                   # re-score at the new price
    _deliver()
    assert len(pushes["sent"]) == 1
    assert "Price drop" in pushes["sent"][0]["title"] \
        and "$7,400" in pushes["sent"][0]["title"]
    events = q("SELECT event, price FROM user_matches WHERE user_id = ? ORDER BY id", (uid,))
    assert events == [{"event": "new", "price": 8100},
                      {"event": "price_drop", "price": 7400}]


def test_ebay_listing_not_reconfirmed_within_6h_is_not_sent(client, world, pushes):
    """eBay API License Agreement: displayed listing data at most 6 h old.
    An alert is a display — the deal feed already hides such listings."""
    uid = _camry_buyer(client)
    world.ingest([L("e1", 8100, source="ebay")])
    score_all()
    assert len(user_jobs(uid)) == 1
    _age_listing("ebay:e1", 7)                    # worker was down for 7 h
    _deliver()
    assert pushes["sent"] == []
    (res,) = _notify_results()
    assert res["skipped"].startswith("stale:") and "6 h" in res["skipped"]


def test_ended_auction_is_not_sent(client, world, pushes):
    _camry_buyer(client, plan="pro", risk_mode="aggressive")
    import db
    from datetime import datetime, timedelta, timezone
    world.ingest([L("au", 8100, is_auction=True,
                    auction_end_at=datetime.now(timezone.utc) + timedelta(hours=1))])
    score_all()
    assert q("SELECT 1 FROM jobs WHERE kind = 'notify'")
    with db.session() as c:
        c.execute("UPDATE listings SET auction_end_at = ? WHERE composite_id = ?",
                  ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                   "craigslist:au"))
    _deliver()
    assert pushes["sent"] == []
    assert "auction has ended" in _notify_results()[0]["skipped"]


def test_deleted_listing_is_not_sent(client, world, pushes):
    _camry_buyer(client)
    world.ingest([L("gone", 8100)])
    score_all()
    import db
    with db.session() as c:
        for t in ("user_matches", "near_misses", "listing_prices", "listings"):
            c.execute(f"DELETE FROM {t} WHERE composite_id = 'craigslist:gone'")
    _deliver()
    assert pushes["sent"] == []
    assert "no longer exists" in _notify_results()[0]["skipped"]


def test_rescored_to_risk_at_same_price_is_not_sent(client, world, pushes):
    """A re-valuation of the same price that no longer rates the car trusted
    (here: confidence fell when comparables went stale) stops the alert."""
    _camry_buyer(client)
    world.ingest([L("rs", 8100)])
    score_all()
    import db
    with db.session() as c:
        c.execute("UPDATE near_misses SET trust = 'risk', confidence = 'low', "
                  "risk_flags = ? WHERE composite_id = 'craigslist:rs'",
                  (json.dumps(["low_confidence"]),))
    _deliver()
    assert pushes["sent"] == []
    assert "no longer rated trusted" in _notify_results()[0]["skipped"]


def test_retry_after_outage_rechecks_the_listing(client, world, pushes):
    import requests
    _camry_buyer(client)
    world.ingest([L("rt", 8100)])
    score_all()
    pushes["fail_next"].append(requests.ConnectionError("ntfy down"))
    _deliver()                                    # attempt 1 fails, retry scheduled
    job = q("SELECT status, attempts FROM jobs WHERE kind = 'notify'")[0]
    assert job == {"status": "pending", "attempts": 1}
    world.ingest([L("rt", 9500)])                 # price raised during the outage
    import db
    with db.session() as c:
        c.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00.000000+00:00' "
                  "WHERE kind = 'notify'")
    _deliver()
    assert pushes["sent"] == []
    job = q("SELECT status, result FROM jobs WHERE kind = 'notify'")[0]
    assert job["status"] == "done" and "stale:" in json.loads(job["result"])["skipped"]


def test_retry_after_outage_still_delivers_an_unchanged_listing(client, world, pushes):
    import requests
    _camry_buyer(client)
    world.ingest([L("rt2", 8100)])
    score_all()
    pushes["fail_next"].append(requests.ConnectionError("ntfy down"))
    _deliver()
    import db
    with db.session() as c:
        c.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00.000000+00:00' "
                  "WHERE kind = 'notify'")
    _deliver()
    assert len(pushes["sent"]) == 1
    assert q("SELECT status FROM jobs WHERE kind = 'notify'") == [{"status": "done"}]


def test_owner_alert_deferred_by_quiet_hours_is_rechecked(client, world, pushes,
                                                         monkeypatch):
    """Owner alerts wait for the end of quiet hours — up to a whole night."""
    from datetime import datetime
    monkeypatch.setenv("NTFY_TOPIC", "sniper-owner-topic-123")
    h = datetime.now().astimezone().hour
    monkeypatch.setitem(world.CONFIG, "quiet_start_hour", h)
    monkeypatch.setitem(world.CONFIG, "quiet_end_hour", (h + 2) % 24)
    world.ingest([L("night", 6900)])              # ~32% under: clears the owner bar
    score_all()
    assert len(q("SELECT 1 FROM jobs WHERE kind = 'notify' AND user_id IS NULL")) == 1
    assert _deliver() == []                       # held until quiet hours end
    world.ingest([L("night", 9900)])              # changed overnight
    import db
    with db.session() as c:                       # ... and now quiet hours are over
        c.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00.000000+00:00' "
                  "WHERE kind = 'notify'")
    _deliver()
    assert pushes["sent"] == []
    assert _notify_results()[0]["skipped"].startswith("stale:")


def test_jobs_queued_before_this_change_are_still_checked(client, world, pushes):
    """A job without the recorded alert basis (queued by an older release)
    still gets the existence, display and price checks."""
    _camry_buyer(client)
    world.ingest([L("legacy", 8100)])
    score_all()
    import db
    with db.session() as c:
        (jid, payload), = [(r[0], r[1]) for r in c.execute(
            "SELECT id, payload FROM jobs WHERE kind = 'notify'").fetchall()]
        p = json.loads(payload)
        p.pop("basis", None)
        c.execute("UPDATE jobs SET payload = ? WHERE id = ?", (json.dumps(p), jid))
    world.ingest([L("legacy", 9000)])
    _deliver()
    assert pushes["sent"] == []
    assert _notify_results()[0]["skipped"].startswith("stale:")


def test_seller_listing_no_longer_discoverable_is_not_sent(client, world, pushes, mail):
    """A FlipFirst seller's car is advertised only while the listing is live:
    expired (not re-confirmed in CONFIRM_DAYS) or contact unverified means no
    alert carries it, even one queued earlier."""
    from datetime import datetime, timedelta, timezone
    from tests.test_seller_listings import car, seller
    buyer = make_user(client, "buyer@example.com", searches=[("camry", {"max_price": 9000})])
    seller(client, box=mail)
    r = client.post("/api/seller/listings", json=car()).get_json()
    score_all()
    assert user_jobs(buyer), "the live listing was matched and queued"
    import db
    with db.session() as c:
        c.execute("UPDATE seller_listings SET last_confirmed_at = ?",
                  ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),))
    _deliver()
    assert pushes["sent"] == []
    res = [x for x in _notify_results() if x]
    assert res and all("seller listing is not live" in x["skipped"] for x in res)
    assert r["composite_id"]


def test_ops_counts_suppressed_alerts_as_not_sent(client, world, pushes):
    import ops
    _camry_buyer(client)
    world.ingest([L("o1", 8100), L("o2", 8000, odo=125000)])
    score_all()
    world.ingest([L("o2", 9800, odo=125000)])     # o2 changes before delivery
    _deliver()
    assert len(pushes["sent"]) == 1
    import db
    with db.session() as c:
        nt = ops.deal_intelligence(c)["notifications"]
    assert nt["delivered_to_provider"] == 1
    assert nt["not_sent"] == 1 and nt["stale_suppressed"] == 1


def test_alert_basis_is_kept_with_the_job_and_never_sent(client, world, pushes):
    uid = _camry_buyer(client)
    world.ingest([L("b1", 8100)])
    score_all()
    (job,) = user_jobs(uid)
    assert job["basis"] == {"trust": "trusted", "blocking_flags": []}
    assert "trust" not in job["deal"] and "risk_flags" not in job["deal"]
    _deliver()
    assert len(pushes["sent"]) == 1
    sent = json.dumps(pushes["sent"])
    assert "blocking_flags" not in sent and "trusted" not in sent
