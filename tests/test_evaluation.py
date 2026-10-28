"""
tests/test_evaluation.py — real-world evaluation harness, exercised with
SYNTHETIC fixture rows (tests/fixtures/eval_samples_FIXTURE.csv) and with
system-generated snapshots. Nothing here is a real-world result.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import register, set_plan  # noqa: F401
from tests.test_matching_and_history import L, make_user, score_all, world  # noqa: F401

FIX = str(Path(__file__).parent / "fixtures" / "eval_samples_FIXTURE.csv")


def test_fixture_rows_are_excluded_unless_requested(app):
    import evaluation
    assert evaluation.import_csv(FIX, fixture=True) == 6
    real = evaluation.report()
    assert real["observations"] == 0 and real["fixture_rows_excluded"] == 6
    demo = evaluation.report(include_fixtures=True)
    assert demo["data"] == "INCLUDES FIXTURE DATA — NOT REAL-WORLD"


def test_metrics_from_labeled_fixture(app):
    import evaluation
    evaluation.import_csv(FIX, fixture=True)
    r = evaluation.report(include_fixtures=True)
    # alerted & labeled pos/neg: f1 pos, f2 pos, f3 neg (f6 unavailable excluded)
    assert r["precision_of_alerts"]["numerator"] == 2
    assert r["precision_of_alerts"]["denominator"] == 3
    # reference set: positives f1,f2,f5 (f5 missed) ; negatives f3,f4 (f3 alerted)
    assert r["recall"]["value"] == round(2 / 3, 3)
    assert r["false_positive_rate"]["value"] == 0.5
    assert r["precision_of_alerts"]["enough_samples"] is False      # n < 20
    assert r["detection_latency"]["n"] == 5 and r["detection_latency"]["median_s"] == 600
    assert r["alert_delivery_latency"]["n"] == 3
    assert r["profit_estimate_vs_realized"] == {
        "n": 1, "mean_abs_error": 250, "mean_bias": 250, "enough_samples": False}


def test_recall_not_claimed_without_reference_set(app):
    import evaluation
    evaluation.label("craigslist", "x1", "genuine_opportunity")
    r = evaluation.report()
    assert r["recall"]["value"] is None and "reference set" in r["recall"]["why"]


def test_labels_are_validated_and_profit_requires_evidence(app):
    import evaluation
    with pytest.raises(ValueError):
        evaluation.label("s", "1", "great_deal")
    with pytest.raises(ValueError):
        evaluation.label("s", "1", "purchased_profit_verified")   # no realized profit
    with pytest.raises(ValueError):
        evaluation.label("s", "1", "genuine_opportunity", buyer_action="teleported")


def test_snapshot_captures_system_timeline_then_human_label(client, world):
    import evaluation, jobqueue, notifications
    make_user(client, "e@example.com", searches=[("c", {"model": "camry"})])
    from datetime import datetime, timedelta, timezone
    posted = (datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat()
    world.ingest([L("deal", 8100, posted_at=posted)])
    score_all()
    import db
    with db.session() as c:
        c.execute("UPDATE jobs SET status='done', finished_at=? WHERE kind='notify'",
                  (jobqueue.ts(),))
    key = evaluation.snapshot("craigslist:deal", in_reference_set=True)
    assert key == {"source": "craigslist", "source_listing_id": "deal"}
    evaluation.label("craigslist", "deal", "genuine_opportunity", buyer_action="contacted",
                     seller_contacted=True, labeled_by="owner")
    evaluation.snapshot("craigslist:deal")        # re-snapshot keeps the human label
    with db.session() as c:
        row = dict(c.execute("SELECT * FROM eval_observations").fetchone())
    assert row["alerted"] == 1 and row["outcome_label"] == "genuine_opportunity"
    assert row["alert_enqueued_at"] and row["notified_at"]
    assert row["evidence_class"] == "asking_comparables" and row["est_profit"] is not None
    assert row["posted_at_kind"] == "seller_posted"
    r = evaluation.report()
    assert r["detection_latency"]["n"] == 1 and 400 < r["detection_latency"]["median_s"] < 600
    assert r["precision_of_alerts"]["value"] == 1.0
    assert r["opportunities_per_active_user"]["active_users"] == 1


def test_valuation_error_and_coverage_from_fixture(app):
    import evaluation
    evaluation.import_csv(FIX, fixture=True)
    r = evaluation.report(include_fixtures=True)
    ve = r["valuation_error"]
    # f1: estimated 10,000 vs confirmed sale 9,800 -> +2.0%
    assert ve["n"] == 1 and ve["median_abs_pct_error"] == 2.0 and ve["mean_bias_pct"] == 2.0
    assert ve["interval_coverage"]["value"] is None          # fixture stores no range
    assert set(ve["by_evidence_class"]) == {"asking_comparables"}
    assert ve["enough_samples"] is False
    cov = r["coverage"]
    assert cov["by_source"]["fixture"]["observations"] == 6
    assert cov["by_source"]["fixture"]["valued_pct"] == 100.0
    assert cov["by_source"]["fixture"]["alerted"] == 4
    assert cov["by_territory"] == {"unknown": cov["by_source"]["fixture"]}


def test_valuation_error_not_claimed_without_sales(app):
    import evaluation
    evaluation.label("craigslist", "x1", "genuine_opportunity")
    r = evaluation.report()
    assert r["valuation_error"] == {"n": 0, "why": "no confirmed sale amounts recorded"}
