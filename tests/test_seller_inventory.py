"""
tests/test_seller_inventory.py — original inventory from dealers and
wholesalers (owner-approved accounts, CSV import of their own cars) and buyer
reports about FlipFirst listings. SIMULATED sellers and cars; SQLite and
PostgreSQL.
"""
from __future__ import annotations

import pytest

from tests.conftest import login, logout, make_admin, register
from tests.test_matching_and_history import make_user, q, score_all, world  # noqa: F401
from tests.test_seller_listings import ATTEST, car, mail, seller  # noqa: F401


def vin(serial: int) -> str:
    import vin as vin_mod
    base = f"4T1BF1FK0FU{serial:06d}"
    return base[:8] + vin_mod.vin_check_digit(base) + base[9:]


HEAD = "vin,year,make,model,trim,mileage,price,zip,title_status,mechanical_issues,cosmetic_issues,description"


def row(serial, price=6800, **kw):
    f = {"vin": vin(serial), "year": 2015, "make": "Toyota", "model": "Camry", "trim": "SE",
         "mileage": 120000 + serial, "price": price, "zip": "35401", "title_status": "clean",
         "mechanical_issues": "", "cosmetic_issues": "", "description": "dealer trade-in", **kw}
    return ",".join(str(f[k]) for k in HEAD.split(","))


def approve(email="dealer@example.com", name="Druid City Wholesale", max_active=20):
    import seller_listings as sl
    assert sl._main(["approve-dealer", email, "--name", name, "--max-active", str(max_active),
                     "--note", "AL dealer license seen (test)"]) == 0


def imp(client, lines, remove_missing=False, attest=ATTEST):
    return client.post("/api/seller/listings/import", json={
        "csv": "\n".join([HEAD, *lines]) + "\n", "attest": attest, "remove_missing": remove_missing})


def test_only_approved_dealers_import_and_their_cars_are_labeled_dealer(client, world, mail):
    seller(client, "dealer@example.com", box=mail)
    assert imp(client, [row(1)]).status_code == 403                 # not approved yet
    approve()
    lim = client.get("/api/seller/listings").get_json()
    assert lim["seller"] == {"kind": "dealer", "business_name": "Druid City Wholesale"}
    assert lim["limits"]["active"] == 20
    r = imp(client, [row(1), row(2, price=7400)])
    assert r.status_code == 200, r.get_json()
    assert (r.get_json()["created"], r.get_json()["errors"]) == (2, 0)
    score_all()
    cid = r.get_json()["rows"][0]["id"]
    listing = q("SELECT is_dealer, extras FROM listings WHERE composite_id = ?", (f"direct:{cid}",))[0]
    assert listing["is_dealer"] and '"seller_type": "dealer"' in listing["extras"]
    make_user(client, "buyer@example.com", plan="starter", topic=False)
    login(client, "buyer@example.com")
    d = client.get(f"/api/deals/direct:{cid}").get_json()
    assert "Druid City Wholesale, a dealer approved by FlipFirst" in d["provenance"]
    assert "dealer" in d["risk_flags"] and "extras" not in d


def test_import_validates_every_row_and_refreshes_by_vin(client, world, mail):
    seller(client, "dealer@example.com", box=mail)
    approve()
    r = imp(client, [row(1), row(2), row(3, year=1975), row(4, description="=HYPERLINK(\"x\")"),
                     "," + row(5).split(",", 1)[1], row(1)]).get_json()
    by = {x["row"]: x for x in r["rows"]}
    assert (r["created"], r["errors"]) == (2, 4)
    assert "year must be between" in by[4]["errors"][0]
    assert "can't start with" in by[5]["errors"][0]
    assert "VIN is required" in by[6]["errors"][0]
    assert "twice" in by[7]["errors"][0]
    again = imp(client, [row(1, price=6500), row(9)], remove_missing=True).get_json()
    assert (again["created"], again["updated"], again["removed"]) == (1, 1, 1)
    live = q("SELECT vin FROM seller_listings WHERE status IN ('pending', 'active')")
    assert sorted(x["vin"] for x in live) == sorted([vin(1), vin(9)])
    assert q("SELECT price FROM listings WHERE title LIKE '%Camry%' AND source = 'direct' "
             "AND odometer = 120001")[0]["price"] == 6500


@pytest.mark.parametrize("body, status, needle", [
    ({"csv": "vin,year\n1,2015\n"}, 400, "missing columns"),
    ({"csv": HEAD + "\n"}, 400, "no vehicle rows"),
    ({"csv": HEAD + "\n" + "x" * 300_000}, 413, "over 256 KB"),
    ({"csv": "\n".join([HEAD] + [row(i) for i in range(201)])}, 413, "at most 200"),
])
def test_import_rejects_bad_files(client, world, mail, body, status, needle):
    seller(client, "dealer@example.com", box=mail)
    approve()
    r = client.post("/api/seller/listings/import", json={**body, "attest": ATTEST})
    assert r.status_code == status and needle in r.get_json()["error"], r.get_json()


def test_import_needs_the_three_confirmations(client, world, mail):
    seller(client, "dealer@example.com", box=mail)
    approve()
    r = imp(client, [row(1)], attest={**ATTEST, "details_accurate": False})
    assert r.status_code == 400 and "confirm all three" in r.get_json()["error"]


def test_private_sellers_keep_self_serve_limits_and_revoked_dealers_lose_theirs(client, world, mail):
    import seller_listings as sl
    seller(client, "dealer@example.com", box=mail)
    approve(max_active=4)
    for i in range(4):
        assert client.post("/api/seller/listings", json=car(vin=vin(10 + i))).status_code == 201
    assert client.post("/api/seller/listings", json=car(vin=vin(20))).status_code == 429
    assert sl._main(["revoke-dealer", "dealer@example.com"]) == 0
    assert client.get("/api/seller/listings").get_json()["limits"]["active"] == sl.MAX_ACTIVE_PER_USER


def test_buyers_report_seller_listings_and_the_owner_resolves(client, world, mail, capsys):
    import seller_listings as sl
    seller(client, box=mail)
    cid = client.post("/api/seller/listings", json=car()).get_json()["composite_id"]
    logout(client)
    make_user(client, "buyer@example.com", plan="free", topic=False)
    login(client, "buyer@example.com")
    r = client.post(f"/api/deals/{cid}/report", json={"reason": "sold", "note": "sold Saturday"})
    assert r.status_code == 201
    assert client.post(f"/api/deals/{cid}/report", json={"reason": "sold"}).status_code == 409
    assert client.post(f"/api/deals/{cid}/report", json={"reason": "meh"}).status_code == 400
    assert client.post("/api/deals/craigslist:peer0/report", json={"reason": "scam"}).status_code == 400
    assert sl._main(["reports"]) == 0
    assert "sold Saturday" in capsys.readouterr().out
    import pilot_report
    assert pilot_report.report(7)["sellers"]["open_reports"] == 1
    register(client, "boss@example.com")
    make_admin("boss@example.com")
    rid = r.get_json()["id"]
    assert sl._main(["resolve", str(rid), "--remove"]) == 1          # --remove needs --admin
    assert sl._main(["resolve", str(rid), "--remove", "--admin", "boss@example.com"]) == 0
    assert q("SELECT 1 FROM listings WHERE composite_id = ?", (cid,)) == []
    assert q("SELECT resolution FROM listing_reports WHERE id = ?", (rid,))[0]["resolution"].startswith(
        "listing removed")


def test_account_deletion_removes_profile_and_reports(client, world, mail):
    import accounts
    seller(client, "dealer@example.com", box=mail)
    approve()
    cid = client.post("/api/seller/listings", json=car()).get_json()["composite_id"]
    logout(client)
    make_user(client, "buyer@example.com", plan="free", topic=False)
    login(client, "buyer@example.com")
    client.post(f"/api/deals/{cid}/report", json={"reason": "scam"})
    logout(client)
    accounts.delete("buyer@example.com", confirm=True)
    accounts.delete("dealer@example.com", confirm=True)
    assert q("SELECT 1 FROM listing_reports") == [] and q("SELECT 1 FROM seller_profiles") == []
