"""
tests/test_odometer_update.py — a re-poll that changes the odometer at an
unchanged price is stored and re-valued (sniper.ingest).

Mileage is a valuation input (mileage adjustment, comparable match quality,
"far beyond every comparable" confidence cap, implausible-mileage flag), but
a same-price re-poll only bumped last_seen_at: a seller correcting "130k" to
"260k" kept the 130k valuation, trust and alert. Rule: a re-poll may replace
the stored odometer only with a reading in (0, 500,000] — the bound the title
parser and the seller form already use; a missing, zero or out-of-range
reading never overwrites a stored one (MarketCheck's structured `miles` is
otherwise unbounded). Each genuine change re-scores once; identical
re-observations queue nothing. A queued alert that states the old mileage is
dropped at send time.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json

from tests.test_e2e_pipeline import _ebay_item, _poll, ebay_feed  # noqa: F401
from tests.test_marketcheck_fsbo import FIX, R, _row, mc  # noqa: F401
from tests.test_matching_and_history import L, q, score_all, world  # noqa: F401
from tests.test_send_time_check import (_camry_buyer, _deliver,  # noqa: F401
                                        _notify_results, pushes)

CID = "craigslist:od"


def _odo():
    return q("SELECT odometer FROM listings WHERE composite_id = ?", (CID,))[0]["odometer"]


def _jobs():
    return [r["dedupe_key"] for r in q("SELECT dedupe_key FROM jobs WHERE kind = 'score' "
                                       "ORDER BY id") if r["dedupe_key"].startswith(f"score:{CID}:")]


def _nm():
    return q("SELECT trust, confidence, refreshed_at FROM near_misses WHERE composite_id = ?",
             (CID,))[0]


def test_same_price_mileage_change_is_stored_and_revalued(world):
    world.ingest([L("od", 8100, odo=130000)])
    score_all()
    assert _nm()["trust"] == "trusted"
    res = world.ingest([L("od", 8100, odo=260000)])       # seller corrects the mileage
    assert res["price_changed"] == []
    assert _odo() == 260000
    assert len(_jobs()) == 2 and ":odometer-changed:" in _jobs()[-1]
    score_all()
    nm = _nm()                    # 97k beyond every comparable: no longer a trusted deal
    assert (nm["trust"], nm["confidence"]) == ("risk", "low")


def test_identical_mileage_creates_no_job(world):
    world.ingest([L("od", 8100, odo=130000)])
    score_all()
    for _ in range(3):
        world.ingest([L("od", 8100, odo=130000)])
    assert _odo() == 130000 and len(_jobs()) == 1


def test_missing_zero_or_implausible_mileage_never_overwrites(world):
    world.ingest([L("od", 8100, odo=130000)])
    score_all()
    for bad in (None, 0, 9_999_999):
        world.ingest([L("od", 8100, odo=bad)])
    assert _odo() == 130000 and len(_jobs()) == 1
    world.ingest([L("od", 7600, odo=9_999_999)])          # nor with a price change
    assert _odo() == 130000


def test_mileage_change_keeps_identity_and_relist_links(world):
    vin = "1HGCM82633A004352"
    world.ingest([L("od", 8100, odo=130000, desc=f"VIN {vin} clean title")])
    before = q("SELECT vin, vehicle_key, relist_of FROM listings WHERE composite_id = ?",
               (CID,))[0]
    world.ingest([L("od", 8100, odo=131500, desc=f"VIN {vin} clean title")])
    after = q("SELECT vin, vehicle_key, relist_of FROM listings WHERE composite_id = ?",
              (CID,))[0]
    assert after == before and before["vehicle_key"] == f"vin:{vin}"
    assert len(q("SELECT 1 FROM listings")) == 11         # 10 peers + this car, no new row


def test_mileage_flip_flop_is_revalued_each_time(world):
    world.ingest([L("od", 8100, odo=130000)])
    score_all()
    world.ingest([L("od", 8100, odo=260000)])
    score_all()
    assert _nm()["trust"] == "risk"
    world.ingest([L("od", 8100, odo=130000)])             # corrected back
    score_all()
    assert _odo() == 130000 and _nm()["trust"] == "trusted"
    assert len(_jobs()) == 3


def test_queued_alert_stating_the_old_mileage_is_not_sent(client, world, pushes):
    _camry_buyer(client)
    world.ingest([L("od", 8100, odo=130000)])
    score_all()                                           # alert queued at 130,000 mi
    world.ingest([L("od", 8100, odo=260000)])
    _deliver()
    assert pushes["sent"] == []
    (res,) = _notify_results()
    assert res["skipped"].startswith("stale:") and "260,000" in res["skipped"]


def test_ebay_title_mileage_edit_is_stored(client, ebay_feed):
    ebay_feed["items"] = [_ebay_item("v1|910|0", 8100, title="2015 Toyota Camry SE 130k miles")]
    _poll()
    ebay_feed["items"] = [_ebay_item("v1|910|0", 8100, title="2015 Toyota Camry SE 168k miles")]
    _poll()
    row = q("SELECT odometer, title FROM listings WHERE composite_id = 'ebay:v1|910|0'")[0]
    assert row == {"odometer": 168000, "title": "2015 Toyota Camry SE 168k miles"}


def test_marketcheck_fsbo_structured_miles_edit_is_stored(mc):
    sniper, calls, state = mc
    sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    assert _row("marketcheck_fsbo:fsbo-1001")["odometer"] == 128000
    edited = json.loads(json.dumps(FIX))
    edited["listings"][0]["miles"] = 141000               # same price, new mileage
    state["resp"] = lambda p: R(200, edited)
    sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    assert _row("marketcheck_fsbo:fsbo-1001")["odometer"] == 141000
    junk = json.loads(json.dumps(edited))
    junk["listings"][0]["miles"] = 0                      # provider sends 0 for unknown
    state["resp"] = lambda p: R(200, junk)
    sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    assert _row("marketcheck_fsbo:fsbo-1001")["odometer"] == 141000
