"""
tests/test_flipfirst_frontend.py — server-side guarantees behind the
FlipFirst redesign: only static/ is web-served (the old static_folder=ROOT
exposed every file next to the code), assets are versioned and cacheable,
the new saved feed values deals exactly like the other feeds, and the
financial breakdown the deal page shows adds up. Runs on SQLite and Postgres.
"""
from __future__ import annotations

import re

import pytest

from tests.conftest import login, register
from tests.test_matching_and_history import L, make_user, score_all, world  # noqa: F401


# ---------- static files: only static/, versioned, cacheable -------------

def test_only_the_static_folder_is_web_served(client):
    import server
    root_name = server.ROOT.name
    for path in (f"/{root_name}/server.py", f"/{root_name}/listings.db",
                 f"/{root_name}/deploy/env.staging.example", "/static/../server.py",
                 "/static/%2e%2e/server.py", "/server.py", "/listings.db"):
        r = client.get(path)
        assert r.status_code == 404 or b"def api_" not in r.data, path
        assert not (r.status_code == 200 and r.data.startswith(b"SQLite format")), path
    assert server.app.static_url_path == "/static"


def test_pages_reference_versioned_assets_that_are_served_and_cacheable(client):
    import server
    page = client.get("/login").get_data(as_text=True)
    assert "__ASSET_V__" not in page
    refs = re.findall(r'(/static/[^"?#]+)\?v=([0-9a-f]+)', page)
    assert refs and all(v == server.ASSET_V for _, v in refs)
    for path, v in set(refs):
        r = client.get(f"{path}?v={v}")
        assert r.status_code == 200, path
        assert "immutable" in r.headers["Cache-Control"] and "max-age=31536000" in r.headers["Cache-Control"]
    assert client.get("/static/ff.css").headers["Cache-Control"] == "public, max-age=3600"
    assert client.get("/login").headers["Cache-Control"] == "no-store"      # pages never cached


def test_brand_assets(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200 and r.mimetype == "image/x-icon" and r.data[:4] == b"\x00\x00\x01\x00"
    m = client.get("/static/manifest.webmanifest")
    assert m.status_code == 200 and m.mimetype == "application/manifest+json"
    man = m.get_json(force=True)
    assert man["name"] == "FlipFirst" and man["start_url"] == "/m"
    for icon in man["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]
    assert client.get("/static/icons.svg").status_code == 200


def test_signed_out_api_me_optional_is_not_an_error(client):
    r = client.get("/api/me?optional=1")
    assert r.status_code == 200 and r.get_json()["user"] is None
    assert r.get_json()["registration"] in ("open", "invite")
    assert client.get("/api/me").status_code == 401                         # contract unchanged
    register(client, "me@example.com")
    assert client.get("/api/me?optional=1").get_json()["user"]["email"] == "me@example.com"


def test_consumer_pages_carry_the_flipfirst_brand(client):
    assert "FlipFirst" in client.get("/login").get_data(as_text=True)
    register(client, "brand@example.com")
    page = client.get("/m").get_data(as_text=True)
    assert "<title>FlipFirst</title>" in page and "Car Sniper" not in page


# ---------- saved feed: same valuation as every other feed ---------------

def test_saved_feed_matches_the_trusted_feed_and_hides_dismissed(client, world):
    world.ingest([L("deal", 8100), L("deal2", 8200)])
    score_all()
    make_user(client, "saver@example.com", plan="pro", topic=False)
    login(client, "saver@example.com")
    trusted = {d["composite_id"]: d for d in client.get("/api/deals?feed=trusted").get_json()}
    cid = next(c for c in trusted if c.endswith(":deal"))
    other = next(c for c in trusted if c.endswith(":deal2"))
    assert client.get("/api/deals?feed=saved").get_json() == []
    assert client.post(f"/api/deals/{cid}/action", json={"action": "save"}).status_code == 200
    assert client.post(f"/api/deals/{other}/action", json={"action": "negotiating"}).status_code == 200
    saved = client.get("/api/deals?feed=saved").get_json()
    assert {d["composite_id"] for d in saved} == {cid, other}
    s = next(d for d in saved if d["composite_id"] == cid)
    for k in ("price", "value_low", "value_mid", "value_high", "confidence", "profit", "opportunity"):
        assert s[k] == trusted[cid][k], k                                    # identical numbers
    assert {d["user_status"] for d in saved} == {"saved", "negotiating"}
    client.post(f"/api/deals/{other}/action", json={"action": "dismiss"})
    assert [d["composite_id"] for d in client.get("/api/deals?feed=saved").get_json()] == [cid]


def test_saved_feed_is_per_user(client, world):
    world.ingest([L("deal", 8100)]); score_all()
    make_user(client, "a@example.com", plan="pro", topic=False)
    make_user(client, "b@example.com", plan="pro", topic=False)
    login(client, "a@example.com")
    cid = client.get("/api/deals?feed=trusted").get_json()[0]["composite_id"]
    client.post(f"/api/deals/{cid}/action", json={"action": "save"})
    client.post("/api/auth/logout", json={})
    login(client, "b@example.com")
    assert client.get("/api/deals?feed=saved").get_json() == []


# ---------- financial breakdown adds up ------------------------------------

@pytest.mark.parametrize("settings", [None, {"negotiation_pct": 7, "sales_tax_pct": 4, "title_registration": 95,
                                             "transport_per_mile": 0.4, "holding_per_day": 8, "misc": 60}])
def test_breakdown_lines_sum_to_invested_and_profit(client, world, settings):
    """The deal page shows purchase + repairs + fees/transport/taxes + holding
    = total invested, and resale − invested = profit (base case)."""
    world.ingest([L("deal", 8100, zip="35401")]); score_all()
    make_user(client, "math@example.com", plan="pro", topic=False)
    login(client, "math@example.com")
    client.post("/api/me/territory", json={"home_zip": "35473", "radius_mi": 150})
    if settings:
        assert client.post("/api/me/profit_settings", json=settings).status_code == 200
    cid = next(d["composite_id"] for d in client.get("/api/deals?feed=trusted").get_json()
               if d["composite_id"].endswith(":deal"))
    p = client.get(f"/api/deals/{cid}").get_json()["profit_detail"]
    base, costs = p["scenarios"]["base"], p["costs"]
    repairs = costs.get("mechanical", 0) + costs.get("recon", 0)
    fees = sum(costs.get(k, 0) for k in ("selling_fees", "transport", "transport_per_mile",
                                         "title_registration", "sales_tax_pct", "misc"))
    shown_total = base["purchase"] + repairs + fees + base["holding"]
    assert abs(shown_total - base["invested"]) <= 2                       # per-line rounding only
    assert abs((base["resale"] - base["invested"]) - base["profit"]) <= 1
    assert set(costs) <= {"mechanical", "recon", "selling_fees", "transport", "transport_per_mile",
                          "title_registration", "sales_tax_pct", "misc", "holding"}
    if settings:
        assert base["purchase"] == round(8100 * 0.93)


# ---------- alert copy is brand-neutral and honest ---------------------------

def test_alert_titles_have_no_emoji_and_say_below_market():
    import notifications
    t, body = notifications.format_deal({"year": 2015, "make": "Toyota", "model": "Camry", "price": 8100,
                                         "discount_pct": 22, "value_low": 9800, "value_high": 10600,
                                         "evidence_class": "asking_comparables", "comp_n": 6,
                                         "est_profit": 900, "url": "https://example.com/x"})
    assert t == "2015 Toyota Camry · $8,100 · 22% below market"
    assert "estimate, not guaranteed" in body
    t2, _ = notifications.format_deal({"year": 2015, "make": "Toyota", "model": "Camry", "price": 7600,
                                       "previous_price": 8100, "discount_pct": 25, "event": "price_drop"})
    assert t2.startswith("Price drop · 2015 Toyota Camry $8,100 → $7,600")
    assert not re.search(r"[\U0001F300-\U0001FAFF]", t + t2)


def test_an_auction_price_is_called_a_current_bid():
    """A running auction's price is its current bid: alert titles and the
    deal card/detail must never call it an asking price."""
    import notifications
    from pathlib import Path
    t, _ = notifications.format_deal({"year": 2015, "make": "Toyota", "model": "Camry",
                                      "price": 6400, "discount_pct": 37, "is_auction": 1})
    assert t == "2015 Toyota Camry · current bid $6,400 · 37% below market"
    js = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text()
    assert 'metric__label">Asking<' not in js and js.count("${priceWord(d)}") >= 4
    assert 'const priceWord = d => (d.is_auction ? "Current bid" : "Asking");' in js
