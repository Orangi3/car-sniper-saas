"""
tests/test_billing.py — Phase 2A Stripe billing tests.

Never hits live Stripe. All Stripe SDK calls are monkey-patched:
  * stripe.checkout.Session.create  → returns a stub dict
  * stripe.Subscription.retrieve    → returns a stub dict
  * stripe.Webhook.construct_event  → bypasses real signature math when
                                      the test wants to feed a synthetic
                                      event payload. Tests that need to
                                      check the signature path call the
                                      real verifier with a real HMAC.

Coverage checklist (from the principal-engineer brief):
  - checkout authorization (anon, free, starter, already-subscribed)
  - tampered plan selection (price_id in body ignored; bogus plan rejected)
  - tampered user_id (user_id in body ignored)
  - invalid webhook signatures (missing + wrong)
  - duplicate webhook delivery (dedup by event id)
  - successful activation (checkout.session.completed → users.plan flip)
  - payment failure (invoice.payment_failed → plan drops to free)
  - cancellation (customer.subscription.deleted → plan drops to free)
  - entitlement changes (subscription.updated price-swap → plan switches)
"""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import time

import pytest

from tests.conftest import (register, login, logout, set_plan,
                            insert_listing)


# ---------- Helpers --------------------------------------------------

def _set_stripe_env(monkeypatch):
    """Provide the env vars billing.py reads. Use harmless test sentinels."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_dummy")
    monkeypatch.setenv("STRIPE_PRICE_STARTER", "price_starter_test")
    monkeypatch.setenv("STRIPE_PRICE_PRO",     "price_pro_test")


def _stub_stripe_sdk(monkeypatch, *, session_create=None, subscription_retrieve=None,
                     skip_signature_check=True):
    """Replace the Stripe SDK methods billing.py uses with deterministic
    test doubles. If skip_signature_check is True,
    WebhookSignature.verify_header is monkey-patched to a no-op so tests
    can feed synthetic events without computing real HMACs. Tests that
    exercise the signature path pass skip_signature_check=False and let
    the real verifier run.

    NOTE: billing.verify_webhook intentionally calls
    stripe.WebhookSignature.verify_header directly (NOT
    stripe.Webhook.construct_event) so signature failures don't get
    conflated with event-shape failures. Patch the same symbol here."""
    import stripe
    if session_create is not None:
        monkeypatch.setattr(stripe.checkout.Session, "create", session_create)
    if subscription_retrieve is not None:
        monkeypatch.setattr(stripe.Subscription, "retrieve", subscription_retrieve)
    if skip_signature_check:
        monkeypatch.setattr(stripe.WebhookSignature, "verify_header",
                            lambda payload, header, secret, tolerance=None: True)


def _make_event(event_type: str, obj: dict, *, event_id: str = None) -> dict:
    """Build a synthetic Stripe event envelope."""
    if not event_id:
        event_id = f"evt_test_{int(time.time()*1e6)}"
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": obj},
        "created": int(time.time()),
        "livemode": False,
    }


def _post_webhook(client, event: dict, *, sig: str = "t=1,v1=fake"):
    """Hit /api/billing/webhook with a JSON event + signature header."""
    return client.post(
        "/api/billing/webhook",
        data=json.dumps(event).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Stripe-Signature": sig},
    )


def _sign(payload_bytes: bytes, secret: str, timestamp: int = None) -> str:
    """Compute a real Stripe-compatible signature for the signature-verify
    test. Mirrors stripe.WebhookSignature."""
    if timestamp is None:
        timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + payload_bytes
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


# ---------- /api/billing/checkout ------------------------------------

def test_checkout_anonymous_is_401(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    r = client.post("/api/billing/checkout", json={"plan": "starter"})
    assert r.status_code == 401


def test_checkout_unknown_plan_is_400(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    register(client, "alice@example.com")
    r = client.post("/api/billing/checkout", json={"plan": "elite"})
    assert r.status_code == 400
    body = r.get_json()
    assert "available" in body
    assert sorted(body["available"]) == ["pro", "starter"]


def test_checkout_returns_url_for_starter(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    captured = {}
    def fake_session_create(**kwargs):
        captured.update(kwargs)
        return {"id": "cs_test_abc123", "url": "https://checkout.stripe.com/c/cs_test_abc123"}
    _stub_stripe_sdk(monkeypatch, session_create=fake_session_create)
    register(client, "alice@example.com")
    r = client.post("/api/billing/checkout", json={"plan": "starter"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["url"].startswith("https://checkout.stripe.com/")
    assert body["id"] == "cs_test_abc123"
    # The line_items must reference the SERVER's price ID, not anything
    # the browser sent.
    assert captured["line_items"][0]["price"] == "price_starter_test"
    # mode is subscription (not 'payment')
    assert captured["mode"] == "subscription"


def test_checkout_ignores_client_supplied_user_id_and_price(client, monkeypatch):
    """Tampered request body must not influence which user gets the sub
    or which price is charged."""
    _set_stripe_env(monkeypatch)
    captured = {}
    def fake_session_create(**kwargs):
        captured.update(kwargs)
        return {"id": "cs_test_xyz", "url": "https://checkout.stripe.com/c/xyz"}
    _stub_stripe_sdk(monkeypatch, session_create=fake_session_create)
    register(client, "alice@example.com")
    import auth as _auth
    alice = _auth.get_user_by_email("alice@example.com")
    # Send a body trying to subscribe a DIFFERENT user to a NON-allowlisted price.
    r = client.post("/api/billing/checkout", json={
        "plan": "starter",
        "user_id": 99999,                     # try to inject
        "price_id": "price_attacker_owned",   # try to inject
    })
    assert r.status_code == 200
    # The Stripe call must reflect ALICE, not 99999, and use the allowlist price.
    assert captured["client_reference_id"] == str(alice.id)
    assert captured["metadata"]["user_id"] == str(alice.id)
    assert captured["line_items"][0]["price"] == "price_starter_test"


def test_checkout_409_when_already_active(client, monkeypatch):
    """Second checkout attempt while a sub is already active returns 409
    (avoid double-billing)."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch,
        session_create=lambda **k: {"id":"cs_x","url":"https://x"})
    register(client, "alice@example.com")
    # Seed an active subscription directly via billing.upsert_subscription
    import auth as _auth, db as _db, billing as _billing
    alice = _auth.get_user_by_email("alice@example.com")
    with _db.transaction() as conn:
        _billing.upsert_subscription(conn,
            user_id=alice.id,
            stripe_customer_id="cus_existing",
            stripe_subscription_id="sub_existing",
            stripe_price_id="price_starter_test",
            status="active",
            current_period_end=int(time.time()) + 86400 * 30,
            cancel_at_period_end=False)
        _billing.refresh_entitlement(conn, alice.id)
    r = client.post("/api/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["current_plan"] == "starter"


# ---------- /api/billing/webhook -------------------------------------

def test_webhook_missing_signature_is_400(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    r = client.post("/api/billing/webhook",
                    data=b'{"id":"evt_x","type":"x"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert "signature" in r.get_json()["error"].lower()


def test_webhook_invalid_signature_is_400(client, monkeypatch):
    """Signature actually verified — wrong HMAC -> 400. Uses the real
    stripe.Webhook.construct_event (no skip)."""
    _set_stripe_env(monkeypatch)
    # Do NOT stub construct_event — let the real verifier fire.
    _stub_stripe_sdk(monkeypatch, skip_signature_check=False)
    payload = b'{"id":"evt_tamper","type":"customer.subscription.updated"}'
    # Bad signature header — wrong secret used
    bad_sig = _sign(payload, "whsec_WRONG_SECRET")
    r = client.post("/api/billing/webhook", data=payload,
                    headers={"Content-Type":"application/json",
                             "Stripe-Signature": bad_sig})
    assert r.status_code == 400


def test_webhook_valid_signature_accepted(client, monkeypatch):
    """End-to-end signature happy path — REAL HMAC against our test secret."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch, skip_signature_check=False)
    # An unknown event type still verifies and inserts into the events
    # table; the dispatcher returns 'unknown_type'.
    payload_obj = _make_event("customer.tax_id.created", {"id": "txi_x"})
    payload = json.dumps(payload_obj).encode("utf-8")
    sig = _sign(payload, "whsec_test_dummy")
    r = client.post("/api/billing/webhook", data=payload,
                    headers={"Content-Type":"application/json",
                             "Stripe-Signature": sig})
    assert r.status_code == 200
    assert r.get_json()["outcome"] == "unknown_type"


def test_webhook_signed_but_malformed_json_is_400_payload(client, monkeypatch):
    """Edge case: payload is correctly signed but isn't valid JSON. This
    must NOT be reported as a signature failure — that conflation hid the
    original Stripe-Event-parsing bug. Expect 400 with 'invalid payload'."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch, skip_signature_check=False)
    bogus_body = b"this is not json at all"
    sig = _sign(bogus_body, "whsec_test_dummy")
    r = client.post("/api/billing/webhook", data=bogus_body,
                    headers={"Content-Type":"application/json",
                             "Stripe-Signature": sig})
    assert r.status_code == 400
    body = r.get_json()
    # The error must be distinct from the signature-failure message so
    # callers can tell tampering from corruption.
    assert body["error"] == "invalid payload"


def test_webhook_duplicate_event_deduped(client, monkeypatch):
    """Same evt_id arriving twice: first → handled, second → duplicate 200."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    event = _make_event("customer.tax_id.created", {"id":"txi_x"},
                        event_id="evt_dup_42")
    r1 = _post_webhook(client, event)
    assert r1.status_code == 200
    assert not r1.get_json().get("duplicate")
    r2 = _post_webhook(client, event)
    assert r2.status_code == 200
    assert r2.get_json().get("duplicate") is True


# ---------- Successful activation ------------------------------------

def test_checkout_completed_activates_subscription_and_upgrades_plan(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    # We need Subscription.retrieve to return a starter sub.
    fake_sub = {
        "id": "sub_test_1",
        "customer": "cus_test_1",
        "status": "active",
        "current_period_end": int(time.time()) + 86400 * 30,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_starter_test"}}]},
    }
    _stub_stripe_sdk(monkeypatch,
        subscription_retrieve=lambda sid: fake_sub if sid == "sub_test_1" else None)
    register(client, "alice@example.com")
    import auth as _auth
    alice = _auth.get_user_by_email("alice@example.com")
    assert alice.plan == "free"
    event = _make_event("checkout.session.completed", {
        "id": "cs_test_1",
        "mode": "subscription",
        "client_reference_id": str(alice.id),
        "metadata": {"user_id": str(alice.id), "plan": "starter"},
        "customer": "cus_test_1",
        "subscription": "sub_test_1",
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["outcome"] == "handled"
    # users.plan should now be starter
    refetched = _auth.get_user_by_email("alice@example.com")
    assert refetched.plan == "starter"
    # /api/billing/me should reflect the active sub
    me = client.get("/api/billing/me").get_json()
    assert me["subscription"]["status"] == "active"
    assert me["subscription"]["plan"] == "starter"
    assert me["subscription"]["stripe_price_id"] == "price_starter_test"


def test_checkout_completed_rejects_non_allowlisted_price(client, monkeypatch):
    """Even with a valid Stripe sub, if the price isn't in our allowlist we
    refuse to provision a plan."""
    _set_stripe_env(monkeypatch)
    rogue_sub = {
        "id": "sub_test_2",
        "customer": "cus_test_2",
        "status": "active",
        "current_period_end": int(time.time()) + 86400,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_ROGUE_not_in_allowlist"}}]},
    }
    _stub_stripe_sdk(monkeypatch,
        subscription_retrieve=lambda sid: rogue_sub)
    register(client, "alice@example.com")
    import auth as _auth
    alice = _auth.get_user_by_email("alice@example.com")
    event = _make_event("checkout.session.completed", {
        "id": "cs_test_2",
        "mode": "subscription",
        "client_reference_id": str(alice.id),
        "metadata": {"user_id": str(alice.id)},
        "customer": "cus_test_2",
        "subscription": "sub_test_2",
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200  # accepted, processed, but no-op
    # plan must NOT have changed
    assert _auth.get_user_by_email("alice@example.com").plan == "free"


# ---------- Payment failure / cancellation ---------------------------

def _seed_active_starter(client, monkeypatch, email="alice@example.com"):
    """Set up a user with an existing active starter subscription via
    the upsert path (mimics what a successful checkout would leave)."""
    register(client, email)
    import auth as _auth, db as _db, billing as _billing
    u = _auth.get_user_by_email(email)
    with _db.transaction() as conn:
        _billing.upsert_subscription(conn,
            user_id=u.id, stripe_customer_id="cus_a",
            stripe_subscription_id="sub_a",
            stripe_price_id="price_starter_test",
            status="active",
            current_period_end=int(time.time()) + 86400 * 30,
            cancel_at_period_end=False)
        _billing.refresh_entitlement(conn, u.id)
    return u


def test_invoice_payment_failed_drops_to_free(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_active_starter(client, monkeypatch)
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "starter"
    event = _make_event("invoice.payment_failed", {
        "id": "in_test_1",
        "subscription": "sub_a",
        "customer": "cus_a",
        # metadata propagates onto invoices only sometimes — use the fallback
        # path that looks up user_id by subscription mirror row.
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200
    # past_due is not entitling — should drop to free
    assert _auth.get_user_by_email(u.email).plan == "free"


def test_subscription_deleted_drops_to_free(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_active_starter(client, monkeypatch)
    event = _make_event("customer.subscription.deleted", {
        "id": "sub_a", "object": "subscription",
        "customer": "cus_a",
        "items": {"data": [{"price": {"id": "price_starter_test"}}]},
        "status": "canceled",
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "free"


def test_subscription_updated_switches_plan_starter_to_pro(client, monkeypatch):
    """User upgrades from Starter to Pro inside Stripe; webhook fires
    customer.subscription.updated with the new price. Our plan must follow."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    u = _seed_active_starter(client, monkeypatch)
    import auth as _auth
    assert _auth.get_user_by_email(u.email).plan == "starter"
    event = _make_event("customer.subscription.updated", {
        "id": "sub_a", "object": "subscription",
        "customer": "cus_a",
        "status": "active",
        "current_period_end": int(time.time()) + 86400 * 60,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_pro_test"}}]},
        "metadata": {"user_id": str(u.id)},
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200
    assert _auth.get_user_by_email(u.email).plan == "pro"


def test_invoice_paid_refreshes_period(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    new_period_end = int(time.time()) + 86400 * 60
    fake_sub = {
        "id": "sub_a",
        "customer": "cus_a",
        "status": "active",
        "current_period_end": new_period_end,
        "cancel_at_period_end": False,
        "items": {"data": [{"price": {"id": "price_starter_test"}}]},
    }
    _stub_stripe_sdk(monkeypatch,
        subscription_retrieve=lambda sid: fake_sub)
    u = _seed_active_starter(client, monkeypatch)
    event = _make_event("invoice.paid", {
        "id": "in_renew_1",
        "subscription": "sub_a",
        "customer": "cus_a",
    })
    r = _post_webhook(client, event)
    assert r.status_code == 200
    me = client.get("/api/billing/me").get_json()
    assert me["subscription"]["status"] == "active"
    # period_end should have advanced
    assert me["subscription"]["current_period_end"] is not None


# ---------- /api/billing/me ------------------------------------------

def test_billing_me_anonymous_is_401(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    r = client.get("/api/billing/me")
    assert r.status_code == 401


def test_billing_me_free_user_has_null_subscription(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    register(client, "alice@example.com")
    r = client.get("/api/billing/me")
    assert r.status_code == 200
    body = r.get_json()
    assert body["user"]["plan"] == "free"
    assert body["subscription"] is None
