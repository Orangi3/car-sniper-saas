#!/usr/bin/env python3
"""
server.py — Flask backend for the sniper dashboard.

Endpoints:
  GET  /                                serve dashboard.html
  GET  /api/alerts?limit=50             ranked deal alerts
  GET  /api/listings?...                filtered listings (see params below)
  GET  /api/listings/closing_soon?hrs=24
  GET  /api/stats                       counts + threshold
  GET  /api/sources                     source on/off list
  GET  /api/facets                      makes/models/trims/years/states for filter dropdowns
  POST /api/poll                        force a poll right now
  POST /api/threshold {"pct": 25}       set deal threshold live
  POST /api/vin       {"vin": "..."}    VIN report
  POST /api/comps     {year,make,model,miles}
  POST /api/import    {listing payload from browser extension}

/api/listings params (all optional):
  source         comma-list of source IDs
  cash_only      "1" to filter to is_cash_only
  hide_dealer    "1" to filter dealers out
  hide_auction   "1" to filter auctions out
  hide_salvage   "1"
  min_price / max_price
  min_miles / max_miles
  min_year  / max_year
  make           comma-list
  model          comma-list
  search         substring on title (case-insensitive)
  sort           "newest" (default) | "price_asc" | "price_desc" | "miles_asc" | "score_desc"
  limit          default 200
"""
from __future__ import annotations

import json
import logging
import sqlite3  # noqa
import threading
import time
import traceback
from pathlib import Path

from flask import (Flask, g, jsonify, make_response, redirect, request,
                   send_from_directory)

import sniper
import sources
import config as _config
from config import CONFIG, save_override
import vin as vin_mod
import comps as comps_mod
import auth
import db as _db

ROOT = Path(__file__).parent
app = Flask(__name__, static_folder=str(ROOT))

# ---------- Production-safe schema check ----------------------------------
#
# Migrations are a SEPARATE, one-shot deploy step. Auto-running them on
# module import (which happens once per Gunicorn worker) created two
# production hazards:
#   1. N workers racing the same DDL — fine for sqlite/IF NOT EXISTS,
#      lethal for any future migration that does data backfills.
#   2. A slow migration would block every worker's startup, throwing
#      health-check failures across the fleet at the same time.
#
# Deploy order is now explicit:
#       1.  set DATABASE_URL / SNIPER_DB_PATH and any other env
#       2.  python3 -m migrations.runner       (exactly once per deploy)
#       3.  gunicorn server:app -w N
#
# To keep misconfigured deploys LOUD (not silently serving 500s), this
# block verifies the schema_migrations table is present and lists every
# expected migration ID as applied. If anything's missing we raise — the
# worker exits, the orchestrator restarts and surfaces the problem.

def _verify_migrations_applied() -> None:
    """Read schema_migrations and confirm every file in /migrations has
    been applied. Read-only — never executes a migration."""
    import os as _os
    from pathlib import Path as _P
    from migrations.runner import _list_migrations  # internal but stable
    expected = [mid for mid, _ in _list_migrations()]
    if not expected:
        return  # No migrations defined yet
    try:
        conn = _db.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM schema_migrations")
            applied = {r[0] if not _db.IS_PG else r["id"] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        raise RuntimeError(
            f"Cannot read schema_migrations from {_db.describe()}: {e}. "
            f"Run `python3 -m migrations.runner` once before starting the server."
        ) from e
    missing = [m for m in expected if m not in applied]
    if missing:
        raise RuntimeError(
            f"DB schema is behind code. Missing migrations: {missing}. "
            f"Run `python3 -m migrations.runner` (idempotent) and restart."
        )

# Opt-OUT (env var) so first-boot tooling that legitimately bootstraps
# its own DB can disable the gate. In normal prod boots, the check is on.
import os as _os
if _os.environ.get("SNIPER_SKIP_MIGRATION_CHECK", "0") != "1":
    try:
        _verify_migrations_applied()
    except RuntimeError as _e:
        # Print AND raise — Gunicorn captures stderr per worker, the raise
        # kills the worker, and the orchestrator's restart loop makes the
        # failure impossible to miss.
        print(f"[server] STARTUP ABORT: {_e}", flush=True)
        raise

# ---------- Structured request logging + global error handling ----------
_logger = logging.getLogger("sniper")
if not _logger.handlers:
    _h = logging.FileHandler(ROOT / "sniper-requests.log")
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)


@app.before_request
def _start_timer():
    request._t0 = time.time()


@app.get("/health")
def _health():
    """Reachability probe. Returns instantly, never auth-gated, never
    blocks on DB/comps/anything. Use this to verify the tunnel reaches the
    server: curl -s https://YOUR-TUNNEL/health should print {"status":"ok"}."""
    return jsonify({"status": "ok", "port": 8765, "service": "sniper"}), 200


@app.get("/debug/status")
def _debug_status():
    """Owner-only state snapshot — useful when something looks stuck.
    Does NOT call any external API. Owner-only because it lists DB paths,
    every registered route, and source IDs."""
    if not _is_owner_request():
        return jsonify({"error": "owner only"}), 403
    routes = sorted({str(r.rule) for r in app.url_map.iter_rules()})
    db_ok, listings_n, deals_n, db_err = True, None, None, None
    try:
        with sniper.db() as conn:
            listings_n = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            deals_n    = conn.execute(
                "SELECT COUNT(*) FROM near_misses WHERE discount_pct>0").fetchone()[0]
    except Exception as e:
        db_ok = False
        db_err = f"{type(e).__name__}: {e}"
    last_poll_time = max((d.get("polled_at")
                          for d in sniper.SOURCE_DIAG.values() if d.get("polled_at")),
                         default=None)
    last_poll_error = next((d.get("error")
                            for d in sniper.SOURCE_DIAG.values() if d.get("error")),
                           None)
    enabled_ids = [s.SOURCE_ID for s in sources.iter_sources(CONFIG)]
    all_ids     = [s.SOURCE_ID for s in sources.all_sources()]
    return jsonify({
        "server_alive":          True,
        "service":               "sniper",
        "port":                  8765,
        "routes_registered":     routes,
        "scanners_registered":   all_ids,
        "source_count":          len(all_ids),
        "enabled_sources":       enabled_ids,
        "last_poll_time":        last_poll_time,
        "last_poll_error":       last_poll_error,
        "listings_count":        listings_n,
        "deals_count":           deals_n,
        "database_ok":           db_ok,
        "database_error":        db_err,
        "data_file_path":        str(sniper.DB_PATH),
        "data_file_exists":      sniper.DB_PATH.exists(),
        "dashboard_html_exists": (ROOT / "dashboard.html").is_file(),
        "scorer":                sniper.get_scorer_stats(),
        "public_url":            _read_public_url(),
    })


def _read_public_url():
    """Returns the most recent public URL written by TEMPORARY LINK.command
    or CUSTOM DOMAIN.command, or None if no tunnel has been started yet."""
    try:
        v = (ROOT / "ngrok-url.txt").read_text().strip()
        return v or None
    except Exception:
        return None


# Headers that any reverse proxy / tunnel inserts. Their presence proves the
# request came in via a tunnel even though remote_addr will still be 127.0.0.1
# (ssh -R, cloudflared, ngrok, lhr.life all terminate locally).
_PROXY_HEADERS = (
    "X-Forwarded-For", "X-Real-IP", "Forwarded",
    "CF-Connecting-IP", "X-Forwarded-Host", "X-Original-Forwarded-For",
)


# Endpoints reachable without a session (the bare minimum for the login flow,
# health probes, and the dashboard shell which JS will then gate on /api/me).
#
# /api/billing/webhook is intentionally in here: Stripe is the caller, it
# proves authenticity via signature (verified in the handler), not via a
# session cookie. Treating it as "public" lets the cookie-gate before_request
# pass; the handler itself rejects any payload whose signature doesn't
# verify with STRIPE_WEBHOOK_SECRET.
_PUBLIC_PATHS = frozenset({
    "/", "/health", "/login", "/login.html",
    "/api/auth/register", "/api/auth/login", "/api/me",
    "/api/billing/webhook",
})


@app.before_request
def _load_user():
    """Resolve the session cookie -> User and stash on flask.g. NEVER enforces
    auth here — endpoints declare their own requirements via @login_required /
    @plan_required / @admin_required decorators. That keeps the access policy
    visible right next to each route instead of buried in a giant if-tree."""
    if request.path == "/health":
        # /health stays unconditionally public — never touches the DB.
        return None
    try:
        auth.load_user_from_request()
    except Exception as e:
        # Auth lookup failures (e.g. DB hiccup) must not 500 the whole app.
        # Treat the request as anonymous; downstream decorators will 401 as
        # needed. The error is logged so we can spot persistent DB outages.
        _logger.exception(f"auth.load_user failed: {e}")
        g.user = None
    u = auth.current_user()
    _logger.info(
        f"REQ path={request.path} method={request.method} "
        f"user={(u.email if u else 'anon')} "
        f"role={(u.role if u else '-')} plan={(u.plan if u else '-')}"
    )


# Backwards-compatible shim — a handful of legacy handlers (e.g. /debug/status,
# /api/notify_settings phone-masking) still want to know "is this an admin
# touching the box?". Map the new world onto the old call site.
def _is_owner_request() -> bool:
    u = auth.current_user()
    return bool(u and u.is_admin)


def _is_external_request() -> bool:
    """True for any request not from a logged-in admin. Used by handlers that
    want to redact owner-only fields (e.g. phone number, tunnel target)."""
    u = auth.current_user()
    return not (u and u.is_admin)


@app.after_request
def _log_and_cache(response):
    # API responses MUST NOT be cached — kills Safari stale-data bugs.
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    # Security headers — lock the dashboard down against framing, MIME
    # sniffing, referrer leaks, and unwanted browser permissions.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), camera=(), microphone=(), interest-cohort=()")
    # CSP — the dashboard is a single file with inline JS/CSS, so 'unsafe-
    # inline' is required. Images allowed from any origin since listings come
    # from many marketplaces. No iframes, no external scripts.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data: https: http:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    try:
        elapsed = (time.time() - getattr(request, "_t0", time.time())) * 1000
        _logger.info(f"{request.method} {request.path} {response.status_code} {elapsed:.0f}ms")
    except Exception:
        pass
    return response


@app.errorhandler(Exception)
def _json_error(e):
    """Global error handler — API routes always return JSON, never HTML 500.

    Routine static-file 404s (favicon.ico, apple-touch-icon*.png, robots.txt,
    etc.) are NOT logged as UNCAUGHT — browsers auto-fetch them and they
    flood the error log otherwise, hiding real problems."""
    from werkzeug.exceptions import HTTPException, NotFound
    # Non-API 404s are mostly browser icon polling — return the normal
    # 404 response without polluting the UNCAUGHT log channel.
    if isinstance(e, NotFound) and not request.path.startswith("/api/"):
        return "Not found", 404
    # Any other HTTPException — preserve its status code, no UNCAUGHT spam.
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description, "code": e.code}), e.code
        return e.description or "HTTP error", e.code or 500
    tb = traceback.format_exc()
    _logger.error(f"UNCAUGHT {request.method} {request.path}: {e}\n{tb}")
    if request.path.startswith("/api/"):
        return jsonify({"error": str(e), "type": type(e).__name__}), 500
    return f"Internal error: {e}", 500


def _rows(query: str, params=()) -> list[dict]:
    with sniper.db() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


# ---------- Pages -----------------------------------------------------

@app.get("/")
def index():
    # Gate: anonymous visitors get the login page; the dashboard is private.
    # We still ALLOW the dashboard HTML to load so its JS can call /api/me
    # and decide for itself, but unauthenticated requests are redirected to
    # /login as the visible entry point.
    if not auth.current_user():
        return redirect("/login", code=302)
    # Cache-buster redirect: every fresh hit goes to /?v=<file mtime> so the
    # browser must fetch the latest dashboard.html and can NEVER keep running
    # an outdated cached JS bundle — even when Safari ignores Cache-Control.
    if "v" not in request.args:
        try:
            mt = int((ROOT / "dashboard.html").stat().st_mtime)
        except Exception:
            mt = int(time.time())
        # Preserve safe, allowlisted return-flow params across the
        # cache-bust redirect so /billing/success?session=… can land on
        # /?v=…&billing_return=success without losing the marker the JS
        # uses to enable bounded post-checkout polling. `session` is the
        # Stripe Checkout Session ID — non-secret, used by the JS only
        # for UX display, never for entitlement decisions.
        extras = []
        rv = (request.args.get("billing_return") or "").strip().lower()
        if rv in ("success", "cancel"):
            extras.append(f"billing_return={rv}")
        sid = (request.args.get("session") or "").strip()
        if sid and sid.startswith("cs_") and len(sid) < 200 \
                and all(c.isalnum() or c == "_" for c in sid):
            extras.append(f"session={sid}")
        suffix = ("&" + "&".join(extras)) if extras else ""
        return redirect(f"/?v={mt}{suffix}", code=302)
    # Serve ONLY the static dashboard file from ROOT. No DB, no comp, no
    # network — the page must load even if every backend dependency is down.
    page = ROOT / "dashboard.html"
    if not page.is_file():
        _logger.error(f"dashboard.html MISSING at {page}")
        return ("Dashboard not found: dashboard.html is missing from the "
                "sniper folder. The server itself is healthy — check "
                "/health. Restore dashboard.html and reload.", 500)
    resp = send_from_directory(ROOT, "dashboard.html")
    # Hard no-cache so design changes always show on next refresh
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ---------- Alerts ----------------------------------------------------

@app.get("/api/alerts")
def api_alerts():
    limit = int(request.args.get("limit", 50))
    cash_only = request.args.get("cash_only") == "1"
    hide_auction = request.args.get("hide_auction") == "1"
    where = []
    args = []
    if cash_only:
        where.append("l.is_cash_only=1")
    if hide_auction:
        where.append("l.is_auction=0")
    if request.args.get("show_scams") != "1":
        where.append("(l.scam_score IS NULL OR l.scam_score < 50)")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = _rows(f"""
        SELECT a.id, a.created_at, a.discount_pct, a.score, a.comp_avg, a.comp_median,
               a.comp_n, a.basis, a.note, a.composite_id,
               l.url, l.title, l.price, l.year, l.make, l.model,
               l.odometer, l.source, l.state, l.location, l.posted_at, l.first_seen_at,
               l.is_auction, l.is_cash_only, l.auction_end_at,
               l.scam_score, l.scam_reasons, l.image_urls,
               (CASE WHEN s.composite_id IS NULL THEN 0 ELSE 1 END) AS is_saved,
               s.status AS save_status
        FROM alerts a JOIN listings l ON l.composite_id = a.composite_id
        LEFT JOIN saved s ON s.composite_id = a.composite_id
        {where_sql}
        ORDER BY a.score DESC LIMIT ?""", (*args, limit))
    # Inject estimated profit
    fees = 1500
    for r in rows:
        if r.get("comp_avg") and r.get("price"):
            r["est_profit"] = int(r["comp_avg"] - r["price"] - fees)
    return jsonify(rows)


@app.get("/api/fresh_listings")
def api_fresh_listings():
    """Newest listings, each carrying its instant-estimate or verified score
    from near_misses (LEFT JOIN). Final fallback so the deals pane is never
    empty — and a row only shows 'pending' if it genuinely has no score yet."""
    limit = int(request.args.get("limit", 30))
    where = ["l.price IS NOT NULL", "l.year IS NOT NULL"]
    if request.args.get("cash_only") == "1":    where.append("l.is_cash_only=1")
    if request.args.get("hide_auction") == "1": where.append("l.is_auction=0")
    if request.args.get("show_scams") != "1":
        where.append("(l.scam_score IS NULL OR l.scam_score < 50)")
    rows = _rows(f"""
        SELECT l.composite_id, l.source, l.source_id, l.url, l.title, l.price,
               l.year, l.make, l.model, l.odometer, l.location, l.state,
               l.posted_at, l.first_seen_at, l.is_dealer, l.is_salvage,
               l.is_auction, l.is_cash_only, l.scam_score, l.scam_reasons,
               l.image_urls, 0 AS is_saved,
               nm.comp_avg, nm.discount_pct, nm.score, nm.comp_n, nm.basis
        FROM listings l
        LEFT JOIN near_misses nm ON nm.composite_id = l.composite_id
        WHERE {' AND '.join(where)}
        ORDER BY l.first_seen_at DESC LIMIT ?""", (limit,))
    fees = 1500
    for r in rows:
        # 'pending' only when there is truly no score row yet
        r["pending_scoring"] = r.get("comp_avg") is None
        if r.get("comp_avg") and r.get("price"):
            r["est_profit"] = int(r["comp_avg"] - r["price"] - fees)
        else:
            r["est_profit"] = None
    return jsonify(rows)


@app.get("/api/near_misses")
def api_near_misses():
    """Every scored listing priced below its comp — the always-populated
    ranked board (instant estimates + verified comps), ordered by score."""
    limit = int(request.args.get("limit", 30))
    where = ["nm.discount_pct > 0"]
    if request.args.get("cash_only") == "1":    where.append("l.is_cash_only=1")
    if request.args.get("hide_auction") == "1": where.append("l.is_auction=0")
    if request.args.get("show_scams") != "1":
        where.append("(l.scam_score IS NULL OR l.scam_score < 50)")
    rows = _rows(f"""
        SELECT nm.discount_pct, nm.score, nm.comp_avg, nm.comp_n, nm.basis, nm.note,
               nm.refreshed_at AS created_at, nm.composite_id,
               l.url, l.title, l.price, l.year, l.make, l.model,
               l.odometer, l.source, l.state, l.posted_at, l.first_seen_at,
               l.is_auction, l.is_cash_only, l.scam_score, l.scam_reasons,
               l.image_urls,
               (CASE WHEN s.composite_id IS NULL THEN 0 ELSE 1 END) AS is_saved,
               s.status AS save_status
        FROM near_misses nm JOIN listings l ON l.composite_id = nm.composite_id
        LEFT JOIN saved s ON s.composite_id = nm.composite_id
        WHERE {' AND '.join(where)}
        ORDER BY nm.score DESC LIMIT ?""", (limit,))
    fees = 1500
    for r in rows:
        if r.get("comp_avg") and r.get("price"):
            r["est_profit"] = int(r["comp_avg"] - r["price"] - fees)
    return jsonify(rows)


# ---------- Saved deals ----------------------------------------------

@app.get("/api/saved")
def api_saved():
    rows = _rows("""
        SELECT s.saved_at, s.note AS user_note, s.status, s.updated_at,
               l.composite_id, l.url, l.title, l.price, l.year, l.make, l.model,
               l.odometer, l.source, l.state, l.posted_at, l.first_seen_at,
               l.is_auction, l.is_cash_only, l.scam_score, l.scam_reasons, l.image_urls,
               nm.discount_pct, nm.comp_avg, nm.score, nm.comp_n, nm.basis
        FROM saved s
        JOIN listings l ON l.composite_id = s.composite_id
        LEFT JOIN near_misses nm ON nm.composite_id = s.composite_id
        ORDER BY s.saved_at DESC""")
    fees = 1500
    for r in rows:
        if r.get("comp_avg") and r.get("price"):
            r["est_profit"] = int(r["comp_avg"] - r["price"] - fees)
    return jsonify(rows)


# Pipeline states a saved deal can be in (used by /api/save, /api/saved/status)
_PIPELINE_STATUSES = {
    "saved", "contacted", "vin_needed", "negotiating",
    "going_to_see", "bought", "passed",
}


@app.post("/api/save")
def api_save():
    body = request.get_json(force=True) or {}
    cid = body.get("composite_id")
    note = body.get("note", "")
    status = (body.get("status") or "saved").strip().lower()
    if status not in _PIPELINE_STATUSES:
        status = "saved"
    if not cid:
        return jsonify({"error": "composite_id required"}), 400
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with sniper.db() as conn:
        # First save preserves saved_at; later updates only touch updated_at.
        conn.execute(
            """INSERT INTO saved(composite_id, note, saved_at, status, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(composite_id) DO UPDATE SET
                 note=excluded.note,
                 status=excluded.status,
                 updated_at=excluded.updated_at""",
            (cid, note, now, status, now))
    return jsonify({"ok": True, "composite_id": cid, "status": status})


@app.post("/api/unsave")
def api_unsave():
    body = request.get_json(force=True) or {}
    cid = body.get("composite_id")
    if not cid:
        return jsonify({"error": "composite_id required"}), 400
    with sniper.db() as conn:
        conn.execute("DELETE FROM saved WHERE composite_id=?", (cid,))
    return jsonify({"ok": True, "unsaved": cid})


@app.post("/api/saved/status")
def api_saved_status():
    """Move a deal through the pipeline (saved → contacted → ... → bought/passed).
    Idempotent — sending the same status again is a no-op."""
    body = request.get_json(force=True) or {}
    cid = body.get("composite_id")
    status = (body.get("status") or "").strip().lower()
    if not cid:
        return jsonify({"error": "composite_id required"}), 400
    if status not in _PIPELINE_STATUSES:
        return jsonify({"error": f"status must be one of "
                                  f"{sorted(_PIPELINE_STATUSES)}"}), 400
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with sniper.db() as conn:
        # Create the saved row if it didn't exist (Pass from the deal board).
        conn.execute(
            """INSERT INTO saved(composite_id, note, saved_at, status, updated_at)
               VALUES(?, '', ?, ?, ?)
               ON CONFLICT(composite_id) DO UPDATE SET
                 status=excluded.status, updated_at=excluded.updated_at""",
            (cid, now, status, now))
    return jsonify({"ok": True, "composite_id": cid, "status": status})


@app.post("/api/saved/note")
def api_saved_note():
    """Edit the note on an already-saved deal. Doesn't touch status."""
    body = request.get_json(force=True) or {}
    cid = body.get("composite_id")
    if not cid:
        return jsonify({"error": "composite_id required"}), 400
    note = str(body.get("note") or "")
    if len(note) > 4000:
        return jsonify({"error": "note too long (max 4000 chars)"}), 400
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with sniper.db() as conn:
        # If the row doesn't exist yet, create it so the user can add a note
        # to a deal before saving it (rare path but harmless).
        conn.execute(
            """INSERT INTO saved(composite_id, note, saved_at, status, updated_at)
               VALUES(?, ?, ?, 'saved', ?)
               ON CONFLICT(composite_id) DO UPDATE SET
                 note=excluded.note, updated_at=excluded.updated_at""",
            (cid, note, now, now))
    return jsonify({"ok": True, "composite_id": cid})


# ---------- Listings with filtering ----------------------------------

def _build_filters(args):
    where, params = [], []
    src = args.get("source")
    if src:
        srcs = [s.strip() for s in src.split(",") if s.strip()]
        if srcs:
            where.append(f"source IN ({','.join('?' for _ in srcs)})")
            params.extend(srcs)
    if args.get("cash_only") == "1":   where.append("is_cash_only=1")
    if args.get("hide_dealer") == "1": where.append("is_dealer=0")
    if args.get("hide_auction") == "1":where.append("is_auction=0")
    if args.get("hide_salvage") == "1":where.append("is_salvage=0")
    # Scam guard — default ON; user must explicitly request show_scams=1 to bypass
    if args.get("show_scams") != "1":
        where.append("(scam_score IS NULL OR scam_score < 50)")
    for k, col, op in [("min_price","price",">="),("max_price","price","<="),
                       ("min_miles","odometer",">="),("max_miles","odometer","<="),
                       ("min_year","year",">="),("max_year","year","<=")]:
        v = args.get(k)
        if v:
            try:
                where.append(f"{col} {op} ?")
                params.append(int(v))
            except ValueError:
                pass
    # Max listing age (minutes) — first_seen_at within this window
    age = args.get("max_age_min") or CONFIG.get("max_listing_age_min")
    try:
        age = int(age) if age else 0
    except (ValueError, TypeError):
        age = 0
    if age > 0:
        where.append("first_seen_at > datetime('now', ?)")
        params.append(f"-{age} minutes")
    for k, col in [("make","make"),("model","model"),("state","state")]:
        v = args.get(k)
        if v:
            vs = [x.strip() for x in v.split(",") if x.strip()]
            if vs:
                where.append(f"LOWER({col}) IN ({','.join('?' for _ in vs)})")
                params.extend(s.lower() for s in vs)
    if args.get("search"):
        where.append("LOWER(title) LIKE ?")
        params.append(f"%{args['search'].lower()}%")
    sort = args.get("sort", "newest")
    order = {
        "newest":     "first_seen_at DESC",
        "price_asc":  "price ASC",
        "price_desc": "price DESC",
        "miles_asc":  "odometer ASC NULLS LAST",
        "score_desc": "first_seen_at DESC",  # use alerts endpoint for score
    }.get(sort, "first_seen_at DESC")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    return where_sql, order, params


@app.get("/api/listings")
def api_listings():
    limit = int(request.args.get("limit", 200))
    where_sql, order, params = _build_filters(request.args)
    return jsonify(_rows(f"""
        SELECT composite_id, source, source_id, url, title, price, year, make, model,
               odometer, location, state, posted_at, first_seen_at, is_dealer, is_salvage,
               is_auction, auction_end_at, bid_count, buy_now_price,
               is_cash_only, accepts_financing, scam_score, scam_reasons, image_urls
        FROM listings {where_sql} ORDER BY {order} LIMIT ?""", (*params, limit)))


@app.get("/api/listings/closing_soon")
def api_closing_soon():
    hrs = int(request.args.get("hrs", CONFIG.get("closing_soon_hours", 24)))
    return jsonify(_rows("""
        SELECT composite_id, source, source_id, url, title, price, year, make, model,
               odometer, location, state, first_seen_at,
               is_auction, auction_end_at, bid_count, buy_now_price
        FROM listings
        WHERE is_auction=1 AND auction_end_at IS NOT NULL
          AND auction_end_at > datetime('now')
          AND auction_end_at < datetime('now', ?)
        ORDER BY auction_end_at ASC LIMIT 200""", (f"+{hrs} hours",)))


# ---------- Stats / Sources / Facets ---------------------------------

@app.get("/api/stats")
def api_stats():
    with sniper.db() as conn:
        n_listings = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        n_alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        # n_deals matches what the BEST DEALS tab actually shows — every
        # near-miss with a positive discount vs. comps. The legacy `alerts`
        # field stays for back-compat (verified-only alerts table), but
        # `deals` is what the hero "DEALS FOUND" tile should read so the
        # tile and the tab badge can't disagree.
        n_deals = conn.execute(
            "SELECT COUNT(*) FROM near_misses WHERE discount_pct > 0"
        ).fetchone()[0]
        n_cash = conn.execute("SELECT COUNT(*) FROM listings WHERE is_cash_only=1").fetchone()[0]
        n_auction = conn.execute("SELECT COUNT(*) FROM listings WHERE is_auction=1").fetchone()[0]
        n_dealer = conn.execute("SELECT COUNT(*) FROM listings WHERE is_dealer=1").fetchone()[0]
        n_salvage = conn.execute("SELECT COUNT(*) FROM listings WHERE is_salvage=1").fetchone()[0]
        last = conn.execute("SELECT MAX(first_seen_at) FROM listings").fetchone()[0]
        per_source = [{"source": r["source"], "n": r["n"]}
                      for r in conn.execute("SELECT source, COUNT(*) n FROM listings "
                                            "GROUP BY source ORDER BY n DESC").fetchall()]
    return jsonify({
        "listings": n_listings, "alerts": n_alerts, "deals": n_deals,
        "cash_only": n_cash, "auctions": n_auction,
        "dealer": n_dealer, "salvage": n_salvage,
        "last_poll_seen": last,
        "per_source": per_source,
        "deal_threshold_pct": CONFIG["deal_threshold_pct"],
        "zip": CONFIG["zip"], "radius_mi": CONFIG["radius_mi"],
    })


@app.get("/api/diagnostics")
def api_diagnostics():
    """Per-source last-poll diagnostics: count, elapsed, error, regions.
    Shown in dashboard so user can immediately see WHY a source is empty."""
    out = []
    enabled_ids = {s.SOURCE_ID for s in sources.iter_sources(CONFIG)}
    for s in sources.all_sources():
        d = sniper.SOURCE_DIAG.get(s.SOURCE_ID, {})
        out.append({
            "id": s.SOURCE_ID, "name": s.SOURCE_NAME,
            "enabled": s.SOURCE_ID in enabled_ids,
            "count": d.get("count"), "elapsed_s": d.get("elapsed_s"),
            "error": d.get("error"), "regions": d.get("regions"),
            "polled_at": d.get("polled_at"),
        })
    return jsonify(out)


@app.get("/api/sources")
def api_sources():
    enabled_ids = {s.SOURCE_ID for s in sources.iter_sources(CONFIG)}
    return jsonify([{
        "id": s.SOURCE_ID, "name": s.SOURCE_NAME,
        "enabled": s.SOURCE_ID in enabled_ids,
        "weight": CONFIG.get("source_weights", {}).get(s.SOURCE_ID, 1.0),
    } for s in sources.all_sources()])


@app.get("/api/facets")
def api_facets():
    """Distinct makes/models/years/states for filter dropdowns."""
    with sniper.db() as conn:
        makes = [r[0] for r in conn.execute(
            "SELECT DISTINCT make FROM listings WHERE make IS NOT NULL ORDER BY make").fetchall()]
        models = [r[0] for r in conn.execute(
            "SELECT DISTINCT model FROM listings WHERE model IS NOT NULL ORDER BY model").fetchall()]
        years = [r[0] for r in conn.execute(
            "SELECT DISTINCT year FROM listings WHERE year IS NOT NULL ORDER BY year DESC").fetchall()]
        states = [r[0] for r in conn.execute(
            "SELECT DISTINCT state FROM listings WHERE state IS NOT NULL ORDER BY state").fetchall()]
        price_range = conn.execute(
            "SELECT MIN(price), MAX(price) FROM listings WHERE price > 0").fetchone()
        miles_range = conn.execute(
            "SELECT MIN(odometer), MAX(odometer) FROM listings WHERE odometer > 0").fetchone()
    return jsonify({
        "makes": makes, "models": models, "years": years, "states": states,
        "price_min": price_range[0] or 0, "price_max": price_range[1] or 100000,
        "miles_min": miles_range[0] or 0, "miles_max": miles_range[1] or 300000,
    })


# ---------- Mutations -----------------------------------------------

@app.post("/api/poll")
def api_poll():
    rl = _rl_or_fail("poll", 6)
    if rl: return rl
    # Force all sources when user clicks Poll Now (ignore tier schedule).
    # Returns immediately after ingestion — scoring runs in background.
    return jsonify(sniper.poll_once(verbose=False, force_all=True))


@app.post("/api/poll-all")
def api_poll_all():
    """Same poll as /api/poll but returns the rich schema requested by the
    spec: per-source attempted/success/error/skipped_reason + totals. One
    failed source never poisons the whole response."""
    rl = _rl_or_fail("poll", 6)
    if rl: return rl
    from datetime import datetime, timezone
    started = datetime.now(timezone.utc)
    base = {}
    try:
        base = sniper.poll_once(verbose=False, force_all=True) or {}
    except Exception as e:
        return jsonify({
            "ok": False,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(e).__name__}: {e}",
            "sources": [],
        }), 500
    finished = datetime.now(timezone.utc)
    enabled_ids = {s.SOURCE_ID for s in sources.iter_sources(CONFIG)}
    per_src = base.get("per_source", {}) or {}
    src_results = []
    for s in sources.all_sources():
        sid  = s.SOURCE_ID
        diag = sniper.SOURCE_DIAG.get(sid, {}) or {}
        per  = per_src.get(sid, {}) or {}
        enabled   = sid in enabled_ids
        attempted = enabled and (diag.get("polled_at") is not None
                                 or per.get("fetched") is not None)
        err       = diag.get("error")
        success   = attempted and not err
        skipped   = None if enabled else "source disabled"
        if enabled and not attempted:
            skipped = "not run this tick (tiered schedule)"
        src_results.append({
            "id":                sid,
            "name":              s.SOURCE_NAME,
            "enabled":           enabled,
            "attempted":         bool(attempted),
            "success":           bool(success),
            "listings_returned": int(per.get("fetched") or diag.get("count") or 0),
            "new_listings":      int(per.get("new") or 0),
            "elapsed_s":         diag.get("elapsed_s"),
            "error":             err,
            "skipped_reason":    skipped,
        })
    # deals_n: count of below-comp listings — same threshold as the BEST
    # DEALS tab and /api/stats `deals` so the poll-button result, the hero
    # tile, and the tab badge always agree.
    # profit_pool: sum of est_profit with a $1500 buffer for fees/repairs,
    # so the dollar figure stays conservative even though the count is
    # the broader "every below-comp deal" set.
    profit_pool = 0
    deals_n = 0
    try:
        with sniper.db() as conn:
            for cavg, price in conn.execute(
                "SELECT nm.comp_avg, l.price FROM near_misses nm "
                "JOIN listings l ON l.composite_id=nm.composite_id "
                "WHERE nm.discount_pct > 0").fetchall():
                if cavg and price:
                    deals_n += 1
                    p = int(cavg - price - 1500)
                    if p > 0:
                        profit_pool += p
    except Exception:
        pass
    return jsonify({
        "ok":                True,
        "started_at":        started.isoformat(),
        "finished_at":       finished.isoformat(),
        "duration_seconds":  round((finished - started).total_seconds(), 2),
        "listings_scanned":  int(base.get("fetched") or 0),
        "new_listings":      int(base.get("new") or 0),
        "deals_found":       deals_n,
        "profit_pool":       profit_pool,
        "scoring_pending":   base.get("scoring_pending", 0),
        "sources":           src_results,
    })


@app.get("/api/score_progress")
def api_score_progress():
    """How many listings still queued for scoring vs done."""
    return jsonify(sniper.get_scorer_stats())


@app.post("/api/threshold")
def api_threshold():
    body = request.get_json(force=True) or {}
    try:
        pct = max(1, min(80, int(body.get("pct"))))
    except (TypeError, ValueError):
        return jsonify({"error": "pct must be 1-80"}), 400
    CONFIG["deal_threshold_pct"] = pct
    save_override("deal_threshold_pct", pct)
    return jsonify({"deal_threshold_pct": pct})


@app.get("/api/settings")
def api_settings_get():
    return jsonify({
        "zip": CONFIG["zip"],
        "radius_mi": CONFIG["radius_mi"],
        "max_listing_age_min": CONFIG.get("max_listing_age_min", 0),
        "closing_soon_hours": CONFIG.get("closing_soon_hours", 24),
        "min_price": CONFIG.get("min_price"),
        "max_price": CONFIG.get("max_price"),
        "craigslist_regions": CONFIG["sources"]["craigslist"]["regions"],
        "govdeals_states": CONFIG["sources"]["govdeals"]["states"],
        "deal_threshold_pct": CONFIG["deal_threshold_pct"],
        "comp_match_threshold_pct": CONFIG.get("comp_match_threshold_pct", 100),
        "bumper_api_key_set": bool(CONFIG.get("bumper_api_key")),
        "clearvin_api_key_set": bool(CONFIG.get("clearvin_api_key")),
    })


@app.post("/api/settings")
def api_settings_post():
    body = request.get_json(force=True) or {}
    saved = {}

    def _save(key, transform=lambda x: x, validate=lambda x: True):
        if key in body and body[key] is not None and body[key] != "":
            try:
                v = transform(body[key])
                if validate(v):
                    save_override(key, v)
                    saved[key] = v
            except (ValueError, TypeError):
                pass

    _save("zip", str, lambda v: len(v) == 5 and v.isdigit())
    _save("radius_mi", int, lambda v: 1 <= v <= 1000)
    _save("max_listing_age_min", int, lambda v: 0 <= v <= 100000)
    _save("closing_soon_hours", int, lambda v: 1 <= v <= 720)
    _save("min_price", int, lambda v: 0 <= v <= 1000000)
    _save("max_price", int, lambda v: 0 <= v <= 10000000)
    _save("deal_threshold_pct", int, lambda v: 1 <= v <= 80)
    _save("comp_match_threshold_pct", int, lambda v: 50 <= v <= 100)

    # CSV-style fields
    if "craigslist_regions" in body:
        regions = [s.strip() for s in str(body["craigslist_regions"]).replace(",", " ").split() if s.strip()]
        if regions:
            save_override("craigslist_regions", regions)
            saved["craigslist_regions"] = regions
    if "govdeals_states" in body:
        states = [s.strip().upper()[:2] for s in str(body["govdeals_states"]).replace(",", " ").split() if s.strip()]
        if states:
            save_override("govdeals_states", states)
            saved["govdeals_states"] = states

    # Optional VIN keys (overrides; doesn't print them back)
    for k in ("bumper_api_key", "clearvin_api_key"):
        if k in body:
            save_override(k, str(body[k] or ""))
            saved[k + "_set"] = bool(body[k])

    # Apply live
    _config.reload()
    return jsonify({"saved": saved, **(api_settings_get().get_json() if False else {})})


@app.post("/api/vin_provider")
def api_vin_provider():
    """Save Bumper or ClearVin API key. Tests it before persisting if test=true."""
    rl = _rl_or_fail("vin_provider", 10)
    if rl: return rl
    body = request.get_json(force=True) or {}
    bumper = (body.get("bumper_api_key") or "").strip()
    clearvin = (body.get("clearvin_api_key") or "").strip()
    if bumper:
        save_override("bumper_api_key", bumper)
        CONFIG["bumper_api_key"] = bumper
    if clearvin:
        save_override("clearvin_api_key", clearvin)
        CONFIG["clearvin_api_key"] = clearvin
    _config.reload()
    # Optional: test by calling the provider on a test VIN
    test_vin = body.get("test_vin")
    if test_vin and len(test_vin) == 17:
        try:
            r = vin_mod.history_report(test_vin)
            return jsonify({"ok": True, "provider": r.get("_provider"),
                            "title_status": r.get("title_status"),
                            "owners": r.get("owners")})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


@app.post("/api/vin")
def api_vin():
    """Legacy endpoint — kept so the existing dashboard VIN panel keeps
    working. New consumers should hit /api/vin/check instead."""
    rl = _rl_or_fail("vin", 30)
    if rl: return rl
    body = request.get_json(force=True) or {}
    v = (body.get("vin") or "").upper().strip()
    if len(v) != 17 or not v.isalnum():
        return jsonify({"error": "VIN must be 17 alphanumeric characters"}), 400
    try:
        return jsonify(vin_mod.report(v))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============= v3 VIN endpoints ==========================================
# All endpoints below return JSON ONLY — never HTML — even on error. The
# `@app.errorhandler` registered for VIN routes catches anything that
# escapes a per-route try/except.

def _vin_json_error(msg: str, code: int = 400, **extra):
    body = {"ok": False, "error": str(msg)}
    body.update(extra)
    return jsonify(body), code


@app.get("/api/vin/decode/<vin>")
def api_vin_decode(vin: str):
    """Free NHTSA decode only. Validation errors return 400 with JSON,
    NHTSA outages return 502 with JSON. Never crashes."""
    rl = _rl_or_fail("vin", 30)
    if rl: return rl
    val = vin_mod.validate_vin(vin)
    if not val["ok"]:
        return _vin_json_error("; ".join(val["errors"]) or "invalid VIN",
                               400, validation=val)
    try:
        force = (request.args.get("force_refresh") in ("1", "true", "yes"))
        decoded = vin_mod.decode_vin(val["normalized"], force_refresh=force)
        return jsonify({"ok": True, "vin": val["normalized"],
                        "validation": val, "decoded": decoded,
                        "source": "nhtsa"})
    except ValueError as e:
        return _vin_json_error(str(e), 400, validation=val)
    except Exception as e:
        _logger.warning(f"NHTSA decode failed for {val['normalized']}: {e}")
        return _vin_json_error(f"NHTSA unreachable: {e}", 502,
                               validation=val)


@app.post("/api/vin/check")
def api_vin_check():
    """Full report. Body: {vin: "...", listing: {year, make, model, trim}?,
    force_refresh: bool?}. Listing is optional; if provided, mismatches are
    surfaced. Always returns 200 with JSON unless the VIN is invalid."""
    rl = _rl_or_fail("vin", 30)
    if rl: return rl
    body = request.get_json(silent=True) or {}
    vin = body.get("vin") or ""
    listing = body.get("listing") or None
    force = bool(body.get("force_refresh"))
    try:
        result = vin_mod.check(vin, listing=listing, force_refresh=force)
        if not result["validation"]["ok"]:
            return jsonify({"ok": False, **result}), 400
        return jsonify({"ok": True, **result})
    except Exception as e:
        _logger.exception("VIN check failed")
        return _vin_json_error(f"unexpected error: {e}", 500)


@app.get("/api/vin/report/<vin>")
def api_vin_report(vin: str):
    """Cached full report (no listing-mismatch — use POST /api/vin/check
    for that). Pulls from cache when fresh."""
    rl = _rl_or_fail("vin", 30)
    if rl: return rl
    val = vin_mod.validate_vin(vin)
    if not val["ok"]:
        return _vin_json_error("; ".join(val["errors"]) or "invalid VIN",
                               400, validation=val)
    try:
        force = (request.args.get("force_refresh") in ("1", "true", "yes"))
        cached = None if force else vin_mod.cache_get(val["normalized"], "report")
        if cached is not None:
            return jsonify({"ok": True, "from_cache": True, **cached})
        result = vin_mod.check(val["normalized"], force_refresh=force)
        return jsonify({"ok": True, "from_cache": False, **result})
    except Exception as e:
        _logger.exception("VIN report failed")
        return _vin_json_error(f"unexpected error: {e}", 500)


@app.get("/api/vin/provider-status")
def api_vin_provider_status():
    try:
        return jsonify({"ok": True, **vin_mod.provider_status()})
    except Exception as e:
        _logger.exception("provider_status failed")
        return _vin_json_error(f"unexpected error: {e}", 500)


@app.get("/api/diagnostics/vin")
def api_diagnostics_vin():
    try:
        return jsonify({"ok": True, **vin_mod.diagnostics()})
    except Exception as e:
        _logger.exception("vin diagnostics failed")
        return _vin_json_error(f"unexpected error: {e}", 500)


# Catch-all JSON error for /api/vin/* so nothing ever returns HTML
@app.errorhandler(404)
def _not_found(e):
    if (request.path or "").startswith("/api/"):
        return jsonify({"ok": False, "error": "not found",
                        "path": request.path}), 404
    return e

@app.errorhandler(405)
def _method_not_allowed(e):
    if (request.path or "").startswith("/api/"):
        return jsonify({"ok": False, "error": "method not allowed",
                        "path": request.path, "method": request.method}), 405
    return e


@app.post("/api/comps")
def api_comps():
    body = request.get_json(force=True) or {}
    try:
        return jsonify(comps_mod.comps_for_listing(
            year=int(body["year"]), make=body["make"], model=body["model"],
            odometer=int(body["miles"]) if body.get("miles") else None,
        ))
    except (KeyError, ValueError) as e:
        return jsonify({"error": f"need year/make/model: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/share_settings")
def api_share_settings_get():
    """Auth state + tunnel URL. Owner-only details are masked for external
    visitors so they don't see the share username or tunnel-target hints."""
    is_owner = _is_owner_request()
    tunnel_url = None
    if is_owner:
        try:
            tunnel_url = (ROOT / "ngrok-url.txt").read_text().strip() or None
        except Exception:
            pass
    return jsonify({
        "auth_enabled": bool(CONFIG.get("share_password")),
        "public":       bool(CONFIG.get("share_public")),
        "is_owner":     is_owner,
        # Owner-only fields below
        "username":     (CONFIG.get("share_username") or "sniper") if is_owner else None,
        "tunnel_url":   tunnel_url,
    })


@app.post("/api/share_settings")
def api_share_settings_post():
    body = request.get_json(force=True) or {}
    if "username" in body:
        u = str(body["username"]).strip() or "sniper"
        save_override("share_username", u)
        CONFIG["share_username"] = u
    if "password" in body:
        p = str(body["password"])
        # Empty/whitespace = "don't change" — never wipe a stored password
        # just because the user clicked Save without retyping it.
        if p.strip():
            save_override("share_password", p)
            CONFIG["share_password"] = p
    if "public" in body:
        pub = bool(body["public"])
        save_override("share_public", pub)
        CONFIG["share_public"] = pub
    _config.reload()
    return jsonify({"ok": True,
                    "auth_enabled": bool(CONFIG.get("share_password")),
                    "public": bool(CONFIG.get("share_public"))})


# _is_external_request was moved up next to the new auth gate — keep this
# stub so any downstream import doesn't break (none currently use it from
# outside server.py, but defensive).


# ---- Per-IP rate limit (in-memory token bucket, 60-second window) ----
# Defense in depth: owner is never throttled, external visitors get a budget
# on the heavier endpoints so a runaway client can't hammer the box.
_RATE: dict = {}
_RATE_LOCK = threading.Lock()


def _rate_ok(key: str, max_per_min: int) -> bool:
    now = time.time()
    with _RATE_LOCK:
        start, n = _RATE.get(key, (now, 0))
        if now - start > 60:
            start, n = now, 0
        n += 1
        _RATE[key] = (start, n)
        return n <= max_per_min


def _rl_or_fail(name: str, max_per_min: int):
    """Returns a 429 response if the caller is over budget, else None. Owner
    requests are not rate-limited (it's just you on your own Mac)."""
    if _is_owner_request():
        return None
    key = f"{name}:{(request.remote_addr or 'unknown')}"
    if not _rate_ok(key, max_per_min):
        _logger.info(f"RATE_LIMIT name={name} ip={request.remote_addr}")
        return (jsonify({"error": f"rate limited — max {max_per_min}/min"}),
                429)
    return None


@app.get("/api/notify_settings")
def api_notify_settings_get():
    # The owner's phone number is masked for anyone reaching the dashboard
    # through the public tunnel — only a direct local request sees it in full.
    phone = CONFIG.get("notify_phone", "") or ""
    if _is_external_request() and len(phone) >= 4:
        phone = "•••••" + phone[-4:]
    return jsonify({
        "phone": phone,
        "enabled": bool(CONFIG.get("notify_text_enabled", False)),
        "min_score": int(CONFIG.get("notify_min_score", 30)),
        "quiet_start_hour": CONFIG.get("quiet_start_hour"),
        "quiet_end_hour":   CONFIG.get("quiet_end_hour"),
    })


@app.post("/api/notify_settings")
def api_notify_settings_post():
    body = request.get_json(force=True) or {}
    if "phone" in body:
        raw = str(body["phone"]).strip()
        # Reject empty (would wipe number) and masked echo-backs (•••••1234)
        # that the UI shows external visitors. Only real phone digits update.
        if raw and "•" not in raw and "*" not in raw and any(c.isdigit() for c in raw):
            save_override("notify_phone", raw)
            CONFIG["notify_phone"] = raw
    if "enabled" in body:
        v = bool(body["enabled"])
        save_override("notify_text_enabled", v)
        CONFIG["notify_text_enabled"] = v
    if "min_score" in body:
        try:
            v = max(1, min(500, int(body["min_score"])))
            save_override("notify_min_score", v)
            CONFIG["notify_min_score"] = v
        except (ValueError, TypeError):
            pass
    for k in ("quiet_start_hour", "quiet_end_hour"):
        if k in body:
            v = body[k]
            if v is None or v == "" or str(v).lower() == "null":
                save_override(k, None)
                CONFIG[k] = None
            else:
                try:
                    v = max(0, min(23, int(v)))
                    save_override(k, v)
                    CONFIG[k] = v
                except (ValueError, TypeError):
                    pass
    _config.reload()
    return jsonify({"ok": True})


@app.post("/api/notify_test")
def api_notify_test():
    """Send a test text to the configured phone."""
    rl = _rl_or_fail("notify_test", 3)
    if rl: return rl
    from notifications import send_imessage
    phone = CONFIG.get("notify_phone")
    if not phone:
        return jsonify({"ok": False, "error": "no phone configured"}), 400
    ok, detail = send_imessage(phone,
        "🎯 SNIPER test — text alerts are wired up. Real deals will arrive here.")
    return jsonify({"ok": ok, "detail": detail, "phone": phone}), (200 if ok else 500)


@app.post("/api/import")
def api_import():
    """Called by the browser extension to push a Marketplace listing."""
    rl = _rl_or_fail("import", 60)
    if rl: return rl
    body = request.get_json(force=True) or {}
    try:
        from sources.marketplace_import import push
        rid = push(body)
        if not rid:
            return jsonify({"error": "missing id/url"}), 400
        return jsonify({"stored": rid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/import_email")
def api_import_email():
    """Parse a pasted saved-search email body and import any listings found.
    Supports Facebook Marketplace, Nextdoor, OfferUp, eBay digest emails."""
    body = request.get_json(force=True) or {}
    text = body.get("text") or ""
    platform_hint = body.get("platform")  # optional override
    if not text:
        return jsonify({"error": "empty"}), 400
    try:
        from sources.marketplace_import import parse_email_digest
        listings = parse_email_digest(text, platform_hint)
        return jsonify({"imported": len(listings), "listings": listings})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===========================================================================
# SaaS Phase 1 — auth, accounts, per-user data isolation, saved searches,
# admin source-health. Everything below was added on top of the original
# personal-tool routes; it does NOT mutate the existing handlers — instead
# the old /api/save{,d,/status,/note} and /api/poll{,-all} are SHADOWED by
# the user-scoped + admin-only versions registered after them. (Flask uses
# the FIRST registration for any (rule, endpoint) pair, so we register the
# new ones with distinct endpoint names and disable the old ones via wrap.)
# ===========================================================================

# ---- Pages: login.html ---------------------------------------------------

@app.get("/login")
def login_page():
    page = ROOT / "login.html"
    if not page.is_file():
        return ("Login page missing.", 500)
    resp = send_from_directory(ROOT, "login.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---- Stripe Checkout return landings ------------------------------------
#
# Stripe redirects the user back here after Hosted Checkout. These routes
# do NOT grant any entitlement — they just forward to the dashboard with
# a `billing_return` flag so the JS knows to show a pending state and
# poll /api/billing/me until the verified webhook flips the user's plan.
# Unauthenticated visitors land on /login (the dashboard is private).

def _billing_return_redirect(outcome: str):
    """Forward to / with safe, allowlisted query params only."""
    sid = (request.args.get("session") or "").strip()
    safe_sid = ""
    if sid and sid.startswith("cs_") and len(sid) < 200 \
            and all(c.isalnum() or c == "_" for c in sid):
        safe_sid = f"&session={sid}"
    target = f"/?billing_return={outcome}{safe_sid}"
    return redirect(target, code=302)


@app.get("/billing/success")
def billing_success_return():
    if not auth.current_user():
        return redirect("/login", code=302)
    return _billing_return_redirect("success")


@app.get("/billing/cancel")
def billing_cancel_return():
    if not auth.current_user():
        return redirect("/login", code=302)
    return _billing_return_redirect("cancel")


# ---- Auth endpoints ------------------------------------------------------

@app.post("/api/auth/register")
def api_register():
    """Create an account. Open registration unless ALLOW_REGISTRATION=0.
    New users default to plan='free', role='user'."""
    if not auth.ALLOW_REGISTRATION:
        return jsonify({"error": "registration disabled"}), 403
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    try:
        user = auth.create_user(email, password)
    except ValueError as e:
        # Includes invalid email, weak password, duplicate email — message
        # is safe to surface (no DB internals leak through).
        return jsonify({"error": str(e)}), 400
    token = auth.create_session(user)
    resp = make_response(jsonify({"ok": True, "user": user.public_dict()}))
    resp.set_cookie(auth.SESSION_COOKIE, token, **auth._cookie_kwargs())
    return resp


@app.post("/api/auth/login")
def api_login():
    """Email + password -> session cookie. Generic error on bad creds so
    valid emails can't be enumerated. Rate-limited per IP."""
    if auth.login_throttled():
        return jsonify({"error": "too many attempts, try again in a few minutes"}), 429
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    user = auth.get_user_by_email(email)
    pw_hash = None
    if user:
        # Re-fetch the password_hash from DB — auth.User intentionally doesn't
        # carry the hash so it can't leak through public_dict().
        with _db.transaction() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT password_hash FROM users WHERE id={_db.placeholder()}",
                (user.id,))
            row = cur.fetchone()
            pw_hash = (row["password_hash"] if row else None)
    if not (user and pw_hash and auth.verify_password(password, pw_hash)):
        auth.record_login_failure()
        return jsonify({"error": "invalid email or password"}), 401
    auth.clear_login_failures()
    token = auth.create_session(user)
    resp = make_response(jsonify({"ok": True, "user": user.public_dict()}))
    resp.set_cookie(auth.SESSION_COOKIE, token, **auth._cookie_kwargs())
    return resp


@app.post("/api/auth/logout")
def api_logout():
    token = request.cookies.get(auth.SESSION_COOKIE)
    auth.destroy_session(token)
    resp = make_response(jsonify({"ok": True}))
    resp.set_cookie(auth.SESSION_COOKIE, "", expires=0, path="/")
    return resp


@app.get("/api/me")
def api_me():
    """Frontend hits this on load. Returns the current user (or 401 anon)
    so the dashboard can either render the app or redirect to /login."""
    u = auth.current_user()
    if not u:
        return jsonify({"error": "not authenticated"}), 401
    return jsonify({"user": u.public_dict()})


# ===========================================================================
# Stripe billing — Phase 2A (TEST MODE only)
# ===========================================================================
import os
import billing as _billing


@app.post("/api/billing/checkout")
@auth.login_required
def api_billing_checkout():
    """Create a Stripe Checkout session for the *currently authenticated*
    user. The browser sends only a plan NAME; the price ID is looked up
    server-side from an env-var allowlist (STRIPE_PRICE_STARTER /
    STRIPE_PRICE_PRO). Any user_id / plan / price_id in the request body
    is ignored.

    Returns {url} for the hosted Checkout page. Plan provisioning
    happens later in /api/billing/webhook — this endpoint never grants
    entitlement on its own."""
    u = auth.current_user()
    body = request.get_json(silent=True) or {}
    plan = (body.get("plan") or "").strip().lower()

    allowlist = _billing.plan_to_price()
    if plan not in allowlist:
        # Includes the case where the env isn't configured — same response
        # so a probing client can't tell config state from a bad plan.
        return jsonify({"error": "unknown plan",
                        "available": sorted(allowlist.keys())}), 400

    # Refuse to start a second active subscription. The Stripe Customer
    # Portal is the right place to switch plans; opening a parallel
    # subscription would double-bill.
    with _db.transaction() as conn:
        existing = _billing.active_subscription_for(conn, u.id)
    if existing:
        return jsonify({
            "error": "already subscribed",
            "current_plan": existing["plan"],
            "current_status": existing["status"],
        }), 409

    origin = (os.environ.get("BILLING_ORIGIN") or "").rstrip("/")
    if not origin:
        # Fall back to the request origin so local dev works without env.
        origin = request.host_url.rstrip("/")

    try:
        session = _billing.stripe_client().checkout.Session.create(
            mode="subscription",
            line_items=[{"price": allowlist[plan], "quantity": 1}],
            customer_email=u.email,
            # client_reference_id is read back on checkout.session.completed
            # so we know which local user finished checkout.
            client_reference_id=str(u.id),
            # metadata is propagated onto the resulting Subscription —
            # subscription.updated / .deleted events for THIS sub will
            # also carry user_id, so we don't need a Stripe Customer lookup.
            metadata={"user_id": str(u.id), "plan": plan},
            subscription_data={"metadata": {"user_id": str(u.id), "plan": plan}},
            success_url=f"{origin}/billing/success?session={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/billing/cancel",
            allow_promotion_codes=True,
        )
    except RuntimeError as e:
        # Missing STRIPE_SECRET_KEY etc. — 500 with safe message.
        _logger.error(f"checkout config error: {e}")
        return jsonify({"error": "billing not configured"}), 500
    except Exception as e:
        _logger.exception("Stripe Checkout.Session.create failed")
        return jsonify({"error": f"stripe error: {e}"}), 502

    return jsonify({"url": session.get("url"),
                    "id":  session.get("id")})


@app.post("/api/billing/webhook")
def api_billing_webhook():
    """Stripe webhook receiver. Must run BEFORE any auth gate (Stripe is
    not a session). The raw request body is signature-verified against
    STRIPE_WEBHOOK_SECRET; anything else is rejected with 400 so Stripe
    retries with the same event id.

    Idempotency: stripe_webhook_events.stripe_event_id is the PK. A
    duplicate insert → return 200 without re-processing."""
    payload = request.get_data()  # raw bytes — required for signature verify
    sig = request.headers.get("Stripe-Signature", "")
    if not sig:
        return jsonify({"error": "missing signature"}), 400

    # Narrow exception handling: a signature failure is the only thing that
    # earns "invalid signature". Malformed JSON is a separate 400. A missing
    # secret is a 500 (server's fault). Anything else propagates and is
    # logged as UNCAUGHT by the global error handler so we don't silently
    # call genuine bugs "tampering".
    import stripe as _stripe_sdk
    try:
        event = _billing.verify_webhook(payload, sig)
    except _stripe_sdk.SignatureVerificationError as e:
        _logger.warning(f"webhook signature rejected: {e}")
        return jsonify({"error": "invalid signature"}), 400
    except ValueError as e:
        # Body is signed but isn't valid JSON. Stripe would never send this,
        # so it's either a misconfigured forwarder or someone replaying a
        # corrupted payload — 400 with a distinct message.
        _logger.warning(f"webhook payload malformed: {e}")
        return jsonify({"error": "invalid payload"}), 400
    except RuntimeError as e:
        _logger.error(f"webhook config error: {e}")
        return jsonify({"error": "billing not configured"}), 500

    # Idempotency + dispatch happen in a single transaction so a crash
    # mid-handler doesn't leave an "already-processed" mark without the
    # effects.
    with _db.transaction() as conn:
        is_new = _billing.record_event_for_processing(conn, event)
        if not is_new:
            return jsonify({"ok": True, "duplicate": True}), 200
        try:
            outcome = _billing.dispatch_event(conn, event)
        except Exception as e:
            _logger.exception(f"webhook handler crashed for {event.get('id')}")
            # Return 500 so Stripe retries (it backs off with jitter).
            return jsonify({"error": f"handler failure: {e}"}), 500
        _billing.mark_event_processed(conn, event["id"])

    return jsonify({"ok": True, "outcome": outcome,
                    "event": event["id"], "type": event["type"]}), 200


def _billing_available() -> bool:
    """True when the server is configured to start Stripe Checkout.
    Booleanized for the dashboard — never leak which env var is missing
    or which Price IDs aren't set. Used by the UI to disable upgrade
    actions with a calm message instead of showing a raw error."""
    try:
        return bool(os.environ.get("STRIPE_SECRET_KEY")) \
               and bool(_billing.plan_to_price())
    except Exception:
        return False


@app.get("/api/billing/me")
@auth.login_required
def api_billing_me():
    """Read the current user's subscription state. Purely a DB read —
    never a live Stripe API call (those happen via the webhook flow
    only). Returns null subscription for free / never-subscribed users.

    Also returns a boolean `billing_available` flag the dashboard uses
    to decide whether to enable upgrade buttons. The flag exposes only
    yes/no — never which piece of configuration is missing."""
    u = auth.current_user()
    with _db.transaction() as conn:
        sub = _billing.active_subscription_for(conn, u.id)
    return jsonify({
        "user": u.public_dict(),
        "subscription": sub,
        "billing_available": _billing_available(),
    })


@app.post("/api/billing/portal")
@auth.login_required
def api_billing_portal():
    """Create a Stripe Billing Portal session for the current user.

    Security stance:
      * The Stripe customer ID comes ONLY from the local subscription
        row owned by the session user. The client cannot supply or
        influence it.
      * The return URL is built ONLY from the BILLING_ORIGIN env var
        (with a request-host fallback for local dev). The client cannot
        supply or override it. This blocks open-redirect attacks via the
        portal's `return_url`.
      * Users who never started a subscription get a clear 404 with a
        machine-readable error code, never a 500.
    """
    u = auth.current_user()
    with _db.transaction() as conn:
        # Pull any subscription row for this user (active or not) so users
        # who canceled can still manage payment methods / view invoices.
        cur = conn.cursor()
        ph = _db.placeholder()
        cur.execute(
            f"SELECT stripe_customer_id FROM subscriptions "
            f"WHERE user_id = {ph} AND stripe_customer_id IS NOT NULL "
            f"  AND stripe_customer_id != '' "
            f"ORDER BY updated_at DESC LIMIT 1",
            (u.id,))
        row = cur.fetchone()
    customer_id = (row["stripe_customer_id"] if row else None)
    if not customer_id:
        return jsonify({"error": "no_stripe_customer",
                        "message": "Subscribe before opening the billing portal."}), 404

    origin = (os.environ.get("BILLING_ORIGIN") or "").rstrip("/") \
             or request.host_url.rstrip("/")
    return_url = f"{origin}/"

    try:
        session = _billing.stripe_client().billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
    except RuntimeError as e:
        _logger.error(f"billing portal config error: {e}")
        return jsonify({"error": "billing not configured"}), 500
    except Exception as e:
        # Stripe will sometimes 400 if the portal isn't configured for the
        # test mode account. Surface that to the caller without leaking
        # implementation details.
        _logger.exception("Stripe billing_portal.Session.create failed")
        return jsonify({"error": "stripe_portal_unavailable",
                        "detail": f"{type(e).__name__}"}), 502

    return jsonify({"url": session.get("url")})


# ---- Admin billing health + reconciliation trigger ----------------------

@app.get("/api/admin/billing/health")
@auth.admin_required
def api_admin_billing_health():
    """Single pane of glass for billing health. Reads the last reconciliation
    run, unresolved anomaly counts, and subscription-status distribution."""
    with _db.transaction() as conn:
        out = _billing.billing_health(conn)
    return jsonify(out)


# NOTE — Reconciliation is intentionally NOT exposed as a public HTTP
# route. It runs as an internal command:
#
#     python3 -m jobs.reconcile_billing
#
# A systemd timer (see DEPLOY.md) calls that command directly on the
# server host. There is no internet-facing scheduled billing action.
# Operators inspect run results via the read-only GET
# /api/admin/billing/health endpoint above, which now includes the
# reconcile_job_lock state.


# ---- View-function REPLACEMENT (the safe upgrade primitive) ------------
#
# Earlier this file tried to *drop* legacy routes from app.url_map so it
# could re-register user-scoped versions under the same URL. Werkzeug
# doesn't support removing rules cleanly — its compiled routing tree keeps
# pointing at the old endpoint name, while app.view_functions had been
# cleared, producing KeyError -> 500 at request time.
#
# Correct fix: keep the URL rule + endpoint name intact, but swap the
# function bound to that endpoint. Flask's dispatcher calls
# app.view_functions[rule.endpoint](...) — replacing that mapping is the
# canonical way to upgrade a route's behavior in place.

def _replace_view(endpoint: str, new_handler) -> None:
    """Bind `new_handler` to an already-registered endpoint name. No-op if
    the endpoint was never registered (defensive)."""
    if endpoint in app.view_functions:
        app.view_functions[endpoint] = new_handler


# ---- Central policy gates ----------------------------------------------

_LOGIN_REQUIRED_READ_PREFIXES = (
    "/api/alerts", "/api/fresh_listings", "/api/near_misses",
    "/api/listings", "/api/stats", "/api/diagnostics",
    "/api/sources", "/api/facets", "/api/score_progress",
    "/api/saved", "/api/saved_searches", "/api/me",
)

_PLAN_REQUIRED = {
    # Comp + VIN lookups consume third-party API quota → 'starter'+
    "/api/comps":            "starter",
    "/api/vin":              "pro",
    "/api/vin/check":        "pro",
    "/api/vin/decode":       "pro",
    "/api/vin/report":       "pro",
    "/api/vin/provider-status": "pro",
    "/api/diagnostics/vin":  "pro",
}


@app.before_request
def _enforce_route_policy():
    """Central policy enforcement for routes that DON'T have inline
    decorators. Auth-required is the floor for every /api/* path that
    isn't explicitly in _PUBLIC_PATHS. Plan gating layered on top.

    Per-route role/plan decorators (e.g. @auth.admin_required on the
    settings handlers below) handle finer-grained checks; they run AFTER
    this gate so they can assume current_user() is non-None."""
    p = request.path or ""
    if p in _PUBLIC_PATHS or p == "/health":
        return None
    if p == "/" or p == "/login":
        return None
    if p.startswith("/api/"):
        if not auth.current_user():
            return jsonify({"error": "authentication required"}), 401
        for prefix, min_plan in _PLAN_REQUIRED.items():
            if p == prefix or p.startswith(prefix + "/"):
                if not auth.current_user().has_plan(min_plan):
                    return jsonify({"error": "upgrade required",
                                    "current_plan": auth.current_user().plan,
                                    "required_plan": min_plan}), 402
                break
    return None


# ---- Per-user saved deals (replaces the legacy global handlers) --------

_PIPELINE_STATUSES_V2 = {
    "saved", "contacted", "vin_needed", "negotiating",
    "going_to_see", "bought", "passed",
}

_PH = _db.placeholder


# Each of these is a PLAIN function (no @app.route — no second rule). We
# install them onto the legacy endpoint names via _replace_view at the
# bottom of this section. Decorators (login_required / plan_required /
# admin_required) wrap the function before installation, so the auth check
# runs first and a 401/402/403 is returned without touching the DB.

@auth.login_required
def api_saved_v2():
    u = auth.current_user()
    with _db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT s.saved_at, s.note AS user_note, s.status, s.updated_at,
                   l.composite_id, l.url, l.title, l.price, l.year, l.make, l.model,
                   l.odometer, l.source, l.state, l.posted_at, l.first_seen_at,
                   l.is_auction, l.is_cash_only, l.scam_score, l.scam_reasons, l.image_urls,
                   nm.discount_pct, nm.comp_avg, nm.score, nm.comp_n, nm.basis
            FROM saved_v2 s
            JOIN listings l ON l.composite_id = s.composite_id
            LEFT JOIN near_misses nm ON nm.composite_id = s.composite_id
            WHERE s.user_id = {_PH()}
            ORDER BY s.saved_at DESC""", (u.id,))
        rows = [dict(r) for r in cur.fetchall()]
    fees = 1500
    for r in rows:
        if r.get("comp_avg") and r.get("price"):
            r["est_profit"] = int(r["comp_avg"] - r["price"] - fees)
    return jsonify(rows)


@auth.plan_required("starter")
def api_save_v2():
    u = auth.current_user()
    body = request.get_json(silent=True) or {}
    cid = body.get("composite_id")
    note = body.get("note", "")
    status = (body.get("status") or "saved").strip().lower()
    if status not in _PIPELINE_STATUSES_V2:
        status = "saved"
    if not cid:
        return jsonify({"error": "composite_id required"}), 400
    now = _db.now_utc_iso()
    with _db.transaction() as conn:
        cur = conn.cursor()
        if _db.IS_PG:
            cur.execute(
                "INSERT INTO saved_v2(user_id, composite_id, note, saved_at, status, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(user_id, composite_id) DO UPDATE SET "
                "  note=excluded.note, status=excluded.status, updated_at=excluded.updated_at",
                (u.id, cid, note, now, status, now))
        else:
            cur.execute(
                "INSERT INTO saved_v2(user_id, composite_id, note, saved_at, status, updated_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(user_id, composite_id) DO UPDATE SET "
                "  note=excluded.note, status=excluded.status, updated_at=excluded.updated_at",
                (u.id, cid, note, now, status, now))
    return jsonify({"ok": True, "composite_id": cid, "status": status})


@auth.plan_required("starter")
def api_unsave_v2():
    u = auth.current_user()
    body = request.get_json(silent=True) or {}
    cid = body.get("composite_id")
    if not cid:
        return jsonify({"error": "composite_id required"}), 400
    with _db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM saved_v2 WHERE user_id={_PH()} AND composite_id={_PH()}",
                    (u.id, cid))
    return jsonify({"ok": True, "unsaved": cid})


@auth.plan_required("starter")
def api_saved_status_v2():
    u = auth.current_user()
    body = request.get_json(silent=True) or {}
    cid = body.get("composite_id")
    status = (body.get("status") or "").strip().lower()
    if not cid:
        return jsonify({"error": "composite_id required"}), 400
    if status not in _PIPELINE_STATUSES_V2:
        return jsonify({"error": f"status must be one of {sorted(_PIPELINE_STATUSES_V2)}"}), 400
    now = _db.now_utc_iso()
    with _db.transaction() as conn:
        cur = conn.cursor()
        if _db.IS_PG:
            cur.execute(
                "INSERT INTO saved_v2(user_id, composite_id, note, saved_at, status, updated_at) "
                "VALUES (%s,%s,'',%s,%s,%s) "
                "ON CONFLICT(user_id, composite_id) DO UPDATE SET "
                "  status=excluded.status, updated_at=excluded.updated_at",
                (u.id, cid, now, status, now))
        else:
            cur.execute(
                "INSERT INTO saved_v2(user_id, composite_id, note, saved_at, status, updated_at) "
                "VALUES (?,?,'',?,?,?) "
                "ON CONFLICT(user_id, composite_id) DO UPDATE SET "
                "  status=excluded.status, updated_at=excluded.updated_at",
                (u.id, cid, now, status, now))
    return jsonify({"ok": True, "composite_id": cid, "status": status})


@auth.plan_required("starter")
def api_saved_note_v2():
    u = auth.current_user()
    body = request.get_json(silent=True) or {}
    cid = body.get("composite_id")
    if not cid:
        return jsonify({"error": "composite_id required"}), 400
    note = str(body.get("note") or "")
    if len(note) > 4000:
        return jsonify({"error": "note too long (max 4000 chars)"}), 400
    now = _db.now_utc_iso()
    with _db.transaction() as conn:
        cur = conn.cursor()
        if _db.IS_PG:
            cur.execute(
                "INSERT INTO saved_v2(user_id, composite_id, note, saved_at, status, updated_at) "
                "VALUES (%s,%s,%s,%s,'saved',%s) "
                "ON CONFLICT(user_id, composite_id) DO UPDATE SET "
                "  note=excluded.note, updated_at=excluded.updated_at",
                (u.id, cid, note, now, now))
        else:
            cur.execute(
                "INSERT INTO saved_v2(user_id, composite_id, note, saved_at, status, updated_at) "
                "VALUES (?,?,?,?,'saved',?) "
                "ON CONFLICT(user_id, composite_id) DO UPDATE SET "
                "  note=excluded.note, updated_at=excluded.updated_at",
                (u.id, cid, note, now, now))
    return jsonify({"ok": True, "composite_id": cid})


# ---- Saved searches -----------------------------------------------------

@app.get("/api/saved_searches")
@auth.login_required
def api_saved_searches_list():
    u = auth.current_user()
    with _db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, name, filter_json, notify_enabled, created_at "
            f"FROM saved_searches WHERE user_id={_PH()} ORDER BY created_at DESC",
            (u.id,))
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            try:
                d["filter"] = json.loads(d.pop("filter_json"))
            except Exception:
                d["filter"] = {}
            d["notify_enabled"] = bool(d.get("notify_enabled"))
            rows.append(d)
    return jsonify(rows)


@app.post("/api/saved_searches")
@auth.plan_required("starter")
def api_saved_searches_create():
    u = auth.current_user()
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()[:120]
    if not name:
        return jsonify({"error": "name required"}), 400
    filter_obj = body.get("filter") or {}
    if not isinstance(filter_obj, dict):
        return jsonify({"error": "filter must be an object"}), 400
    notify = 1 if body.get("notify_enabled") else 0
    with _db.transaction() as conn:
        cur = conn.cursor()
        if _db.IS_PG:
            cur.execute(
                "INSERT INTO saved_searches(user_id, name, filter_json, notify_enabled) "
                "VALUES (%s,%s,%s,%s) RETURNING id",
                (u.id, name, json.dumps(filter_obj), bool(notify)))
            new_id = cur.fetchone()["id"]
        else:
            cur.execute(
                "INSERT INTO saved_searches(user_id, name, filter_json, notify_enabled) "
                "VALUES (?,?,?,?)",
                (u.id, name, json.dumps(filter_obj), notify))
            new_id = cur.lastrowid
    return jsonify({"ok": True, "id": int(new_id)})


@app.delete("/api/saved_searches/<int:sid>")
@auth.plan_required("starter")
def api_saved_searches_delete(sid: int):
    u = auth.current_user()
    with _db.transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM saved_searches WHERE id={_PH()} AND user_id={_PH()}",
            (int(sid), u.id))
    return jsonify({"ok": True, "deleted": int(sid)})


# ---- Admin: source health + manual poll trigger -------------------------

@app.get("/api/admin/sources/health")
@auth.admin_required
def api_admin_sources_health():
    """Per-source health + scheduler heartbeat for the admin dashboard.

    Reads source_health and scheduler_state (both written by the
    out-of-process scheduler), plus the current `billing_job_locks` row
    for `job_name='scheduler'` so operators can see who's holding the
    scheduler lease and when it expires."""
    rows: list[dict] = []
    enabled_ids = {s.SOURCE_ID for s in sources.iter_sources(CONFIG)}
    import joblock as _joblock
    sched_lock = _joblock.current_holder("scheduler") or {}
    with _db.transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM source_health")
        persisted = {r["source_id"]: dict(r) for r in cur.fetchall()}
        cur.execute("SELECT * FROM scheduler_state WHERE id=1")
        sched_row = cur.fetchone()
        sched = dict(sched_row) if sched_row else {}
    for s in sources.all_sources():
        p = persisted.get(s.SOURCE_ID, {})
        live = sniper.SOURCE_DIAG.get(s.SOURCE_ID, {}) or {}
        rows.append({
            "id": s.SOURCE_ID,
            "name": s.SOURCE_NAME,
            "enabled": s.SOURCE_ID in enabled_ids,
            "last_attempt_at": live.get("polled_at") or p.get("last_attempt_at"),
            "last_success_at": p.get("last_success_at"),
            "last_error":      live.get("error") if live.get("error") else p.get("last_error"),
            "last_count":      live.get("count") if live.get("count") is not None else p.get("last_count"),
            "last_elapsed_s":  live.get("elapsed_s") if live.get("elapsed_s") is not None else p.get("last_elapsed_s"),
            "listings_total":  p.get("listings_total"),
        })
    return jsonify({
        "sources": rows,
        "scheduler": {
            "holder":           sched.get("holder") or sched_lock.get("holder"),
            "lock_expires_at":  sched_lock.get("expires_at"),
            "lock_acquired_at": sched_lock.get("acquired_at"),
            "last_tick_at":     sched.get("last_tick_at"),
            "last_tick_secs":   sched.get("last_tick_secs"),
            "last_success_at":  sched.get("last_success_at"),
            "last_error":       sched.get("last_error"),
            "last_error_at":    sched.get("last_error_at"),
            "tick_counter":     sched.get("tick_counter"),
            "enabled_sources":  (sched.get("enabled_sources") or "").split(",")
                                if sched.get("enabled_sources") else [],
        },
        "scorer": sniper.get_scorer_stats(),
        "db": _db.describe(),
    })


# NOTE — Phase 2C.1 removed `POST /api/admin/poll`.
#
# Scraping is owned exclusively by the out-of-process scheduler service:
#
#     python3 -m jobs.scheduler
#
# (See DEPLOY.md for the systemd unit.) Web workers must NEVER call
# sniper.poll_once — otherwise N gunicorn workers can race the scheduler
# and the scrape source, fan out duplicate writes, and burn source quota.
# Operators trigger an ad-hoc tick by SSH'ing to the host and running
# `python3 -m jobs.scheduler --once` while the long-running scheduler is
# stopped (the job lock prevents concurrent runs).


# ---- Admin: shadow legacy settings/share/notify/import endpoints --------

@auth.admin_required
def _settings_get_v2():
    return jsonify({
        "zip": CONFIG["zip"],
        "radius_mi": CONFIG["radius_mi"],
        "max_listing_age_min": CONFIG.get("max_listing_age_min", 0),
        "closing_soon_hours":  CONFIG.get("closing_soon_hours", 24),
        "min_price": CONFIG.get("min_price"),
        "max_price": CONFIG.get("max_price"),
        "craigslist_regions": CONFIG["sources"]["craigslist"]["regions"],
        "govdeals_states":    CONFIG["sources"]["govdeals"]["states"],
        "deal_threshold_pct": CONFIG["deal_threshold_pct"],
        "comp_match_threshold_pct": CONFIG.get("comp_match_threshold_pct", 100),
        "bumper_api_key_set":   bool(CONFIG.get("bumper_api_key")),
        "clearvin_api_key_set": bool(CONFIG.get("clearvin_api_key")),
    })


@auth.admin_required
def _settings_post_v2():
    body = request.get_json(silent=True) or {}
    saved = {}

    def _save(key, transform=lambda x: x, validate=lambda x: True):
        if key in body and body[key] is not None and body[key] != "":
            try:
                v = transform(body[key])
                if validate(v):
                    save_override(key, v)
                    saved[key] = v
            except (ValueError, TypeError):
                pass

    _save("zip", str, lambda v: len(v) == 5 and v.isdigit())
    _save("radius_mi", int, lambda v: 1 <= v <= 1000)
    _save("max_listing_age_min", int, lambda v: 0 <= v <= 100000)
    _save("closing_soon_hours", int, lambda v: 1 <= v <= 720)
    _save("min_price", int, lambda v: 0 <= v <= 1000000)
    _save("max_price", int, lambda v: 0 <= v <= 10000000)
    _save("deal_threshold_pct", int, lambda v: 1 <= v <= 80)
    _save("comp_match_threshold_pct", int, lambda v: 50 <= v <= 100)

    if "craigslist_regions" in body:
        regions = [s.strip() for s in str(body["craigslist_regions"]).replace(",", " ").split() if s.strip()]
        if regions:
            save_override("craigslist_regions", regions)
            saved["craigslist_regions"] = regions
    if "govdeals_states" in body:
        states = [s.strip().upper()[:2] for s in str(body["govdeals_states"]).replace(",", " ").split() if s.strip()]
        if states:
            save_override("govdeals_states", states)
            saved["govdeals_states"] = states

    for k in ("bumper_api_key", "clearvin_api_key"):
        if k in body:
            save_override(k, str(body[k] or ""))
            saved[k + "_set"] = bool(body[k])

    _config.reload()
    return jsonify({"saved": saved})


@auth.admin_required
def _threshold_v2():
    body = request.get_json(silent=True) or {}
    try:
        pct = max(1, min(80, int(body.get("pct"))))
    except (TypeError, ValueError):
        return jsonify({"error": "pct must be 1-80"}), 400
    CONFIG["deal_threshold_pct"] = pct
    save_override("deal_threshold_pct", pct)
    return jsonify({"deal_threshold_pct": pct})


@auth.login_required
def _share_get_v2():
    """Used by the dashboard header to show the public tunnel URL. Only
    admins see the full URL + username; regular users see whether sharing
    is enabled but not the address."""
    u = auth.current_user()
    is_admin = u.is_admin
    tunnel_url = None
    if is_admin:
        try:
            tunnel_url = (ROOT / "ngrok-url.txt").read_text().strip() or None
        except Exception:
            pass
    return jsonify({
        "auth_enabled": True,  # always true now — accounts required
        "public":       False, # legacy field; no public-share mode in SaaS build
        "is_owner":     is_admin,
        "username":     u.email if is_admin else None,
        "tunnel_url":   tunnel_url,
    })


@auth.admin_required
def _share_post_v2():
    # Legacy share-password mode is intentionally retired in the SaaS build.
    # We keep the endpoint so the old dashboard doesn't break, but it now
    # only echoes back state — accounts are the access mechanism.
    return jsonify({"ok": True, "deprecated": True,
                    "message": "Use user accounts; share_password is retired."})


@auth.admin_required
def _notify_get_v2():
    return jsonify({
        "phone": CONFIG.get("notify_phone", "") or "",
        "enabled": bool(CONFIG.get("notify_text_enabled", False)),
        "min_score": int(CONFIG.get("notify_min_score", 30)),
        "quiet_start_hour": CONFIG.get("quiet_start_hour"),
        "quiet_end_hour":   CONFIG.get("quiet_end_hour"),
    })


@auth.admin_required
def _notify_post_v2():
    body = request.get_json(silent=True) or {}
    if "phone" in body:
        raw = str(body["phone"]).strip()
        if raw and any(c.isdigit() for c in raw):
            save_override("notify_phone", raw)
            CONFIG["notify_phone"] = raw
    if "enabled" in body:
        v = bool(body["enabled"])
        save_override("notify_text_enabled", v)
        CONFIG["notify_text_enabled"] = v
    if "min_score" in body:
        try:
            v = max(1, min(500, int(body["min_score"])))
            save_override("notify_min_score", v)
            CONFIG["notify_min_score"] = v
        except (ValueError, TypeError):
            pass
    for k in ("quiet_start_hour", "quiet_end_hour"):
        if k in body:
            v = body[k]
            if v is None or v == "" or str(v).lower() == "null":
                save_override(k, None); CONFIG[k] = None
            else:
                try:
                    v = max(0, min(23, int(v)))
                    save_override(k, v); CONFIG[k] = v
                except (ValueError, TypeError):
                    pass
    _config.reload()
    return jsonify({"ok": True})


@auth.admin_required
def _notify_test_v2():
    from notifications import send_imessage
    phone = CONFIG.get("notify_phone")
    if not phone:
        return jsonify({"ok": False, "error": "no phone configured"}), 400
    ok, detail = send_imessage(phone,
        "SNIPER test alert — your number is wired up.")
    return jsonify({"ok": ok, "detail": detail, "phone": phone}), (200 if ok else 500)


@auth.admin_required
def _vin_provider_v2():
    body = request.get_json(silent=True) or {}
    bumper = (body.get("bumper_api_key") or "").strip()
    clearvin = (body.get("clearvin_api_key") or "").strip()
    if bumper:
        save_override("bumper_api_key", bumper); CONFIG["bumper_api_key"] = bumper
    if clearvin:
        save_override("clearvin_api_key", clearvin); CONFIG["clearvin_api_key"] = clearvin
    _config.reload()
    return jsonify({"ok": True})


@auth.admin_required
def _import_v2():
    """Browser-extension push — admin-only in the SaaS model. Subscribers
    cannot inject listings."""
    body = request.get_json(silent=True) or {}
    try:
        from sources.marketplace_import import push
        rid = push(body)
        if not rid:
            return jsonify({"error": "missing id/url"}), 400
        return jsonify({"stored": rid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth.admin_required
def _import_email_v2():
    body = request.get_json(silent=True) or {}
    text = body.get("text") or ""
    platform_hint = body.get("platform")
    if not text:
        return jsonify({"error": "empty"}), 400
    try:
        from sources.marketplace_import import parse_email_digest
        listings = parse_email_digest(text, platform_hint)
        return jsonify({"imported": len(listings), "listings": listings})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth.admin_required
def _debug_status_v2():
    routes = sorted({str(r.rule) for r in app.url_map.iter_rules()})
    db_ok, listings_n, deals_n, db_err = True, None, None, None
    try:
        with sniper.db() as conn:
            listings_n = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            deals_n    = conn.execute(
                "SELECT COUNT(*) FROM near_misses WHERE discount_pct>0").fetchone()[0]
    except Exception as e:
        db_ok = False
        db_err = f"{type(e).__name__}: {e}"
    return jsonify({
        "server_alive": True,
        "routes_registered": routes,
        "scanners_registered": [s.SOURCE_ID for s in sources.all_sources()],
        "enabled_sources": [s.SOURCE_ID for s in sources.iter_sources(CONFIG)],
        "listings_count": listings_n,
        "deals_count":    deals_n,
        "database_ok":    db_ok,
        "database_error": db_err,
        "scorer":         sniper.get_scorer_stats(),
        "db":             _db.describe(),
    })


# ===========================================================================
# Install the upgraded handlers onto the legacy endpoint names. This is the
# ONLY place that swaps view functions; doing it after all definitions
# guarantees the legacy @app.post/get rules are present in app.view_functions
# (which is what _replace_view checks), so the swap actually takes effect.
# ===========================================================================

# --- User-scoped saved deals (replace global handlers) ---
_replace_view("api_saved",        api_saved_v2)
_replace_view("api_save",         api_save_v2)
_replace_view("api_unsave",       api_unsave_v2)
_replace_view("api_saved_status", api_saved_status_v2)
_replace_view("api_saved_note",   api_saved_note_v2)

# --- Admin-only settings + share + notify + import + debug ---
_replace_view("api_settings_get",       _settings_get_v2)
_replace_view("api_settings_post",      _settings_post_v2)
_replace_view("api_threshold",          _threshold_v2)
_replace_view("api_share_settings_get", _share_get_v2)
_replace_view("api_share_settings_post", _share_post_v2)
_replace_view("api_notify_settings_get", _notify_get_v2)
_replace_view("api_notify_settings_post", _notify_post_v2)
_replace_view("api_notify_test",        _notify_test_v2)
_replace_view("api_vin_provider",       _vin_provider_v2)
_replace_view("api_import",             _import_v2)
_replace_view("api_import_email",       _import_email_v2)
_replace_view("_debug_status",          _debug_status_v2)


# --- Legacy poll endpoints: GONE (subscribers must never trigger scrapes) ---
# Phase 1 product rule: data collection happens centrally via the scheduler,
# and the only manual trigger is /api/admin/poll (admin_required). The old
# /api/poll and /api/poll-all rules from the personal-tool era are kept on
# the URL map so the dashboard doesn't 500 on a stale fetch, but they
# return 404 — the URL is genuinely gone as a feature.

def _legacy_poll_gone():
    return jsonify({
        "error": "endpoint removed in Phase 1 SaaS build",
        "replacement": "/api/admin/poll (admin only)",
    }), 404

_replace_view("api_poll",     _legacy_poll_gone)
_replace_view("api_poll_all", _legacy_poll_gone)


if __name__ == "__main__":
    import socket, sys, threading
    BIND = "0.0.0.0"
    PORT = 8765

    # ----- Pre-flight: verify the port isn't already taken -----
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((BIND, PORT))
        sock.close()
    except OSError as e:
        print(f"⚠ STARTUP ERROR: port {PORT} already in use ({e}). "
              f"Run STOP.command first to kill the old instance.", file=sys.stderr)
        sys.exit(2)

    # ----- Startup banner — every relevant value visible -----
    print("="*60)
    print(f"  SNIPER backend starting")
    print(f"  bind          : {BIND}:{PORT}")
    print(f"  local URL     : http://127.0.0.1:{PORT}")
    print(f"  tunnel target : http://localhost:{PORT}  (use this in ssh -R)")
    print(f"  health probe  : http://127.0.0.1:{PORT}/health")
    if CONFIG.get('share_public'):
        _access_line = "PUBLIC — no login required (anyone with the link)"
    elif CONFIG.get('share_password'):
        _access_line = "PASSWORD — user=" + str(CONFIG.get('share_username', 'sniper'))
    else:
        _access_line = "OFF (local-only)"
    print(f"  external access: {_access_line}")
    print(f"  sources       : {[s.SOURCE_ID for s in sources.iter_sources(CONFIG)]}")
    print(f"  ROOT          : {ROOT}")
    _dash = (ROOT / "dashboard.html").is_file()
    print(f"  dashboard.html: {'present (' + str((ROOT/'dashboard.html').stat().st_size) + ' bytes)' if _dash else '*** MISSING ***'}")
    print(f"  routes        : {len(list(app.url_map.iter_rules()))} registered")
    print("="*60)

    # ----- Scorer backfill runs in a background thread -----
    # It does a DB read + starts worker threads; keeping it off the main
    # path guarantees / and /health are served the instant Flask binds.
    def _bg_backfill():
        try:
            n = sniper.backfill_unscored()
            print(f"  scorer queued : {n} unscored listings (background)", flush=True)
        except Exception as e:
            print(f"  scorer queued : SKIPPED (backfill failed: {e})", file=sys.stderr)
    threading.Thread(target=_bg_backfill, daemon=True, name="backfill").start()

    try:
        app.run(host=BIND, port=PORT, debug=False, threaded=True)
    except Exception as e:
        print(f"⚠ STARTUP ERROR: Flask failed to bind: {e}", file=sys.stderr)
        sys.exit(1)
