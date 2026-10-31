"""
tests/test_beta_journey.py — the first beta end to end, with no paid data:
one flipper and one authorized seller (an owner-approved dealer). The
database has no comparables, so FlipFirst must never show a market value.
SIMULATED people and cars; SQLite and PostgreSQL.
"""
from __future__ import annotations

import pytest

from tests.conftest import login, logout
from tests.test_matching_and_history import make_user, q, score_all
from tests.test_seller_inventory import approve, imp, row
from tests.test_seller_listings import mail, seller  # noqa: F401


@pytest.fixture()
def no_paid_data(app, monkeypatch):
    import comps
    import sniper
    monkeypatch.setattr(comps, "raw_sold", lambda y, mk, md: ({}, 0))   # no sold-price lookups
    monkeypatch.setattr(sniper._config_mod, "reload", lambda: None)


def test_one_buyer_one_dealer_without_paid_data(client, no_paid_data, mail):
    # 1-2. a flipper enters a car by hand and evaluates its costs and risks
    make_user(client, "flipper@example.com", plan="free", topic=False)
    login(client, "flipper@example.com")
    car = {"year": 2012, "make": "Honda", "model": "Accord", "trim": "EX", "mileage": 150000,
           "asking_price": 5200, "zip": "35401", "title_status": "clean",
           "condition": "runs_drives", "description": "runs and drives, one owner",
           "resale_estimate": 7500, "costs": {"mechanical": 600}}
    a = client.post("/api/analyze", json=car).get_json()
    assert a["value_mid"] is None and a["valuation_basis"] == "user_estimate"
    assert a["decision"]["class"] == "worth_investigating"
    assert a["break_even_resale"] > 5200 and a["max_offer"]["expected"] > 0

    # 3, 5. save it, keep a note, record the actual purchase and sale
    oid = client.post("/api/opportunities", json={"input": car}).get_json()["id"]
    r = client.patch(f"/api/opportunities/{oid}", json={
        "notes": "rear tires worn", "status": "sold", "purchase_price": 5000,
        "purchased_on": "2026-10-02", "repair_spent": 450, "sale_price": 7300,
        "sold_on": "2026-10-19"}).get_json()
    assert r["realized"]["profit"] == 7300 - 5000 - 450
    logout(client)

    # 6-7. an approved dealer imports authorized inventory; the car goes live
    seller(client, "dealer@example.com", box=mail)
    approve(name="Northport Wholesale")
    res = imp(client, [row(77, price=6400)]).get_json()
    assert res["created"] == 1, res
    cid = f"direct:{res['rows'][0]['id']}"
    score_all()
    logout(client)

    # 4, 8. the flipper finds it, analyzes it with their own numbers, compares, reports
    login(client, "flipper@example.com")
    d = client.get(f"/api/deals/{cid}").get_json()
    assert "Northport Wholesale, a dealer approved by FlipFirst" in d["provenance"]
    assert d.get("value_mid") is None                       # no comparables: no market value
    second = client.post("/api/opportunities", json={"input": {
        "composite_id": cid, "year": 2015, "make": "Toyota", "model": "Camry",
        "asking_price": 6400, "mileage": 120077, "resale_estimate": 8200}}).get_json()
    pair = [client.get(f"/api/opportunities/{i}").get_json() for i in (oid, second["id"])]
    assert [p["valuation_basis"] for p in pair] == ["user_estimate", "user_estimate"]
    assert all(p["break_even_resale"] for p in pair)
    assert client.post(f"/api/deals/{cid}/report",
                       json={"reason": "wrong_details", "note": "mileage differs"}).status_code == 201

    import pilot_report
    r = pilot_report.report(7)
    assert r["analyze"]["deals_saved"] == 2 and r["analyze"]["outcomes_recorded"] == 1
    assert r["sellers"]["approved_dealers"] == 1 and r["sellers"]["open_reports"] == 1
    assert q("SELECT COUNT(*) AS n FROM listings WHERE source = 'direct'")[0]["n"] == 1
