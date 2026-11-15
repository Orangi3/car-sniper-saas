"""
tests/test_disclosure_trust.py — a listing that discloses a material problem
stops being shown as trusted at once, not when its re-score finally runs.

The re-score a disclosure queues can wait behind a saturated score queue,
be held by a crashed worker until its lease expires, or dead-letter after
max_attempts (backfill_unscored only re-queues price mismatches, so a dead
same-price re-score was never retried). Until it ran, the deal feeds, the
detail page and saved-search browsing kept showing the car as trusted.
ingest() now lowers the stored valuation to "risk" and adds the disclosed
flags in the same transaction that stores the disclosure. Policy: a car
only moves from the trusted feed to the risk feed (still visible, labeled);
the re-score recomputes the rest and, the flags being sticky, reaches the
same verdict.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json

from tests.conftest import login
from tests.test_matching_and_history import L, make_user, q, score_all, world  # noqa: F401
from tests.test_send_time_check import (_camry_buyer, _deliver,  # noqa: F401
                                        _notify_results, pushes)

CID = "craigslist:dt"
SALVAGE = "rebuilt title, runs great"


def _nm():
    r = q("SELECT trust, risk_flags, price_at_score FROM near_misses WHERE composite_id = ?",
          (CID,))[0]
    return r["trust"], json.loads(r["risk_flags"] or "[]"), r["price_at_score"]


def _feed(client, email, feed):
    login(client, email)
    ids = [d["composite_id"] for d in client.get(f"/api/deals?feed={feed}").get_json()]
    client.post("/api/auth/logout")
    return ids


def _score_jobs(status=None):
    sql = ("SELECT dedupe_key, status, attempts FROM jobs WHERE kind = 'score'"
           + (" AND status = ?" if status else "") + " ORDER BY id")
    rows = q(sql, (status,) if status else ())
    return [r for r in rows if r["dedupe_key"].startswith(f"score:{CID}:")]


def _run_now():
    import db
    with db.session() as c:
        c.execute("UPDATE jobs SET run_after = '2000-01-01T00:00:00.000000+00:00' "
                  "WHERE status = 'pending'")


def _trusted_car(client, world, email="u@example.com"):
    _camry_buyer(client, email)
    world.ingest([L("dt", 8100)])
    score_all()
    assert _nm()[0] == "trusted" and CID in _feed(client, email, "trusted")


# 1 + 6: normal disclosure; safe feed before the re-score; successful re-score
def test_disclosure_moves_the_car_to_the_risk_feed_before_the_rescore(client, world, pushes):
    _trusted_car(client, world)
    world.ingest([L("dt", 8100, desc=SALVAGE)])          # re-score queued, not yet run
    assert [j["status"] for j in _score_jobs()][-1] == "pending"
    trust, flags, price = _nm()
    assert trust == "risk" and "branded_title" in flags and price == 8100
    assert CID not in _feed(client, "u@example.com", "trusted")
    assert CID in _feed(client, "u@example.com", "risk")
    login(client, "u@example.com")
    detail = client.get(f"/api/deals/{CID}").get_json()
    assert detail["trust"] == "risk" and "branded_title" in detail["risk_flags"]
    client.post("/api/auth/logout")
    score_all()                                          # the re-score succeeds
    trust, flags, _ = _nm()
    assert trust == "risk" and flags.count("branded_title") == 1
    assert _score_jobs("pending") == []


# 2: saturated score queue
def test_disclosure_during_queue_saturation(client, world, monkeypatch):
    import jobqueue
    _trusted_car(client, world)
    world.ingest([L(f"flood{i}", 9000 + i, odo=90000 + 1000 * i) for i in range(3)])
    backlog = len(q("SELECT 1 FROM jobs WHERE kind = 'score' AND status = 'pending'"))
    monkeypatch.setitem(jobqueue.MAX_PENDING, "score", backlog)    # queue is full
    world.ingest([L("late", 9100, odo=95500)])            # new listing: not queued (cap)
    assert not q("SELECT 1 FROM jobs WHERE dedupe_key LIKE 'score:craigslist:late:%'")
    world.ingest([L("dt", 8100, desc=SALVAGE)])
    assert _nm()[0] == "risk"                             # safe without any worker
    assert CID not in _feed(client, "u@example.com", "trusted")
    assert len(_score_jobs("pending")) == 1               # re-scores are not capped


# 3: worker crash mid re-score, recovery after the lease expires
def test_worker_crash_during_rescore_recovers_and_stays_safe(client, world):
    import db
    import jobqueue
    _trusted_car(client, world)
    world.ingest([L("dt", 8100, desc=SALVAGE)])
    job = jobqueue.claim("crashed-worker", kinds=["score"], lease_s=60)
    assert job and job["dedupe_key"].startswith(f"score:{CID}:")
    assert _nm()[0] == "risk" and CID not in _feed(client, "u@example.com", "trusted")
    with db.session() as c:                               # the worker died; lease expires
        c.execute("UPDATE jobs SET locked_until = '2000-01-01T00:00:00.000000+00:00' "
                  "WHERE id = ?", (job["id"],))
    score_all()                                           # another worker reclaims it
    assert q("SELECT status, attempts FROM jobs WHERE id = ?", (job["id"],)) \
        == [{"status": "done", "attempts": 2}]
    trust, flags, _ = _nm()
    assert trust == "risk" and "branded_title" in flags


def test_dead_lettered_rescore_never_leaves_the_car_trusted(client, world, monkeypatch):
    import sniper
    import valuation
    _trusted_car(client, world)

    def broken(conn, target):
        raise RuntimeError("comparables unavailable")
    monkeypatch.setattr(valuation, "load_peers", broken)
    world.ingest([L("dt", 8100, desc=SALVAGE)])
    for _ in range(6):                                    # every attempt fails
        _run_now()
        score_all()
    assert [j["status"] for j in _score_jobs()][-1] == "dead"
    assert _nm()[0] == "risk"
    assert CID not in _feed(client, "u@example.com", "trusted")
    assert sniper.backfill_unscored() == 0                # same price: nothing to re-queue


# 4: duplicate disclosure observations
def test_duplicate_disclosures_update_once(client, world):
    _trusted_car(client, world)
    world.ingest([L("dt", 8100, desc=SALVAGE)])
    world.ingest([L("dt", 8100, desc=SALVAGE + ". salvage title, rebuilt.")])
    trust, flags, _ = _nm()
    assert trust == "risk" and flags.count("branded_title") == 1
    assert len(_score_jobs()) == 2                        # first valuation + one re-score


# 5: repeated polling while the re-score is pending
def test_repolls_while_rescore_pending_change_nothing(client, world):
    _trusted_car(client, world)
    world.ingest([L("dt", 8100, desc=SALVAGE)])
    before = (_nm(), _score_jobs())
    for _ in range(3):
        world.ingest([L("dt", 8100, desc=SALVAGE)])
        world.ingest([L("dt", 8100, desc="")])            # a poll without description
    assert (_nm(), _score_jobs()) == before


# 7: alerts
def test_alert_behavior_around_a_disclosure(client, world, pushes):
    pro = make_user(client, "pro@example.com", plan="pro",
                    searches=[("c", {"model": "camry", "risk_mode": "aggressive"})])
    _trusted_car(client, world)                                  # alerts queued for both buyers
    world.ingest([L("dt", 8100, desc=SALVAGE)])
    _deliver()
    assert pushes["sent"] == []                           # queued alerts dropped as stale
    assert all("branded_title" in r["skipped"] for r in _notify_results())
    score_all()
    _deliver()
    assert pushes["sent"] == []                           # the re-score alerts nobody
    assert len(q("SELECT 1 FROM user_matches WHERE user_id = ?", (pro,))) == 1


# 8: ordinary price updates are unchanged
def test_price_change_without_disclosure_keeps_valuation_until_rescore(client, world):
    _trusted_car(client, world)
    world.ingest([L("dt", 7700)])
    trust, _, price = _nm()
    assert (trust, price) == ("trusted", 8100)            # untouched until re-scored
    score_all()
    trust, _, price = _nm()
    assert (trust, price) == ("trusted", 7700)


def test_price_change_with_disclosure_lowers_trust_at_once(client, world):
    _trusted_car(client, world)
    world.ingest([L("dt", 6900, desc=SALVAGE)])
    trust, flags, price = _nm()
    assert trust == "risk" and "branded_title" in flags and price == 8100
    score_all()
    trust, flags, price = _nm()
    assert trust == "risk" and "branded_title" in flags and price == 6900


def test_disclosure_during_a_running_valuation_is_not_overwritten(client, world, monkeypatch):
    """A score job reads the listing, then looks up sold comparables over the
    network before writing; a poll that stores a disclosure meanwhile must
    not be overwritten by a verdict from the row read before it."""
    import jobqueue
    import sniper
    world.ingest([L("dt", 8100)])                         # new: valuation queued
    real = sniper._sold_evidence

    def lookup_while_the_seller_edits(l):
        sniper.ingest([L("dt", 8100, desc=SALVAGE)])      # a concurrent poll commits
        return real(l)
    monkeypatch.setattr(sniper, "_sold_evidence", lookup_while_the_seller_edits)
    jobqueue.drain({"score": sniper.score_job}, max_jobs=1)   # only the in-flight job
    trust, flags, _ = _nm()
    assert trust == "risk" and "branded_title" in flags
