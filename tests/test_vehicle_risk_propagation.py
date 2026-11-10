"""
tests/test_vehicle_risk_propagation.py — a car whose posting on one
marketplace discloses a branded title or a mechanical problem is not a
trusted deal on another marketplace (sniper.assess_risk via
vehicle_identity.vehicle_risk_elsewhere).

Each listing was judged only by its own text. The same car posted twice —
one copy saying "rebuilt title" — left the clean-looking copy TRUSTED, and a
buyer was alerted to it. Policy:

  * vehicle-level facts travel between high-confidence postings of one car
    (same VIN, or the strict no-VIN identity): a branded title (permanent)
    and a mechanical disclosure (only from a posting seen within 14 days —
    repairs happen);
  * listing-level concerns stay with their listing: scam wording, price,
    auction format, description, seller type, buyer reports;
  * lookalikes never inherit anything.

The derived flags (`branded_title_other_posting`, `needs_work_other_posting`)
block trust like their own-listing counterparts, name the posting they came
from in the confidence reasons, and never touch the clean listing's own row.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tests.test_matching_and_history import make_user, q, score_all, world  # noqa: F401
from tests.test_send_time_check import _deliver, _notify_results, pushes  # noqa: F401
from tests.test_valuation_self_comps import A, PEERS4, twin

VIN_A = "1HGCM82633A004352"
VIN_B = "1M8GDM9AXKP042788"
T = "craigslist:t"
TW = "marketcheck_fsbo:tw"
REBUILT = "rebuilt title, runs great"
NEEDS = "needs transmission, runs rough"


def _nm(cid=T):
    r = q("SELECT trust, risk_flags, confidence_reasons FROM near_misses "
          "WHERE composite_id = ?", (cid,))[0]
    return r["trust"], json.loads(r["risk_flags"] or "[]"), \
        " | ".join(json.loads(r["confidence_reasons"] or "[]"))


def _buyer(client):
    return make_user(client, "b@example.com", searches=[("accord", {"model": "accord"})])


def _alerts(uid):
    return len(q("SELECT 1 FROM user_matches WHERE user_id = ? AND composite_id = ?", (uid, T)))


def _run(client, world, pushes, the_twin, target=None):
    uid = _buyer(client)
    world.ingest(PEERS4 + [the_twin])
    world.ingest([target or A("t", 8000)])
    score_all()
    _deliver()
    return uid


def _set_last_seen(cid, days_ago):
    import db
    with db.session() as c:
        c.execute("UPDATE listings SET last_seen_at = ? WHERE composite_id = ?",
                  ((datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(), cid))


# ---------- same vehicle: risk travels -------------------------------------------

def test_same_vin_copy_discloses_rebuilt_title(client, world, pushes):
    uid = _run(client, world, pushes, twin(desc=f"VIN {VIN_A} {REBUILT}"),
               A("t", 8000, desc=f"VIN {VIN_A} clean title, one owner"))
    trust, flags, reasons = _nm()
    assert trust == "risk" and "branded_title_other_posting" in flags
    assert "branded_title" not in flags                   # its own text says nothing
    assert TW in reasons and "same VIN" in reasons        # traceable to the source
    assert _alerts(uid) == 0 and pushes["sent"] == []
    assert q("SELECT is_salvage FROM listings WHERE composite_id = ?", (T,)) \
        == [{"is_salvage": 0}]                            # its own row is untouched


def test_strict_identity_copy_discloses_a_mechanical_problem(client, world, pushes):
    uid = _run(client, world, pushes, twin(desc=NEEDS))
    trust, flags, reasons = _nm()
    assert trust == "risk" and "needs_work_other_posting" in flags
    assert TW in reasons and "matching details" in reasons
    assert _alerts(uid) == 0 and pushes["sent"] == []


def test_disclosure_after_the_alert_was_queued(client, world, pushes):
    uid = _buyer(client)
    world.ingest(PEERS4 + [twin()])                       # copy is clean at first
    world.ingest([A("t", 8000)])
    score_all()                                           # alert for t queued
    assert _alerts(uid) == 1
    world.ingest([twin(desc=REBUILT)])                    # the copy now discloses
    trust, flags, _ = _nm()
    assert trust == "risk" and "branded_title_other_posting" in flags   # immediately
    _deliver()
    assert pushes["sent"] == []
    assert any("branded_title_other_posting" in (r or {}).get("skipped", "")
               for r in _notify_results())


def test_new_copy_with_a_disclosure_arrives_after_the_alert_was_queued(client, world, pushes):
    uid = _buyer(client)
    world.ingest(PEERS4 + [A("t", 8000)])
    score_all()
    assert _alerts(uid) == 1
    world.ingest([twin(desc=REBUILT)])                    # a NEW listing, already disclosing
    assert _nm()[0] == "risk"
    _deliver()
    assert pushes["sent"] == []


def test_disclosure_during_the_other_postings_valuation(client, world, monkeypatch):
    import jobqueue
    import sniper
    world.ingest(PEERS4 + [twin()])
    score_all()
    world.ingest([A("t", 8000)])                          # t's valuation queued
    real = sniper._sold_evidence

    def lookup_while_the_copy_discloses(l):
        sniper.ingest([twin(desc=REBUILT)])               # a concurrent poll commits
        return real(l)
    monkeypatch.setattr(sniper, "_sold_evidence", lookup_while_the_copy_discloses)
    jobqueue.drain({"score": sniper.score_job}, max_jobs=1)   # only t's job
    trust, flags, _ = _nm()
    assert trust == "risk" and "branded_title_other_posting" in flags


def test_repeated_disclosure_is_idempotent(client, world, pushes):
    _run(client, world, pushes, twin())
    for _ in range(3):
        world.ingest([twin(desc=REBUILT)])
    trust, flags, _ = _nm()
    assert trust == "risk" and flags.count("branded_title_other_posting") == 1
    jobs = [r["dedupe_key"] for r in q("SELECT dedupe_key FROM jobs WHERE kind = 'score'")
            if r["dedupe_key"].startswith(f"score:{T}:")]
    assert len(jobs) == 2                                 # first valuation + one re-check


def test_revaluation_keeps_the_vehicle_risk(client, world, pushes):
    _run(client, world, pushes, twin())
    world.ingest([twin(desc=REBUILT)])
    score_all()                                           # t is re-valued from scratch
    trust, flags, _ = _nm()
    assert trust == "risk" and "branded_title_other_posting" in flags


def test_branded_title_outlives_the_posting_but_a_repair_may_not(client, world, pushes):
    """A title brand is permanent; a mechanical problem from a posting not
    seen for over 14 days no longer marks today's copy."""
    import sniper
    world.ingest(PEERS4 + [twin(desc=REBUILT), twin("tw2", source="ebay", desc=NEEDS,
                                                    zip_code="35473")])
    world.ingest([A("t", 8000)])
    score_all()
    assert {"branded_title_other_posting", "needs_work_other_posting"} <= set(_nm()[1])
    _set_last_seen(TW, 40)
    _set_last_seen("ebay:tw2", 20)
    sniper._score_one_listing(T)
    trust, flags, _ = _nm()
    assert trust == "risk" and "branded_title_other_posting" in flags
    assert "needs_work_other_posting" not in flags


# ---------- different vehicles: nothing travels -------------------------------------

def _stays_trusted(client, world, pushes, the_twin, target=None):
    uid = _run(client, world, pushes, the_twin, target)
    trust, flags, _ = _nm()
    assert trust == "trusted" and not {"branded_title_other_posting",
                                       "needs_work_other_posting"} & set(flags)
    assert _alerts(uid) == 1 and len(pushes["sent"]) == 1


def test_different_vins(client, world, pushes):
    _stays_trusted(client, world, pushes, twin(desc=f"VIN {VIN_B} {REBUILT}"),
                   A("t", 8000, desc=f"VIN {VIN_A} clean title, one owner"))


def test_similar_model_different_trim(client, world, pushes):
    _stays_trusted(client, world, pushes, twin(desc=REBUILT, title="2014 Honda Accord LX"))


def test_same_title_different_odometer(client, world, pushes):
    _stays_trusted(client, world, pushes, twin(desc=REBUILT, odo=101900))


def test_missing_identity_evidence(client, world, pushes):
    _stays_trusted(client, world, pushes, twin(desc=REBUILT, odo=None))


def test_different_primary_photograph(client, world, pushes):
    _stays_trusted(client, world, pushes, twin(desc=REBUILT, extras={"photo_hash": "ph-2"}),
                   A("t", 8000, extras={"photo_hash": "ph-1"}))


def test_incompatible_locations(client, world, pushes):
    _stays_trusted(client, world, pushes, twin(desc=REBUILT, zip_code="35203"))  # Birmingham


def test_two_legitimate_lookalikes(client, world, pushes):
    _stays_trusted(client, world, pushes, twin(desc=REBUILT, odo=100000),
                   A("t", 8000, odo=100000))


def test_seller_specific_allegation_stays_with_its_listing(client, world, pushes):
    """Scam wording on a copy (a scammer may copy a real car's details) is
    about that posting, not about the car."""
    _stays_trusted(client, world, pushes,
                   twin(desc="clean title. western union only, shipping at my expense"))
    assert "scam_signals" in _nm(TW)[1]


def test_salvage_opt_in_applies_to_the_derived_fact(client, world, pushes):
    """Aggressive mode shows risk-labeled cars, but a car known to be salvage
    through its other posting still needs the buyer's salvage opt-in, as its
    own disclosure would."""
    no_opt = make_user(client, "agg@example.com", plan="pro",
                       searches=[("a", {"model": "accord", "risk_mode": "aggressive"})])
    opt_in = make_user(client, "salvage@example.com", plan="pro",
                       searches=[("a", {"model": "accord", "risk_mode": "aggressive",
                                        "allow_salvage": True})])
    world.ingest(PEERS4 + [twin(desc=REBUILT)])
    world.ingest([A("t", 8000)])
    score_all()
    assert _alerts(no_opt) == 0 and _alerts(opt_in) == 1
    trust = q("SELECT trust FROM user_matches WHERE user_id = ?", (opt_in,))
    assert any(r["trust"] == "risk" for r in trust)        # labeled, never trusted
