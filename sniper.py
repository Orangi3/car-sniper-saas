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
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Optional

import config as _config_mod
import jobqueue
import profit as profit_mod
import valuation
from config import CONFIG
import sources
from sources import health
from sources.base import (NormalizedListing, looks_like_dealer, looks_needs_work,
                          looks_salvage,
                          to_iso)

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


# ---------- Ingestion: upsert, price history, identity --------------------

VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
MATERIAL_DROP_PCT = 3.0
MATERIAL_DROP_USD = 250
RELIST_GAP_HOURS = 6


def find_vin(*texts: str) -> Optional[str]:
    """A VIN printed in the listing, accepted only if its check digit
    verifies (North American VINs) — a random 17-char token is not a VIN."""
    import vin as vin_mod
    for t in texts:
        for m in VIN_RE.finditer((t or "").upper()):
            cand = m.group(1)
            if not (re.search(r"[A-Z]", cand) and re.search(r"\d", cand)):
                continue
            v = vin_mod.validate_vin(cand)
            if v.get("ok") and v.get("check_digit_ok") is True:
                return v["normalized"]
    return None


def _norm_title(t: str) -> str:
    """Title without its price tag ("… - $9,000", "$9k"): a relist at a new
    price must still compare equal."""
    t = re.sub(r"\$\s?[\d,.]+\s?k?\b", " ", (t or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _find_relist(conn, l: NormalizedListing, vehicle_key: Optional[str],
                 cid: str) -> Optional[dict]:
    """An earlier listing of the SAME vehicle, matched cautiously:
      1. same verified VIN (any source), or
      2. same source + region, identical normalized title, identical
         non-null odometer, (if both have photos) the same first photo, AND
         the earlier post has been gone for RELIST_GAP_HOURS — two posts that
         are both live are two listings, not a relist.
    Never on year/make/model/price alone."""
    if vehicle_key:
        r = conn.execute(
            "SELECT composite_id, source, price, last_seen_at FROM listings "
            "WHERE vehicle_key = ? AND composite_id <> ? "
            "ORDER BY last_seen_at DESC LIMIT 1", (vehicle_key, cid)).fetchone()
        return dict(r) if r else None
    if not l.odometer or not l.title:
        return None
    region = str(l.source_id).split(":", 1)[0] if ":" in str(l.source_id) else None
    rows = conn.execute(
        "SELECT composite_id, source_id, title, price, last_seen_at, image_urls "
        "FROM listings WHERE source = ? AND odometer = ? AND year = ? "
        "  AND composite_id <> ? ORDER BY last_seen_at DESC LIMIT 20",
        (l.source, l.odometer, l.year, cid)).fetchall()
    nt = _norm_title(l.title)
    first_img = (l.image_urls or [None])[0]
    gone_before = (datetime.now(timezone.utc)
                   - timedelta(hours=RELIST_GAP_HOURS)).isoformat()
    for r in rows:
        if _norm_title(r["title"]) != nt:
            continue
        if str(r["last_seen_at"] or "") > gone_before:
            continue                      # still live: a different listing
        if region and not str(r["source_id"]).startswith(region + ":"):
            continue
        try:
            imgs = json.loads(r["image_urls"] or "[]")
        except ValueError:
            imgs = []
        if first_img and imgs and imgs[0] != first_img:
            continue
        return dict(r)
    return None


def store_new(listings: Iterable[NormalizedListing]) -> list[NormalizedListing]:
    """Upsert listings; returns the ones that were new. See ingest()."""
    return ingest(listings)["new"]


def ingest(listings: Iterable[NormalizedListing]) -> dict:
    """Upsert a batch of listings in one transaction.

    New listing   → insert, price-history row, relist detection, score job.
    Seen before   → last_seen_at bumped; a changed asking price is stored
                    (previous_price, price_changed_at, history row) and the
                    listing is re-scored — so a $9,000 → $6,500 drop becomes
                    a price-change opportunity instead of being discarded
                    as a duplicate.
    Score jobs are keyed by (listing, price): unchanged re-polls never
    re-score or re-alert."""
    from scam_detector import score_listing as scam_score_listing
    now = datetime.now(timezone.utc).isoformat()
    batch: dict[str, NormalizedListing] = {}
    for l in listings:
        if CONFIG.get("require_price", True) and not l.price:
            continue
        _normalize_listing(l)
        batch[l.composite_id()] = l
    out = {"new": [], "price_changed": [], "seen": 0}
    if not batch:
        return out
    with db() as conn:
        room = jobqueue.has_capacity(conn, "score")
        ids = list(batch)
        existing: dict[str, dict] = {}
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            for r in conn.execute(
                    f"SELECT composite_id, price, odometer FROM listings "
                    f"WHERE composite_id IN ({','.join('?' for _ in chunk)})", chunk):
                existing[r["composite_id"]] = dict(r)
        touched = []
        for cid, l in batch.items():
            prev = existing.get(cid)
            if prev is None:
                _insert_listing(conn, l, now, scam_score_listing)
                out["new"].append(l)
                if l.year and l.make and l.model and room:
                    enqueue_score(conn, cid, l.price)
                continue
            out["seen"] += 1
            if l.price and prev.get("price") and int(l.price) != int(prev["price"]):
                conn.execute(
                    "UPDATE listings SET price = ?, previous_price = ?, "
                    "  price_changed_at = ?, last_seen_at = ?, title = ?, "
                    "  description = ?, odometer = COALESCE(?, odometer) "
                    "WHERE composite_id = ?",
                    (l.price, prev["price"], now, now, l.title, l.description,
                     l.odometer, cid))
                conn.execute(
                    "INSERT INTO listing_prices(composite_id, price, event, observed_at) "
                    "VALUES (?,?,?,?)", (cid, l.price, "price_change", now))
                out["price_changed"].append(l)
                if l.year and l.make and l.model:
                    enqueue_score(conn, cid, l.price)
            else:
                touched.append((now, cid))
        if touched:
            conn.executemany("UPDATE listings SET last_seen_at = ? WHERE composite_id = ?",
                             touched)
    return out


def _location(l: NormalizedListing) -> tuple:
    """(zip, lat, lon, location_source) from STRUCTURED provider fields only.
    A provider ZIP without coordinates gets its ZIP-centroid (geo.py)."""
    z = (str(l.zip)[:5] if getattr(l, "zip", None) else None)
    if z and not z.isdigit():
        z = None
    lat, lon = getattr(l, "latitude", None), getattr(l, "longitude", None)
    if lat is not None and lon is not None:
        return z, float(lat), float(lon), "provider"
    if z:
        try:
            import geo
            c = geo.zip_centroid(z)
        except Exception:
            c = None
        if c:
            return z, c[0], c[1], "zip_centroid"
    return z, None, None, None


def _insert_listing(conn, l: NormalizedListing, now: str, scam_fn) -> None:
    cid = l.composite_id()
    sc, reasons = scam_fn(l.title, l.description, l.price, None)
    vin = find_vin(l.title, l.description)
    vehicle_key = f"vin:{vin}" if vin else None
    trim = valuation.parse_trim(l.title, l.model)
    relist = _find_relist(conn, l, vehicle_key, cid)
    previous_price = None
    extras = dict(l.extras or {})
    if relist:
        extras["relist_of"] = relist["composite_id"]
        if relist.get("price") and l.price and l.price < relist["price"]:
            previous_price = relist["price"]   # relisted cheaper = price drop
    conn.execute(
        """INSERT INTO listings(composite_id, source, source_id, url, title,
                price, year, make, model, odometer, location, state,
                description, posted_at, first_seen_at, last_seen_at, is_dealer,
                is_salvage, is_auction, auction_end_at, bid_count,
                buy_now_price, is_cash_only, accepts_financing,
                scam_score, scam_reasons, image_urls, extras, vin, vehicle_key,
                relist_of, trim, needs_work, previous_price, price_changed_at,
                zip, latitude, longitude, location_source)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(composite_id) DO NOTHING""",
        (cid, l.source, l.source_id, l.url, l.title,
         l.price, l.year, l.make, l.model, l.odometer, l.location, l.state,
         l.description, l.posted_at, now, now,
         bool(l.is_dealer), bool(l.is_salvage),
         bool(l.is_auction), to_iso(l.auction_end_at), l.bid_count,
         l.buy_now_price,
         bool(l.is_cash_only), bool(l.accepts_financing),
         sc, json.dumps(reasons),
         json.dumps(getattr(l, "image_urls", []) or []),
         json.dumps(extras), vin, vehicle_key,
         relist["composite_id"] if relist else None, trim,
         bool(extras.get("needs_work")), previous_price,
         now if previous_price else None, *_location(l)))
    conn.execute(
        "INSERT INTO listing_prices(composite_id, price, event, observed_at) "
        "VALUES (?,?,?,?)", (cid, l.price, "relist" if relist else "first_seen", now))


# ---------- Ingestion normalization ---------------------------------------

def _normalize_listing(l: NormalizedListing) -> None:
    """Apply the cross-source checks every listing needs, whatever source it
    came from (feeds, APIs, browser extension, email digests):
      * posted_at → ISO 8601 UTC or None (RSS gives RFC 822)
      * branded-title / parts-car wording → is_salvage
      * runs/drives problems → extras["needs_work"]"""
    raw_posted = l.posted_at
    l.posted_at = to_iso(raw_posted)
    if raw_posted and not l.posted_at:
        l.extras = {**(l.extras or {}), "posted_raw": str(raw_posted)[:64]}
    if not l.is_salvage and looks_salvage(l.title, l.description):
        l.is_salvage = True
    if not l.is_dealer and looks_like_dealer(l.title, l.description):
        l.is_dealer = True
        l.is_cash_only = False
    if looks_needs_work(l.title, l.description):
        l.extras = {**(l.extras or {}), "needs_work": True}


# ---------- Opportunity assessment ----------------------------------------

BLOCKING_FLAGS = {"scam_signals", "too_good_to_be_true", "branded_title", "needs_work"}


def assess_risk(l: dict, v: "valuation.Valuation", discount: float) -> tuple[str, list[str]]:
    """(trust, flags). 'trusted' = no blocking flag AND medium+ confidence.
    Risky listings stay visible, labeled; they're just kept out of the
    default trusted feed and conservative alerts."""
    from scam_detector import score_listing as scam_score_listing
    flags = []
    scam, _ = scam_score_listing(l.get("title"), l.get("description"),
                                 l.get("price"), v.value_mid)
    if max(scam, l.get("scam_score") or 0) >= 50:
        flags.append("scam_signals")
    if discount > int(CONFIG.get("max_believable_discount_pct", 65)) / 100.0:
        flags.append("too_good_to_be_true")
    if l.get("is_salvage"):
        flags.append("branded_title")
    if l.get("needs_work"):
        flags.append("needs_work")
    if l.get("is_dealer"):
        flags.append("dealer")
    if not l.get("odometer"):
        flags.append("mileage_unknown")
    if l.get("relist_of"):
        flags.append("relisted")
    if v.confidence in ("low", "insufficient"):
        flags.append("low_confidence")
    trusted = not (set(flags) & BLOCKING_FLAGS) and v.confidence in ("medium", "high")
    return ("trusted" if trusted else "risk"), flags


def _event_for(l: dict) -> str:
    pp, p = l.get("previous_price"), l.get("price")
    if pp and p and p < pp:
        drop = pp - p
        if drop >= MATERIAL_DROP_USD and 100.0 * drop / pp >= MATERIAL_DROP_PCT:
            return "price_drop"
    return "new"


def _owner_alert_ok(l: dict, v, discount: float, trust: str) -> bool:
    if trust != "trusted":
        return False
    if not (int(CONFIG.get("min_price", 0)) <= l["price"]
            <= int(CONFIG.get("max_price", 10**9))):
        return False
    thr = CONFIG.get("deal_threshold_pct", 25) / 100.0
    if v.evidence_class != valuation.CONFIRMED_SALES:
        if not CONFIG.get("estimate_alerts_enabled", True):
            return False
        if v.n < int(CONFIG.get("estimate_alert_min_peers", 8)):
            return False
        thr = max(thr, int(CONFIG.get("estimate_alert_threshold_pct", 30)) / 100.0)
    return discount >= thr


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


def _sold_evidence(l: dict) -> tuple[Optional[dict], Optional[str]]:
    """Classified completed-sale evidence, or (None, reason)."""
    try:
        import comps
        raw, _ = comps.raw_sold(l["year"], l["make"], l["model"])
        return valuation.classify_sold_items(raw, l["year"], l["make"], l["model"]), None
    except Exception as e:
        return None, f"sold-comp lookup failed ({type(e).__name__})"


def _score_one_listing(composite_id: str) -> Optional[dict]:
    """Value one listing, assess risk, and — in ONE transaction — record the
    valuation, the owner's alert (if it qualifies) and every subscriber
    match with their notification jobs. Raises on DB errors so the durable
    job retries."""
    try:
        _config_mod.reload()
    except Exception:
        pass
    with db() as conn:
        row = conn.execute("SELECT * FROM listings WHERE composite_id = ?",
                           (composite_id,)).fetchone()
    if not row:
        return None
    l = dict(row)
    if not l.get("price") or not (l.get("year") and l.get("make") and l.get("model")):
        return None

    sold, sold_err = _sold_evidence(l)          # network; outside the txn
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        peers = valuation.load_peers(conn, l)
        v = valuation.value_listing(l, peers, sold)
        if sold_err:
            v.reasons.append(sold_err)
        if v.evidence_class == valuation.INSUFFICIENT or not v.value_mid:
            _record_valuation(conn, l, v, None, 0.0, "risk", ["insufficient_data"],
                              f"[{l['source']}] {l['year']} {l['make']} {l['model']}: "
                              f"insufficient comparables", now)
            return {"composite_id": composite_id, "evidence_class": v.evidence_class}

        discount = (v.value_mid - l["price"]) / v.value_mid
        trust, flags = assess_risk(l, v, discount)
        weight = CONFIG.get("source_weights", {}).get(l["source"], 1.0)
        score = max(0.0, discount) * 100 * weight * _freshness_factor(l.get("posted_at"))
        pr = profit_mod.compute(l["price"], v.value_low, v.value_mid, v.value_high,
                                v.evidence_class, None, scenarios=False)
        est_profit = pr["scenarios"]["base"]["profit"] if pr else None
        label = {"confirmed_sales": "sold comps"}.get(v.evidence_class, "asking comps")
        note = (f"[{l['source']}] {l['year']} {l['make']} {l['model']}"
                + (f" / {l['odometer']:,} mi" if l.get('odometer') else "")
                + f" listed ${l['price']:,} vs {label} ${v.value_mid:,.0f}"
                + f" (n={v.n}, {v.confidence} confidence, score={score:.1f}"
                + (f", est. profit ${est_profit:,}" if est_profit is not None else "") + ")")
        _record_valuation(conn, l, v, discount, score, trust, flags, note, now)

        event = _event_for(l)
        opp = {**l, "discount_pct": round(discount * 100, 2), "value_low": v.value_low,
               "value_mid": v.value_mid, "value_high": v.value_high,
               "comp_avg": v.value_mid, "comp_n": v.n, "basis": v.basis,
               "evidence_class": v.evidence_class, "confidence": v.confidence,
               "trust": trust, "risk_flags": flags, "score": score,
               "est_profit": est_profit, "event": event}

        alert_id = None
        if _owner_alert_ok(l, v, discount, trust):
            if event == "price_drop":
                r = conn.execute(
                    """INSERT INTO alerts(composite_id, comp_avg, comp_median, comp_n,
                             discount_pct, score, note, created_at, basis)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(composite_id) DO UPDATE SET
                         comp_avg=excluded.comp_avg, comp_median=excluded.comp_median,
                         comp_n=excluded.comp_n, discount_pct=excluded.discount_pct,
                         score=excluded.score, note=excluded.note, basis=excluded.basis
                       RETURNING id""",
                    (composite_id, v.value_mid, v.value_mid, v.n, discount * 100,
                     score, note, now, v.basis)).fetchone()
                alert_id = r[0]
                owner_key = f"{composite_id}:drop:{l['price']}"
            else:
                r = conn.execute(
                    """INSERT INTO alerts(composite_id, comp_avg, comp_median, comp_n,
                             discount_pct, score, note, created_at, basis)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(composite_id) DO NOTHING RETURNING id""",
                    (composite_id, v.value_mid, v.value_mid, v.n, discount * 100,
                     score, note, now, v.basis)).fetchone()
                alert_id = r[0] if r else None
                owner_key = f"alert{alert_id}"
            if alert_id and CONFIG.get("notify_text_enabled") \
                    and score >= int(CONFIG.get("notify_min_score", 30)):
                import notifications
                notifications.enqueue_owner(conn, {**opp, "alert_id": alert_id},
                                            owner_key, phone=CONFIG.get("notify_phone"))

        import saved_search_alerts
        matched = saved_search_alerts.evaluate(conn, opp)

    return {"composite_id": composite_id, "score": score, "discount": discount,
            "basis": v.basis, "evidence_class": v.evidence_class,
            "confidence": v.confidence, "trust": trust, "event": event,
            "alert_id": alert_id, "user_matches": len(matched)}


def _record_valuation(conn, l: dict, v, discount: Optional[float], score: float,
                      trust: str, flags: list[str], note: str, now: str) -> None:
    """Upsert the per-listing valuation record (table near_misses)."""
    conn.execute(
        """INSERT INTO near_misses(composite_id, discount_pct, score, comp_avg,
                comp_n, basis, note, refreshed_at, evidence_class, value_low,
                value_high, confidence, confidence_reasons, evidence, trust,
                risk_flags, price_at_score)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(composite_id) DO UPDATE SET
             discount_pct=excluded.discount_pct, score=excluded.score,
             comp_avg=excluded.comp_avg, comp_n=excluded.comp_n,
             basis=excluded.basis, note=excluded.note,
             refreshed_at=excluded.refreshed_at,
             evidence_class=excluded.evidence_class, value_low=excluded.value_low,
             value_high=excluded.value_high, confidence=excluded.confidence,
             confidence_reasons=excluded.confidence_reasons,
             evidence=excluded.evidence, trust=excluded.trust,
             risk_flags=excluded.risk_flags, price_at_score=excluded.price_at_score""",
        (l["composite_id"], None if discount is None else round(discount * 100, 2),
         score, v.value_mid, v.n, v.basis, note, now, v.evidence_class,
         v.value_low, v.value_high, v.confidence, json.dumps(v.reasons),
         valuation.evidence_json(v), trust, json.dumps(flags), l["price"]))


def ensure_scorer_started() -> None:
    """Deprecated no-op. Scoring now runs from durable jobs processed by
    jobs/worker.py; kept so older callers don't break."""
    return None


def backfill_unscored(limit: int = 2000) -> int:
    """Queue scoring for listings with no valuation at their current price
    (e.g. refused earlier by queue backpressure). Returns jobs queued."""
    with db() as conn:
        rows = conn.execute("""
            SELECT l.composite_id, l.price FROM listings l
            LEFT JOIN near_misses nm ON nm.composite_id = l.composite_id
            WHERE (nm.composite_id IS NULL OR nm.price_at_score IS NULL
                   OR nm.price_at_score <> l.price)
              AND l.year IS NOT NULL AND l.make IS NOT NULL AND l.model IS NOT NULL
              AND l.price IS NOT NULL
            ORDER BY l.first_seen_at DESC LIMIT ?""", (limit,)).fetchall()
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
    # paid per call: 30 min ≈ 1,440 calls/month for one territory
    "marketcheck_fsbo": 1800,
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
        blocked = sources.licensing_block(s, CONFIG)
        if blocked:
            statuses[sid] = {"status": health.DISABLED, "reason": blocked, "polled": False}
        elif sid not in enabled_ids:
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
