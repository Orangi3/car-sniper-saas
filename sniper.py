#!/usr/bin/env python3
"""
sniper.py — multi-source car-deal sniper with weighted ranking.

Polls every enabled source in sources/. Drops listings without a price.
Scores each new listing against sold comps (2025+ only) and ranks by:

    score = discount_pct * source_weight * freshness_factor

Cash-only (no auctions, no financing) is the default view. Auctions live
in their own "Closing soon" tab.

Usage:
  python sniper.py poll          # one-shot poll
  python sniper.py daemon        # forever
  python sniper.py recent [N]    # last N alerts
  python sniper.py stats         # per-source counts
  python sniper.py sources       # which sources are on
  python sniper.py reset         # wipe DB
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Optional

import config as _config_mod
import jobqueue
from config import CONFIG
import sources
from sources import health
from sources.base import (NormalizedListing, looks_needs_work, looks_salvage,
                          to_iso)

# Estimated fees + reconditioning for the flipper profit calculator
FEES_RECON_AVG = 1500

# Per-source diagnostics — captured each poll, exposed via /api/diagnostics
SOURCE_DIAG: dict[str, dict] = {}

# Source of truth for the SQLite file path lives in db.py so a single env
# var (SNIPER_DB_PATH) — or DATABASE_URL — switches every caller, including
# tests. Imported lazily inside db() to avoid a hard import-time dependency
# and keep `python sniper.py poll` cheap to run.
def _resolve_db_path() -> Path:
    try:
        import db as _db_mod  # noqa: PLC0415 — intentional lazy import
        return _db_mod.SQLITE_PATH
    except Exception:
        return Path(__file__).parent / "listings.db"

# Kept as a module attribute for back-compat callers (server.py's
# /debug/status reads sniper.DB_PATH). Resolved once at import — tests that
# need a different path also pop `sniper` from sys.modules so they pick up
# the env-driven path on re-import.
DB_PATH = _resolve_db_path()

DEAL_THRESHOLD = CONFIG["deal_threshold_pct"] / 100.0
MIN_PRICE = CONFIG["min_price"]
MAX_PRICE = CONFIG["max_price"]
DEFAULT_POLL_SEC = CONFIG["poll_interval_sec"]


def db():
    """Portable connection to the configured database (SQLite locally,
    Postgres when DATABASE_URL is set) — the SAME store auth, billing and
    saved searches use. Schema comes only from migrations/; nothing is
    created at connect time. Use as `with db() as conn:`."""
    import db as _db_mod  # noqa: PLC0415 — re-resolved per call for tests
    return _db_mod.session()


def _record_score(conn, cid: str, comp_avg: float, comp_n: int,
                  discount_pct: float, score: float, basis: str,
                  note: str, now: str) -> None:
    """Upsert one near_misses row — the single write path every scorer uses.
    discount_pct may be <= 0 (priced at/above comp); those rows simply won't
    surface in the deals pane but the listing still shows a real comp."""
    conn.execute(
        """INSERT INTO near_misses(composite_id, discount_pct, score, comp_avg,
                comp_n, basis, note, refreshed_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(composite_id) DO UPDATE SET
             discount_pct=excluded.discount_pct, score=excluded.score,
             comp_avg=excluded.comp_avg, comp_n=excluded.comp_n,
             basis=excluded.basis, note=excluded.note,
             refreshed_at=excluded.refreshed_at""",
        (cid, discount_pct, score, comp_avg, comp_n, basis, note, now))


def instant_estimate(year, make, model, price, odometer=None, conn=None):
    """Network-free market-value estimate from peer ASKING prices already in
    the DB. Tier A = same make/model within 3 model-years; Tier B = same
    make/model any year, each peer aged ~8%/yr toward the target year so
    older/newer model years still inform the estimate. Instant, never raises.
    Returns {'avg','n','basis'} or None when there are no usable peers."""
    try:
        if not (year and make and model and price):
            return None
        own = conn is None
        c = conn or db()
        try:
            rows = c.execute(
                """SELECT price, year FROM listings
                   WHERE LOWER(make)=LOWER(?) AND LOWER(model)=LOWER(?)
                     AND price IS NOT NULL AND price BETWEEN 1000 AND 250000
                     AND year IS NOT NULL AND is_salvage = FALSE""",
                (str(make).strip(), str(model).strip())).fetchall()
        finally:
            if own:
                c.close()
        if len(rows) < 2:
            return None
        tight = [r["price"] for r in rows if abs(r["year"] - year) <= 3]
        if len(tight) >= 2:
            sample, n = tight, len(tight)
        else:
            # age peers within 8 model-years toward the target year (~8%/yr).
            # The window + clamp keep a sparse feed from producing wild
            # estimates (e.g. a 2002 truck priced off a 2022 one).
            nearish = [r for r in rows if abs(r["year"] - year) <= 8]
            if len(nearish) < 2:
                return None
            sample = [r["price"] * (1.08 ** max(-8, min(8, year - r["year"])))
                      for r in nearish]
            n = len(sample)
        prices = sorted(sample)
        # trim 10% off each tail to drop outliers, then take the median
        k = int(len(prices) * 0.10)
        core = prices[k:len(prices) - k] or prices
        med = core[len(core) // 2]
        # asking prices run a few % above realistic resale — small haircut
        est = med * 0.93
        # light mileage adjustment past a 120k-mile baseline
        if odometer and odometer > 120_000:
            est -= (odometer - 120_000) * 0.06
        return {"avg": round(max(est, 500.0), 2), "n": n, "basis": "estimate"}
    except Exception as e:
        print(f"[instant_estimate] {make} {model}: {e}", file=sys.stderr)
        return None


def store_new(listings: Iterable[NormalizedListing]) -> list[NormalizedListing]:
    """Insert new listings + queue them for async scoring. Returns the new ones.

    DECOUPLING: this function is FAST — it only inserts rows and enqueues
    durable `score` jobs in the same transaction. Scoring (which calls
    expensive comp APIs) runs in jobs/worker.py.
    """
    from scam_detector import score_listing as scam_score_listing
    now = datetime.now(timezone.utc).isoformat()
    new: list[NormalizedListing] = []
    with db() as conn:
        # Backpressure: past the queue cap, listings are still stored but
        # not queued; backfill_unscored() picks them up once the queue drains.
        room = jobqueue.has_capacity(conn, "score")
        for l in listings:
            if CONFIG.get("require_price", True) and not l.price:
                continue
            _normalize_listing(l)
            sc, reasons = scam_score_listing(l.title, l.description, l.price, None)
            row = conn.execute(
                """INSERT INTO listings(composite_id, source, source_id, url, title,
                        price, year, make, model, odometer, location, state,
                        description, posted_at, first_seen_at, is_dealer,
                        is_salvage, is_auction, auction_end_at, bid_count,
                        buy_now_price, is_cash_only, accepts_financing,
                        scam_score, scam_reasons, image_urls, extras)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(composite_id) DO NOTHING
                   RETURNING composite_id""",
                (l.composite_id(), l.source, l.source_id, l.url, l.title,
                 l.price, l.year, l.make, l.model, l.odometer, l.location, l.state,
                 l.description, l.posted_at, now,
                 bool(l.is_dealer), bool(l.is_salvage),
                 bool(l.is_auction), to_iso(l.auction_end_at), l.bid_count,
                 l.buy_now_price,
                 bool(l.is_cash_only), bool(l.accepts_financing),
                 sc, json.dumps(reasons),
                 json.dumps(getattr(l, 'image_urls', []) or []),
                 json.dumps(l.extras)),
            ).fetchone()
            if row is None:
                continue  # already known
            new.append(l)
            # Durable scoring job, committed atomically with the listing row
            if l.year and l.make and l.model and not l.is_salvage and room:
                enqueue_score(conn, l.composite_id(), l.price)

    # INSTANT SCORING PASS — give every brand-new listing a peer-estimate
    # score right now (network-free) so the dashboard shows a number the
    # moment it appears. The background scorer refines this into a verified
    # sold-comp score shortly after. Wrapped so it can never break ingestion.
    if new:
        try:
            now2 = datetime.now(timezone.utc).isoformat()
            weights = CONFIG.get("source_weights", {})
            with db() as conn:
                for l in new:
                    try:
                        if l.is_salvage or not (l.year and l.make and l.model and l.price):
                            continue
                        est = instant_estimate(l.year, l.make, l.model,
                                               l.price, l.odometer, conn=conn)
                        if not est:
                            continue
                        avg = float(est["avg"])
                        discount = (avg - l.price) / avg if avg else 0.0
                        weight = weights.get(l.source, 1.0)
                        fresh = _freshness_factor(l.posted_at)
                        score = max(0.0, discount) * 100 * weight * fresh
                        note = (f"[{l.source}] {l.year} {l.make} {l.model}"
                                + f" listed ${l.price:,} vs est. value ${avg:,.0f}"
                                + f" (peers={est['n']}, score={score:.1f})")
                        _record_score(conn, l.composite_id(), avg, est["n"],
                                      discount * 100, score, "estimate", note, now2)
                    except Exception as e:
                        print(f"[instant] {getattr(l,'source','?')}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[instant pass] {e}", file=sys.stderr)

    return new


# ---------- Ingestion normalization + alert gating -------------------------

def _normalize_listing(l: NormalizedListing) -> None:
    """Apply the cross-source checks every listing needs, whatever source it
    came from (feeds, APIs, browser extension, email digests):
      * posted_at → ISO 8601 UTC (RSS gives RFC 822, which the freshness
        bonus could never parse)
      * branded-title / parts-car wording → is_salvage
      * runs/drives problems → extras["needs_work"]"""
    raw_posted = l.posted_at
    l.posted_at = to_iso(raw_posted)
    if raw_posted and not l.posted_at:
        l.extras = {**(l.extras or {}), "posted_raw": str(raw_posted)[:64]}
    if not l.is_salvage and looks_salvage(l.title, l.description):
        l.is_salvage = True
    if looks_needs_work(l.title, l.description):
        l.extras = {**(l.extras or {}), "needs_work": True}


def _alert_gate(l: dict, basis: str, comp_n: int, discount: float,
                threshold: float, comp_avg: float) -> tuple[bool, str]:
    """Decide whether a scored listing becomes an alert. Returns (ok, reason).

    Verified sold comps alert at the user's threshold. Peer estimates alert
    only with enough peers AND a deeper discount, so a broken sold-comp
    scraper degrades to fewer alerts instead of zero alerts."""
    if discount < threshold:
        return False, "below threshold"
    max_disc = int(CONFIG.get("max_believable_discount_pct", 65)) / 100.0
    if discount > max_disc:
        return False, f"discount {discount*100:.0f}% too good to be true"
    try:
        extras = json.loads(l.get("extras") or "{}") if isinstance(l.get("extras"), str) \
            else (l.get("extras") or {})
    except ValueError:
        extras = {}
    if extras.get("needs_work"):
        return False, "needs work / not running"
    if l.get("is_dealer") and l.get("source") not in ("marketcheck", "ebay"):
        return False, "dealer listing"
    from scam_detector import score_listing as scam_score_listing
    scam, reasons = scam_score_listing(l.get("title"), l.get("description"),
                                       l.get("price"), comp_avg)
    if scam >= 50:
        return False, f"scam score {scam}: {', '.join(reasons)}"
    if basis == "verified":
        return True, ""
    if not CONFIG.get("estimate_alerts_enabled", True):
        return False, "estimate alerts disabled"
    if comp_n < int(CONFIG.get("estimate_alert_min_peers", 8)):
        return False, f"only {comp_n} peers for estimate"
    est_thr = max(threshold, int(CONFIG.get("estimate_alert_threshold_pct", 30)) / 100.0)
    if discount < est_thr:
        return False, f"estimate discount below {est_thr*100:.0f}%"
    return True, ""


def _enqueue_alert_deliveries(conn, deal: dict, event_key: str) -> None:
    """Queue owner notifications + saved-search fan-out for a new alert,
    inside the transaction that wrote the alert (atomic: either the alert
    and all its delivery jobs exist, or none do)."""
    import notifications
    import saved_search_alerts
    if (CONFIG.get("notify_text_enabled")
            and deal["score"] >= int(CONFIG.get("notify_min_score", 30))):
        notifications.enqueue_owner(conn, deal, event_key,
                                    phone=CONFIG.get("notify_phone"))
    saved_search_alerts.fan_out(conn, deal, event_key)


def enqueue_score(conn, composite_id: str, price) -> Optional[int]:
    """One scoring job per (listing, price): a re-poll of an unchanged
    listing is a no-op, a price change gets re-scored."""
    return jobqueue.enqueue(conn, "score", f"score:{composite_id}:{price}",
                            {"composite_id": composite_id}, priority=50,
                            max_attempts=4)


def score_job(job: dict) -> dict:
    """jobs/worker.py handler for kind='score'."""
    r = _score_one_listing(job["payload"]["composite_id"])
    return r or {"skipped": True}


# ---------- Async background scorer --------------------------------------

def _score_one_listing(composite_id: str) -> Optional[dict]:
    """Refine one listing's score. Tries live VERIFIED sold comps first; if
    those are unavailable it guarantees an instant peer ESTIMATE is recorded
    so the listing is never left blank. Called from a background worker —
    wrapped so it can never raise."""
    try:
        from comps import comps_for_listing
    except Exception:
        comps_for_listing = None
    try:
        _config_mod.reload()
    except Exception:
        pass
    threshold = CONFIG.get("deal_threshold_pct", 20) / 100.0
    weights = CONFIG.get("source_weights", {})
    now = datetime.now(timezone.utc).isoformat()

    try:
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM listings WHERE composite_id=?", (composite_id,)
            ).fetchone()
            prior = conn.execute(
                "SELECT basis FROM near_misses WHERE composite_id=?", (composite_id,)
            ).fetchone()
    except Exception as e:
        print(f"[score] {composite_id} db read failed: {e}", file=sys.stderr)
        return None
    if not row:
        return None
    l = dict(row)
    if not l.get("price") or not (l.get("year") and l.get("make") and l.get("model")):
        return None
    if l.get("is_salvage"):
        return None
    # Respect the user's price band — the async scorer used to skip this, so
    # $500 parts cars came through as 90%-off "deals".
    if not (int(CONFIG.get("min_price", 0)) <= l["price"]
            <= int(CONFIG.get("max_price", 10**9))):
        return None

    weight = weights.get(l["source"], 1.0)
    fresh = _freshness_factor(l.get("posted_at"))

    # --- Tier 1: live VERIFIED sold comps (2025+) ---
    comp = None
    if comps_for_listing:
        try:
            comp = comps_for_listing(year=l["year"], make=l["make"],
                                     model=l["model"], odometer=l.get("odometer"))
        except Exception as e:
            print(f"[score] {composite_id} comp lookup failed: {e}", file=sys.stderr)
            comp = None

    if comp and comp.get("avg"):
        # High-mileage cars compare against the mileage-adjusted average
        avg = float(comp.get("mileage_adjusted_avg") or comp["avg"])
        comp_n, basis = int(comp.get("n") or 0), "verified"
        comp_median = comp.get("median")
    else:
        # --- Tier 2: instant peer ESTIMATE (network-free fallback) ---
        est = instant_estimate(l["year"], l["make"], l["model"],
                               l["price"], l.get("odometer"))
        if not est:
            return None  # not enough data anywhere — leave any prior row intact
        avg, comp_n, basis = float(est["avg"]), int(est["n"]), "estimate"
        comp_median = None

    # Never downgrade an already-verified score back to an estimate.
    if prior and prior["basis"] == "verified" and basis == "estimate":
        return None

    discount = (avg - l["price"]) / avg if avg else 0.0
    score = max(0.0, discount) * 100 * weight * fresh
    est_profit = int(avg - l["price"] - FEES_RECON_AVG)
    label = "sold-comp" if basis == "verified" else "est. value"
    note = (f"[{l['source']}] {l['year']} {l['make']} {l['model']}"
            + (f" / {l['odometer']:,} mi" if l.get('odometer') else "")
            + f" listed ${l['price']:,} vs {label} ${avg:,.0f}"
            + f" (n={comp_n}, score={score:.1f}, est. profit ${est_profit:,})")

    alertable, why_not = _alert_gate(l, basis, comp_n, discount, threshold, avg)
    if not alertable and discount >= threshold:
        print(f"[score] {composite_id} not alerted: {why_not}", file=sys.stderr)

    new_alert = False
    alert_id = None
    with db() as conn:
            _record_score(conn, composite_id, avg, comp_n, discount * 100,
                          score, basis, note, now)
            if alertable:
                row = conn.execute(
                    """INSERT INTO alerts(composite_id, comp_avg, comp_median,
                             comp_n, discount_pct, score, note, created_at, basis)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(composite_id) DO NOTHING
                       RETURNING id""",
                    (composite_id, avg, comp_median, comp_n,
                     discount * 100, score, note, now, basis)).fetchone()
                if row is not None:
                    alert_id = row[0]
                    new_alert = True
                    deal = {**l, "comp_avg": avg, "comp_n": comp_n, "basis": basis,
                            "discount_pct": discount * 100, "score": score,
                            "est_profit": est_profit, "alert_id": alert_id,
                            "event": "new"}
                    _enqueue_alert_deliveries(conn, deal, f"alert{alert_id}")

    return {"composite_id": composite_id, "score": score,
            "discount": discount, "basis": basis}


def ensure_scorer_started() -> None:
    """Deprecated no-op. Scoring now runs from durable jobs processed by
    jobs/worker.py; kept so older callers don't break."""
    return None


def backfill_unscored() -> int:
    """Queue listings that still need a VERIFIED score, AND immediately give
    any that have no score row at all an instant peer estimate — so the whole
    dashboard is populated the moment the server starts, then refined to
    verified sold-comp numbers by the background scorer."""
    with db() as conn:
        rows = conn.execute("""
            SELECT l.composite_id, l.source, l.year, l.make, l.model,
                   l.price, l.odometer, l.posted_at,
                   nm.composite_id AS has_nm
            FROM listings l
            LEFT JOIN near_misses nm ON nm.composite_id = l.composite_id
            WHERE (nm.composite_id IS NULL OR nm.basis IS NULL OR nm.basis='estimate')
              AND l.year IS NOT NULL AND l.make IS NOT NULL AND l.model IS NOT NULL
              AND l.is_salvage = FALSE AND l.price IS NOT NULL
            ORDER BY l.first_seen_at DESC LIMIT 500
        """).fetchall()
        # INSTANT pass — estimate everything that has no score row at all,
        # so nothing shows blank while the slow verified scorer catches up.
        now = datetime.now(timezone.utc).isoformat()
        weights = CONFIG.get("source_weights", {})
        for r in rows:
            try:
                if r["has_nm"]:
                    continue  # already carries at least an estimate
                est = instant_estimate(r["year"], r["make"], r["model"],
                                       r["price"], r["odometer"], conn=conn)
                if not est:
                    continue
                avg = float(est["avg"])
                discount = (avg - r["price"]) / avg if avg else 0.0
                weight = weights.get(r["source"], 1.0)
                fresh = _freshness_factor(r["posted_at"])
                score = max(0.0, discount) * 100 * weight * fresh
                note = (f"[{r['source']}] {r['year']} {r['make']} {r['model']}"
                        + f" listed ${r['price']:,} vs est. value ${avg:,.0f}"
                        + f" (peers={est['n']}, score={score:.1f})")
                _record_score(conn, r["composite_id"], avg, est["n"],
                              discount * 100, score, "estimate", note, now)
            except Exception as e:
                print(f"[backfill instant] {e}", file=sys.stderr)
        queued = 0
        for r in rows:
            if enqueue_score(conn, r["composite_id"], r["price"]):
                queued += 1
    return queued


def get_scorer_stats() -> dict:
    """Durable queue metrics for the `score` kind (plus totals)."""
    st = jobqueue.stats()
    k = st["by_kind"].get("score", {})
    return {"pending": k.get("pending", 0) + k.get("running", 0),
            "scored": k.get("done", 0), "failed": k.get("dead", 0),
            "retried": st["retried"], "queue": st}


def _in_quiet_hours() -> bool:
    """True if the current local hour falls inside the user's configured
    quiet window. Wrap-around windows (start>end, e.g. 22→7) are supported.
    Either endpoint missing = no quiet hours."""
    qs = CONFIG.get("quiet_start_hour")
    qe = CONFIG.get("quiet_end_hour")
    if qs is None or qe is None or qs == qe:
        return False
    h = datetime.now().hour
    if qs < qe:
        return qs <= h < qe
    return h >= qs or h < qe   # wrap-around (e.g. 22→7 covers 22,23,0..6)


def _freshness_factor(posted_at: Optional[str]) -> float:
    """Newer listings get a bonus. Returns 1.0 (new) → 0.6 (older)."""
    iso = to_iso(posted_at)
    if not iso:
        return 0.85
    try:
        t = datetime.fromisoformat(iso)
        age_h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
        if age_h <= 0.5:   return 1.20
        if age_h <= 2:     return 1.10
        if age_h <= 12:    return 1.00
        if age_h <= 48:    return 0.85
        return 0.60
    except (ValueError, TypeError):
        return 0.85


# Minimum seconds between polls of each source. A scheduler tick is not a
# discovery guarantee: a source is only polled when it is due here AND not
# backing off. Override per source with CONFIG["source_min_interval_s"].
DEFAULT_MIN_INTERVAL_S = {
    "craigslist": 60, "marketplace": 30, "email_imap": 120, "ebay": 300,
    "bat": 300, "hemmings": 600, "govdeals": 600, "marketcheck": 900,
}
BACKOFF_BASE_S, BACKOFF_CAP_S, AUTH_RETRY_S = 60, 3600, 1800


def _min_interval(source_id: str) -> int:
    over = CONFIG.get("source_min_interval_s") or {}
    return int(over.get(source_id, DEFAULT_MIN_INTERVAL_S.get(source_id, 60)))


def _health_rows() -> dict[str, dict]:
    try:
        with db() as conn:
            return {r["source_id"]: dict(r) for r in
                    conn.execute("SELECT * FROM source_health").fetchall()}
    except Exception as e:
        print(f"[health] read failed: {e}", file=sys.stderr)
        return {}


def _due(source_id: str, row: Optional[dict], now: datetime,
         force_all: bool) -> tuple[bool, str]:
    """(due?, reason-if-not). Backoff is honoured even for manual polls."""
    row = row or {}
    nxt = row.get("next_poll_after")
    if nxt and str(nxt) > now.isoformat():
        return False, f"backing off until {nxt} ({row.get('status')})"
    if force_all:
        return True, ""
    last = row.get("last_attempt_at")
    if last:
        try:
            age = (now - datetime.fromisoformat(str(last))).total_seconds()
            if age < _min_interval(source_id):
                return False, "not due (min interval)"
        except ValueError:
            pass
    return True, ""


def _backoff_after(status: str, failures: int, retry_after: Optional[float]) -> Optional[float]:
    import random
    if status in (health.S_BLOCKED, health.S_RATE_LIMITED, health.S_TIMEOUT):
        delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(0, failures - 1))
        delay *= random.uniform(0.8, 1.2)
        return max(delay, retry_after or 0)
    if status == health.AUTH_CONFIG:
        return AUTH_RETRY_S
    return None


def _ingest_latency(new: list[NormalizedListing], now: datetime) -> Optional[float]:
    vals = []
    for l in new:
        try:
            if l.posted_at:
                d = (now - datetime.fromisoformat(l.posted_at)).total_seconds()
                if 0 <= d < 7 * 86400:
                    vals.append(d)
        except ValueError:
            continue
    if not vals:
        return None
    vals.sort()
    return round(vals[len(vals) // 2], 1)


def poll_once(verbose: bool = True, only_sources: list[str] | None = None,
              force_all: bool = False) -> dict:
    """Poll every due source in parallel, ingest new listings, and record an
    honest health status per source (see sources/health.py).

    force_all=True ignores minimum intervals (dashboard "Poll now") but still
    respects backoff after blocks / rate limits.
    """
    _config_mod.reload()
    now = datetime.now(timezone.utc)
    rows = _health_rows()
    enabled_ids = {s.SOURCE_ID for s in sources.iter_sources(CONFIG)}
    candidates = [s for s in sources.all_sources()
                  if only_sources is None or s.SOURCE_ID in only_sources]

    to_poll, statuses = [], {}
    for s in candidates:
        sid = s.SOURCE_ID
        issue = getattr(s, "config_issue", lambda cfg: None)(CONFIG)
        if sid not in enabled_ids:
            statuses[sid] = {"status": health.DISABLED, "reason": "disabled in settings",
                             "polled": False}
        elif issue:
            statuses[sid] = {"status": health.AUTH_CONFIG, "reason": issue, "polled": False}
        else:
            due, why = _due(sid, rows.get(sid), now, force_all)
            if due:
                to_poll.append(s)
            else:
                statuses[sid] = {"status": (rows.get(sid) or {}).get("status")
                                 or health.UNVERIFIED, "reason": why, "polled": False,
                                 "skipped": True}

    by_source: dict[str, list[NormalizedListing]] = {}
    reports: dict[str, health.PollReport] = {}
    errors: dict[str, str] = {}
    elapsed: dict[str, float] = {}
    t0 = time.time()

    def _poll_one(s):
        rep = health.begin(s.SOURCE_ID)
        t = time.time()
        try:
            items = list(s.poll(CONFIG))
            err = None
        except Exception as e:
            items, err = [], f"{type(e).__name__}: {e}"
            print(f"[{s.SOURCE_ID}] poll failed: {err}", file=sys.stderr)
        return s.SOURCE_ID, items, rep, err, round(time.time() - t, 2)

    POLL_BUDGET_S = 20
    ex = ThreadPoolExecutor(max_workers=12)
    try:
        futs = {ex.submit(_poll_one, s): s for s in to_poll}
        done, not_done = wait(list(futs.keys()), timeout=POLL_BUDGET_S)
        for fut in done:
            sid, items, rep, err, el = fut.result()
            by_source[sid], reports[sid], elapsed[sid] = items, rep, el
            if err:
                errors[sid] = err
        for fut in not_done:
            fut.cancel()
            sid = futs[fut].SOURCE_ID
            rep = health.report(sid)
            rep.record("poll", health.TIMEOUT, detail=f"exceeded {POLL_BUDGET_S}s budget")
            by_source[sid], reports[sid], elapsed[sid] = [], rep, POLL_BUDGET_S
    finally:
        ex.shutdown(wait=False)
    poll_secs = round(time.time() - t0, 2)

    total_fetched = sum(len(v) for v in by_source.values())
    total_new = 0
    per_source: dict[str, dict] = {}
    for sid, listings in by_source.items():
        new = store_new(listings)
        total_new += len(new)
        prev = rows.get(sid) or {}
        empty_streak = 0 if listings else int(prev.get("consecutive_empty") or 0) + 1
        ever = bool(prev.get("last_genuine_success_at")) or bool(listings)
        status, reason = health.classify(reports.get(sid), len(listings),
                                         error=errors.get(sid),
                                         consecutive_empty=empty_streak,
                                         ever_succeeded=ever)
        attempted, ok = health.request_counts(reports.get(sid))
        statuses[sid] = {"status": status, "reason": reason, "polled": True,
                         "requests_attempted": attempted, "requests_ok": ok,
                         "listings_discovered": len(listings), "listings_new": len(new),
                         "consecutive_empty": empty_streak,
                         "elapsed_s": elapsed.get(sid),
                         "ingest_latency_s": _ingest_latency(new, now),
                         "retry_after_s": getattr(reports.get(sid), "retry_after_s", None),
                         "attempts": (reports.get(sid).attempts[:20]
                                      if reports.get(sid) else [])}
        per_source[sid] = {"fetched": len(listings), "new": len(new),
                           "status": status}
        SOURCE_DIAG[sid] = {"count": len(listings), "elapsed_s": elapsed.get(sid),
                            "error": None if status == health.HEALTHY else reason,
                            "status": status,
                            "regions": getattr(sys.modules.get(f"sources.{sid}"),
                                               "LAST_RESULTS", None),
                            "polled_at": now.isoformat()}
        if verbose:
            print(f"  [{sid:14s}] {status:12s} fetched={len(listings):4d} "
                  f"new={len(new):3d}  {reason}")

    try:
        _persist_source_health(statuses, rows, poll_secs,
                               [s for s in candidates if s.SOURCE_ID in enabled_ids], now)
    except Exception as e:
        print(f"[health] persist failed: {e}", file=sys.stderr)

    if verbose:
        print(f"  TOTAL: fetched={total_fetched} new={total_new} in {poll_secs}s "
              f"(scoring pending: {get_scorer_stats()['pending']})")
    return {"fetched": total_fetched, "new": total_new,
            "scoring_pending": get_scorer_stats()["pending"],
            "poll_secs": poll_secs, "per_source": per_source,
            "source_status": {k: {kk: vv for kk, vv in v.items() if kk != "attempts"}
                              for k, v in statuses.items()}}


def _persist_source_health(statuses: dict, prev_rows: dict, poll_secs: float,
                           enabled: list, now: datetime) -> None:
    """Upsert one source_health row per known source and bump the single
    scheduler_state row."""
    now_iso = now.isoformat()
    with db() as conn:
        totals = {r[0]: int(r[1]) for r in conn.execute(
            "SELECT source, COUNT(*) FROM listings GROUP BY source").fetchall()}
        for sid, st in statuses.items():
            prev = prev_rows.get(sid) or {}
            if not st.get("polled"):
                # Not polled this tick: only refresh disabled/config state.
                if st.get("skipped"):
                    continue
                conn.execute(
                    "INSERT INTO source_health(source_id, status, status_reason, "
                    "  listings_total, updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(source_id) DO UPDATE SET status=excluded.status, "
                    "  status_reason=excluded.status_reason, "
                    "  listings_total=excluded.listings_total, updated_at=excluded.updated_at",
                    (sid, st["status"], st["reason"], totals.get(sid, 0), now_iso))
                continue
            status = st["status"]
            failing = status in (health.S_BLOCKED, health.S_RATE_LIMITED,
                                 health.S_TIMEOUT, health.AUTH_CONFIG)
            failures = int(prev.get("consecutive_failures") or 0) + 1 if failing else 0
            delay = _backoff_after(status, failures, st.get("retry_after_s"))
            next_after = (now + timedelta(seconds=delay)).isoformat() if delay else None
            success = st["requests_ok"] > 0 or (st["requests_attempted"] == 0
                                                and status == health.HEALTHY)
            genuine = st["listings_discovered"] > 0
            conn.execute(
                "INSERT INTO source_health(source_id, last_attempt_at, last_success_at, "
                "  last_error, last_count, last_elapsed_s, listings_total, updated_at, "
                "  status, status_reason, requests_attempted, requests_ok, listings_new, "
                "  consecutive_empty, consecutive_failures, last_genuine_success_at, "
                "  next_poll_after, ingest_latency_s) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "  last_attempt_at=excluded.last_attempt_at, "
                "  last_success_at=COALESCE(excluded.last_success_at, source_health.last_success_at), "
                "  last_error=excluded.last_error, last_count=excluded.last_count, "
                "  last_elapsed_s=excluded.last_elapsed_s, "
                "  listings_total=excluded.listings_total, updated_at=excluded.updated_at, "
                "  status=excluded.status, status_reason=excluded.status_reason, "
                "  requests_attempted=excluded.requests_attempted, "
                "  requests_ok=excluded.requests_ok, listings_new=excluded.listings_new, "
                "  consecutive_empty=excluded.consecutive_empty, "
                "  consecutive_failures=excluded.consecutive_failures, "
                "  last_genuine_success_at=COALESCE(excluded.last_genuine_success_at, "
                "    source_health.last_genuine_success_at), "
                "  next_poll_after=excluded.next_poll_after, "
                "  ingest_latency_s=COALESCE(excluded.ingest_latency_s, "
                "    source_health.ingest_latency_s)",
                (sid, now_iso, now_iso if success else None,
                 None if status == health.HEALTHY else st["reason"],
                 st["listings_discovered"], st.get("elapsed_s"), totals.get(sid, 0),
                 now_iso, status, st["reason"], st["requests_attempted"],
                 st["requests_ok"], st["listings_new"], st["consecutive_empty"],
                 failures, now_iso if genuine else None, next_after,
                 st.get("ingest_latency_s")))
        conn.execute(
            "UPDATE scheduler_state SET last_tick_at=?, last_tick_secs=?, "
            "  tick_counter=tick_counter+1, enabled_sources=? WHERE id=1",
            (now_iso, poll_secs, ",".join(s.SOURCE_ID for s in enabled)))


def daemon(interval: int = DEFAULT_POLL_SEC) -> None:
    print(f"[daemon] base interval {interval}s — Ctrl+C to stop")
    print(f"[daemon] enabled sources: {[s.SOURCE_ID for s in sources.iter_sources(CONFIG)]}")
    while True:
        try:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] tick")
            poll_once(verbose=True)
        except Exception as e:
            print(f"[daemon] tick failed: {e}", file=sys.stderr)
        time.sleep(interval)


def cmd_recent(n: int) -> None:
    with db() as conn:
        rows = conn.execute("""
            SELECT a.created_at, a.discount_pct, a.score, a.note,
                   l.url, l.source, l.state, l.is_auction, l.is_cash_only
            FROM alerts a JOIN listings l ON l.composite_id = a.composite_id
            ORDER BY a.score DESC LIMIT ?""", (n,)).fetchall()
    if not rows:
        print("No alerts yet.")
        return
    for r in rows:
        tags = []
        if r["is_cash_only"]: tags.append("cash")
        if r["is_auction"]:   tags.append("auction")
        tag_s = "[" + ",".join(tags) + "]" if tags else ""
        print(f"score={r['score']:6.1f}  -{r['discount_pct']:.1f}%  {r['source']} {tag_s}")
        print(f"  {r['note']}")
        print(f"  {r['url']}\n")


def cmd_stats() -> None:
    with db() as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) c, SUM(CASE WHEN is_auction THEN 1 ELSE 0 END) a, "
            "SUM(CASE WHEN is_cash_only THEN 1 ELSE 0 END) cash "
            "FROM listings GROUP BY source ORDER BY c DESC").fetchall()
        n_alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        last = conn.execute("SELECT MAX(first_seen_at) FROM listings").fetchone()[0]
    print(f"Total alerts:   {n_alerts}")
    print(f"Last poll seen: {last or 'never'}")
    print("\nSource          listings  auct  cash")
    for r in rows:
        print(f"  {r['source']:14s} {r['c']:>6}  {r['a'] or 0:>4}  {r['cash'] or 0:>4}")


def cmd_sources() -> None:
    enabled = sources.iter_sources(CONFIG)
    enabled_ids = {s.SOURCE_ID for s in enabled}
    print("Configured sources:\n")
    for s in sources.all_sources():
        flag = "ON " if s.SOURCE_ID in enabled_ids else "off"
        w = CONFIG["source_weights"].get(s.SOURCE_ID, 1.0)
        print(f"  [{flag}]  weight {w:.2f}  {s.SOURCE_ID:14s}  {s.SOURCE_NAME}")


def cmd_reset() -> None:
    import db as _db
    if _db.IS_PG:
        print("reset only supports the local SQLite file; drop/recreate the "
              "Postgres database and run `python -m migrations.runner`.")
        return
    if input(f"Wipe {DB_PATH}? [y/N] ").strip().lower() != "y":
        print("aborted"); return
    DB_PATH.unlink(missing_ok=True)
    _ensure_migrated()
    print("DB reset.")


def _ensure_migrated() -> None:
    """CLI convenience: apply pending migrations explicitly (never at
    connection time). Local SQLite only — production runs migrations as a
    deploy step."""
    import db as _db
    from migrations import runner
    if _db.IS_PG:
        return
    runner.run_pending(verbose=False)


def main() -> None:
    p = argparse.ArgumentParser(description="Multi-source car sniper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("poll")
    d = sub.add_parser("daemon"); d.add_argument("--interval", type=int, default=DEFAULT_POLL_SEC)
    r = sub.add_parser("recent"); r.add_argument("n", type=int, nargs="?", default=25)
    sub.add_parser("stats"); sub.add_parser("sources"); sub.add_parser("reset")
    args = p.parse_args()
    _ensure_migrated()
    if args.cmd == "poll": poll_once()
    elif args.cmd == "daemon": daemon(args.interval)
    elif args.cmd == "recent": cmd_recent(args.n)
    elif args.cmd == "stats": cmd_stats()
    elif args.cmd == "sources": cmd_sources()
    elif args.cmd == "reset": cmd_reset()


if __name__ == "__main__":
    main()
