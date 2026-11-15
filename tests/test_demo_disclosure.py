"""
tests/test_demo_disclosure.py — synthetic demo listings can never pass for
live marketplace inventory.
  * The app is told to show a "DEMO DATA — NOT LIVE MARKETPLACE LISTINGS"
    banner while demo listings exist (/api/me demo_data). The browser smoke
    test checks that the banner is visible and that no card links out.
  * Demo listings use a dedicated source and carry no external URL.
  * preflight refuses any beta while demo data is in the database.
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import register
from tests.test_matching_and_history import q, world  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _demo_listing(sid="deal", price=7600):
    from sources.base import NormalizedListing
    return NormalizedListing(
        source="demo", source_id=sid, url="", title=f"2015 Toyota Camry SE - ${price:,}",
        price=price, year=2015, make="Toyota", model="Camry", odometer=128000,
        description="clean title", state="AL", location="Tuscaloosa", zip="35401",
        is_cash_only=True)


def test_a_real_database_reports_no_demo_data(client):
    assert client.get("/api/me?optional=1").get_json()["demo_data"] is False
    register(client, "a@example.com")
    assert client.get("/api/me").get_json()["demo_data"] is False


def test_demo_listings_turn_the_banner_on_and_carry_no_external_link(client, world):
    world.ingest([_demo_listing()])
    assert client.get("/api/me?optional=1").get_json()["demo_data"] is True
    register(client, "b@example.com")
    assert client.get("/api/me").get_json()["demo_data"] is True
    assert q("SELECT source, url FROM listings WHERE source = 'demo'") == [{"source": "demo", "url": ""}]


def test_preflight_refuses_any_beta_while_demo_data_exists(client, world):
    import preflight
    world.ingest([_demo_listing()])
    r = preflight.run()
    (demo,) = [c for c in r["checks"] if c["check"] == "no demo data"]
    assert demo["status"] == "fail" and demo["blocks"] == "controlled_free_beta"
    assert preflight.LEVELS.index(r["ready_for"]) < preflight.LEVELS.index("controlled_free_beta")


def test_the_demo_seed_never_uses_a_marketplace_name_or_link():
    seed = (ROOT / "tests" / "browser" / "seed_demo.py").read_text()
    assert 'source="demo"' in seed and 'url=""' in seed and "craigslist" not in seed
    app = (ROOT / "static" / "app.js").read_text()
    assert "DEMO DATA — NOT LIVE MARKETPLACE LISTINGS" in app
    assert 'demo: "Demo data (synthetic)"' in app and "if (me.demo_data) demoBanner();" in app
