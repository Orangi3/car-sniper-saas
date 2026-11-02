"""
tests/test_probe_tuscaloosa.py — offline fixture-probe tests.

These tests are deliberately independent of the app's database fixtures:
they import only probe_tuscaloosa, geo, and sources.base, make no
network calls, and never touch a production database or notification
path. They do NOT prove anything about real inventory, valuation, or
licensing; they prove the harness behaves deterministically on fixtures.

Synthetic identifiers are unmistakably synthetic (FIXTURE-VIN-*), never
realistic VINs.
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

BASE = {"segment": "trucks", "source_id": "fx-1", "title": "2015 Ford F-150 XLT",
        "price": 9000, "year": 2015, "make": "Ford", "model": "F-150",
        "odometer": 120000, "zip": "35401", "latitude": None, "longitude": None,
        "posted_at": "2026-10-20", "extras": {"vin": "FIXTURE-VIN-001",
                                        "first_seen_at": "2026-10-22",
                                        "photo_hash": "fixture-photo-1"}}


def _rec(**over):
    d = dict(BASE)
    d.update(over)
    d["extras"] = dict(BASE["extras"])
    d["extras"].update(over.get("extras", {}))
    return d


def _seg(**over):
    d = {"id": "trucks", "name": "Trucks", "min_price": 2000, "max_price": 15000}
    d.update(over)
    return Segment.from_dict(d)


def test_zero_network_access():
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
    segs = [Segment.from_dict({"id": f"s{i}", "name": f"S{i}"}) for i in range(10)]
    fixtures = [{"segment": f"s{i}", **{k: v for k, v in _rec().items() if k != "segment"},
                 "source_id": f"fx-{i}"} for i in range(10)]
    report = run_probe(segments=segs, fixtures=fixtures, budget_cap=3)
    assert report["budget"]["used"] == 3
    assert report["budget"]["stopped_at_cap"] is True
    assert sum(1 for s in report["per_search"].values() if s["stopped_at_cap"]) == 7


def test_retries_and_pagination_are_accounted():
    budget = SimulatedBudget(cap=450)
    assert budget.reserve(3, kind="page", segment="trucks") is True
    assert budget.reserve(1, kind="retry-failed", segment="trucks") is True
    assert budget.reserve(1, kind="retry", segment="trucks") is True
    assert budget.used == 5
    assert len(budget.ledger) == 3
    assert all(e["accepted"] for e in budget.ledger)


def test_blocked_providers_remain_inaccessible():
    for sid in ("ebay", "marketcheck_fsbo", "marketcheck", "craigslist", "facebook"):
        with pytest.raises(BlockedSourceError):
            run_probe(segments=[_seg()], fixtures=[_rec()], source_ids=(sid,))


def test_no_credentials_or_sensitive_data_leak_into_reports():
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
    # Same VIN, same segment, later first_seen, different source_id:
    # a genuine relist under the documented definition.
    dup = _rec(source_id="fx-2", extras={"first_seen_at": "2026-10-23"})
    report = run_probe(segments=[_seg()], fixtures=[_rec(), dup])
    assert report["listings_seen"] == 2
    assert report["unique_listings"] == 1
    assert report["relist_records"] == 1
    assert report["repeated_observations"] == 0
    # No VIN: same title + odometer + photo hash is also one unique,
    # and the second record (later first_seen, different source) is a relist.
    a = _rec(source_id="fx-a", extras={"vin": "", "photo_hash": "fixture-photo-9"})
    b = _rec(source_id="fx-b", title="2015 Ford F-150 XLT", odometer=120000,
             extras={"vin": "", "photo_hash": "fixture-photo-9", "first_seen_at": "2026-10-23"})
    report2 = run_probe(segments=[_seg()], fixtures=[a, b])
    assert report2["unique_listings"] == 1
    assert report2["relist_records"] == 1


def test_freshness_handles_missing_dates():
    # Missing posted date, valid first_seen: lag unknown, but the
    # known first_seen date still populates first_seen_by_day.
    no_posted = _rec(source_id="fx-noposted", posted_at=None,
                     extras={"vin": "FIXTURE-VIN-009", "first_seen_at": "2026-10-22"})
    report = run_probe(segments=[_seg()], fixtures=[_rec(), no_posted])
    assert report["freshness"]["unknown_date_listings"] == 1
    assert report["freshness"]["count_with_dates"] == 1
    assert report["freshness"]["median_days"] == 2
    # Both unique vehicles were first seen on 2026-10-22: the default
    # fixture and the no-posted-date fixture.
    assert report["first_seen_by_day"] == {"2026-10-22": 2}


def test_repeated_runs_produce_identical_report_data():
    args = dict(segments=[_seg()], fixtures=[_rec(), _rec(source_id="fx-2",
               extras={"vin": "FIXTURE-VIN-002", "first_seen_at": "2026-10-21"})])
    r1 = run_probe(**args)
    r2 = run_probe(**args)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    assert pt.render_markdown(r1) == pt.render_markdown(r2)


def test_existing_app_behavior_is_unaffected():
    """Checked in a fresh subprocess, because other test files in the same
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


# ---------- Review regressions: segment classification ----------

def _fullsize():
    return Segment.from_dict({"id": "trucks-fullsize", "name": "Full-size trucks",
                              "models": ["f150", "f250", "silverado", "sierra"]})


def _midsize():
    return Segment.from_dict({"id": "trucks-midsize", "name": "Midsize trucks",
                              "models": ["ranger", "colorado", "tacoma"]})


def test_accord_does_not_qualify_as_fullsize_truck():
    accord = pt.fixture_listing(_rec(title="2016 Honda Accord LX", make="Honda",
                                     model="Accord", price=11000, year=2016))
    assert _fullsize().matches(accord) is False


def test_f150_does_not_qualify_as_midsize_truck():
    f150 = pt.fixture_listing(_rec())
    assert _midsize().matches(f150) is False
    assert _fullsize().matches(f150) is True


def test_ranger_does_not_qualify_as_fullsize_truck():
    ranger = pt.fixture_listing(_rec(title="2018 Ford Ranger XLT", make="Ford",
                                     model="Ranger", price=12000, year=2018))
    assert _fullsize().matches(ranger) is False
    assert _midsize().matches(ranger) is True


def test_missing_or_ambiguous_model_does_not_match_category():
    no_model = pt.fixture_listing(_rec(model=None))
    assert _fullsize().matches(no_model) is False
    assert _midsize().matches(no_model) is False
    ambiguous = pt.fixture_listing(_rec(model="Truck"))
    assert _fullsize().matches(ambiguous) is False
    assert _midsize().matches(ambiguous) is False


def test_price_year_band_claims_no_category():
    band = _seg(id="band-2008-2020", name="General band")
    assert band.kind == "price_year_band"
    assert _fullsize().kind == "category"
    # A band match is a price/year match only; it does not claim the
    # vehicle is a truck, SUV, sedan, or anything else.
    accord = pt.fixture_listing(_rec(title="2016 Honda Accord LX", make="Honda",
                                     model="Accord", price=11000, year=2016))
    assert band.matches(accord) is True


# ---------- Review regressions: cross-segment dedupe ----------

def test_cross_segment_dedupe_both_orders():
    segs_a = [_fullsize(), _seg(id="band-2008-2020", name="Band")]
    segs_b = list(reversed(segs_a))
    fixtures = [_rec(segment="trucks-fullsize"),
                _rec(segment="band-2008-2020", source_id="fx-2")]
    r1 = run_probe(segments=segs_a, fixtures=fixtures)
    r2 = run_probe(segments=segs_b, fixtures=fixtures)
    for r in (r1, r2):
        assert r["unique_listings"] == 1
        assert r["global_matches"] == 1
        assert r["relist_records"] == 0
        assert r["repeated_observations"] == 0
        assert r["per_search"]["trucks-fullsize"]["unique"] == 1
        assert r["per_search"]["trucks-fullsize"]["matches"] == 1
        assert r["per_search"]["band-2008-2020"]["unique"] == 1
        assert r["per_search"]["band-2008-2020"]["matches"] == 1
    assert r1["per_search"] == r2["per_search"]
    # Per-segment matches sum to 2, but global matches is 1: summing
    # per-segment totals would double-count this vehicle.
    assert sum(s["matches"] for s in r1["per_search"].values()) == 2
    assert r1["global_matches"] == 1


def test_genuine_relist_vs_repeated_observation():
    # Genuine relist: later first_seen, different source, same segment.
    r = run_probe(segments=[_seg()], fixtures=[
        _rec(), _rec(source_id="fx-2", extras={"first_seen_at": "2026-10-23"})])
    assert r["relist_records"] == 1 and r["repeated_observations"] == 0
    # Repeated observation: same first_seen, different source, same segment.
    r = run_probe(segments=[_seg()], fixtures=[
        _rec(), _rec(source_id="fx-2", extras={"first_seen_at": "2026-10-22"})])
    assert r["relist_records"] == 0 and r["repeated_observations"] == 1
    # Repeated observation: earlier first_seen, different source.
    r = run_probe(segments=[_seg()], fixtures=[
        _rec(), _rec(source_id="fx-2", extras={"first_seen_at": "2026-10-21"})])
    assert r["relist_records"] == 0 and r["repeated_observations"] == 1


# ---------- Review regressions: freshness ----------

def test_freshness_missing_first_seen_is_unknown_not_zero():
    missing = _rec(source_id="fx-missing", posted_at="2026-10-19",
                   extras={"vin": "FIXTURE-VIN-010"})
    # extras without first_seen_at: _rec merges, so remove it explicitly.
    missing["extras"].pop("first_seen_at", None)
    report = run_probe(segments=[_seg()], fixtures=[missing])
    assert report["freshness"]["unknown_date_listings"] == 1
    assert report["freshness"].get("count_with_dates", 0) == 0
    assert "median_days" not in report["freshness"]
    assert report["first_seen_by_day"] == {}


def test_freshness_malformed_and_out_of_order_dates():
    malformed = _rec(source_id="fx-mal", posted_at="not-a-date",
                     extras={"vin": "FIXTURE-VIN-011", "first_seen_at": "2026-10-21"})
    report = run_probe(segments=[_seg()], fixtures=[malformed])
    assert report["freshness"]["unknown_date_listings"] == 1
    # First_seen before posted: invalid, never a negative lag.
    inverted = _rec(source_id="fx-inv", posted_at="2026-10-25",
                    extras={"vin": "FIXTURE-VIN-012", "first_seen_at": "2026-10-21"})
    report = run_probe(segments=[_seg()], fixtures=[inverted])
    assert report["freshness"]["invalid_date_listings"] == 1
    assert report["freshness"].get("count_with_dates", 0) == 0
    assert "median_days" not in report["freshness"]
    assert report["first_seen_by_day"] == {}


def test_freshness_future_run_date_is_invalid():
    future = _rec(source_id="fx-fut", posted_at="2026-10-19",
                  extras={"vin": "FIXTURE-VIN-013", "first_seen_at": "2026-10-30"})
    report = run_probe(segments=[_seg()], fixtures=[future], run_date="2026-10-22")
    assert report["freshness"]["invalid_date_listings"] == 1
    assert report["first_seen_by_day"] == {}


def test_freshness_median_range_valid_unknown_invalid():
    fixtures = [
        _rec(source_id="fx-1", posted_at="2026-10-19",
             extras={"vin": "FIXTURE-VIN-020", "first_seen_at": "2026-10-21"}),  # lag 2
        _rec(source_id="fx-2", posted_at="2026-10-19",
             extras={"vin": "FIXTURE-VIN-021", "first_seen_at": "2026-10-23"}),  # lag 4
        _rec(source_id="fx-3", posted_at=None,
             extras={"vin": "FIXTURE-VIN-022", "first_seen_at": "2026-10-22"}),  # unknown lag
        _rec(source_id="fx-4", posted_at="2026-10-25",
             extras={"vin": "FIXTURE-VIN-023", "first_seen_at": "2026-10-21"}),  # invalid
    ]
    report = run_probe(segments=[_seg()], fixtures=fixtures)
    f = report["freshness"]
    assert f["count_with_dates"] == 2
    assert f["median_days"] == 3
    assert f["min_days"] == 2 and f["max_days"] == 4
    assert f["unknown_date_listings"] == 1
    assert f["invalid_date_listings"] == 1
    assert report["first_seen_by_day"] == {"2026-10-21": 1, "2026-10-22": 1,
                                           "2026-10-23": 1}


# ---------- Review regressions: concurrency ----------

def test_shared_poller_concurrent_requests_fetch_once():
    import threading
    import time
    budget = SimulatedBudget(cap=450)
    poller = SharedPoller(budget)
    barrier = threading.Barrier(8)
    calls = []

    def fetch():
        calls.append(1)
        time.sleep(0.05)
        return ["listing"]

    def worker():
        barrier.wait()
        poller.poll("trucks", fetch)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1
    assert budget.used == 1
    assert poller.poll_calls == 1


def test_shared_poller_fetch_exception_leaves_no_cache():
    budget = SimulatedBudget(cap=450)
    poller = SharedPoller(budget)
    state = {"n": 0}

    def fetch():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("simulated fixture failure")
        return ["listing"]

    with pytest.raises(RuntimeError):
        poller.poll("trucks", fetch)
    # No cache: the next poll retries and succeeds. Budget charged for
    # the failed attempt (1) and the successful attempt (1).
    assert poller.poll("trucks", fetch) == ["listing"]
    assert budget.used == 2
    assert poller.poll_calls == 1
    # Third poll uses the cache and charges nothing.
    assert poller.poll("trucks", fetch) == ["listing"]
    assert budget.used == 2


# ---------- Review regressions: budget validation ----------

def test_budget_rejects_negative_and_zero_reservations():
    budget = SimulatedBudget(cap=10)
    with pytest.raises(ValueError):
        budget.reserve(0)
    with pytest.raises(ValueError):
        budget.reserve(-3)
    assert budget.used == 0
    assert budget.reserve(4) is True
    assert budget.used == 4
    # An invalid reservation cannot reduce usage or create capacity.
    with pytest.raises(ValueError):
        budget.reserve(-4)
    assert budget.used == 4
    assert budget.reserve(6) is True
    assert budget.used == 10
    assert budget.reserve(1) is False


def test_budget_rejects_non_positive_caps():
    with pytest.raises(ValueError):
        SimulatedBudget(cap=0)
    with pytest.raises(ValueError):
        SimulatedBudget(cap=-5)
    with pytest.raises(ValueError):
        run_probe(segments=[_seg()], fixtures=[], budget_cap=0)
    with pytest.raises(ValueError):
        run_probe(segments=[_seg()], fixtures=[], budget_cap=-1)


# ---------- Review regressions: pagination / retry ----------

def test_paged_poll_counts_pages_and_retries():
    budget = SimulatedBudget(cap=450)
    poller = SharedPoller(budget)
    attempts = {"n": 0}

    def fetch_page(page_index):
        attempts["n"] += 1
        if page_index == 0 and attempts["n"] == 1:
            raise RuntimeError("simulated page failure")
        return [f"page-{page_index}-item"]

    result = poller.poll_paged("trucks", fetch_page, page_count=3, retries=1)
    assert result == ["page-0-item", "page-1-item", "page-2-item"]
    # 1 failed page-0 attempt + 1 retry + page 1 + page 2 = 4 calls.
    assert budget.used == 4
    assert poller.poll_calls == 1
    # Cached: a second paged poll charges nothing.
    assert poller.poll_paged("trucks", fetch_page, page_count=3) == result
    assert budget.used == 4


def test_paged_poll_budget_exhaustion_leaves_no_partial_cache():
    budget = SimulatedBudget(cap=2)
    poller = SharedPoller(budget)

    def fetch_page(page_index):
        return [f"page-{page_index}"]

    with pytest.raises(Exception):
        poller.poll_paged("trucks", fetch_page, page_count=3)
    assert budget.used == 2
    assert poller.poll_calls == 0
    # No partial cache: a later poll does not return pages 0-1 alone.
    with pytest.raises(Exception):
        poller.poll_paged("trucks", fetch_page, page_count=3)


def test_run_probe_paged_budget_counts_pages():
    fixtures = [_rec(source_id=f"fx-{i}",
                     extras={"vin": f"FIXTURE-VIN-03{i}", "first_seen_at": "2026-10-22"})
                for i in range(3)]
    report = run_probe(segments=[_seg()], fixtures=fixtures, page_size=2)
    # 3 fixtures at 2 per page = 2 page calls for the one segment.
    assert report["budget"]["used"] == 2
    assert report["budget"]["poll_calls"] == 1
    assert report["unique_listings"] == 3


def test_empty_segment_poll_charge_is_explained():
    report = run_probe(segments=[_seg()], fixtures=[])
    assert report["budget"]["used"] == 1
    assert "zero fixtures" in report["budget_note"]
    assert report["per_search"]["trucks"]["listings_seen"] == 0


def test_sample_fixtures_use_synthetic_identifiers():
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parent.parent
                       / "probe_sample_fixtures.json").read_text())
    vins = [r["extras"]["vin"] for r in data]
    assert vins and all(v.startswith("FIXTURE-") for v in vins)
