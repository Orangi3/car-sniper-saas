"""
tests/test_send_time_robustness.py — the send-time check must not drop a
legitimate alert by misreading a timestamp, by missing optional data, or
because the database hiccupped (review of 34a0042).

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.test_matching_and_history import L, q, score_all, world  # noqa: F401
from tests.test_send_time_check import (_camry_buyer, _deliver,  # noqa: F401
                                        _notify_results, pushes)


def _set_last_seen(cid, value):
    import db
    with db.session() as c:
        c.execute("UPDATE listings SET last_seen_at = ? WHERE composite_id = ?", (value, cid))


def _run_now():
    import db
    with db.session() as c:
        c.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00.000000+00:00' "
                  "WHERE status = 'pending'")


def test_database_error_during_the_check_retries_instead_of_dropping(client, world, pushes,
                                                                     monkeypatch):
    import notifications
    _camry_buyer(client)
    world.ingest([L("db1", 8100)])
    score_all()
    real, calls = notifications.stale_reason, {"n": 0}

    def flaky(conn, deal, basis):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("server closed the connection unexpectedly")
        return real(conn, deal, basis)
    monkeypatch.setattr(notifications, "stale_reason", flaky)
    _deliver()
    job = q("SELECT status, result FROM jobs WHERE kind = 'notify'")[0]
    assert job == {"status": "pending", "result": None}      # retried, not "skipped"
    _run_now()
    _deliver()
    assert len(pushes["sent"]) == 1
    assert _notify_results() == [{"sent": "ntfy 200"}]


def test_non_utc_and_naive_timestamps_are_read_correctly(client, world, pushes):
    """eBay's 6 h display limit: a confirmation 1 h ago written with a -06:00
    offset (as a Postgres session in that zone returns it) or without any
    zone (read as UTC) is fresh, not stale."""
    _camry_buyer(client)
    world.ingest([L("tz1", 8100, source="ebay"), L("tz2", 8050, source="ebay", odo=129000)])
    score_all()
    one_h = datetime.now(timezone.utc) - timedelta(hours=1)
    _set_last_seen("ebay:tz1", one_h.astimezone(timezone(timedelta(hours=-6))).isoformat())
    _set_last_seen("ebay:tz2", one_h.replace(tzinfo=None).isoformat())
    _deliver()
    assert len(pushes["sent"]) == 2


def test_six_hour_boundary(client, world, pushes):
    _camry_buyer(client)
    world.ingest([L("b1", 8100, source="ebay"), L("b2", 8050, source="ebay", odo=129000)])
    score_all()
    now = datetime.now(timezone.utc)
    _set_last_seen("ebay:b1", (now - timedelta(hours=5, minutes=55)).isoformat())
    _set_last_seen("ebay:b2", (now - timedelta(hours=6, minutes=5)).isoformat())
    _deliver()
    assert len(pushes["sent"]) == 1
    skipped = [r for r in _notify_results() if r and "skipped" in r]
    assert len(skipped) == 1 and "6 h" in skipped[0]["skipped"]


def test_unknown_mileage_does_not_block_an_alert(client, world, pushes):
    """An optional fact missing on either side is not a mismatch."""
    _camry_buyer(client, plan="pro", risk_mode="aggressive")
    world.ingest([L("nm1", 8100, odo=None)])
    score_all()
    _deliver()
    assert len(pushes["sent"]) == 1
