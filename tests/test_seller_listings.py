"""
tests/test_seller_listings.py — seller-submitted listings: authorization,
validation, abuse limits, pipeline integration, privacy and removal.
"""
from __future__ import annotations

import pytest

from tests.conftest import login, logout, make_admin, register, set_plan
from tests.test_matching_and_history import make_user, q, score_all, world  # noqa: F401

ATTEST = {"authorized_to_sell": True, "details_accurate": True,
          "share_contact_with_buyers": True}


def car(**kw):
    return {"year": 2015, "make": "Toyota", "model": "Camry", "trim": "SE",
            "mileage": 128000, "price": 6800, "zip": "35473", "title_status": "clean",
            "description": "One owner, runs great.", "attest": dict(ATTEST), **kw}


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setenv("SNIPER_SELLER_SUBMISSIONS", "1")


def _seller(client, email="seller@example.com"):
    register(client, email)
    login(client, email)


def test_off_by_default(client, world, monkeypatch):
    monkeypatch.delenv("SNIPER_SELLER_SUBMISSIONS", raising=False)
    _seller(client)
    assert client.get("/api/seller/listings").get_json()["enabled"] is False
    assert client.post("/api/seller/listings", json=car()).status_code == 403


def test_requires_sign_in(client, world, on):
    assert client.post("/api/seller/listings", json=car()).status_code == 401


def test_validation_and_attestation(client, world, on):
    _seller(client)
    r = client.post("/api/seller/listings", json=car(year=1960, price=50, zip="00000",
                                                     mileage=-1, attest={}))
    errs = " ".join(r.get_json()["errors"])
    assert r.status_code == 400
    for part in ("year", "price", "ZIP", "mileage", "you may sell this car"):
        assert part in errs
    r = client.post("/api/seller/listings", json=car(vin="1HGCM82633A004353"))   # bad check digit
    assert r.status_code == 400 and "check digit" in r.get_json()["error"]


def test_submission_flows_through_valuation_and_alerts(client, world, on):
    buyer = make_user(client, "buyer@example.com", searches=[("camry", {"max_price": 9000})])
    _seller(client)
    r = client.post("/api/seller/listings", json=car(vin="1HGCM82633A004352"))
    assert r.status_code == 201
    cid = r.get_json()["composite_id"]
    row = q("SELECT * FROM listings WHERE composite_id = ?", (cid,))[0]
    assert row["source"] == "direct" and row["vehicle_key"] == "vin:1HGCM82633A004352"
    assert row["location_source"] == "zip_centroid" and not row["is_dealer"]
    score_all()
    nm = q("SELECT * FROM near_misses WHERE composite_id = ?", (cid,))[0]
    assert nm["evidence_class"] == "asking_comparables" and nm["value_low"]
    assert q("SELECT 1 FROM user_matches WHERE user_id = ? AND composite_id = ?", (buyer, cid))
    # duplicate active VIN refused
    assert client.post("/api/seller/listings", json=car(vin="1HGCM82633A004352")).status_code == 409
    mine = client.get("/api/seller/listings").get_json()["listings"]
    assert len(mine) == 1 and mine[0]["confidence"]


def test_contact_only_for_paid_signed_in_users(client, world, on):
    _seller(client)
    cid = client.post("/api/seller/listings", json=car()).get_json()["composite_id"]
    score_all()
    logout(client)
    register(client, "free@example.com"); login(client, "free@example.com")
    d = client.get(f"/api/deals/{cid}").get_json()
    assert d["seller_contact"] == {"available": False, "why": "upgrade to see the seller's contact"}
    assert "seller@example.com" not in str(d)
    assert "not independently verified" in d["provenance"]
    set_plan("free@example.com", "starter")
    d = client.get(f"/api/deals/{cid}").get_json()
    assert d["seller_contact"] == {"available": True, "email": "seller@example.com"}
    feed = client.get("/api/deals?feed=trusted").get_json()
    assert "seller@example.com" not in str(feed)         # never in feeds


def test_price_update_records_history_and_rescoring(client, world, on):
    _seller(client)
    sid = client.post("/api/seller/listings", json=car(title_status="rebuilt")).get_json()["id"]
    score_all()
    r = client.patch(f"/api/seller/listings/{sid}", json={"price": 5900})
    assert r.status_code == 200 and r.get_json()["price_changed"]
    hist = [h["price"] for h in q("SELECT price FROM listing_prices WHERE composite_id = ? "
                                  "ORDER BY id", (f"direct:{sid}",))]
    assert hist == [6800, 5900]
    desc = q("SELECT description, is_salvage FROM listings WHERE composite_id = ?",
             (f"direct:{sid}",))[0]
    assert desc["description"].count("rebuilt title") == 1 and desc["is_salvage"]


def test_only_owner_or_admin_can_change_or_remove(client, world, on):
    _seller(client)
    sid = client.post("/api/seller/listings", json=car()).get_json()["id"]
    logout(client)
    _seller(client, "other@example.com")
    assert client.patch(f"/api/seller/listings/{sid}", json={"price": 1000}).status_code == 404
    assert client.delete(f"/api/seller/listings/{sid}").status_code == 404
    logout(client)
    register(client, "admin@example.com"); make_admin("admin@example.com")
    login(client, "admin@example.com")
    assert client.delete(f"/api/seller/listings/{sid}").status_code == 200
    stub = q("SELECT * FROM seller_listings WHERE id = ?", (sid,))[0]
    assert stub["status"] == "removed" and stub["removed_by"] == "admin"


def test_removal_deletes_listing_and_everything_derived(client, world, on):
    make_user(client, "buyer2@example.com", searches=[("camry", {"max_price": 9000})])
    _seller(client)
    sid = client.post("/api/seller/listings", json=car(vin="1HGCM82633A004352")).get_json()["id"]
    cid = f"direct:{sid}"
    score_all()
    assert q("SELECT 1 FROM user_matches WHERE composite_id = ?", (cid,))
    assert client.delete(f"/api/seller/listings/{sid}").get_json()["removed"]
    for t in ("listings", "near_misses", "user_matches", "listing_prices", "alerts"):
        assert not q(f"SELECT 1 FROM {t} WHERE composite_id = ?", (cid,)), t
    assert not q("SELECT 1 FROM jobs WHERE status = 'pending' AND payload LIKE ?",
                 (f'%"{cid}"%',))
    stub = q("SELECT vin, contact_email, status FROM seller_listings WHERE id = ?", (sid,))[0]
    assert stub == {"vin": None, "contact_email": None, "status": "removed"}
    assert client.delete(f"/api/seller/listings/{sid}").status_code == 404
    # the VIN can be listed again after removal
    assert client.post("/api/seller/listings", json=car(vin="1HGCM82633A004352")).status_code == 201


def test_abuse_limits(client, world, on, monkeypatch):
    import seller_listings as sl
    monkeypatch.setattr(sl, "MAX_ACTIVE_PER_USER", 2)
    monkeypatch.setattr(sl, "MAX_NEW_PER_USER_PER_DAY", 3)
    _seller(client)
    a = client.post("/api/seller/listings", json=car()).get_json()["id"]
    client.post("/api/seller/listings", json=car(price=7000))
    assert client.post("/api/seller/listings", json=car(price=7100)).status_code == 429
    client.delete(f"/api/seller/listings/{a}")
    client.post("/api/seller/listings", json=car(price=7200))          # 3rd today
    client.delete(f"/api/seller/listings/{a + 2}")
    r = client.post("/api/seller/listings", json=car(price=7300))      # 4th today
    assert r.status_code == 429 and "today" in r.get_json()["error"]
    monkeypatch.setattr(sl, "MAX_NEW_PER_USER_PER_DAY", 99)
    monkeypatch.setattr(sl, "MAX_NEW_PER_DAY", 3)                      # global cap
    logout(client)
    _seller(client, "s2@example.com")
    assert client.post("/api/seller/listings", json=car(price=7400)).status_code == 429
