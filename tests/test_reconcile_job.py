"""
tests/test_reconcile_job.py — internal reconciliation job + DB lock.

Proves:
  * `python -m jobs.reconcile_billing` is runnable end-to-end (via main()
    with mocked Stripe). Exit codes encode the operational outcome.
  * The DB-backed advisory lock prevents overlapping runs.
  * The deleted public route `/api/admin/billing/reconcile` is gone.
  * Read-only `GET /api/admin/billing/health` still works AND now
    surfaces the reconcile job lock state.
  * Mismatch correction + Stripe API failure paths flow through the
    same code as the (now-removed) HTTP path.
  * No regression: existing webhook security and admin-health authz hold.

Every test stubs the Stripe SDK; nothing touches the network.
"""
from __future__ import annotations

import io
import json
import time
import sys

import pytest

from tests.conftest import register, login, logout, set_plan
from tests.test_billing import _set_stripe_env, _stub_stripe_sdk
from tests.test_billing_lifecycle import _seed_subscription


# ---------- Public route is gone -------------------------------------

def test_public_reconcile_route_is_removed(client, monkeypatch):
    """The Phase 2C handoff originally proposed an HTTP-scheduled
    admin endpoint. That has been removed; cron must use the internal
    CLI instead. Anything that POSTs to the old URL must 404 (or 405
    if some unrelated route accidentally claims it)."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    # Anonymous
    r = client.post("/api/admin/billing/reconcile")
    assert r.status_code in (401, 404, 405), \
        f"unexpected status {r.status_code}: {r.get_data(as_text=True)}"
    # As authenticated admin — still must not be a scheduled action surface.
    register(client, "admin@example.com")
    import auth as _auth
    _auth.set_role(_auth.get_user_by_email("admin@example.com").id, "admin")
    logout(client); login(client, "admin@example.com")
    r = client.post("/api/admin/billing/reconcile")
    assert r.status_code in (404, 405), \
        f"admin reconcile route should be gone, got {r.status_code}"


# ---------- /api/admin/billing/health still works --------------------

def test_admin_health_still_authenticated_and_surfaces_lock(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    register(client, "admin@example.com")
    import auth as _auth
    _auth.set_role(_auth.get_user_by_email("admin@example.com").id, "admin")
    logout(client); login(client, "admin@example.com")
    r = client.get("/api/admin/billing/health")
    assert r.status_code == 200
    body = r.get_json()
    # New field — the read-only view exposes the current lock holder so
    # operators can see whether a reconcile is in progress.
    assert "reconcile_job_lock" in body


def test_admin_health_requires_admin(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    register(client, "alice@example.com")
    r = client.get("/api/admin/billing/health")
    assert r.status_code == 403


# ---------- DB-backed lock primitive ---------------------------------

def test_lock_acquire_release_roundtrip(client, monkeypatch):
    """First acquire wins; second on a different holder fails until the
    first releases. release_job_lock is no-op when called by a stale
    holder (we already lost the lock)."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    # `client` fixture just ensures the test DB + migrations exist.
    import billing as _b
    assert _b.acquire_job_lock("reconcile_billing", holder="host-a:1:aaa")
    # Second acquire by a DIFFERENT holder must fail (lock held, not
    # yet stealable).
    assert not _b.acquire_job_lock(
        "reconcile_billing", holder="host-b:2:bbb",
        steal_after_seconds=3600)
    # The current holder snapshot reflects host-a
    snap = _b.current_lock_holder("reconcile_billing")
    assert snap is not None
    assert snap["holder"] == "host-a:1:aaa"
    # host-b cannot release someone else's lock
    _b.release_job_lock("reconcile_billing", holder="host-b:2:bbb")
    snap2 = _b.current_lock_holder("reconcile_billing")
    assert snap2["holder"] == "host-a:1:aaa"  # still held
    # host-a releases properly
    _b.release_job_lock("reconcile_billing", holder="host-a:1:aaa")
    snap3 = _b.current_lock_holder("reconcile_billing")
    assert snap3["holder"] in (None, "", )
    # Now host-b can acquire
    assert _b.acquire_job_lock("reconcile_billing", holder="host-b:2:bbb")


def test_lock_steals_stale_holder(client, monkeypatch):
    """If a previous reconcile crashed and left an expired lock past the
    steal_after window, the next caller takes the lock instead of
    blocking forever."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    import billing as _b
    # Acquire with a 1-second lease, so it expires almost immediately.
    assert _b.acquire_job_lock(
        "reconcile_billing", holder="crashed-host:1:x",
        lease_seconds=1)
    time.sleep(1.2)  # let lease expire
    # steal_after_seconds=0 means "steal anything expired".
    assert _b.acquire_job_lock(
        "reconcile_billing", holder="fresh-host:2:y",
        lease_seconds=600, steal_after_seconds=0)
    snap = _b.current_lock_holder("reconcile_billing")
    assert snap["holder"] == "fresh-host:2:y"


# ---------- reconcile_billing_with_lock ------------------------------

def test_with_lock_reports_existing_holder_when_held(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    import billing as _b
    # Pre-acquire so the wrapper finds it held
    assert _b.acquire_job_lock(
        "reconcile_billing", holder="held-by-other:1:z")
    summary = _b.reconcile_billing_with_lock(
        holder="me:2:w", lease_seconds=60,
        steal_after_seconds=3600, sleep_between_s=0)
    assert summary["lock_acquired"] is False
    assert summary["existing_holder"] == "held-by-other:1:z"


def test_with_lock_runs_and_corrects(client, monkeypatch):
    """End-to-end: seeded local sub diverges from mocked Stripe sub,
    the wrapper acquires the lock, calls reconcile_all, mismatches get
    corrected, lock is released."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="pro", price_id="price_pro_test",
                           status="active")
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "pro"

    remote = {
        "id": "sub_alice", "customer": "cus_alice",
        "status": "canceled",
        "current_period_end": int(time.time()) - 1,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_pro_test"}}]},
    }
    import stripe, billing as _b
    monkeypatch.setattr(stripe.Subscription, "retrieve",
                        lambda sid: remote if sid == "sub_alice" else None)
    summary = _b.reconcile_billing_with_lock(
        holder="test-runner:1:a", lease_seconds=60,
        steal_after_seconds=3600,
        max_count=10, time_budget_s=5, sleep_between_s=0)
    assert summary["lock_acquired"] is True
    assert summary["dry_run"] is False
    assert summary["checked"] >= 1
    assert summary["corrections_applied"] >= 1
    # Plan should now be free
    assert _auth.get_user_by_email(u.email).plan == "free"
    # Lock released
    snap = _b.current_lock_holder("reconcile_billing")
    assert snap["holder"] in (None, "")


def test_with_lock_dry_run_does_not_correct(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="pro", price_id="price_pro_test",
                           status="active")
    import auth as _auth
    remote = {
        "id": "sub_alice", "customer": "cus_alice",
        "status": "canceled",
        "current_period_end": int(time.time()) - 1,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_pro_test"}}]},
    }
    import stripe, billing as _b
    monkeypatch.setattr(stripe.Subscription, "retrieve",
                        lambda sid: remote)
    summary = _b.reconcile_billing_with_lock(
        holder="dry-runner:1:a", lease_seconds=60,
        dry_run=True, max_count=10, time_budget_s=5, sleep_between_s=0)
    assert summary["lock_acquired"] is True
    assert summary["dry_run"] is True
    assert summary["mismatches_found"] >= 1
    assert summary["corrections_applied"] == 0
    # Plan must NOT have changed — dry run never writes
    assert _auth.get_user_by_email(u.email).plan == "pro"


# ---------- The CLI main() entry point -------------------------------

def _run_main(argv, capsys):
    """Invoke jobs.reconcile_billing.main and return (rc, stdout, stderr)."""
    # Fresh import each call so it picks up the test's env / monkeypatches.
    sys.modules.pop("jobs.reconcile_billing", None)
    sys.modules.pop("jobs", None)
    from jobs import reconcile_billing as job
    rc = job.main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


def test_cli_missing_stripe_key_is_exit_1(client, monkeypatch, capsys):
    """No STRIPE_SECRET_KEY → operational failure (exit 1), no JSON
    output, and we never echo the env var name's *value* (there is none
    to echo, but the test confirms the safe-path)."""
    # Clear the env var explicitly
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    rc, out, err = _run_main([], capsys)
    assert rc == 1
    assert "STRIPE_SECRET_KEY" in err  # the NAME is logged
    assert out == ""


def test_cli_runs_and_returns_json_summary(client, monkeypatch, capsys):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="starter", status="active")
    import stripe
    # Mock Stripe to agree with local — no mismatches
    import db as _db
    with _db.transaction() as conn:
        cur = conn.cursor()
        ph = _db.placeholder()
        cur.execute(f"SELECT current_period_end FROM subscriptions WHERE stripe_subscription_id = {ph}",
                    ("sub_alice",))
        local_end = cur.fetchone()["current_period_end"]
    from datetime import datetime
    dt = datetime.fromisoformat(local_end.replace("Z","+00:00"))
    remote = {
        "id": "sub_alice", "customer": "cus_alice", "status": "active",
        "current_period_end": int(dt.timestamp()),
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_starter_test"}}]},
    }
    monkeypatch.setattr(stripe.Subscription, "retrieve",
                        lambda sid: remote)
    rc, out, err = _run_main(
        ["--max-count", "5", "--time-budget", "5", "--sleep-between", "0"],
        capsys)
    assert rc == 0
    # stdout is exactly one JSON line
    payload = json.loads(out.strip())
    assert payload["lock_acquired"] is True
    assert payload["checked"] >= 1
    assert payload["corrections_applied"] == 0
    # The summary does NOT contain any sk_test_ / whsec_ / sub_id key values
    blob = out.lower()
    assert "sk_test_" not in blob
    assert "whsec_" not in blob


def test_cli_lock_held_returns_exit_2(client, monkeypatch, capsys):
    """If something else is holding the lock, the CLI returns exit 2
    and prints a structured 'lock_acquired: false' summary."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    import billing as _b
    assert _b.acquire_job_lock("reconcile_billing", holder="other:1:a")
    rc, out, err = _run_main([], capsys)
    assert rc == 2
    payload = json.loads(out.strip())
    assert payload["lock_acquired"] is False
    assert payload["existing_holder"] == "other:1:a"


def test_cli_dry_run_path(client, monkeypatch, capsys):
    """--dry-run reports mismatches but exits 0 without correcting."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="pro", price_id="price_pro_test",
                           status="active")
    import stripe
    remote = {
        "id": "sub_alice", "customer": "cus_alice", "status": "canceled",
        "current_period_end": int(time.time()) - 1,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_pro_test"}}]},
    }
    monkeypatch.setattr(stripe.Subscription, "retrieve", lambda sid: remote)
    rc, out, err = _run_main(
        ["--dry-run", "--max-count","5","--time-budget","5","--sleep-between","0"],
        capsys)
    assert rc == 0
    payload = json.loads(out.strip())
    assert payload["dry_run"] is True
    assert payload["mismatches_found"] >= 1
    assert payload["corrections_applied"] == 0
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "pro"  # unchanged


def test_cli_stripe_errors_return_exit_3(client, monkeypatch, capsys):
    """A reconcile that completed but had per-row Stripe API failures
    returns exit 3 so the timer's monitoring picks it up."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    _seed_subscription(client, plan="starter", status="active")
    import stripe
    def _explode(sid):
        raise stripe.error.APIConnectionError("network down")
    monkeypatch.setattr(stripe.Subscription, "retrieve", _explode)
    rc, out, err = _run_main(
        ["--max-count","5","--time-budget","5","--sleep-between","0"],
        capsys)
    assert rc == 3
    payload = json.loads(out.strip())
    assert payload["errors"] >= 1
    assert payload["last_error"]
    # Error string is bounded length and contains the type name
    assert "APIConnectionError" in payload["last_error"]


# ---------- Regression: webhook + Phase 1 still hold -----------------

def test_webhook_signature_unchanged(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    # No signature header
    r = client.post("/api/billing/webhook", data=b'{}',
                    headers={"Content-Type":"application/json"})
    assert r.status_code == 400


def test_phase1_authz_unchanged(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    r = client.post("/api/save", json={"composite_id": "x"})
    assert r.status_code == 401
