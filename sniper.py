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
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone, timedelta
from pathlib import Path
from queue import Queue, Empty
from typing import Iterable, Optional

import config as _config_mod
from config import CONFIG
import sources
from sources.base import NormalizedListing

# Async scorer state
_SCORE_QUEUE: "Queue[str]" = Queue()         # composite_id values pending scoring
_SCORE_STATS = {"pending": 0, "scored": 0, "failed": 0,
                "last_scored_at": None}
_SCORER_STARTED = False
_SCORER_LOCK = threading.Lock()

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    composite_id    TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    price           INTEGER,
    year            INTEGER,
    make            TEXT,
    model           TEXT,
    odometer        INTEGER,
    location        TEXT,
    state           TEXT,
    description     TEXT,
    posted_at       TEXT,
    first_seen_at   TEXT NOT NULL,
    is_dealer       INTEGER DEFAULT 0,
    is_salvage      INTEGER DEFAULT 0,
    is_auction      INTEGER DEFAULT 0,
    auction_end_at  TEXT,
    bid_count       INTEGER,
    buy_now_price   INTEGER,
    is_cash_only    INTEGER DEFAULT 0,
    accepts_financing INTEGER DEFAULT 0,
    scam_score      INTEGER DEFAULT 0,
    scam_reasons    TEXT,
    image_urls      TEXT,
    extras          TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    composite_id    TEXT NOT NULL REFERENCES listings(composite_id),
    comp_avg        REAL,
    comp_median     REAL,
    comp_n          INTEGER,
    discount_pct    REAL,
    score           REAL,
    note            TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(composite_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings(first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_source     ON listings(source);
CREATE INDEX IF NOT EXISTS idx_listings_auction    ON listings(is_auction, auction_end_at);
CREATE INDEX IF NOT EXISTS idx_listings_scam       ON listings(scam_score);
CREATE INDEX IF NOT EXISTS idx_listings_make_model ON listings(make, model);
CREATE INDEX IF NOT EXISTS idx_alerts_created      ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_score        ON alerts(score DESC);

CREATE TABLE IF NOT EXISTS saved (
    composite_id TEXT PRIMARY KEY REFERENCES listings(composite_id),
    note         TEXT,
    saved_at     TEXT NOT NULL
);

-- "Near miss" cache so we can always surface SOMETHING even when nothing
-- meets the user's discount threshold. Discount must be > 0 (priced below
-- comp avg by any amount). Updated on every poll.
CREATE TABLE IF NOT EXISTS near_misses (
    composite_id  TEXT PRIMARY KEY REFERENCES listings(composite_id),
    discount_pct  REAL,
    score         REAL,
    comp_avg      REAL,
    note          TEXT,
    refreshed_at  TEXT NOT NULL
);
"""

DEAL_THRESHOLD = CONFIG["deal_threshold_pct"] / 100.0
MIN_PRICE = CONFIG["min_price"]
MAX_PRICE = CONFIG["max_price"]
DEFAULT_POLL_SEC = CONFIG["poll_interval_sec"]


def db() -> sqlite3.Connection:
    # Re-resolve every call so a test that swapped SNIPER_DB_PATH after
    # import (e.g. via the conftest fixture) still gets the right file.
    path = _resolve_db_path()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL + busy timeout: readers never block writers and concurrent writers
    # wait politely instead of raising "database is locked". This removes the
    # main source of intermittent scoring errors under the 6-thread scorer.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass
    conn.executescript(SCHEMA)
    # Forward-compat ALTERs — safely add columns to pre-existing DBs
    for stmt in (
        "ALTER TABLE listings ADD COLUMN scam_score INTEGER DEFAULT 0",
        "ALTER TABLE listings ADD COLUMN scam_reasons TEXT",
        "ALTER TABLE listings ADD COLUMN image_urls TEXT",
        "ALTER TABLE near_misses ADD COLUMN comp_n INTEGER",
        "ALTER TABLE near_misses ADD COLUMN basis TEXT",
        "ALTER TABLE alerts ADD COLUMN basis TEXT",
        "ALTER TABLE saved ADD COLUMN status TEXT DEFAULT 'saved'",
        "ALTER TABLE saved ADD COLUMN updated_at TEXT",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    return conn


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
                     AND year IS NOT NULL AND is_salvage=0""",
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

    DECOUPLING: this function is FAST — it only inserts rows and enqueues IDs.
    The scoring (which calls expensive comp APIs) happens in a background
    worker thread via ensure_scorer_started(). Callers (Poll Now button,
    daemon ticks) get an immediate response.
    """
    from scam_detector import score_listing as scam_score_listing
    now = datetime.now(timezone.utc).isoformat()
    new: list[NormalizedListing] = []
    with db() as conn:
        for l in listings:
            if CONFIG.get("require_price", True) and not l.price:
                continue
            sc, reasons = scam_score_listing(l.title, l.description, l.price, None)
            try:
                conn.execute(
                    """INSERT INTO listings(composite_id, source, source_id, url, title,
                            price, year, make, model, odometer, location, state,
                            description, posted_at, first_seen_at, is_dealer,
                            is_salvage, is_auction, auction_end_at, bid_count,
                            buy_now_price, is_cash_only, accepts_financing,
                            scam_score, scam_reasons, image_urls, extras)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (l.composite_id(), l.source, l.source_id, l.url, l.title,
                     l.price, l.year, l.make, l.model, l.odometer, l.location, l.state,
                     l.description, l.posted_at, now,
                     1 if l.is_dealer else 0, 1 if l.is_salvage else 0,
                     1 if l.is_auction else 0, l.auction_end_at, l.bid_count,
                     l.buy_now_price,
                     1 if l.is_cash_only else 0, 1 if l.accepts_financing else 0,
                     sc, json.dumps(reasons),
                     json.dumps(getattr(l, 'image_urls', []) or []),
                     json.dumps(l.extras)),
                )
                new.append(l)
                # Enqueue for async scoring (only if it has the data we need)
                if l.year and l.make and l.model and not l.is_salvage:
                    _SCORE_QUEUE.put(l.composite_id())
                    _SCORE_STATS["pending"] = _SCORE_QUEUE.qsize()
            except sqlite3.IntegrityError:
                pass
    ensure_scorer_started()

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
        avg, comp_n, basis = float(comp["avg"]), int(comp.get("n") or 0), "verified"
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

    new_alert = False
    try:
        with db() as conn:
            _record_score(conn, composite_id, avg, comp_n, discount * 100,
                          score, basis, note, now)
            # Alerts (and the text notification) fire ONLY on verified comps —
            # we never text the user a "deal" based on a rough estimate.
            if basis == "verified" and discount >= threshold:
                try:
                    conn.execute(
                        """INSERT INTO alerts(composite_id, comp_avg, comp_median,
                                 comp_n, discount_pct, score, note, created_at, basis)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (composite_id, avg, comp_median, comp_n,
                         discount * 100, score, note, now, basis))
                    new_alert = True
                except sqlite3.IntegrityError:
                    pass
    except Exception as e:
        print(f"[score] {composite_id} write failed: {e}", file=sys.stderr)
        return None

    if new_alert and CONFIG.get("notify_text_enabled") and CONFIG.get("notify_phone"):
        try:
            min_score = int(CONFIG.get("notify_min_score", 30))
            if score < min_score:
                pass  # below user's alert threshold — silent
            elif _in_quiet_hours():
                print(f"[notify] {composite_id} skipped — quiet hours active",
                      file=sys.stderr)
            else:
                from notifications import send_alert_text
                ok, detail = send_alert_text(
                    CONFIG["notify_phone"], l["year"], l["make"], l["model"],
                    l["price"], discount * 100, l["url"])
                if not ok:
                    print(f"[notify] {composite_id}: {detail}", file=sys.stderr)
        except Exception as e:
            print(f"[notify] {composite_id} crashed: {e}", file=sys.stderr)

    return {"composite_id": composite_id, "score": score,
            "discount": discount, "basis": basis}


def _scorer_loop():
    """Background worker — drains the score queue continuously."""
    while True:
        try:
            cid = _SCORE_QUEUE.get(timeout=5)
        except Empty:
            _SCORE_STATS["pending"] = 0
            continue
        try:
            _score_one_listing(cid)
            _SCORE_STATS["scored"] += 1
            _SCORE_STATS["last_scored_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            _SCORE_STATS["failed"] += 1
            print(f"[scorer] {cid} failed: {e}", file=sys.stderr)
        finally:
            _SCORE_STATS["pending"] = _SCORE_QUEUE.qsize()
            _SCORE_QUEUE.task_done()


def ensure_scorer_started() -> None:
    """Idempotent — start the background scorer thread on first call."""
    global _SCORER_STARTED
    with _SCORER_LOCK:
        if _SCORER_STARTED:
            return
        # Start 6 worker threads — they share the queue. The bottleneck is
        # network comp fetches, so more workers drains the queue faster.
        for _ in range(6):
            t = threading.Thread(target=_scorer_loop, daemon=True, name="scorer")
            t.start()
        _SCORER_STARTED = True


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
              AND l.is_salvage = 0 AND l.price IS NOT NULL
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
    for r in rows:
        _SCORE_QUEUE.put(r["composite_id"])
    _SCORE_STATS["pending"] = _SCORE_QUEUE.qsize()
    ensure_scorer_started()
    return len(rows)


def get_scorer_stats() -> dict:
    return {**_SCORE_STATS, "pending": _SCORE_QUEUE.qsize()}


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
    if not posted_at:
        return 0.85
    try:
        t = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - t).total_seconds() / 3600
        if age_h <= 0.5:   return 1.20
        if age_h <= 2:     return 1.10
        if age_h <= 12:    return 1.00
        if age_h <= 48:    return 0.85
        return 0.60
    except (ValueError, TypeError):
        return 0.85


def score_and_alert(listings: list[NormalizedListing]) -> list[dict]:
    """Score listings against sold comps. Always record near-misses (any
    listing priced below comp avg) so the dashboard never goes empty."""
    try:
        from comps import comps_for_listing
    except ImportError:
        comps_for_listing = None

    # Re-read overrides every call so live threshold changes apply immediately
    _config_mod.reload()
    threshold = CONFIG["deal_threshold_pct"] / 100.0
    weights = CONFIG.get("source_weights", {})
    alerts: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        for l in listings:
            if not l.price or l.price < MIN_PRICE or l.price > MAX_PRICE:
                continue
            if not (l.year and l.make and l.model):
                continue
            if l.is_salvage:
                continue

            try:
                comp = comps_for_listing(year=l.year, make=l.make, model=l.model,
                                         odometer=l.odometer) if comps_for_listing else None
            except Exception as e:
                print(f"[comps] {l.composite_id()} failed: {e}", file=sys.stderr)
                comp = None
            if not comp or not comp.get("avg"):
                continue

            avg = float(comp["avg"])
            discount = (avg - l.price) / avg
            if discount <= 0:
                continue  # listing is at or above comp avg — not a deal

            weight = weights.get(l.source, 1.0)
            fresh = _freshness_factor(l.posted_at)
            score = discount * 100 * weight * fresh
            est_profit = int(avg - l.price - FEES_RECON_AVG)

            note = (
                f"[{l.source}] {l.year} {l.make} {l.model}"
                + (f" / {l.odometer:,} mi" if l.odometer else "")
                + f" listed at ${l.price:,} vs sold-comp avg ${avg:,.0f}"
                + f" (n={comp.get('n', 0)}, score={score:.1f}, est. profit ${est_profit:,})"
            )

            # Always upsert near-miss snapshot (any positive discount)
            try:
                conn.execute(
                    """INSERT INTO near_misses(composite_id, discount_pct, score,
                            comp_avg, note, refreshed_at)
                       VALUES(?, ?, ?, ?, ?, ?)
                       ON CONFLICT(composite_id) DO UPDATE SET
                         discount_pct=excluded.discount_pct, score=excluded.score,
                         comp_avg=excluded.comp_avg, note=excluded.note,
                         refreshed_at=excluded.refreshed_at""",
                    (l.composite_id(), discount * 100, score, avg, note, now),
                )
            except sqlite3.Error:
                pass

            # Only promote to "alert" if threshold is hit
            if discount < threshold:
                continue

            try:
                conn.execute(
                    """INSERT INTO alerts(composite_id, comp_avg, comp_median, comp_n,
                                          discount_pct, score, note, created_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                    (l.composite_id(), avg, comp.get("median"), comp.get("n"),
                     discount * 100, score, note, now),
                )
                alerts.append({"listing": l.to_dict(), "comp": comp,
                               "discount_pct": discount * 100, "score": score,
                               "est_profit": est_profit, "note": note})
            except sqlite3.IntegrityError:
                pass
    return alerts


_TICK_COUNTER = 0


def _should_poll_source(source_id: str) -> bool:
    """Tiered polling so Craigslist runs every tick, salvage every 4."""
    global _TICK_COUNTER
    if source_id == "craigslist":
        return _TICK_COUNTER % CONFIG.get("craigslist_every_n_ticks", 1) == 0
    if source_id in ("copart_live", "iaa_live"):
        return _TICK_COUNTER % CONFIG.get("salvage_every_n_ticks", 4) == 0
    if source_id in ("bat", "carsandbids", "hemmings", "govdeals", "gsa", "publicsurplus"):
        return _TICK_COUNTER % CONFIG.get("auction_sites_every_n_ticks", 2) == 0
    return True


def poll_once(verbose: bool = True, only_sources: list[str] | None = None,
              force_all: bool = False) -> dict:
    """Poll all enabled sources IN PARALLEL via ThreadPoolExecutor.

    force_all=True ignores the polling-tier schedule (used by the dashboard's
    'Poll all sources now' button so every source fires immediately).
    """
    global _TICK_COUNTER
    _TICK_COUNTER += 1

    _config_mod.reload()
    enabled = sources.iter_sources(CONFIG)
    if only_sources is not None:
        enabled = [s for s in enabled if s.SOURCE_ID in only_sources]
    if not force_all:
        enabled = [s for s in enabled if _should_poll_source(s.SOURCE_ID)]

    by_source: dict[str, list[NormalizedListing]] = {}
    t0 = time.time()

    def _poll_one(s):
        try:
            t = time.time()
            items = list(s.poll(CONFIG))
            elapsed = round(time.time() - t, 2)
            SOURCE_DIAG[s.SOURCE_ID] = {
                "count": len(items),
                "elapsed_s": elapsed,
                "error": None,
                "regions": getattr(s, "LAST_RESULTS", None),
                "polled_at": datetime.now(timezone.utc).isoformat(),
            }
            return s.SOURCE_ID, items
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"[{s.SOURCE_ID}] poll failed: {err}", file=sys.stderr)
            SOURCE_DIAG[s.SOURCE_ID] = {
                "count": 0, "elapsed_s": None, "error": err,
                "polled_at": datetime.now(timezone.utc).isoformat(),
            }
            return s.SOURCE_ID, []

    # Parallel HTTP — hard wall-clock cap of 12s. Use wait() instead of
    # as_completed(timeout=) so we never raise TimeoutError on slow sources;
    # they're just recorded as "exceeded poll budget" in diagnostics.
    POLL_BUDGET_S = 12
    ex = ThreadPoolExecutor(max_workers=12)
    try:
        futs = {ex.submit(_poll_one, s): s for s in enabled}
        done, not_done = wait(list(futs.keys()), timeout=POLL_BUDGET_S)

        for fut in done:
            try:
                src_id, listings = fut.result(timeout=0.5)
                by_source[src_id] = listings
            except Exception as e:
                src = futs[fut]
                SOURCE_DIAG[src.SOURCE_ID] = {
                    "count": 0, "elapsed_s": None,
                    "error": f"{type(e).__name__}: {str(e)[:80]}",
                    "polled_at": datetime.now(timezone.utc).isoformat(),
                }
                by_source[src.SOURCE_ID] = []
        for fut in not_done:
            fut.cancel()
            src = futs[fut]
            SOURCE_DIAG[src.SOURCE_ID] = {
                "count": 0, "elapsed_s": POLL_BUDGET_S,
                "error": "exceeded poll budget (12s)",
                "polled_at": datetime.now(timezone.utc).isoformat(),
            }
            by_source[src.SOURCE_ID] = []
    finally:
        # Don't block on shutdown — cancelled futures will release their threads
        ex.shutdown(wait=False)

    poll_secs = round(time.time() - t0, 2)

    total_fetched = sum(len(v) for v in by_source.values())
    total_new = 0
    per_source: dict[str, dict] = {}

    # FAST PATH: ingest only. Scoring is queued + happens in background.
    for src_id, listings in by_source.items():
        new = store_new(listings)
        per_source[src_id] = {"fetched": len(listings), "new": len(new)}
        total_new += len(new)
        if verbose:
            print(f"  [{src_id:14s}] fetched={len(listings):4d}  new={len(new):3d}")

    # Persist per-source health + scheduler tick for the admin source-health
    # endpoint. Wrapped so a DB hiccup never breaks the poll itself.
    try:
        _persist_source_health(by_source, poll_secs, enabled)
    except Exception as e:
        print(f"[health] persist failed: {e}", file=sys.stderr)

    if verbose:
        print(f"  TOTAL: fetched={total_fetched} new={total_new} in {poll_secs}s "
              f"(scoring queued: {_SCORE_QUEUE.qsize()})")
    return {"fetched": total_fetched, "new": total_new,
            "scoring_pending": _SCORE_QUEUE.qsize(),
            "poll_secs": poll_secs, "per_source": per_source}


def _persist_source_health(by_source: dict, poll_secs: float,
                            enabled: list) -> None:
    """Upsert one source_health row per source attempted this tick, and bump
    the single scheduler_state row. Uses db.py (works for SQLite & Postgres
    interchangeably) — kept separate from the legacy sqlite3 connect path
    so the SaaS deployment can point at Postgres without rewriting poll_once."""
    import db as _db
    now = _db.now_utc_iso()
    enabled_ids = [s.SOURCE_ID for s in enabled]
    with _db.transaction() as conn:
        cur = conn.cursor()
        ph = _db.placeholder()
        # listings_total per source
        if _db.IS_PG:
            cur.execute("SELECT source, COUNT(*) AS n FROM listings GROUP BY source")
            totals = {r["source"]: int(r["n"]) for r in cur.fetchall()}
        else:
            totals = {r[0]: r[1] for r in cur.execute(
                "SELECT source, COUNT(*) FROM listings GROUP BY source").fetchall()}
        for src_id in {*by_source.keys(), *(SOURCE_DIAG.keys())}:
            d = SOURCE_DIAG.get(src_id, {}) or {}
            err = d.get("error")
            count = d.get("count")
            elapsed = d.get("elapsed_s")
            ltot = int(totals.get(src_id, 0))
            # Compute last_success_at: bump only if this tick succeeded (no err).
            if _db.IS_PG:
                cur.execute(
                    "INSERT INTO source_health(source_id, last_attempt_at, "
                    "  last_success_at, last_error, last_count, last_elapsed_s, "
                    "  listings_total, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT(source_id) DO UPDATE SET "
                    "  last_attempt_at=excluded.last_attempt_at, "
                    "  last_success_at=COALESCE(excluded.last_success_at, source_health.last_success_at), "
                    "  last_error=excluded.last_error, "
                    "  last_count=excluded.last_count, "
                    "  last_elapsed_s=excluded.last_elapsed_s, "
                    "  listings_total=excluded.listings_total, "
                    "  updated_at=excluded.updated_at",
                    (src_id, now, (now if not err else None), err, count, elapsed, ltot, now))
            else:
                cur.execute(
                    "INSERT INTO source_health(source_id, last_attempt_at, "
                    "  last_success_at, last_error, last_count, last_elapsed_s, "
                    "  listings_total, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(source_id) DO UPDATE SET "
                    "  last_attempt_at=excluded.last_attempt_at, "
                    "  last_success_at=COALESCE(excluded.last_success_at, source_health.last_success_at), "
                    "  last_error=excluded.last_error, "
                    "  last_count=excluded.last_count, "
                    "  last_elapsed_s=excluded.last_elapsed_s, "
                    "  listings_total=excluded.listings_total, "
                    "  updated_at=excluded.updated_at",
                    (src_id, now, (now if not err else None), err, count, elapsed, ltot, now))
        # Scheduler tick
        if _db.IS_PG:
            cur.execute(
                "UPDATE scheduler_state SET last_tick_at=%s, last_tick_secs=%s, "
                "  tick_counter=tick_counter+1, enabled_sources=%s WHERE id=1",
                (now, poll_secs, ",".join(enabled_ids)))
        else:
            cur.execute(
                "UPDATE scheduler_state SET last_tick_at=?, last_tick_secs=?, "
                "  tick_counter=tick_counter+1, enabled_sources=? WHERE id=1",
                (now, poll_secs, ",".join(enabled_ids)))


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
            "SELECT source, COUNT(*) c, SUM(is_auction) a, SUM(is_cash_only) cash "
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
    if input(f"Wipe {DB_PATH}? [y/N] ").strip().lower() != "y":
        print("aborted"); return
    DB_PATH.unlink(missing_ok=True)
    db().close()
    print("DB reset.")


def main() -> None:
    p = argparse.ArgumentParser(description="Multi-source car sniper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("poll")
    d = sub.add_parser("daemon"); d.add_argument("--interval", type=int, default=DEFAULT_POLL_SEC)
    r = sub.add_parser("recent"); r.add_argument("n", type=int, nargs="?", default=25)
    sub.add_parser("stats"); sub.add_parser("sources"); sub.add_parser("reset")
    args = p.parse_args()
    if args.cmd == "poll": poll_once()
    elif args.cmd == "daemon": daemon(args.interval)
    elif args.cmd == "recent": cmd_recent(args.n)
    elif args.cmd == "stats": cmd_stats()
    elif args.cmd == "sources": cmd_sources()
    elif args.cmd == "reset": cmd_reset()


if __name__ == "__main__":
    main()
