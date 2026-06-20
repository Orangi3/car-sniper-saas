"""
tests/test_billing_lifecycle.py — Phase 2B coverage.

Exercises the lifecycle hardening added on top of Phase 2A:
  * /api/billing/portal authorization + server-owned customer/return URL
  * plan switches Starter <-> Pro via verified subscription.updated
  * cancel_at_period_end keeps access until current_period_end actually
    elapses (defensive downgrade afterwards)
  * unknown Price IDs never grant paid access AND get logged as anomalies
  * out-of-order webhook events do not clobber newer state
  * Stripe-backed reconcile_all() fixes stale local data
  * full refund revokes access; partial refund is recorded but does not
    change access; refunds we can't link to a sub are recorded
  * dispute.created suspends access; dispute.closed is informational
  * no regression in signature verification, dedup, or Phase 1 authz

Every test stubs the Stripe SDK; nothing touches the network.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import register, login, logout
from tests.test_billing import (
    _set_stripe_env, _stub_stripe_sdk, _make_event, _post_webhook,
)


# ---------- Helpers ---------------------------------------------------

def _seed_subscription(client, *, email="alice@example.com",
                       price_id="price_starter_test",
                       plan="starter", status="active",
                       cancel_at_period_end=False,
                       period_end_offset_s=30 * 86400,
                       sub_id="sub_alice", customer_id="cus_alice",
                       last_event_at=None, last_event_id=None):
    """Register + seed a subscription row directly via the billing helpers."""
    register(client, email)
    import auth as _auth, db as _db, billing as _billing
    u = _auth.get_user_by_email(email)
    with _db.transaction() as conn:
        _billing.upsert_subscription(conn,
            user_id=u.id, stripe_customer_id=customer_id,
            stripe_subscription_id=sub_id, stripe_price_id=price_id,
            status=status,
            current_period_end=int(time.time()) + int(period_end_offset_s),
            cancel_at_period_end=cancel_at_period_end)
        # Manually patch plan since upsert reads from price_to_plan() which
        # might not match the test's seeded price_id.
        ph = _db.placeholder()
        cur = conn.cursor()
        cur.execute(f"UPDATE subscriptions SET plan = {ph} WHERE stripe_subscription_id = {ph}",
                    (plan, sub_id))
        if last_event_at:
            cur.execute(
                f"UPDATE subscriptions SET last_event_at = {ph}, last_event_id = {ph} "
                f"WHERE stripe_subscription_id = {ph}",
                (last_event_at, last_event_id, sub_id))
        _billing.refresh_entitlement(conn, u.id)
    return u


def _count_anomalies(anomaly_type: str = None) -> int:
    import db as _db
    with _db.transaction() as conn:
        cur = conn.cursor()
        if anomaly_type:
            ph = _db.placeholder()
            cur.execute(
                f"SELECT COUNT(*) AS n FROM billing_anomalies WHERE type = {ph}",
                (anomaly_type,))
        else:
            cur.execute("SELECT COUNT(*) AS n FROM billing_anomalies")
        return int(cur.fetchone()["n"])


# ---------- /api/billing/portal --------------------------------------

def test_portal_requires_auth(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    r = client.post("/api/billing/portal")
    assert r.status_code == 401


def test_portal_returns_404_when_no_subscription(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    register(client, "alice@example.com")
    r = client.post("/api/billing/portal")
    assert r.status_code == 404
    body = r.get_json()
    assert body["error"] == "no_stripe_customer"


def test_portal_uses_server_owned_customer_and_return_url(client, monkeypatch):
    """Even if the client tries to inject a customer ID or return URL,
    the server uses ONLY the local subscription's customer + BILLING_ORIGIN."""
    _set_stripe_env(monkeypatch)
    monkeypatch.setenv("BILLING_ORIGIN", "https://app.sniper.test")
    captured = {}
    class _FakePortalSession:
        @staticmethod
        def create(**kw):
            captured.update(kw)
            return {"url": "https://billing.stripe.com/p/session/test_xyz"}
    class _FakePortal:
        Session = _FakePortalSession
    import stripe
    monkeypatch.setattr(stripe, "billing_portal", _FakePortal, raising=False)
    _stub_stripe_sdk(monkeypatch)

    _seed_subscription(client, customer_id="cus_REAL_LOCAL")
    # Attempt to inject — server must ignore.
    r = client.post("/api/billing/portal", json={
        "customer": "cus_ATTACKER_OWNED",
        "return_url": "https://attacker.example.com/steal",
    })
    assert r.status_code == 200
    assert r.get_json()["url"].startswith("https://billing.stripe.com/")
    # The Stripe call must reference OUR customer and OUR origin.
    assert captured["customer"] == "cus_REAL_LOCAL"
    assert captured["return_url"].startswith("https://app.sniper.test")


# ---------- Plan switches (verified subscription.updated) ------------

def test_subscription_updated_pro_to_starter_drops_plan(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, price_id="price_pro_test", plan="pro")
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "pro"
    event = _make_event("customer.subscription.updated", {
        "id": "sub_alice", "object": "subscription",
        "customer": "cus_alice", "status": "active",
        "current_period_end": int(time.time()) + 60 * 86400,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_starter_test"}}]},
        "metadata": {"user_id": str(u.id)},
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200
    assert _auth.get_user_by_email(u.email).plan == "starter"


def test_subscription_updated_unknown_price_records_anomaly_no_plan_change(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, price_id="price_starter_test", plan="starter")
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "starter"
    event = _make_event("customer.subscription.updated", {
        "id": "sub_alice", "object": "subscription",
        "customer": "cus_alice", "status": "active",
        "current_period_end": int(time.time()) + 60 * 86400,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_ROGUE_unallowlisted"}}]},
        "metadata": {"user_id": str(u.id)},
    })
    before = _count_anomalies("unknown_price")
    r = _post_webhook(client, event)
    assert r.status_code == 200
    # Plan must NOT have switched
    assert _auth.get_user_by_email(u.email).plan == "starter"
    assert _count_anomalies("unknown_price") == before + 1


# ---------- Cancel-at-period-end retains access ----------------------

def test_cancel_at_period_end_retains_access_until_end(client, monkeypatch):
    """Stripe's cancel_at_period_end=True keeps the sub status='active'
    until period_end actually arrives. Entitlement must follow."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    # Period end is two weeks away — user must remain on starter.
    u = _seed_subscription(client, plan="starter",
                            cancel_at_period_end=True,
                            period_end_offset_s=14 * 86400)
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "starter"
    # /api/save should still succeed (was 402 for free users)
    r = client.post("/api/save", json={"composite_id": "any:cid"})
    # 200 (write happens) OR 400 (no such listing) — but NEVER 402
    assert r.status_code != 402, r.get_data(as_text=True)


def test_cancel_at_period_end_past_end_drops_access(client, monkeypatch):
    """Defensive: if subscription.deleted webhook was lost AND we're past
    period_end, refresh_entitlement should still demote the user."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    # period_end is 1 hour AGO; cancel_at_period_end is True.
    u = _seed_subscription(client, plan="starter",
                            cancel_at_period_end=True,
                            period_end_offset_s=-3600)
    import auth as _auth, db as _db, billing as _billing
    with _db.transaction() as conn:
        _billing.refresh_entitlement(conn, u.id)
    assert _auth.get_user_by_email(u.email).plan == "free"


# ---------- Out-of-order event protection ----------------------------

def test_out_of_order_subscription_update_skipped(client, monkeypatch):
    """An older subscription.updated must not overwrite a newer one."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    # Seed with last_event_at = now (i.e., we've already processed a "newer" event)
    new_iso = datetime.now(timezone.utc).isoformat()
    u = _seed_subscription(client, plan="pro",
                           price_id="price_pro_test",
                           last_event_at=new_iso,
                           last_event_id="evt_newer_pro")
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "pro"
    # Now feed an OLDER event (created 1 hour ago) trying to downgrade.
    old_epoch = int(time.time()) - 3600
    event = _make_event("customer.subscription.updated", {
        "id": "sub_alice", "object": "subscription",
        "customer": "cus_alice", "status": "active",
        "current_period_end": int(time.time()) + 60 * 86400,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_starter_test"}}]},
        "metadata": {"user_id": str(u.id)},
    })
    event["created"] = old_epoch
    before = _count_anomalies("out_of_order_event")
    r = _post_webhook(client, event)
    assert r.status_code == 200
    # Plan stayed pro because the older event was rejected.
    assert _auth.get_user_by_email(u.email).plan == "pro"
    assert _count_anomalies("out_of_order_event") == before + 1


# ---------- Reconciliation -------------------------------------------

def test_reconciliation_corrects_stale_status(client, monkeypatch):
    """Local says 'active' but Stripe says 'canceled' — reconciliation
    must patch the local row AND drop the user's plan."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="starter", status="active")
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "starter"

    # Stripe says canceled with starter price
    remote_sub = {
        "id": "sub_alice",
        "customer": "cus_alice",
        "status": "canceled",
        "current_period_end": int(time.time()) - 1,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_starter_test"}}]},
    }
    import stripe
    monkeypatch.setattr(stripe.Subscription, "retrieve",
                        lambda sid: remote_sub if sid == "sub_alice" else None)
    import billing as _billing
    summary = _billing.reconcile_all(max_count=10, time_budget_s=5,
                                     sleep_between_s=0)
    assert summary["checked"] >= 1
    assert summary["mismatches_found"] >= 1
    assert summary["corrections_applied"] >= 1
    assert summary["errors"] == 0
    # Plan should now be free
    assert _auth.get_user_by_email(u.email).plan == "free"
    # Anomaly recorded
    assert _count_anomalies("reconciliation_correction") >= 1


def test_reconciliation_noop_when_already_in_sync(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="starter", status="active",
                           period_end_offset_s=30 * 86400)
    # Stripe agrees
    remote_sub = {
        "id": "sub_alice", "customer": "cus_alice",
        "status": "active",
        "current_period_end": int(time.time()) + 30 * 86400,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_starter_test"}}]},
    }
    import stripe, billing as _billing
    # Need to match the local timestamp precisely; relax by reading it.
    import db as _db
    with _db.transaction() as conn:
        cur = conn.cursor()
        ph = _db.placeholder()
        cur.execute(f"SELECT current_period_end FROM subscriptions WHERE stripe_subscription_id = {ph}",
                    ("sub_alice",))
        local_end_iso = cur.fetchone()["current_period_end"]
    # Convert local_end_iso back to epoch so the remote matches.
    dt = datetime.fromisoformat(local_end_iso.replace("Z","+00:00"))
    remote_sub["current_period_end"] = int(dt.timestamp())
    monkeypatch.setattr(stripe.Subscription, "retrieve",
                        lambda sid: remote_sub if sid == "sub_alice" else None)
    summary = _billing.reconcile_all(max_count=10, time_budget_s=5,
                                     sleep_between_s=0)
    assert summary["checked"] >= 1
    assert summary["mismatches_found"] == 0
    assert summary["corrections_applied"] == 0


def test_admin_billing_health_endpoint(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    # Set up admin + a sub + run reconciliation
    register(client, "admin@example.com")
    import auth as _auth
    _auth.set_role(_auth.get_user_by_email("admin@example.com").id, "admin")
    logout(client); login(client, "admin@example.com")
    r = client.get("/api/admin/billing/health")
    assert r.status_code == 200
    body = r.get_json()
    assert "last_reconciliation" in body
    assert "unresolved_anomalies_by_severity" in body
    assert "subscriptions_by_status" in body


def test_admin_billing_health_blocked_for_non_admin(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    register(client, "regular@example.com")
    r = client.get("/api/admin/billing/health")
    assert r.status_code == 403


# ---------- Refunds + disputes ---------------------------------------

def test_full_refund_revokes_access(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="starter")
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "starter"
    # Mock invoice.retrieve to return our sub
    import stripe
    monkeypatch.setattr(stripe.Invoice, "retrieve",
                        lambda iid: {"id": iid, "subscription": "sub_alice"})
    event = _make_event("charge.refunded", {
        "id": "ch_test", "customer": "cus_alice",
        "invoice": "in_test",
        "amount": 1900, "amount_refunded": 1900,
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200
    assert _auth.get_user_by_email(u.email).plan == "free"


def test_partial_refund_records_anomaly_no_plan_change(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="starter")
    import stripe
    monkeypatch.setattr(stripe.Invoice, "retrieve",
                        lambda iid: {"id": iid, "subscription": "sub_alice"})
    event = _make_event("charge.refunded", {
        "id": "ch_test", "customer": "cus_alice",
        "invoice": "in_test",
        "amount": 1900, "amount_refunded": 500,
    })
    before = _count_anomalies("partial_refund_unlinked")
    r = _post_webhook(client, event)
    assert r.status_code == 200
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "starter"  # unchanged
    assert _count_anomalies("partial_refund_unlinked") == before + 1


def test_unlinked_refund_records_anomaly(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    # No invoice on the charge — can't link to a sub.
    event = _make_event("charge.refunded", {
        "id": "ch_one_off", "customer": "cus_unknown",
        "amount": 500, "amount_refunded": 500,
    })
    before = _count_anomalies("refund_unlinked")
    r = _post_webhook(client, event)
    assert r.status_code == 200
    assert _count_anomalies("refund_unlinked") == before + 1


def test_dispute_created_suspends_access(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="pro", price_id="price_pro_test")
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "pro"
    # Mock charge + invoice retrieve so dispute -> charge -> invoice -> sub
    import stripe
    monkeypatch.setattr(stripe.Charge, "retrieve",
                        lambda cid: {"id": cid, "customer": "cus_alice",
                                     "invoice": "in_test"})
    monkeypatch.setattr(stripe.Invoice, "retrieve",
                        lambda iid: {"id": iid, "subscription": "sub_alice"})
    event = _make_event("charge.dispute.created", {
        "id": "dp_test", "charge": "ch_test",
        "status": "warning_needs_response", "reason": "fraudulent",
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200
    # Pro access suspended
    assert _auth.get_user_by_email(u.email).plan == "free"


def test_dispute_closed_is_informational(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_subscription(client, plan="starter")
    event = _make_event("charge.dispute.closed", {
        "id": "dp_test", "status": "won",
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200
    # No automatic re-grant: caller relies on subscription.updated /
    # reconciliation to restore state. Test just confirms no crash.
    import auth as _auth
    # Plan stayed starter because we never suspended in this test.
    assert _auth.get_user_by_email(u.email).plan == "starter"


# ---------- Phase 1 / Phase 2A still pass ----------------------------

def test_phase1_authz_still_in_force(client, monkeypatch):
    """Anonymous /api/save still 401. Free user still 402. Webhook
    without signature still 400. (Regression guard.)"""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    r = client.post("/api/save", json={"composite_id":"x"})
    assert r.status_code == 401
    register(client, "alice@example.com")
    r = client.post("/api/save", json={"composite_id":"x"})
    assert r.status_code == 402
    r = client.post("/api/billing/webhook", data=b'{}',
                    headers={"Content-Type":"application/json"})
    assert r.status_code == 400
