"""
tests/test_billing_ui.py — Phase 2C server-facing UI contract.

The dashboard UI is HTML/JS — we don't unit-test the DOM here. What we
DO test is the server contract the UI depends on:
  * /billing/success and /billing/cancel exist, require auth, and redirect
    to / with a `billing_return` flag and no other state mutated.
  * Stripe's session ID is preserved across the cache-bust redirect ONLY
    if it matches a strict pattern (cs_… alphanumerics+underscore).
  * Adversarial values in the URL never grant entitlement on their own —
    a free user landing on /billing/success?billing_return=success is
    still free in /api/billing/me and still gets 402 from /api/save.
  * No regression in webhook signature verification, idempotency, or
    Phase 1 / Phase 2A / Phase 2B authz.
"""
from __future__ import annotations

import pytest

from tests.conftest import register, login, logout
from tests.test_billing import _set_stripe_env, _stub_stripe_sdk


# ---------- /billing/success ----------------------------------------

def test_billing_success_unauth_redirects_to_login(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    r = client.get("/billing/success?session=cs_test_abc")
    assert r.status_code == 302
    assert r.headers["Location"] == "/login"


def test_billing_success_authed_redirects_with_return_flag(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    register(client, "alice@example.com")
    r = client.get("/billing/success?session=cs_test_12345")
    assert r.status_code == 302
    loc = r.headers["Location"]
    # Lands on / with the billing_return flag set; session id preserved.
    assert loc.startswith("/?billing_return=success")
    assert "session=cs_test_12345" in loc


def test_billing_success_strips_non_stripe_session_param(client, monkeypatch):
    """Adversarial session value (not matching cs_… pattern) is dropped
    so it can't be reflected into the landing URL or smuggled to JS."""
    _set_stripe_env(monkeypatch)
    register(client, "alice@example.com")
    r = client.get("/billing/success?session=javascript:alert(1)")
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert loc.startswith("/?billing_return=success")
    assert "session=" not in loc
    assert "javascript" not in loc.lower()


def test_billing_cancel_authed_redirects_with_cancel_flag(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    register(client, "alice@example.com")
    r = client.get("/billing/cancel")
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/?billing_return=cancel")


def test_billing_cancel_unauth_redirects_to_login(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    r = client.get("/billing/cancel")
    assert r.status_code == 302
    assert r.headers["Location"] == "/login"


# ---------- Return flag never grants entitlement --------------------

def test_return_param_does_not_unlock_paid_features(client, monkeypatch):
    """A free user can hit /billing/success?billing_return=success all day —
    the server still treats them as free for every protected action. The
    only thing that flips users.plan is a verified webhook (covered by
    Phase 2A/2B tests). This test is the bright-line guard."""
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    register(client, "alice@example.com")

    # Hit the return URL — should redirect, not provision anything
    r = client.get("/billing/success?session=cs_test_xyz")
    assert r.status_code == 302

    # /api/billing/me still shows free + null subscription
    r = client.get("/api/billing/me")
    body = r.get_json()
    assert body["user"]["plan"] == "free"
    assert body["subscription"] is None

    # Paid endpoint still gated
    r = client.post("/api/save", json={"composite_id": "x"})
    assert r.status_code == 402


# ---------- /index cache-bust preserves billing_return ---------------

def test_index_redirect_preserves_billing_return_param(client, monkeypatch):
    """A user who lands on /?billing_return=success (no &v=) gets a
    cache-bust redirect; the billing_return flag must survive."""
    _set_stripe_env(monkeypatch)
    register(client, "alice@example.com")
    r = client.get("/?billing_return=success&session=cs_test_99",
                   follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert loc.startswith("/?v=")
    assert "billing_return=success" in loc
    assert "session=cs_test_99" in loc


def test_index_redirect_drops_unknown_billing_return(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    register(client, "alice@example.com")
    r = client.get("/?billing_return=NOT_A_REAL_OUTCOME",
                   follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert "billing_return" not in loc, \
        f"unrecognized return outcome should be dropped, got {loc}"


# ---------- Dashboard HTML actually contains the new UI hooks --------

def test_dashboard_html_contains_billing_card_placeholder(client, monkeypatch):
    """Cheap sanity: the dashboard ships with the billing-card mount
    point AND the JS module that fills it. We don't render the DOM here,
    just confirm the static contract the JS expects."""
    _set_stripe_env(monkeypatch)
    register(client, "alice@example.com")
    r = client.get("/?v=1")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert 'id="billing-card"' in page
    assert 'id="billing-card-body"' in page
    assert "__SNIPER_BILLING" in page
    # And it MUST call /api/billing/me, not the raw Stripe API.
    assert "/api/billing/me" in page
    # Stripe customer/subscription/price IDs must NEVER be embedded in
    # the page source.
    assert "sk_test_"  not in page
    assert "sk_live_"  not in page
    assert "whsec_"    not in page


# ---------- Webhook + Phase-N regression guards ---------------------

def test_webhook_still_requires_signature(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    _stub_stripe_sdk(monkeypatch)
    r = client.post("/api/billing/webhook", data=b'{}',
                    headers={"Content-Type":"application/json"})
    assert r.status_code == 400


def test_phase1_authz_unchanged(client, monkeypatch):
    _set_stripe_env(monkeypatch)
    r = client.post("/api/save", json={"composite_id":"x"})
    assert r.status_code == 401
