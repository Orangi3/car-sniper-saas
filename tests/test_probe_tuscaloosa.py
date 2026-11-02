"""
tests/test_probe_tuscaloosa.py — offline fixture-probe tests.

These tests are deliberately independent of the app's database fixtures:
they import only probe_tuscaloosa, geo, and sources.base, make no
network calls, and never touch a production database or notification
path. They do NOT prove anything about real inventory, valuation, or
licensing; they prove the harness behaves deterministically on fixtures.
"""
import json
import socket
import sys

import pytest

import probe_tuscaloosa as pt
from probe_tuscaloosa import (
    BlockedSourceError,
    Segment,
    SharedPoller,
    SimulatedBudget,
    run_probe,
)

# Tuscaloosa ZIP 35401 centroid is in the local data; a listing at the
# same ZIP is 0.0 mi away, safely inside the 100-mile radius. Birmingham
# (35201) is also inside, by design of the radius, but tests use 35401
# so they do not depend on a second city's exact distance.
BASE = {"segment": "trucks", "source_id": "fx-1", "title": "2015 Ford F-150 XLT",
        "price": 9000, "year": 2015, "make": "Ford", "model": "F-150",
        "odometer": 120000, "zip": "35401", "latitude": None, "longitude": None,
        "posted_at": "2026-10-20", "extras": {"vin": "1FTEX1EP5FFA00001",
                                        "first_seen_at": "2026-10-22",
                                        "photo_hash": "ph1"}}


def _rec(**over):
    d = dict(BASE)
    d.update(over)
    # Deep-copy extras so tests cannot mutate each other's fixtures.
    d["extras"] = dict(BASE["extras"])
    d["extras"].update(over.get("extras", {}))
    return d


def _seg(**over):
    d = {"id": "trucks", "name": "Trucks", "min_price": 2000, "max_price": 15000}
    d.update(over)
    return Segment.from_dict(d)


def test_zero_network_access():
    """Required test 1: fixture execution makes zero network calls."""
    real_socket = socket.socket

    def _blocked(*a, **k):
        raise AssertionError("network call attempted")

    socket.socket = _blocked
    try:
        report = run_probe(segments=[_seg()], fixtures=[_rec()])
    finally:
        socket.socket = real_socket
    assert report["network_calls"] == 0
    assert report["live_access"] is False
    assert report["unique_listings"] == 1


def test_shared_polling_does_not_multiply_calls():
    """Required test 2: many searches, one segment, one poll charge."""
    budget = SimulatedBudget(cap=450)
    poller = SharedPoller(budget)
    fetch_calls = []

    def fetch():
        fetch_calls.append(1)
        return [pt.fixture_listing(_rec())]

    a = poller.poll("trucks", fetch)
    b = poller.poll("trucks", fetch)
    c = poller.poll("trucks", fetch)
    assert a is b is c
    assert len(fetch_calls) == 1
    assert budget.used == 1
    assert poller.poll_calls == 1


def test_budget_simulation_stops_at_cap():
    """Required test 3: simulated budget stops at the configured cap."""
    segs = [Segment.from_dict({"id": f"s{i}", "name": f"S{i}"}) for i in range(10)]
    fixtures = [{"segment": f"s{i}", **{k: v for k, v in _rec().items() if k != "segment"},
                 "source_id": f"fx-{i}"} for i in range(10)]
    report = run_probe(segments=segs, fixtures=fixtures, budget_cap=3)
    assert report["budget"]["used"] == 3
    assert report["budget"]["stopped_at_cap"] is True
    assert sum(1 for s in report["per_search"].values() if s["stopped_at_cap"]) == 7


def test_retries_and_pagination_are_accounted():
    """Required test 4: pages and retries count as attempted calls."""
    budget = SimulatedBudget(cap=450)
    # 3 pages, one failed attempt retried once: 4 attempted calls.
    assert budget.reserve(3, kind="page", segment="trucks") is True
    assert budget.reserve(1, kind="retry-failed", segment="trucks") is True
    assert budget.reserve(1, kind="retry", segment="trucks") is True
    assert budget.used == 5
    assert len(budget.ledger) == 3
    assert all(e["accepted"] for e in budget.ledger)


def test_blocked_providers_remain_inaccessible():
    """Required test 5: real sources are blocked before any poll."""
    for sid in ("ebay", "marketcheck_fsbo", "marketcheck", "craigslist", "facebook"):
        with pytest.raises(BlockedSourceError):
            run_probe(segments=[_seg()], fixtures=[_rec()], source_ids=(sid,))


def test_no_credentials_or_sensitive_data_leak_into_reports():
    """Required test 6: secrets never appear in JSON or Markdown reports."""
    secret = "FIXTURE_SECRET_DO_NOT_LEAK_123"
    import os
    os.environ["MARKETCHECK_API_KEY"] = secret
    try:
        report = run_probe(segments=[_seg()], fixtures=[_rec()])
    finally:
        del os.environ["MARKETCHECK_API_KEY"]
    blob = json.dumps(report) + pt.render_markdown(report)
    assert secret not in blob
    assert "FIXTURE" in blob


def test_deduplication_and_relisting_are_deterministic():
    """Required test 7: same VIN twice is one unique + one relist."""
    dup = _rec(source_id="fx-2", extras={"first_seen_at": "2026-10-23"})
    report = run_probe(segments=[_seg()], fixtures=[_rec(), dup])
    assert report["listings_seen"] == 2
    assert report["unique_listings"] == 1
    assert report["relist_records"] == 1
    # No VIN: same title + odometer + photo hash is also one unique.
    a = _rec(source_id="fx-a", extras={"vin": "", "photo_hash": "ph9"})
    b = _rec(source_id="fx-b", title="2015 Ford F-150 XLT", odometer=120000,
             extras={"vin": "", "photo_hash": "ph9", "first_seen_at": "2026-10-23"})
    report2 = run_probe(segments=[_seg()], fixtures=[a, b])
    assert report2["unique_listings"] == 1
    assert report2["relist_records"] == 1


def test_freshness_handles_missing_dates():
    """Required test 8: missing posted dates are unknown, never zero."""
    no_date = _rec(source_id="fx-nodate", posted_at=None,
                   extras={"vin": "1FTEX1EP5FFA00009",
                           "first_seen_at": "2026-10-22"})
    report = run_probe(segments=[_seg()], fixtures=[_rec(), no_date])
    assert report["freshness"]["unknown_date_listings"] == 1
    assert report["freshness"]["count_with_dates"] == 1
    assert report["freshness"]["median_days"] == 2


def test_repeated_runs_produce_identical_report_data():
    """Required test 9: determinism across runs."""
    args = dict(segments=[_seg()], fixtures=[_rec(), _rec(source_id="fx-2",
               extras={"vin": "1FTEX1EP5FFA00002", "first_seen_at": "2026-10-21"})])
    r1 = run_probe(**args)
    r2 = run_probe(**args)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    assert pt.render_markdown(r1) == pt.render_markdown(r2)


def test_existing_app_behavior_is_unaffected():
    """Required test 10: probe imports do not pull in server/db/sniper.

    Checked in a fresh subprocess, because other test files in the same
    pytest process legitimately import server/db; a global sys.modules
    check here would fail by test ordering, not by probe behaviour.
    """
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    code = ("import probe_tuscaloosa, sys; "
            "print([m for m in ('server','db','sniper','billing','auth') "
            "if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], cwd=root,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
