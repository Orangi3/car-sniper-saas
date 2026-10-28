"""
tests/test_seller_listings.py — seller-submitted listings: contact
verification, lifecycle, authorization, privacy, abuse limits, concurrency
and pipeline integration. Runs on SQLite and (SNIPER_TEST_PG_URL) Postgres.
All submissions here are fixtures, not real sellers.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import login, logout, make_admin, register, set_plan
from tests.test_matching_and_history import make_user, q, score_all, world  # noqa: F401

ATTEST = {"authorized_to_sell": True, "details_accurate": True,
          "share_contact_with_buyers": True}


def car(**kw):
    return {"year": 2015, "make": "Toyota", "model": "Camry", "trim": "SE",
            "mileage": 128000, "price": 6800, "zip": "35473", "title_status": "clean",
            "mechanical_issues": "", "cosmetic_issues": "door ding",
            "description": "One owner, runs great.", "attest": dict(ATTEST), **kw}


@pytest.fixture()
def mail(monkeypatch):
    """SMTP stand-in: records messages; tokens are read from them like a
    person reading their inbox."""
    import mailer
    box = []
    monkeypatch.setenv("SNIPER_SELLER_SUBMISSIONS", "1")
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subj, body: box.append((to, body)))
    return box


def token_from(box, to=None):
    for addr, body in reversed(box):
        if to is None or addr == to:
            return re.search(r"#verify=([A-Za-z0-9_\-]+)", body).group(1)
    raise AssertionError("no email")


def seller(client, email="seller@example.com", verify=True, box=None):
    register(client, email)
    login(client, email)
    if verify:
        assert client.post("/api/seller/contact", json={}).status_code == 200
        r = client.post("/api/seller/contact/verify", json={"token": token_from(box, email)})
        assert r.status_code == 200, r.get_json()


def ids(client, feed="trusted"):
    return [d["composite_id"] for d in client.get(f"/api/deals?feed={feed}").get_json()]


# ---------- feature gate --------------------------------------------------

def test_off_by_default_and_requires_sign_in(client, world, monkeypatch):
    monkeypatch.delenv("SNIPER_SELLER_SUBMISSIONS", raising=False)
    assert client.post("/api/seller/listings", json=car()).status_code == 401
    register(client, "s@example.com"); login(client, "s@example.com")
    assert client.get("/api/seller/listings").get_json()["enabled"] is False
    assert client.post("/api/seller/listings", json=car()).status_code == 403


def test_no_email_delivery_means_nothing_publishes(client, world, monkeypatch):
    monkeypatch.setenv("SNIPER_SELLER_SUBMISSIONS", "1")
    import mailer
    monkeypatch.setattr(mailer, "configured", lambda: False)
    register(client, "s@example.com"); login(client, "s@example.com")
    r = client.post("/api/seller/listings", json=car())
    assert r.status_code == 201 and r.get_json()["status"] == "pending"
    v = client.post("/api/seller/contact", json={})
    assert v.status_code == 503 and "isn't set up" in v.get_json()["error"]
    assert not q("SELECT 1 FROM listings WHERE source = 'direct'")       # not published


# ---------- verification ----------------------------------------------------

def test_unverified_listing_is_pending_until_email_verified(client, world, mail):
    buyer = make_user(client, "buyer@example.com", searches=[("camry", {"max_price": 9000})])
    register(client, "s@example.com"); login(client, "s@example.com")
    r = client.post("/api/seller/listings", json=car()).get_json()
    assert r["status"] == "pending" and r["needs_contact_verification"]
    assert not q("SELECT 1 FROM listings WHERE source = 'direct'")
    assert client.get("/api/seller/listings").get_json()["listings"][0]["status"] == "pending"
    client.post("/api/seller/contact", json={})
    assert len(mail) == 1 and mail[0][0] == "s@example.com"
    assert "#verify=" in mail[0][1]                            # fragment: never sent to server logs
    v = client.post("/api/seller/contact/verify", json={"token": token_from(mail)}).get_json()
    assert v["verified"] and v["published"] == 1
    score_all()
    assert q("SELECT 1 FROM user_matches WHERE user_id = ? AND composite_id = ?",
             (buyer, r["composite_id"]))


def test_tokens_are_hashed_single_use_bound_and_expire(client, world, mail):
    import db
    register(client, "s@example.com"); login(client, "s@example.com")
    client.post("/api/seller/contact", json={})
    tok = token_from(mail)
    stored = q("SELECT token_hash FROM contact_verifications")[0]["token_hash"]
    assert stored != tok and len(stored) == 64                 # sha-256, not the token
    # another account can't use it
    logout(client); register(client, "x@example.com"); login(client, "x@example.com")
    assert client.post("/api/seller/contact/verify", json={"token": tok}).status_code == 400
    logout(client); login(client, "s@example.com")
    # expired
    with db.session() as c:
        c.execute("UPDATE contact_verifications SET expires_at = ?",
                  ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    r = client.post("/api/seller/contact/verify", json={"token": tok})
    assert r.status_code == 400 and "expired" in r.get_json()["error"]
    with db.session() as c:
        c.execute("UPDATE contact_verifications SET expires_at = ?, created_at = ?",
                  ((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                   (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()))
    assert client.post("/api/seller/contact/verify", json={"token": tok}).status_code == 200
    assert client.post("/api/seller/contact/verify", json={"token": tok}).status_code == 400  # reused


def test_changing_contact_revokes_verification_and_tokens(client, world, mail):
    seller(client, box=mail)
    sid = client.post("/api/seller/listings", json=car()).get_json()
    assert sid["status"] == "active"
    score_all()
    assert sid["composite_id"] in ids(client)
    old_tok_box = len(mail)
    import db
    with db.session() as c:                                    # past the 60 s resend gap
        c.execute("UPDATE contact_verifications SET created_at = ?",
                  ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),))
    client.post("/api/seller/contact", json={"email": "new@example.com"})
    assert client.get("/api/seller/contact").get_json() == {
        "email": "new@example.com", "verified": False, "email_delivery": True}
    assert sid["composite_id"] not in ids(client)              # hidden until re-verified
    assert client.get(f"/api/deals/{sid['composite_id']}").status_code == 410
    assert client.patch(f"/api/seller/listings/{sid['id']}", json={"price": 6000}).status_code == 409
    new_tok = token_from(mail, "new@example.com")
    assert len(mail) == old_tok_box + 1
    assert client.post("/api/seller/contact/verify", json={"token": new_tok}).status_code == 200
    assert sid["composite_id"] in ids(client)


def test_send_and_attempt_rate_limits(client, world, mail, monkeypatch):
    import seller_listings as sl
    register(client, "s@example.com"); login(client, "s@example.com")
    assert client.post("/api/seller/contact", json={}).status_code == 200
    assert client.post("/api/seller/contact", json={}).status_code == 429   # < 60 s
    monkeypatch.setattr(sl, "MAX_FAILED_VERIFY_PER_HOUR", 3)
    for _ in range(3):
        assert client.post("/api/seller/contact/verify", json={"token": "nope"}).status_code == 400
    r = client.post("/api/seller/contact/verify", json={"token": token_from(mail)})
    assert r.status_code == 429                                # locked out even with the right token


# ---------- validation, content, hidden fields ------------------------------

def test_validation_requires_title_status_and_rejects_markup(client, world, mail):
    seller(client, box=mail)
    r = client.post("/api/seller/listings", json=car(title_status=None, year=1960, price=50,
                                                     zip="00000", mileage=-1, attest={}))
    errs = " ".join(r.get_json()["errors"])
    assert r.status_code == 400
    for part in ("year", "price", "ZIP", "mileage", "title status", "you may sell this car"):
        assert part in errs
    r = client.post("/api/seller/listings", json=car(model="<script>x</script>"))
    assert r.status_code == 400
    r = client.post("/api/seller/listings", json=car(vin="1HGCM82633A004353"))
    assert r.status_code == 400 and "check digit" in r.get_json()["error"]


def test_hidden_fields_cannot_steer_valuation_or_identity(client, world, mail):
    seller(client, box=mail)
    r = client.post("/api/seller/listings", json=car(
        is_dealer=True, source="ebay", composite_id="ebay:x", user_id=1, comp_avg=99999,
        extras={"verified": True}, status="active", contact_email="evil@example.com"))
    cid = r.get_json()["composite_id"]
    row = q("SELECT * FROM listings WHERE composite_id = ?", (cid,))[0]
    assert row["source"] == "direct" and not row["is_dealer"] and cid.startswith("direct:")
    assert '"verified": false' in row["extras"]                  # vehicle never "verified"
    s = q("SELECT contact_email, user_id FROM seller_listings WHERE id = ?", (r.get_json()["id"],))[0]
    assert s["contact_email"] == "seller@example.com"


def test_seller_text_is_data_and_labeled_seller_reported(client, world, mail):
    seller(client, box=mail)
    cid = client.post("/api/seller/listings", json=car(
        description='<img src=x onerror="alert(1)"> great car',
        mechanical_issues="needs brakes")).get_json()["composite_id"]
    d = q("SELECT description FROM listings WHERE composite_id = ?", (cid,))[0]["description"]
    assert "Known mechanical issues (seller-reported): needs brakes" in d
    assert "Title (seller-reported): clean" in d
    assert '<img src=x onerror="alert(1)">' in d                 # stored verbatim; UIs escape it


# ---------- pipeline outcomes -------------------------------------------------

def test_suspicious_price_is_flagged_not_trusted(client, world, mail):
    seller(client, box=mail)
    cid = client.post("/api/seller/listings", json=car(price=1500)).get_json()["composite_id"]
    score_all()
    nm = q("SELECT trust, risk_flags FROM near_misses WHERE composite_id = ?", (cid,))[0]
    assert nm["trust"] == "risk" and "too_good_to_be_true" in nm["risk_flags"]


def test_insufficient_comparables_and_negative_profit_never_alert(client, world, mail):
    buyer = make_user(client, "b@example.com", searches=[("any", {})])
    seller(client, box=mail)
    rare = client.post("/api/seller/listings", json=car(make="Saab", model="9-3",
                                                         trim=None)).get_json()["composite_id"]
    pricey = client.post("/api/seller/listings", json=car(price=12500)).get_json()["composite_id"]
    score_all()
    assert q("SELECT evidence_class FROM near_misses WHERE composite_id = ?",
             (rare,))[0]["evidence_class"] == "insufficient"
    assert not q("SELECT 1 FROM user_matches WHERE user_id = ? AND composite_id IN (?, ?)",
                 (buyer, rare, pricey))


def test_branded_title_never_trusted(client, world, mail):
    seller(client, box=mail)
    cid = client.post("/api/seller/listings", json=car(title_status="salvage")).get_json()["composite_id"]
    score_all()
    nm = q("SELECT trust, risk_flags FROM near_misses WHERE composite_id = ?", (cid,))[0]
    assert nm["trust"] == "risk" and "branded_title" in nm["risk_flags"]


# ---------- privacy -----------------------------------------------------------

def test_contact_only_verified_and_only_for_paid_users(client, world, mail):
    seller(client, box=mail)
    cid = client.post("/api/seller/listings", json=car()).get_json()["composite_id"]
    score_all()
    logout(client)
    register(client, "free@example.com"); login(client, "free@example.com")
    d = client.get(f"/api/deals/{cid}").get_json()
    assert d["seller_contact"] == {"available": False, "why": "upgrade to see the seller's contact"}
    assert "verified; the vehicle details are seller-reported" in d["provenance"]
    set_plan("free@example.com", "starter")
    d = client.get(f"/api/deals/{cid}").get_json()
    assert d["seller_contact"] == {"available": True, "email": "seller@example.com",
                                   "verified_email": True}
    for path in ("/api/deals?feed=trusted", "/api/deals?feed=risk", "/api/diagnostics",
                 "/api/seller/listings", "/m", "/api/listings"):
        assert "seller@example.com" not in client.get(path).get_data(as_text=True), path
    logout(client)
    assert client.get(f"/api/deals/{cid}").status_code == 401


# ---------- lifecycle ---------------------------------------------------------

def test_price_edit_keeps_history_and_rescoring(client, world, mail):
    seller(client, box=mail)
    r = client.post("/api/seller/listings", json=car(title_status="rebuilt")).get_json()
    score_all()
    e = client.patch(f"/api/seller/listings/{r['id']}", json={"price": 5900})
    assert e.status_code == 200 and e.get_json()["price_changed"]
    assert [h["price"] for h in q("SELECT price FROM listing_prices WHERE composite_id = ? "
                                  "ORDER BY id", (r["composite_id"],))] == [6800, 5900]
    row = q("SELECT description, is_salvage FROM listings WHERE composite_id = ?",
            (r["composite_id"],))[0]
    assert row["description"].count("rebuilt title") == 1 and row["is_salvage"]


def test_stale_listing_expires_until_seller_confirms(client, world, mail):
    import db
    seller(client, box=mail)
    r = client.post("/api/seller/listings", json=car()).get_json()
    score_all()
    assert r["composite_id"] in ids(client)
    with db.session() as c:
        c.execute("UPDATE seller_listings SET last_confirmed_at = ?",
                  ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),))
    assert r["composite_id"] not in ids(client)
    assert client.get("/api/seller/listings").get_json()["listings"][0]["status"] == "expired"
    assert client.post(f"/api/seller/listings/{r['id']}/renew").status_code == 200
    assert r["composite_id"] in ids(client)


def test_withdrawal_removes_everything_and_stops_alerts(client, world, mail):
    make_user(client, "buyer2@example.com", searches=[("camry", {"max_price": 9000})])
    seller(client, box=mail)
    r = client.post("/api/seller/listings", json=car(vin="1HGCM82633A004352")).get_json()
    sid, cid = r["id"], r["composite_id"]
    assert q("SELECT 1 FROM jobs WHERE status = 'pending' AND dedupe_key LIKE ?", (f"score:{cid}:%",))
    assert client.delete(f"/api/seller/listings/{sid}").get_json()["removed"]
    score_all()
    for t in ("listings", "near_misses", "user_matches", "listing_prices", "alerts"):
        assert not q(f"SELECT 1 FROM {t} WHERE composite_id = ?", (cid,)), t
    assert not q("SELECT 1 FROM jobs WHERE status IN ('pending','running') AND "
                 "(dedupe_key LIKE ? OR payload LIKE ?)", (f"score:{cid}:%", f'%"{cid}"%'))
    assert q("SELECT vin, contact_email, payload, status FROM seller_listings WHERE id = ?",
             (sid,))[0] == {"vin": None, "contact_email": None, "payload": None,
                            "status": "removed"}
    assert client.delete(f"/api/seller/listings/{sid}").status_code == 404
    assert client.post("/api/seller/listings", json=car(vin="1HGCM82633A004352")).status_code == 201


# ---------- authorization -------------------------------------------------------

def test_only_owner_or_admin_can_change_renew_or_remove(client, world, mail):
    seller(client, box=mail)
    sid = client.post("/api/seller/listings", json=car()).get_json()["id"]
    logout(client)
    seller(client, "other@example.com", box=mail)
    assert client.patch(f"/api/seller/listings/{sid}", json={"price": 1000}).status_code == 404
    assert client.post(f"/api/seller/listings/{sid}/renew").status_code == 404
    assert client.delete(f"/api/seller/listings/{sid}").status_code == 404
    assert all(l["id"] != sid for l in client.get("/api/seller/listings").get_json()["listings"])
    logout(client)
    register(client, "admin@example.com"); make_admin("admin@example.com")
    login(client, "admin@example.com")
    assert client.delete(f"/api/seller/listings/{sid}").status_code == 200
    assert q("SELECT removed_by FROM seller_listings WHERE id = ?", (sid,))[0]["removed_by"] == "admin"


def test_abuse_limits(client, world, mail, monkeypatch):
    import seller_listings as sl
    monkeypatch.setattr(sl, "MAX_ACTIVE_PER_USER", 2)
    monkeypatch.setattr(sl, "MAX_NEW_PER_USER_PER_DAY", 3)
    seller(client, box=mail)
    a = client.post("/api/seller/listings", json=car()).get_json()["id"]
    client.post("/api/seller/listings", json=car(price=7000))
    assert client.post("/api/seller/listings", json=car(price=7100)).status_code == 429
    client.delete(f"/api/seller/listings/{a}")
    b = client.post("/api/seller/listings", json=car(price=7200)).get_json()["id"]   # 3rd today
    client.delete(f"/api/seller/listings/{b}")
    r = client.post("/api/seller/listings", json=car(price=7300))
    assert r.status_code == 429 and "today" in r.get_json()["error"]
    monkeypatch.setattr(sl, "MAX_NEW_PER_USER_PER_DAY", 99)
    monkeypatch.setattr(sl, "MAX_NEW_PER_DAY", 3)                                  # global cap
    logout(client)
    seller(client, "s2@example.com", box=mail)
    assert client.post("/api/seller/listings", json=car(price=7400)).status_code == 429


def test_concurrent_submissions_cannot_beat_limits_or_duplicate_vin(app, world, mail, monkeypatch):
    """Real threads, separate connections: the per-account lock and the
    unique live-VIN index hold under concurrency (meaningful on Postgres)."""
    import auth
    import seller_listings as sl
    monkeypatch.setattr(sl, "MAX_ACTIVE_PER_USER", 2)
    c = app.test_client()
    register(c, "race@example.com"); login(c, "race@example.com")
    u = auth.get_user_by_email("race@example.com")
    results = []

    def go(i, vin=None):
        try:
            results.append(sl.create(u, car(price=7000 + i, **({"vin": vin} if vin else {}))))
        except sl.SubmissionError as e:
            results.append(e.status)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(1 for r in results if isinstance(r, dict)) == 2      # never more than the limit
    monkeypatch.setattr(sl, "MAX_ACTIVE_PER_USER", 99)
    register(c, "race2@example.com"); u2 = auth.get_user_by_email("race2@example.com")
    results.clear()

    def go2(user):
        try:
            results.append(sl.create(user, car(vin="1HGCM82633A004352")))
        except sl.SubmissionError as e:
            results.append(e.status)
    ts = [threading.Thread(target=go2, args=(x,)) for x in (u, u2, u, u2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(1 for r in results if isinstance(r, dict)) == 1      # one live listing per VIN
    assert all(r == 409 or isinstance(r, dict) for r in results), results
