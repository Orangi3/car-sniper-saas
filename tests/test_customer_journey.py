"""
tests/test_customer_journey.py — the minimum customer workflow, end to end,
through the HTTP API in one session:

  visitor -> invite-only registration -> paid plan through a Stripe
  TEST-mode webhook (real signature check) -> onboarding (territory, alert
  channel) -> discovery -> deal analysis -> saved search -> a relevant
  alert delivered -> return visit (alert tray, deal detail) -> logout

SYNTHETIC listings and a stubbed Stripe SDK: no provider, no Stripe call,
no real data. It proves the pieces work together; it is not evidence about
real marketplace coverage or live billing.
"""
from __future__ import annotations

import json
import time

from tests.conftest import login, logout
from tests.test_billing import _make_event, _set_stripe_env, _sign, _stub_stripe_sdk
from tests.test_matching_and_history import L, q, score_all, world  # noqa: F401
from tests.test_send_time_check import _deliver, pushes  # noqa: F401

EMAIL, PASSWORD, ZIP = "first.customer@example.com", "pilot-pass-123", "35401"


def _car(sid, price, odo):
    return L(sid, price, odo=odo, zip=ZIP)        # Tuscaloosa, inside the territory


def test_first_customer_journey(client, world, pushes, monkeypatch):
    import auth
    import invites

    # 1. visitor: an invite-only pilot sends anonymous visitors to sign-in; data needs an account
    for path in ("/", "/m"):
        r = client.get(path)
        assert (r.status_code, r.headers.get("Location")) == (302, "/login"), path
    assert client.get("/login").status_code == 200
    assert client.get("/api/deals").status_code == 401

    # 2. invite-only registration: refused without an invitation, accepted with one
    monkeypatch.setattr(auth, "ALLOW_REGISTRATION", False)
    body = {"email": EMAIL, "password": PASSWORD}
    assert client.post("/api/auth/register", json=body).status_code == 403
    ((_, token),) = invites.issue(1)
    assert client.post("/api/auth/register", json={**body, "invite": token}).status_code \
        in (200, 201)
    me = client.get("/api/me").get_json()["user"]
    assert me["email"] == EMAIL and me["plan"] == "free"
    assert client.get("/m").status_code == 200       # signed in: the app itself

    # 3. subscription: Stripe test-mode checkout completion, signature verified
    _set_stripe_env(monkeypatch)
    sub = {"id": "sub_journey", "customer": "cus_journey", "status": "active",
           "current_period_end": int(time.time()) + 30 * 86400,
           "cancel_at_period_end": False,
           "items": {"data": [{"price": {"id": "price_starter_test"}}]}}
    _stub_stripe_sdk(monkeypatch, skip_signature_check=False,
                     subscription_retrieve=lambda sid: sub if sid == "sub_journey" else None)
    uid = auth.get_user_by_email(EMAIL).id
    event = _make_event("checkout.session.completed", {
        "id": "cs_journey", "mode": "subscription", "client_reference_id": str(uid),
        "metadata": {"user_id": str(uid), "plan": "starter"},
        "customer": "cus_journey", "subscription": "sub_journey"})
    payload = json.dumps(event).encode()
    tampered = client.post("/api/billing/webhook", data=payload, headers={
        "Content-Type": "application/json", "Stripe-Signature": _sign(payload, "whsec_wrong")})
    assert tampered.status_code == 400
    r = client.post("/api/billing/webhook", data=payload, headers={
        "Content-Type": "application/json", "Stripe-Signature": _sign(payload, "whsec_test_dummy")})
    assert r.status_code == 200 and r.get_json()["outcome"] == "handled"
    assert client.get("/api/me").get_json()["user"]["plan"] == "starter"

    # 4. onboarding: buying territory and a push channel
    assert client.post("/api/me/territory", json={"home_zip": ZIP, "radius_mi": 50}).status_code \
        == 200
    assert client.post("/api/me/alert_channels", json={"ntfy_topic": "generate"}).status_code \
        == 200

    # 5. discovery: a market of comparables plus one car well below it
    world.ingest([_car(f"p{i}", p, 100_000 + 7_000 * i) for i, p in
                  enumerate([10000, 10400, 9800, 10200, 10600, 9900, 10100, 10300])])
    world.ingest([_car("d1", 8100, 128_000)])
    score_all()
    deals = client.get("/api/deals?feed=trusted").get_json()
    d1 = [d for d in deals if d["composite_id"] == "craigslist:d1"]
    assert d1, [d["composite_id"] for d in deals]
    assert d1[0]["distance_mi"] is not None and d1[0]["distance_mi"] <= 50

    # 6. deal analysis of a car the customer found elsewhere
    a = client.post("/api/analyze", json={"year": 2015, "make": "Toyota", "model": "Camry",
                                          "mileage": 128_000, "asking_price": 8100,
                                          "zip": ZIP, "title_status": "clean",
                                          "condition": "runs_drives"})
    assert a.status_code == 200, a.get_data(as_text=True)
    analysis = a.get_json()
    assert analysis["comp_n"] >= 3 and analysis["value_mid"] > 8100
    assert analysis["discount_pct"] > 10          # the opportunity is well below market
    assert analysis["evidence_class"] != "insufficient" and analysis["confidence"]

    # 7. saved search
    r = client.post("/api/saved_searches", json={"name": "camry", "notify_enabled": True,
                                                 "filter": {"model": "camry"}})
    assert r.status_code == 200, r.get_json()

    # 8. a relevant alert: a new matching car arrives and the push goes out
    world.ingest([_car("d2", 7600, 126_000)])
    score_all()
    _deliver()
    titles = [p["title"] for p in pushes["sent"]]
    assert len(titles) == 1 and "$7,600" in titles[0], titles

    # 9. return visit: log out and back in; the tray and the deal detail show it
    logout(client)
    assert client.get("/api/me/alerts").status_code == 401
    login(client, EMAIL, PASSWORD)
    tray = client.get("/api/me/alerts").get_json()
    assert [t["composite_id"] for t in tray] == ["craigslist:d2"]
    assert tray[0]["current_price"] == 7600 and tray[0]["current_price_confirmed"] is True
    assert client.get("/api/deals/craigslist:d2").status_code == 200

    # 10. logout ends the session
    logout(client)
    assert client.get("/api/me").status_code == 401
    assert q("SELECT COUNT(*) AS n FROM user_matches")[0]["n"] == 1
