"""
evaluation.py — real-world evaluation of deal finding.

Unit tests and the synthetic benchmark prove behavior on known cases; they
say nothing about whether customers find worthwhile cars. This module
tracks real listings and their real outcomes:

  * snapshot(composite_id)  — record what the SYSTEM knew and did: first
    seen, detection, alert enqueue + delivery times (from the jobs table),
    asking price, valuation, evidence, confidence, whether it alerted
  * label(...)              — a HUMAN records buyer action and outcome;
    nothing here infers an outcome
  * import_csv(path)        — manually collected, properly authorized samples
  * report()                — precision, recall/FPR (only with a complete
    reference set), latencies, opportunities per active user, estimated vs
    realized profit, valuation error against confirmed sale amounts, and
    coverage by source and territory — each with its sample size

Fixture rows (is_fixture) are for demonstrating the workflow and are
excluded from reports unless include_fixtures=True, in which case the
report is stamped as fixture data.

CLI:
    python -m evaluation snapshot <composite_id> [<composite_id> ...]
    python -m evaluation label <source> <listing_id> <outcome> [--action contacted] ...
    python -m evaluation import <file.csv> [--fixture]
    python -m evaluation report [--include-fixtures] [--json]
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from typing import Optional

OUTCOMES = {
    "genuine_opportunity", "weak_opportunity", "bad_comparison",
    "mechanical_condition_problem", "suspicious_listing", "duplicate",
    "listing_unavailable", "insufficient_evidence", "purchased_profit_verified",
}
POSITIVE = {"genuine_opportunity", "purchased_profit_verified"}
NEGATIVE = {"weak_opportunity", "bad_comparison", "mechanical_condition_problem",
            "suspicious_listing", "duplicate"}
# listing_unavailable / insufficient_evidence: outcome unknown -> excluded
BUYER_ACTIONS = {"none", "viewed", "contacted", "negotiated", "purchased", "passed"}
MIN_N = 20   # below this, a metric is reported but marked "too few samples"

_FIELDS = ("posted_at", "posted_at_kind", "first_seen_at", "detected_at",
           "alert_enqueued_at", "notified_at", "asking_price", "est_value",
           "value_low", "value_high", "est_profit", "evidence_class", "confidence",
           "alerted", "in_reference_set", "buyer_action", "seller_contacted",
           "sold_confirmed", "sale_amount", "acquisition_costs", "realized_profit",
           "outcome_label", "labeled_by", "notes", "is_fixture", "composite_id")
_BOOLS = {"alerted", "in_reference_set", "seller_contacted", "sold_confirmed", "is_fixture"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert(conn, source: str, listing_id: str, values: dict) -> None:
    values = {k: (bool(v) if k in _BOOLS and v is not None else v)
              for k, v in values.items() if k in _FIELDS}
    now = _now()
    cols = list(values)
    conn.execute(
        f"INSERT INTO eval_observations(source, source_listing_id, "
        f"{', '.join(cols + ['created_at', 'updated_at'])}) "
        f"VALUES ({', '.join('?' for _ in range(len(cols) + 4))}) "
        f"ON CONFLICT(source, source_listing_id) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in cols + ["updated_at"]),
        (source, listing_id, *values.values(), now, now))


# Sources whose posted_at is the seller's own post time. For others (e.g.
# MarketCheck's first_seen) it is the provider's first observation.
SELLER_POSTED_SOURCES = {"craigslist", "ebay"}


def snapshot(composite_id: str, *, in_reference_set: bool = False) -> Optional[dict]:
    """Record the system's view of one listing. Safe to call repeatedly."""
    import db as _db
    with _db.session() as conn:
        l = conn.execute("SELECT * FROM listings WHERE composite_id = ?",
                         (composite_id,)).fetchone()
        if not l:
            return None
        l = dict(l)
        nm = conn.execute("SELECT * FROM near_misses WHERE composite_id = ?",
                          (composite_id,)).fetchone()
        nm = dict(nm) if nm else {}
        alert = conn.execute("SELECT id FROM alerts WHERE composite_id = ?",
                             (composite_id,)).fetchone()
        matched = conn.execute("SELECT COUNT(*) FROM user_matches WHERE composite_id = ?",
                               (composite_id,)).fetchone()[0]
        # notify jobs carry the composite id inside their payload
        like = f'%"composite_id": "{composite_id}"%'
        jobs = conn.execute(
            "SELECT created_at, finished_at, status FROM jobs WHERE kind = 'notify' "
            "AND payload LIKE ?", (like,)).fetchall()
        enq = min((j[0] for j in jobs), default=None)
        sent = min((j[1] for j in jobs if j[2] == "done" and j[1]), default=None)
        sid = str(l["source_id"])
        import profit as profit_mod
        pr = profit_mod.compute(l.get("price"), nm.get("value_low"), nm.get("comp_avg"),
                                nm.get("value_high"), nm.get("evidence_class"), None,
                                scenarios=False)
        _upsert(conn, l["source"], sid, {
            "est_profit": pr["scenarios"]["base"]["profit"] if pr else None,
            "composite_id": composite_id,
            "posted_at": l.get("posted_at"),
            "posted_at_kind": ("seller_posted" if l["source"] in SELLER_POSTED_SOURCES
                               and l.get("posted_at") else
                               "provider_first_seen" if l.get("posted_at") else "unknown"),
            "first_seen_at": l.get("first_seen_at"), "detected_at": l.get("first_seen_at"),
            "alert_enqueued_at": enq, "notified_at": sent,
            "asking_price": l.get("price"), "est_value": nm.get("comp_avg"),
            "value_low": nm.get("value_low"), "value_high": nm.get("value_high"),
            "evidence_class": nm.get("evidence_class"), "confidence": nm.get("confidence"),
            "alerted": bool(alert) or matched > 0,
            "in_reference_set": in_reference_set,
        })
    return {"source": l["source"], "source_listing_id": sid}


def label(source: str, listing_id: str, outcome: str, *, buyer_action: Optional[str] = None,
          seller_contacted: Optional[bool] = None, sold_confirmed: Optional[bool] = None,
          sale_amount: Optional[int] = None, acquisition_costs: Optional[int] = None,
          realized_profit: Optional[int] = None, labeled_by: str = "",
          notes: str = "", in_reference_set: Optional[bool] = None) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}")
    if buyer_action is not None and buyer_action not in BUYER_ACTIONS:
        raise ValueError(f"buyer_action must be one of {sorted(BUYER_ACTIONS)}")
    if outcome == "purchased_profit_verified" and realized_profit is None:
        raise ValueError("purchased_profit_verified requires realized_profit")
    vals = {"outcome_label": outcome, "labeled_by": labeled_by, "notes": notes}
    for k, v in (("buyer_action", buyer_action), ("seller_contacted", seller_contacted),
                 ("sold_confirmed", sold_confirmed), ("sale_amount", sale_amount),
                 ("acquisition_costs", acquisition_costs),
                 ("realized_profit", realized_profit),
                 ("in_reference_set", in_reference_set)):
        if v is not None:
            vals[k] = v
    import db as _db
    with _db.session() as conn:
        _upsert(conn, source, str(listing_id), vals)


def import_csv(path: str, *, fixture: bool = False) -> int:
    """Rows need source, source_listing_id; any other _FIELDS columns are
    taken as given (empty = unknown)."""
    import db as _db
    n = 0
    with open(path, newline="") as f, _db.session() as conn:
        for row in csv.DictReader(f):
            if not row.get("source") or not row.get("source_listing_id"):
                continue
            vals = {}
            for k in _FIELDS:
                v = (row.get(k) or "").strip()
                if v == "":
                    continue
                if k in _BOOLS:
                    vals[k] = v.lower() in ("1", "true", "yes", "y")
                elif k in ("asking_price", "sale_amount", "acquisition_costs",
                           "realized_profit"):
                    vals[k] = int(float(v))
                elif k in ("est_value", "value_low", "value_high", "est_profit"):
                    vals[k] = float(v)
                else:
                    vals[k] = v
            if vals.get("outcome_label") and vals["outcome_label"] not in OUTCOMES:
                raise ValueError(f"row {n + 1}: unknown outcome {vals['outcome_label']}")
            if fixture:
                vals["is_fixture"] = True
            _upsert(conn, row["source"], row["source_listing_id"], vals)
            n += 1
    return n


# ---------- report --------------------------------------------------------

def _secs(a, b) -> Optional[float]:
    try:
        return (datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))).total_seconds()
    except (TypeError, ValueError):
        return None


def _dist(vals: list[float]) -> dict:
    vals = sorted(v for v in vals if v is not None and v >= 0)
    if not vals:
        return {"n": 0}
    p90 = vals[min(len(vals) - 1, int(round(0.9 * (len(vals) - 1))))]
    return {"n": len(vals), "median_s": round(statistics.median(vals), 1),
            "p90_s": round(p90, 1), "enough_samples": len(vals) >= MIN_N}


def _ratio(a: int, b: int) -> dict:
    return {"value": round(a / b, 3) if b else None, "numerator": a, "denominator": b,
            "enough_samples": b >= MIN_N}


def report(include_fixtures: bool = False, active_user_days: int = 30) -> dict:
    import db as _db
    with _db.session() as conn:
        where = "" if include_fixtures else " WHERE e.is_fixture = FALSE"
        rows = [dict(r) for r in conn.execute(
            "SELECT e.*, l.state AS listing_state FROM eval_observations e "
            f"LEFT JOIN listings l ON l.composite_id = e.composite_id{where}")]
        n_fixture = int(conn.execute("SELECT COUNT(*) FROM eval_observations "
                                     "WHERE is_fixture = TRUE").fetchone()[0])
        try:
            from datetime import timedelta
            since = (datetime.now(timezone.utc) - timedelta(days=active_user_days)).isoformat()
            m = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM user_matches "
                "WHERE created_at >= ?", (since,)).fetchone()
            active = int(conn.execute(
                "SELECT COUNT(DISTINCT s.user_id) FROM saved_searches s "
                "JOIN users u ON u.id = s.user_id WHERE s.notify_enabled = TRUE "
                "AND u.plan IN ('starter', 'pro')").fetchone()[0])
            matches = int(m[0])
        except Exception as e:          # pre-migration DB: report it, don't hide it
            matches, active = None, None
            print(f"[evaluation] match stats unavailable: {e}", file=sys.stderr)

    labeled_alerts = [r for r in rows if r["alerted"] and r["outcome_label"] in POSITIVE | NEGATIVE]
    tp = sum(1 for r in labeled_alerts if r["outcome_label"] in POSITIVE)
    ref = [r for r in rows if r["in_reference_set"] and r["outcome_label"] in POSITIVE | NEGATIVE]
    ref_pos = [r for r in ref if r["outcome_label"] in POSITIVE]
    ref_neg = [r for r in ref if r["outcome_label"] in NEGATIVE]
    det = [_secs(r["posted_at"], r["detected_at"]) for r in rows
           if r["posted_at_kind"] == "seller_posted"]
    enq = [_secs(r["detected_at"], r["alert_enqueued_at"]) for r in rows if r["alert_enqueued_at"]]
    dlv = [_secs(r["detected_at"], r["notified_at"]) for r in rows if r["notified_at"]]
    prof = [(r["est_profit"], r["realized_profit"]) for r in rows
            if r["outcome_label"] == "purchased_profit_verified"
            and r["realized_profit"] is not None and r["est_profit"] is not None]
    by_label: dict[str, int] = {}
    for r in rows:
        by_label[r["outcome_label"] or "unlabeled"] = by_label.get(r["outcome_label"] or "unlabeled", 0) + 1
    out = {
        "data": "INCLUDES FIXTURE DATA — NOT REAL-WORLD" if include_fixtures and n_fixture
                else "real observations only",
        "observations": len(rows), "fixture_rows_excluded": 0 if include_fixtures else n_fixture,
        "by_label": by_label,
        "precision_of_alerts": _ratio(tp, len(labeled_alerts)),
        "recall": (_ratio(sum(1 for r in ref_pos if r["alerted"]), len(ref_pos)) if ref_pos
                   else {"value": None, "why": "no labeled reference set (in_reference_set)"}),
        "false_positive_rate": (_ratio(sum(1 for r in ref_neg if r["alerted"]), len(ref_neg))
                                if ref_neg else {"value": None,
                                                 "why": "no labeled reference set"}),
        "detection_latency": {**_dist(det), "basis": "seller-posted time -> first seen"},
        "alert_enqueue_latency": {**_dist(enq), "basis": "first seen -> notification queued"},
        "alert_delivery_latency": {**_dist(dlv), "basis": "first seen -> provider accepted"},
        "opportunities_per_active_user": (
            {"value": round(matches / active, 2) if active else None, "matches": matches,
             "active_users": active, "window_days": active_user_days}
            if matches is not None else {"value": None, "why": "unavailable"}),
        "profit_estimate_vs_realized": (
            {"n": len(prof),
             "mean_abs_error": round(statistics.mean(abs(e - r) for e, r in prof)),
             "mean_bias": round(statistics.mean(e - r for e, r in prof)),
             "enough_samples": len(prof) >= MIN_N} if prof else {"n": 0}),
        "valuation_error": valuation_error(rows),
        "coverage": coverage(rows),
        "min_samples_for_confidence": MIN_N,
    }
    return out


def valuation_error(rows: list[dict]) -> dict:
    """Estimated market value vs the independently confirmed sale amount,
    overall and per evidence class. interval_coverage = share of sales that
    landed inside the reported value range (only rows that stored one)."""
    def summarize(rs):
        errs = [(r["est_value"] - r["sale_amount"]) / r["sale_amount"] for r in rs]
        inside = [r["value_low"] <= r["sale_amount"] <= r["value_high"] for r in rs
                  if r.get("value_low") is not None and r.get("value_high") is not None]
        return {"n": len(rs),
                "median_abs_pct_error": round(100 * statistics.median(abs(e) for e in errs), 1),
                "mean_bias_pct": round(100 * statistics.mean(errs), 1),
                "interval_coverage": (_ratio(sum(inside), len(inside)) if inside
                                      else {"value": None, "why": "no stored ranges"}),
                "enough_samples": len(rs) >= MIN_N}
    sold = [r for r in rows if r["sold_confirmed"] and r.get("sale_amount")
            and r.get("est_value")]
    if not sold:
        return {"n": 0, "why": "no confirmed sale amounts recorded"}
    by_class: dict[str, list] = {}
    for r in sold:
        by_class.setdefault(r.get("evidence_class") or "unknown", []).append(r)
    return {**summarize(sold), "basis": "estimated value vs confirmed sale amount",
            "by_evidence_class": {k: summarize(v) for k, v in sorted(by_class.items())}}


def coverage(rows: list[dict]) -> dict:
    """How much of what we observe we can value, alert on and label, per
    source and per territory (listing state; 'unknown' when not linked)."""
    def agg(rs):
        n = len(rs)
        return {"observations": n,
                "valued_pct": round(100 * sum(1 for r in rs if r.get("est_value")) / n, 1),
                "alerted": sum(1 for r in rs if r["alerted"]),
                "labeled": sum(1 for r in rs if r.get("outcome_label")),
                "enough_samples": n >= MIN_N}
    out: dict[str, dict] = {"by_source": {}, "by_territory": {}}
    for key, field in (("by_source", "source"), ("by_territory", "listing_state")):
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r.get(field) or "unknown", []).append(r)
        out[key] = {k: agg(v) for k, v in sorted(groups.items())}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evaluation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot"); s.add_argument("ids", nargs="+")
    s.add_argument("--reference", action="store_true")
    lb = sub.add_parser("label")
    for a in ("source", "listing_id", "outcome"):
        lb.add_argument(a)
    lb.add_argument("--action"); lb.add_argument("--realized-profit", type=int)
    lb.add_argument("--sale-amount", type=int); lb.add_argument("--by", default="")
    lb.add_argument("--notes", default=""); lb.add_argument("--contacted", action="store_true")
    im = sub.add_parser("import"); im.add_argument("path"); im.add_argument("--fixture", action="store_true")
    rp = sub.add_parser("report"); rp.add_argument("--include-fixtures", action="store_true")
    rp.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        for cid in a.ids:
            print(cid, "->", snapshot(cid, in_reference_set=a.reference))
    elif a.cmd == "label":
        label(a.source, a.listing_id, a.outcome, buyer_action=a.action,
              realized_profit=a.realized_profit, sale_amount=a.sale_amount,
              seller_contacted=a.contacted or None, labeled_by=a.by, notes=a.notes)
    elif a.cmd == "import":
        print(import_csv(a.path, fixture=a.fixture), "rows")
    else:
        print(json.dumps(report(include_fixtures=a.include_fixtures), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
