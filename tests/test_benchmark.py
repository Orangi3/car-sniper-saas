"""
tests/test_benchmark.py — the labeled benchmark runs, and the categories
that must never produce a trusted "deal" don't.

The benchmark is synthetic (see benchmarks/cases.json); these assertions
guard behavior on known edge cases, not real-world accuracy.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))

SAFETY = {"salvage", "needs_repair", "misleading_price", "suspicious_claims",
          "sparse_data", "dealer", "unrealistic_asking"}


def test_benchmark_runs_and_reports_all_metrics():
    import run_benchmark
    out = run_benchmark.run()
    m = out["metrics"]
    assert m["cases"] == len(out["cases"]) >= 25
    assert m["tp"] + m["fp"] + m["tn"] + m["fn"] == m["cases"]
    for k in ("precision", "recall", "false_positive_rate", "false_negative_rate"):
        assert m[k] is not None


def test_no_false_positives_in_safety_categories():
    import run_benchmark
    bad = [c["id"] for c in run_benchmark.run()["cases"]
           if c["category"] in SAFETY and c["predicted"]]
    assert bad == []


def test_known_limitation_is_still_visible():
    """A seller mislabeling the trim fools a text-only pipeline. Kept in the
    set so the limitation stays measured rather than hidden."""
    import run_benchmark
    case = {c["id"]: c for c in run_benchmark.run()["cases"]}["trim-mismatch-fp"]
    assert case["outcome"] == "FP"
