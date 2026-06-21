"""
tests/test_scheduler_isolation.py — Phase 2C.1 hardening tests.

Proves:
  * Importing the web app (server module + app factory) spawns ZERO
    background threads. Web workers never own polling/scheduler/scorer.
  * The internal CLI `python -m jobs.scheduler` is the only entry that
    acquires the scheduler lease and drives sniper.poll_once.
  * A second scheduler attempt fails to acquire the fresh lease and
    exits with code 2 (stand-by behavior, no work done).
  * A stale lease (older than --steal-after-seconds) gets stolen safely.
  * The removed `POST /api/admin/poll` route is gone.
  * Admin source-health is still admin-only AND now surfaces the
    scheduler holder + lease state.
  * Billing-unavailable UX:
      - /api/billing/me reports billing_available: false when STRIPE_*
        env isn't set, AND the dashboard HTML stays clean (no "unknown
        plan" / "available: []" strings reflected to end users).
      - Direct tampered POST /api/billing/checkout still returns 400
        regardless of UI state.
  * No regression in Phase 1 / Phase 2A / Phase 2B authz, webhook
    signature verification, or billing-core behavior.
"""
from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from tests.conftest import register, login, logout


# ---------- Web import is side-effect free ---------------------------

def test_importing_server_spawns_no_threads(client, monkeypatch):
    """The `client` fixture has already imported server once. Confirm
    that the resulting thread set contains no scheduler/scorer/backfill
    threads — only the main thread (and possibly pytest's own threads,
    which we ignore by name prefix)."""
    forbidden_names = {"scorer", "backfill", "daemon", "scheduler"}
    live = [t.name for t in threading.enumerate()]
    intruders = [n for n in live if n in forbidden_names
                 or any(n.startswith(p + "-") for p in forbidden_names)]
    assert not intruders, \
        f"Web import spawned forbidden background threads: {intruders}\n" \
        f"All live threads: {live}"


def test_server_module_has_no_polling_route(client, monkeypatch):
    """The removed /api/admin/poll endpoint must not be re-added by
    accident. Authenticated admin still gets 404/405; anonymous gets
    401/404. Either way, never 200."""
    register(client, "alice@example.com")
    import auth as _auth
    _auth.set_role(_auth.get_user_by_email("alice@example.com").id, "admin")
    logout(client); login(client, "alice@example.com")
    r = client.post("/api/admin/poll")
    assert r.status_code in (404, 405), \
        f"public scrape trigger is back: {r.status_code} {r.get_data(as_text=True)}"


# ---------- Scheduler CLI behavior -----------------------------------

def _run_scheduler(argv, capsys):
    """Invoke jobs.scheduler.run with a fresh import."""
    for m in ("jobs.scheduler", "jobs"):
        sys.modules.pop(m, None)
    from jobs import scheduler as sched
    rc = sched.run(argv)
    out, err = capsys.readouterr()
    return rc, out, err


def test_scheduler_runs_one_tick_and_exits(client, monkeypatch, capsys):
    """--once + a stubbed poll_once → exits 0, writes a heartbeat,
    releases the lock."""
    import sniper, joblock
    # Stub the scrape so the test doesn't hit the network.
    monkeypatch.setattr(sniper, "poll_once",
        lambda **k: {"fetched": 0, "new": 0, "scoring_pending": 0,
                     "poll_secs": 0.01, "per_source": {}})
    monkeypatch.setattr(sniper, "ensure_scorer_started", lambda: None)
    rc, out, err = _run_scheduler(["--once", "--interval", "1"], capsys)
    assert rc == 0, f"err={err}\nout={out}"
    # Emits two JSON lines: "started" and "tick", plus "stopped" on cleanup.
    events = [json.loads(l) for l in out.strip().splitlines() if l.startswith("{")]
    kinds = [e["event"] for e in events]
    assert "started" in kinds
    assert "tick" in kinds
    assert "stopped" in kinds
    # Lock released
    snap = joblock.current_holder("scheduler")
    assert snap and snap["holder"] in (None, "")


def test_scheduler_second_instance_exits_2_when_lock_held(client, monkeypatch, capsys):
    """A second scheduler that races a fresh lease must NOT run any
    ticks — it exits with code 2 and emits one `lock_held` event."""
    import joblock
    # Pre-acquire the lock as a different holder.
    assert joblock.acquire("scheduler", holder="other-host:1:x",
                            lease_seconds=600, steal_after_seconds=3600)
    rc, out, err = _run_scheduler(["--once"], capsys)
    assert rc == 2, f"expected exit 2, got {rc}\nout={out}\nerr={err}"
    events = [json.loads(l) for l in out.strip().splitlines() if l.startswith("{")]
    assert len(events) == 1
    assert events[0]["event"] == "lock_held"
    assert events[0]["existing_holder"] == "other-host:1:x"


def test_scheduler_steals_stale_lease(client, monkeypatch, capsys):
    """A crashed scheduler left an expired lease. The next start, with
    --steal-after-seconds 0, must take over and run normally."""
    import sniper, joblock
    assert joblock.acquire("scheduler", holder="crashed-host:1:z",
                            lease_seconds=1)
    time.sleep(1.2)  # let lease expire
    monkeypatch.setattr(sniper, "poll_once",
        lambda **k: {"fetched": 0, "new": 0, "scoring_pending": 0,
                     "poll_secs": 0.01, "per_source": {}})
    monkeypatch.setattr(sniper, "ensure_scorer_started", lambda: None)
    rc, out, err = _run_scheduler(
        ["--once", "--steal-after-seconds", "0"], capsys)
    assert rc == 0, f"err={err}\nout={out}"
    events = [json.loads(l) for l in out.strip().splitlines() if l.startswith("{")]
    assert any(e["event"] == "tick" for e in events)


# ---------- Admin source-health surfaces scheduler state -------------

def test_admin_source_health_surfaces_scheduler_lease(client, monkeypatch):
    import joblock, auth as _auth
    register(client, "admin@example.com")
    _auth.set_role(_auth.get_user_by_email("admin@example.com").id, "admin")
    logout(client); login(client, "admin@example.com")
    # Simulate a running scheduler by taking the lock
    assert joblock.acquire("scheduler", holder="scheduler-host:42:abc",
                            lease_seconds=600)
    r = client.get("/api/admin/sources/health")
    assert r.status_code == 200
    body = r.get_json()
    sched = body["scheduler"]
    # The lock-derived fields must be present
    assert "holder" in sched
    assert sched["holder"] == "scheduler-host:42:abc"
    assert sched["lock_expires_at"]
    assert sched["lock_acquired_at"]


def test_admin_source_health_requires_admin(client, monkeypatch):
    register(client, "regular@example.com")
    r = client.get("/api/admin/sources/health")
    assert r.status_code == 403


# ---------- Billing-unavailable UX -----------------------------------

def test_billing_me_returns_unavailable_flag_when_stripe_not_configured(
        client, monkeypatch):
    """No STRIPE_SECRET_KEY / STRIPE_PRICE_* → billing_available: false."""
    monkeypatch.delenv("STRIPE_SECRET_KEY",      raising=False)
    monkeypatch.delenv("STRIPE_PRICE_STARTER",   raising=False)
    monkeypatch.delenv("STRIPE_PRICE_PRO",       raising=False)
    register(client, "alice@example.com")
    r = client.get("/api/billing/me")
    assert r.status_code == 200
    body = r.get_json()
    assert "billing_available" in body
    assert body["billing_available"] is False


def test_billing_me_returns_available_when_fully_configured(client, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY",    "sk_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_STARTER", "price_starter_test")
    monkeypatch.setenv("STRIPE_PRICE_PRO",     "price_pro_test")
    register(client, "alice@example.com")
    r = client.get("/api/billing/me")
    assert r.status_code == 200
    assert r.get_json()["billing_available"] is True


def test_direct_checkout_still_rejected_when_unavailable(client, monkeypatch):
    """Even with the UI disabled, a hand-crafted POST must still be
    rejected by the server-side allowlist — UI state never grants access."""
    monkeypatch.delenv("STRIPE_SECRET_KEY",    raising=False)
    monkeypatch.delenv("STRIPE_PRICE_STARTER", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_PRO",     raising=False)
    register(client, "alice@example.com")
    r = client.post("/api/billing/checkout", json={"plan": "starter"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "unknown plan"


def test_dashboard_html_does_not_leak_billing_internals(client, monkeypatch):
    """The dashboard source must not contain the raw error strings or
    'available: []' that the server returns for misconfigured billing.
    Operators see those in logs; end users see the calm fallback only."""
    register(client, "alice@example.com")
    r = client.get("/?v=1")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    # Strings that would indicate leaked server error / config detail
    assert "available: []"  not in page
    assert "unknown plan"   not in page.lower()
    assert "sk_test_"       not in page
    assert "whsec_"         not in page
    # Calm copy is present
    assert "temporarily unavailable" in page.lower()


# ---------- Regression guards ----------------------------------------

def test_webhook_signature_still_required(client, monkeypatch):
    """No regression in webhook verification."""
    monkeypatch.setenv("STRIPE_SECRET_KEY",     "sk_test_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_dummy")
    r = client.post("/api/billing/webhook", data=b'{}',
                    headers={"Content-Type":"application/json"})
    assert r.status_code == 400


def test_phase1_authz_unchanged(client, monkeypatch):
    r = client.post("/api/save", json={"composite_id":"x"})
    assert r.status_code == 401


def test_billing_reconciliation_route_still_absent(client, monkeypatch):
    """Phase 2B hardening removed POST /api/admin/billing/reconcile.
    Phase 2C.1 must not accidentally re-introduce that route either."""
    register(client, "admin@example.com")
    import auth as _auth
    _auth.set_role(_auth.get_user_by_email("admin@example.com").id, "admin")
    logout(client); login(client, "admin@example.com")
    r = client.post("/api/admin/billing/reconcile")
    assert r.status_code in (404, 405)
