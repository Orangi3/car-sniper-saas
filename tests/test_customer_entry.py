"""
tests/test_customer_entry.py — first-customer journey routing.

Customers must land in the current app (/m), whose deal cards carry the
full profit model, not the legacy owner dashboard whose "est. profit" is
the gross gap to the comparables average.
"""
from tests.conftest import login, make_admin, register


def test_customer_lands_in_the_customer_app(client):
    register(client, "new@example.com")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["Location"] == "/m"
    assert client.get("/m").status_code == 200


def test_checkout_return_reaches_the_customer_app(client):
    register(client, "payer@example.com")
    r = client.get("/billing/success?session=cs_test_1", follow_redirects=True)
    assert r.request.path == "/m" and r.request.args.get("billing_return") == "success"
    r = client.get("/?billing_return=bogus", follow_redirects=False)
    assert r.headers["Location"] == "/m"              # unknown outcomes dropped


def test_owner_keeps_the_console(client):
    register(client, "owner@example.com")
    make_admin("owner@example.com")
    login(client, "owner@example.com")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["Location"].startswith("/?v=")


def test_anonymous_visitor_sees_value_and_honest_terms(client):
    r = client.get("/", follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "underpriced used cars" in page
    assert "estimates from comparable listings, not" in page
    assert "agree to the source-of-data terms" not in page


def test_customer_app_offers_plan_management_and_sign_out(client):
    import re
    register(client, "m@example.com")
    page = client.get("/m").get_data(as_text=True)
    # the app's code ships as versioned static scripts; follow them
    srcs = re.findall(r'<script src="(/static/[^"?]+)\?v=([0-9a-f]+|dev)"', page)
    assert srcs, "app scripts not referenced"
    bundle = page
    for src, _ in srcs:
        r = client.get(src)
        assert r.status_code == 200, src
        bundle += r.get_data(as_text=True)
    for hook in ("/api/billing/me", "/api/billing/checkout", "/api/billing/portal",
                 "data-signout", "billing_return"):
        assert hook in bundle, hook
