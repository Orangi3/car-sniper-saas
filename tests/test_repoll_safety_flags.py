"""
tests/test_repoll_safety_flags.py — a re-polled listing is judged by what it
says now (sniper.ingest).

A seller who edits a live listing to disclose a branded title, a mechanical
problem or scam wording — or a provider that changes the condition to "for
parts or not working" — must not keep the car in the trusted feed, as a
clean comparable, or alertable. Before this, a price-change re-score used
the stale clean-title column, and a same-price edit was never stored or
re-scored. Disclosed problems are sticky: a later edit that removes the
words does not clear them.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json

from tests.test_e2e_pipeline import _ebay_item, _poll, ebay_feed  # noqa: F401
from tests.test_matching_and_history import (PEER_PRICES, L, q, score_all,  # noqa: F401
                                             world)
from tests.test_send_time_check import (_camry_buyer, _deliver,  # noqa: F401
                                        _notify_results, pushes)



def test_salvage_disclosed_with_a_price_drop_never_alerts(client, world, pushes):
    """Before: the re-score used the stale clean-title column, so a Pro
    buyer got a TRUSTED price-drop alert for a salvage car."""
    pro = _camry_buyer(client, plan="pro", min_discount_pct=15)
    world.ingest([L("s1", 9000)])                 # ~11% under: no match yet
    score_all()
    assert q("SELECT * FROM user_matches WHERE user_id = ?", (pro,)) == []
    world.ingest([L("s1", 6500, desc="salvage title, runs great")])
    score_all()
    assert q("SELECT is_salvage FROM listings WHERE composite_id = 'craigslist:s1'") \
        == [{"is_salvage": 1}]
    nm = q("SELECT trust, risk_flags FROM near_misses WHERE composite_id = 'craigslist:s1'")[0]
    assert nm["trust"] == "risk" and "branded_title" in json.loads(nm["risk_flags"])
    assert q("SELECT * FROM user_matches WHERE user_id = ?", (pro,)) == []
    _deliver()
    assert pushes["sent"] == []


def test_salvage_disclosed_at_same_price_leaves_trusted_feed_and_stops_alert(
        client, world, pushes):
    """Before: an edit at the same price was never stored or re-scored; the
    car stayed trusted and its queued alert went out."""
    _camry_buyer(client)
    world.ingest([L("s2", 8100)])
    score_all()
    assert q("SELECT trust FROM near_misses WHERE composite_id = 'craigslist:s2'") \
        == [{"trust": "trusted"}]
    res = world.ingest([L("s2", 8100, desc="rebuilt title, runs great")])
    assert res["new"] == [] and res["price_changed"] == []
    row = q("SELECT is_salvage, description FROM listings "
            "WHERE composite_id = 'craigslist:s2'")[0]
    assert row["is_salvage"] == 1 and "rebuilt title" in row["description"]
    _deliver()                                    # before the re-score runs
    assert pushes["sent"] == []
    assert "branded_title" in _notify_results()[0]["skipped"]
    score_all()
    nm = q("SELECT trust, risk_flags FROM near_misses WHERE composite_id = 'craigslist:s2'")[0]
    assert nm["trust"] == "risk" and "branded_title" in json.loads(nm["risk_flags"])


def test_disclosed_problem_is_sticky(world):
    world.ingest([L("s3", 8100)])
    world.ingest([L("s3", 8100, desc="needs transmission, runs rough")])
    world.ingest([L("s3", 8100, desc="clean title, runs great")])  # wording removed
    world.ingest([L("s3", 7900, desc="clean title, runs great")])  # and a price change
    row = q("SELECT needs_work FROM listings WHERE composite_id = 'craigslist:s3'")[0]
    assert row["needs_work"] == 1


def _comps_used(cid):
    nm = q("SELECT comp_n, evidence FROM near_misses WHERE composite_id = ?", (cid,))[0]
    return nm["comp_n"], {c.get("composite_id")
                          for c in json.loads(nm["evidence"]).get("comps", [])}


def test_disclosed_salvage_stops_being_a_clean_comparable(world):
    # peer0 (a world comparable, $10,000 / 100k mi) is edited to disclose it
    world.ingest([L("peer0", 10000, odo=100000, desc="rebuilt title, runs great")])
    world.ingest([L("tgt", 8100, odo=131000)])
    score_all()
    n, used = _comps_used("craigslist:tgt")
    assert "craigslist:peer0" not in used
    assert n == len(PEER_PRICES) - 1


def test_ebay_condition_changed_to_for_parts_is_rechecked(client, ebay_feed):
    """eBay's own condition 'For parts or not working' (id 7000) marks a car
    as needing work; a re-poll that brings it must be honored."""
    item = _ebay_item("v1|900|0", 8100)
    ebay_feed["items"] = [item]
    _poll()
    score_all()
    assert q("SELECT trust FROM near_misses WHERE composite_id = 'ebay:v1|900|0'") \
        == [{"trust": "trusted"}]
    ebay_feed["items"] = [{**item, "conditionId": "7000",
                           "condition": "For parts or not working"}]
    _poll()
    score_all()
    assert q("SELECT needs_work FROM listings WHERE composite_id = 'ebay:v1|900|0'") \
        == [{"needs_work": 1}]
    nm = q("SELECT trust, risk_flags FROM near_misses WHERE composite_id = 'ebay:v1|900|0'")[0]
    assert nm["trust"] == "risk" and "needs_work" in json.loads(nm["risk_flags"])


def test_repoll_without_a_description_keeps_the_stored_one(world):
    """A source that omits the description on one poll must not erase it
    (that would add description_unchecked and churn re-scores)."""
    world.ingest([L("d1", 8100, desc="clean title, runs great")])
    score_all()
    n_jobs = len(q("SELECT id FROM jobs WHERE kind = 'score'"))
    world.ingest([L("d1", 8100, desc="")])
    assert q("SELECT description FROM listings WHERE composite_id = 'craigslist:d1'") \
        == [{"description": "clean title, runs great"}]
    assert len(q("SELECT id FROM jobs WHERE kind = 'score'")) == n_jobs


def test_flag_rescore_happens_once_per_disclosure(world):
    world.ingest([L("once", 8100)])
    score_all()
    n_jobs = len(q("SELECT id FROM jobs WHERE kind = 'score'"))
    for _ in range(3):
        world.ingest([L("once", 8100, desc="salvage title")])
    assert len(q("SELECT id FROM jobs WHERE kind = 'score'")) == n_jobs + 1


def test_scam_wording_added_at_same_price_stops_the_alert(client, world, pushes):
    _camry_buyer(client)
    world.ingest([L("sc", 8100)])
    score_all()
    res = world.ingest([L("sc", 8100, desc="runs great. western union only, "
                                           "shipping at my expense")])
    assert [x.composite_id() for x in res["disclosed"]] == ["craigslist:sc"]
    _deliver()
    assert pushes["sent"] == []
    assert "scam_signals" in _notify_results()[0]["skipped"]
    score_all()
    nm = q("SELECT trust, risk_flags FROM near_misses WHERE composite_id = 'craigslist:sc'")[0]
    assert nm["trust"] == "risk" and "scam_signals" in json.loads(nm["risk_flags"])


def test_a_denial_is_not_a_disclosure(client, world, pushes):
    """"Never salvaged, never wrecked" is not a branded title: no flag, no
    re-score, and the queued alert still goes out."""
    _camry_buyer(client)
    world.ingest([L("den", 8100)])
    score_all()
    n_jobs = len(q("SELECT id FROM jobs WHERE kind = 'score'"))
    res = world.ingest([L("den", 8100, desc="clean title, never salvaged, never wrecked")])
    assert res["disclosed"] == []
    assert len(q("SELECT id FROM jobs WHERE kind = 'score'")) == n_jobs
    assert q("SELECT is_salvage FROM listings WHERE composite_id = 'craigslist:den'") \
        == [{"is_salvage": 0}]
    _deliver()
    assert len(pushes["sent"]) == 1
