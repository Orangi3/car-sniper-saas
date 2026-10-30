"""
validate_deals.py — real-deal validation harness (owner tool; never
customer-facing).

    python -m validate_deals run --sources ebay --zip 35401 --radius 150
    python -m validate_deals run --polls 4 --interval 1800 --out validation-runs/tusc-1
    python -m validate_deals report validation-runs/tusc-1   # rebuild + reviewed metrics
    python -m validate_deals purge validation-runs/tusc-1    # drop listing data

`run` does, in order:
  1. refuses every source that is not licensed for a paid product
     (sources.licensing_block in commercial mode) or has no credentials
  2. polls each permitted source a bounded number of times, at least the
     source's minimum interval apart, for one territory and price band
  3. ingests through the production pipeline into a PRIVATE SQLite database
     in the run directory: normalize -> dedupe (listing id, cross-posts) ->
     price-change detection -> valuation -> risk flags; after the last poll
     every listing is valued again against everything the run collected
  4. writes report.json (provenance, requests, freshness, every candidate
     with its comparables and reasons) and review.csv (one row per candidate
     for a person to label; existing labels are kept on re-runs)

Isolation: the configured database (DATABASE_URL / SNIPER_DB_PATH) is never
opened. The run database has no users or saved searches, text notifications
are switched off, and notification jobs are never processed — nothing here
can reach a customer. Sold-comparable lookups are off unless --sold-comps
(they cost provider calls); values then come from asking prices only.

Provenance: each source's data is stamped "real" (the provider's production
endpoint), "sandbox" (eBay's test data — not real listings) or "stand_in"
(a local or staging endpoint). Exit code 0 means real listings were
retrieved and processed; 3 = data came back but none of it was real;
2 = no permitted source had credentials; 1 = nothing was retrieved.

Estimated values and profits are estimates from asking-price comparables
with default assumptions — never verified profits. Precision is computed
only from human labels in review.csv (outcomes as in evaluation.OUTCOMES).
The run directory holds provider data (titles, URLs, prices): keep it
private and `purge` it when the review is done (eBay's API License Agreement
limits how long such copies may be kept).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

HARNESS_SOURCES = ("ebay", "marketcheck_fsbo")     # licensed API adapters
BAND = (2000, 15000)
CANDIDATE_MIN_DISCOUNT_PCT = 10    # the deal feeds' own floor (server.api_deals)
MAX_POLLS = 48
MIN_REVIEWED = 20                  # below this a precision figure is not a finding
REAL_ENDPOINTS = {"ebay": "https://api.ebay.com/", "marketcheck_fsbo": "https://api.marketcheck.com/"}
REVIEW_COLUMNS = ("source", "source_listing_id", "trust", "discount_pct", "asking_price",
                  "est_value", "confidence", "risk_flags", "title", "url", "first_seen_at",
                  "last_confirmed_at", "outcome_label", "buyer_action", "labeled_by", "notes")
LABEL_COLUMNS = ("outcome_label", "buyer_action", "labeled_by", "notes")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def provenance(module) -> tuple[str, str]:
    """("real" | "sandbox" | "stand_in", endpoint base) for the URL the
    adapter will actually call."""
    url = getattr(module, "BROWSE_URL", None) or getattr(module, "SEARCH_URL", None) or ""
    base = "/".join(url.split("/")[:3])
    if url.startswith(REAL_ENDPOINTS.get(module.SOURCE_ID, "\0")):
        return "real", base
    if url.startswith("https://api.sandbox.ebay.com/"):
        return "sandbox", base
    return "stand_in", base


def _isolate(run_dir: Path) -> None:
    """Point every database access at the run's own SQLite file. Must run
    before anything imports `db` (cached modules are dropped)."""
    os.environ["SNIPER_DB_PATH"] = str(run_dir / "harness.db")
    os.environ.pop("DATABASE_URL", None)
    os.environ["SNIPER_COMMERCIAL_MODE"] = "1"
    for mod in [m for m in sys.modules if m in ("db", "config", "sniper", "jobqueue",
                                                 "valuation", "comps", "evaluation")
                or m.startswith(("migrations", "sources"))]:
        sys.modules.pop(mod, None)
    from migrations import runner
    runner.run_pending(verbose=False)


def _code_version() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=Path(__file__).parent, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _polls(run_dir: Path) -> list[dict]:
    p = run_dir / "polls.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


# ---------- run ---------------------------------------------------------------

def run(run_dir: Path, source_ids=("ebay",), zip_code: str = "35401", radius: int = 150,
        band: tuple[int, int] = BAND, polls: int = 1, interval_s: int = 0,
        sold_comps: bool = False, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Poll, ingest and value into the CURRENT database (main() isolates it
    first), then write the report. Returns the report."""
    import comps
    import jobqueue
    import sniper
    import sources
    from config import CONFIG
    from sources import health
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cfg = json.loads(json.dumps(CONFIG))                 # never mutate the global config
    cfg.update(commercial_mode=True, zip=zip_code, radius_mi=int(radius),
               min_price=int(band[0]), max_price=int(band[1]))
    mods = {m.SOURCE_ID: m for m in sources.all_sources()}
    plan, refused = [], []
    for sid in source_ids:
        m = mods.get(sid)
        src = cfg.setdefault("sources", {}).setdefault(sid, {})
        src.pop("zip", None), src.pop("radius_mi", None)   # the run's territory wins
        why = ("not a licensed API adapter the harness runs" if sid not in HARNESS_SOURCES or not m
               else sources.licensing_block(m, cfg))
        if why:
            refused.append({"source": sid, "why": why, "kind": "licensing"})
        elif getattr(m, "config_issue", lambda c: None)(cfg):
            refused.append({"source": sid, "why": m.config_issue(cfg), "kind": "credentials"})
        else:
            plan.append(m)

    saved = {k: CONFIG.get(k) for k in ("notify_text_enabled", "commercial_mode",
                                         "min_price", "max_price")}
    saved_reload, saved_sold = sniper._config_mod.reload, comps.raw_sold
    CONFIG.update(notify_text_enabled=False, commercial_mode=True,
                  min_price=int(band[0]), max_price=int(band[1]))
    sniper._config_mod.reload = lambda: None            # keep notifications off mid-run
    if not sold_comps:
        comps.raw_sold = lambda *a, **k: ({}, 0)
    log = run_dir / "polls.jsonl"
    first = max((p["poll"] for p in _polls(run_dir)), default=0)
    try:
        for i in range(1, polls + 1 if plan else 1):
            if i > 1:
                sleep(interval_s)
            for m in plan:
                rep = health.begin(m.SOURCE_ID)
                t0, err = time.monotonic(), None
                try:
                    got = m.poll(cfg) or []
                except Exception as e:                 # recorded, never hidden
                    got, err = [], f"{type(e).__name__}: {e}"[:200]
                status, detail = health.classify(rep, len(got), error=err, ever_succeeded=False)
                res = sniper.ingest(got)
                jobqueue.drain({"score": sniper.score_job}, max_jobs=20000)
                kind, endpoint = provenance(m)
                with log.open("a") as f:
                    f.write(json.dumps({
                        "poll": first + i, "at": _now(), "source": m.SOURCE_ID,
                        "provenance": kind, "endpoint": endpoint, "status": status,
                        "detail": detail, "elapsed_s": round(time.monotonic() - t0, 2),
                        "requests": [{"label": a["label"], "outcome": a["outcome"],
                                      "http_status": a["status"]} for a in rep.attempts],
                        "listings": len(got), "new": len(res["new"]),
                        "price_changed": len(res["price_changed"]),
                        "dropped_no_price": res["dropped_no_price"],
                        "ids": [x.composite_id() for x in got]}) + "\n")
        with sniper.db() as conn:                      # final pass: full peer set
            ids = [r[0] for r in conn.execute("SELECT composite_id FROM listings")]
        for cid in ids:
            sniper._score_one_listing(cid)
    finally:
        CONFIG.update(saved)
        sniper._config_mod.reload, comps.raw_sold = saved_reload, saved_sold
    meta = {"territory": {"zip": zip_code, "radius_mi": int(radius)},
            "price_band": list(band), "polls_requested": polls, "interval_s": interval_s,
            "sold_comps": sold_comps, "refused": refused,
            "started_polls_at": first + 1}
    prev = run_dir / "run.json"
    runs = json.loads(prev.read_text()) if prev.exists() else []
    runs.append({**meta, "finished_at": _now(), "code_version": _code_version()})
    prev.write_text(json.dumps(runs, indent=2))
    return build_report(run_dir)


# ---------- report ------------------------------------------------------------

def _hours(a: Optional[str], b: datetime) -> Optional[float]:
    try:
        t = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        return round(((b - (t if t.tzinfo else t.replace(tzinfo=timezone.utc)))
                      .total_seconds()) / 3600, 2)
    except (TypeError, ValueError):
        return None


def _shown_now(r: dict, now: datetime) -> bool:
    """Would the customer app display it now (server._displayable rules)?"""
    import sources
    if r.get("is_auction") and r.get("auction_end_at") and (_hours(r["auction_end_at"], now) or 0) >= 0:
        return False
    hrs = {m.SOURCE_ID: getattr(m, "DISPLAY_MAX_AGE_HOURS", None)
           for m in sources.all_sources()}.get(r["source"])
    age = _hours(r.get("last_seen_at") or r.get("first_seen_at"), now)
    return not hrs or (age is not None and age <= hrs)


def _read_review(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {(r.get("source"), r.get("source_listing_id")): r for r in csv.DictReader(f)}


def review_metrics(rows: list[dict]) -> dict:
    """False-positive tracking from human labels. Unlabeled rows count as
    unreviewed; listing_unavailable / insufficient_evidence are not
    outcomes and are reported separately."""
    from evaluation import NEGATIVE, POSITIVE
    out = {}
    for trust in ("trusted", "risk"):
        rs = [r for r in rows if r.get("trust") == trust]
        lab = [r for r in rs if r.get("outcome_label")]
        pos = sum(1 for r in lab if r["outcome_label"] in POSITIVE)
        neg = [r["outcome_label"] for r in lab if r["outcome_label"] in NEGATIVE]
        judged = pos + len(neg)
        out[trust] = {
            "candidates": len(rs), "labeled": len(lab), "genuine": pos,
            "false_positives": len(neg),
            "false_positive_reasons": {k: neg.count(k) for k in sorted(set(neg))},
            "unavailable": sum(1 for r in lab if r["outcome_label"] == "listing_unavailable"),
            "insufficient_evidence": sum(1 for r in lab
                                         if r["outcome_label"] == "insufficient_evidence"),
            "precision": round(pos / judged, 3) if judged else None,
            "sample_note": (None if judged >= MIN_REVIEWED else
                            f"only {judged} judged; at least {MIN_REVIEWED} needed to conclude")}
    return out


def build_report(run_dir: Path) -> dict:
    """Report from the run database + poll log; never touches the network."""
    import db as _db
    import evaluation
    import profit
    import sniper
    from geo import distance_mi
    from valuation import cross_post_key
    now = datetime.now(timezone.utc)
    polls = _polls(run_dir)
    runs = json.loads((run_dir / "run.json").read_text()) if (run_dir / "run.json").exists() else []
    meta = runs[-1] if runs else {}
    seen: dict[str, list[int]] = {}
    last_poll: dict[str, int] = {}
    prov: dict[str, set] = {}
    for p in polls:
        last_poll[p["source"]] = max(last_poll.get(p["source"], 0), p["poll"])
        prov.setdefault(p["source"], set()).add(p["provenance"])
        for cid in p["ids"]:
            seen.setdefault(cid, []).append(p["poll"])
    with sniper.db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT l.*, nm.discount_pct, nm.comp_avg, nm.value_low, nm.value_high, nm.comp_n, "
            "  nm.evidence_class, nm.confidence, nm.confidence_reasons, nm.evidence, "
            "  nm.trust, nm.risk_flags FROM listings l "
            "LEFT JOIN near_misses nm ON nm.composite_id = l.composite_id "
            "ORDER BY l.first_seen_at, l.composite_id").fetchall()]
        isolation = {
            "database": _db.describe(),
            "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "user_matches": conn.execute("SELECT COUNT(*) FROM user_matches").fetchone()[0],
            "notify_jobs": conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE kind = 'notify'").fetchone()[0],
            "owner_alert_rows": conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]}
    zip_code = (meta.get("territory") or {}).get("zip")
    groups: dict = {}
    for r in rows:
        groups.setdefault(cross_post_key(r), []).append(r["composite_id"])
    review = _read_review(run_dir / "review.csv")
    cands = []
    for r in rows:
        disc = r.get("discount_pct")
        if r.get("comp_avg") is None or disc is None or disc < CANDIDATE_MIN_DISCOUNT_PCT:
            continue
        ev = json.loads(r.get("evidence") or "{}")
        flags = json.loads(r.get("risk_flags") or "[]")
        dist = distance_mi(zip_code, r.get("latitude"), r.get("longitude")) if zip_code else None
        pr = profit.compute(r["price"], r["value_low"], r["comp_avg"], r["value_high"],
                            r["evidence_class"], None, scenarios=True, distance_mi=dist,
                            dealer_share=ev.get("dealer_share") or 0.0)
        sc = (pr or {}).get("scenarios", {})
        polls_src = [p["poll"] for p in polls if p["source"] == r["source"]]
        got = seen.get(r["composite_id"], [])
        lab = review.get((r["source"], str(r["source_id"])), {})
        cands.append({
            "source": r["source"], "source_listing_id": str(r["source_id"]),
            "provenance": sorted(prov.get(r["source"], {"unknown"})),
            "url": r["url"], "title": r["title"], "price": r["price"],
            "year": r["year"], "make": r["make"], "model": r["model"], "trim": r.get("trim"),
            "odometer": r.get("odometer"), "location": ", ".join(
                x for x in (r.get("location"), r.get("state"), r.get("zip")) if x),
            "distance_mi": round(dist) if dist is not None else None,
            "is_auction": bool(r.get("is_auction")), "auction_end_at": r.get("auction_end_at"),
            "freshness": {
                "posted_at": r.get("posted_at"), "first_seen_at": r["first_seen_at"],
                "last_confirmed_at": r.get("last_seen_at"),
                "seen_in_polls": f"{len(got)}/{len(polls_src)}",
                "confirmed_in_last_poll": bool(got) and got[-1] == last_poll.get(r["source"]),
                "hours_since_confirmed": _hours(r.get("last_seen_at"), now),
                "shown_to_customers_now": _shown_now(r, now)},
            "valuation": {
                "evidence_class": r["evidence_class"], "value_low": r["value_low"],
                "value_mid": r["comp_avg"], "value_high": r["value_high"], "n": r["comp_n"],
                "confidence": r["confidence"],
                "reasons": json.loads(r.get("confidence_reasons") or "[]"),
                "comparables": ev.get("comps", []), "geography": ev.get("geography")},
            "discount_pct": disc,
            "estimated_profit": {
                "base": (sc.get("base") or {}).get("profit"),
                "conservative": (sc.get("conservative") or {}).get("profit"),
                "basis": "ESTIMATE from asking-price comparables and default cost "
                         "assumptions; not a verified or realized profit"},
            "trust": r["trust"], "risk_flags": flags,
            "would_alert_owner": sniper._owner_alert_ok(
                r, SimpleNamespace(evidence_class=r["evidence_class"], n=r["comp_n"] or 0),
                disc / 100, r["trust"]),
            "cross_posts": len(groups.get(cross_post_key(r), [])) - 1,
            "outcome_label": lab.get("outcome_label") or None})
    cands.sort(key=lambda c: (c["trust"] != "trusted", -(c["discount_pct"] or 0)))
    kinds = {k for s in prov.values() for k in s}
    retrieved = sum(p["listings"] for p in polls)
    data_kind = ("none" if not retrieved else next(iter(kinds)) if len(kinds) == 1 else "mixed")
    real_retrieved = sum(p["listings"] for p in polls if p["provenance"] == "real")
    by_source = {}
    for p in polls:
        s = by_source.setdefault(p["source"], {"polls": 0, "listings": [], "requests": 0,
                                               "statuses": [], "provenance": p["provenance"],
                                               "endpoint": p["endpoint"], "rate_limited": 0})
        s["polls"] += 1
        s["listings"].append(p["listings"])
        s["requests"] += len(p["requests"])
        s["statuses"].append(p["status"])
        s["rate_limited"] += sum(1 for a in p["requests"] if a["outcome"] == "rate_limited")
    report = {
        "harness": "validate_deals", "generated_at": now.isoformat(),
        "data_kind": data_kind,
        "live_data_verified": real_retrieved > 0 and data_kind == "real",
        "headline": ("REAL listings from production provider endpoints" if data_kind == "real"
                     else "NOT REAL MARKET DATA — " + {
                         "sandbox": "eBay Sandbox test data", "stand_in": "stand-in endpoint",
                         "mixed": "real and non-real data mixed", "none": "nothing retrieved"
                     }.get(data_kind, data_kind)),
        "runs": runs, "isolation": isolation, "sources": by_source,
        "funnel": {
            "retrieved": retrieved, "unique_listings": len(rows),
            "cross_post_groups": sum(1 for g in groups.values() if len(g) > 1),
            "valued": sum(1 for r in rows if r.get("comp_avg") is not None),
            "insufficient_comparables": sum(1 for r in rows
                                            if r.get("evidence_class") == "insufficient"),
            "candidates": len(cands),
            "trusted_candidates": sum(1 for c in cands if c["trust"] == "trusted"),
            "would_alert_owner": sum(1 for c in cands if c["would_alert_owner"]),
            "shown_to_customers_now": sum(1 for c in cands if c["freshness"]["shown_to_customers_now"])},
        "cohort": evaluation.cohort(None, n=10 ** 6) if rows else None,
        "thresholds": {"candidate_min_discount_pct": CANDIDATE_MIN_DISCOUNT_PCT,
                       **{k: sniper.CONFIG.get(k) for k in (
                           "deal_threshold_pct", "estimate_alert_threshold_pct",
                           "estimate_alert_min_peers", "max_believable_discount_pct")}},
        "review": review_metrics(cands),
        "outcome_labels": sorted(evaluation.OUTCOMES),
        "candidates": cands,
        "notes": [
            "Values are estimates from asking prices of listings this run collected; "
            "no completed-sale evidence unless sold_comps was enabled.",
            "Estimated profits use default cost assumptions and are not verified profits.",
            "Precision comes only from human labels in review.csv; until then it is null.",
            "A listing missing from the last poll may have sold or may only have fallen "
            "outside the poll's page limit; it is reported as not re-confirmed."]}
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    _write_review(run_dir / "review.csv", cands, review)
    return report


def _write_review(path: Path, cands: list[dict], existing: dict) -> None:
    out = {}
    for c in cands:
        key = (c["source"], c["source_listing_id"])
        row = {"source": c["source"], "source_listing_id": c["source_listing_id"],
               "trust": c["trust"], "discount_pct": c["discount_pct"],
               "asking_price": c["price"], "est_value": c["valuation"]["value_mid"],
               "confidence": c["valuation"]["confidence"],
               "risk_flags": " ".join(c["risk_flags"]), "title": c["title"], "url": c["url"],
               "first_seen_at": c["freshness"]["first_seen_at"],
               "last_confirmed_at": c["freshness"]["last_confirmed_at"]}
        old = existing.get(key, {})
        out[key] = {**row, **{k: old.get(k, "") for k in LABEL_COLUMNS}}
    for key, old in existing.items():                  # never drop a person's label
        out.setdefault(key, {k: old.get(k, "") for k in REVIEW_COLUMNS})
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out.values())


def purge(run_dir: Path) -> list[str]:
    """Delete the run database and strip listing content (titles, URLs,
    comparables) from the report and review sheet; ids, labels and
    aggregates stay. Returns what was removed."""
    gone = []
    for p in run_dir.glob("harness.db*"):
        p.unlink()
        gone.append(p.name)
    rp = run_dir / "report.json"
    if rp.exists():
        rep = json.loads(rp.read_text())
        keep = ("source", "source_listing_id", "trust", "risk_flags", "discount_pct",
                "outcome_label")
        rep["candidates"] = [{k: c.get(k) for k in keep} for c in rep.get("candidates", [])]
        rep.pop("cohort", None)
        rep["purged_at"] = _now()
        rp.write_text(json.dumps(rep, indent=2, default=str))
        gone.append("report.json listing content")
    rv = run_dir / "review.csv"
    if rv.exists():
        rows = list(_read_review(rv).values())
        with rv.open("w", newline="") as f:
            cols = [c for c in REVIEW_COLUMNS if c not in ("title", "url")]
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        gone.append("review.csv titles and URLs")
    return gone


# ---------- CLI ---------------------------------------------------------------

def _print(rep: dict) -> None:
    f = rep["funnel"]
    print(rep["headline"])
    for sid, s in rep["sources"].items():
        print(f"  {sid}: {s['provenance']} ({s['endpoint']}) polls {s['polls']}, listings "
              f"{s['listings']}, requests {s['requests']}, statuses {sorted(set(s['statuses']))}"
              + (f", RATE LIMITED x{s['rate_limited']}" if s["rate_limited"] else ""))
    for r in (rep["runs"][-1].get("refused") if rep["runs"] else []) or []:
        print(f"  refused {r['source']} ({r['kind']}): {r['why']}")
    print(f"  retrieved {f['retrieved']} -> unique {f['unique_listings']} -> valued "
          f"{f['valued']} (insufficient comparables {f['insufficient_comparables']}) -> "
          f"candidates {f['candidates']} (trusted {f['trusted_candidates']}, would alert "
          f"{f['would_alert_owner']})")
    iso = rep["isolation"]
    print(f"  isolation: {iso['database']}; users {iso['users']}, notify jobs "
          f"{iso['notify_jobs']}, owner alert rows (not sent) {iso['owner_alert_rows']}")
    for t, m in rep["review"].items():
        print(f"  review {t}: labeled {m['labeled']}/{m['candidates']}, precision "
              f"{m['precision']}" + (f" ({m['sample_note']})" if m["sample_note"] else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m validate_deals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--sources", nargs="*", default=["ebay"], choices=HARNESS_SOURCES)
    r.add_argument("--zip", default="35401")
    r.add_argument("--radius", type=int, default=150)
    r.add_argument("--min-price", type=int, default=BAND[0])
    r.add_argument("--max-price", type=int, default=BAND[1])
    r.add_argument("--polls", type=int, default=1)
    r.add_argument("--interval", type=int, default=1800, help="seconds between polls")
    r.add_argument("--sold-comps", action="store_true",
                   help="also query licensed sold-comparable feeds (costs provider calls)")
    r.add_argument("--out", help="run directory (default validation-runs/<UTC time>); "
                                 "an existing one is continued")
    for name in ("report", "purge"):
        s = sub.add_parser(name)
        s.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd in ("report", "purge"):
        d = Path(a.dir)
        if not (d / "polls.jsonl").exists():
            print(f"{d} is not a validation run directory", file=sys.stderr)
            return 1
        if a.cmd == "purge":
            print("removed:", ", ".join(purge(d)) or "nothing")
            return 0
        if not (d / "harness.db").exists():
            print("run database already purged; showing the stored report")
            _print(json.loads((d / "report.json").read_text()))
            return 0
        _isolate(d)
        _print(build_report(d))
        return 0
    if not 1 <= a.polls <= MAX_POLLS:
        ap.error(f"--polls must be 1..{MAX_POLLS}")
    out = Path(a.out or Path("validation-runs") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    _isolate(out)
    import sniper
    floor = max((sniper.DEFAULT_MIN_INTERVAL_S.get(s, 0) for s in a.sources), default=0)
    interval = max(a.interval, floor)
    if a.polls > 1 and interval != a.interval:
        print(f"interval raised to {interval}s (source minimum)")
    rep = run(out, a.sources, a.zip, a.radius, (a.min_price, a.max_price), a.polls, interval,
              a.sold_comps)
    _print(rep)
    print(f"\nreport: {out / 'report.json'}\nreview sheet: {out / 'review.csv'}")
    refused = rep["runs"][-1]["refused"] if rep["runs"] else []
    if rep["funnel"]["retrieved"] == 0:
        return 2 if refused and all(x["kind"] == "credentials" for x in refused) \
            and len(refused) == len(a.sources) else 1
    return 0 if rep["live_data_verified"] else 3


if __name__ == "__main__":
    sys.exit(main())
