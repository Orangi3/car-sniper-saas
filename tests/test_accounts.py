"""
tests/test_accounts.py — deleting an account on request removes the
person's data and nobody else's. SIMULATED users and listings; SQLite and
PostgreSQL.
"""
from __future__ import annotations

import pytest

from tests.conftest import login, logout, make_admin, register
from tests.test_matching_and_history import L, make_user, q, score_all, user_jobs, world  # noqa: F401
from tests.test_seller_listings import car, mail, seller  # noqa: F401


def counts(uid):
    return {t: q(f"SELECT COUNT(*) AS n FROM {t} WHERE user_id = ?", (uid,))[0]["n"]
            for t in ("sessions", "saved_searches", "user_matches", "jobs", "saved_v2")}


def test_dry_run_changes_nothing_and_delete_removes_only_that_person(client, world):
    import accounts
    gone = make_user(client, "gone@example.com", searches=[("c", {"model": "camry"})])
    kept = make_user(client, "kept@example.com", searches=[("c", {"model": "camry"})])
    world.ingest([L("deal", 8100, odo=120000)])
    score_all()
    login(client, "gone@example.com")
    client.post("/api/deals/craigslist:deal/action", json={"action": "save"})
    before = counts(gone)
    assert before["saved_searches"] == 1 and before["user_matches"] == 1 and before["jobs"] >= 1
    r = accounts.delete("gone@example.com")
    assert r["deleted"] is False and counts(gone) == before          # dry run
    assert accounts.delete("gone@example.com", confirm=True)["deleted"] is True
    assert q("SELECT 1 FROM users WHERE id = ?", (gone,)) == []
    assert all(n == 0 for n in counts(gone).values())
    assert counts(kept)["saved_searches"] == 1 and counts(kept)["user_matches"] == 1
    assert client.get("/api/me").status_code in (200, 401)          # the old session is dead
    assert client.get("/api/deals?feed=saved").status_code == 401


def test_a_sellers_listings_and_everything_derived_from_them_go(client, world, mail):
    import accounts
    seller(client, box=mail)
    cid = client.post("/api/seller/listings", json=car()).get_json()["composite_id"]
    score_all()
    logout(client)
    buyer = make_user(client, "buyer@example.com", plan="starter")
    login(client, "buyer@example.com")
    client.post(f"/api/deals/{cid}/action", json={"action": "save"})
    assert q("SELECT 1 FROM listings WHERE composite_id = ?", (cid,))
    accounts.delete("seller@example.com", confirm=True)
    for table in ("listings", "near_misses", "listing_prices", "saved_v2", "user_matches"):
        assert q(f"SELECT 1 FROM {table} WHERE composite_id = ?", (cid,)) == [], table
    assert q("SELECT 1 FROM seller_listings") == [] and q("SELECT 1 FROM seller_contacts") == []
    assert q("SELECT 1 FROM users WHERE id = ?", (buyer,))


def test_refuses_a_live_subscription_and_the_last_admin(client, world):
    import accounts
    import billing
    import db
    uid = make_user(client, "payer@example.com", plan="pro", topic=False)
    with db.transaction() as conn:
        billing.upsert_subscription(conn, user_id=uid, stripe_customer_id="cus_x",
                                    stripe_subscription_id="sub_x", stripe_price_id="price_x",
                                    status="active", current_period_end=None,
                                    cancel_at_period_end=False)
    with pytest.raises(accounts.Refused, match="cancel it in Stripe"):
        accounts.delete("payer@example.com", confirm=True)
    register(client, "boss@example.com")
    make_admin("boss@example.com")
    with pytest.raises(accounts.Refused, match="only admin"):
        accounts.delete("boss@example.com", confirm=True)
    with pytest.raises(accounts.Refused, match="no account"):
        accounts.delete("nobody@example.com")


def test_cli_is_a_dry_run_unless_told_otherwise(client, world, capsys):
    import accounts
    make_user(client, "cli@example.com", topic=False)
    assert accounts.main(["delete", "CLI@example.com"]) == 0
    assert "dry run" in capsys.readouterr().out
    assert q("SELECT 1 FROM users WHERE email = 'cli@example.com'")
    assert accounts.main(["delete", "cli@example.com", "--yes"]) == 0
    assert q("SELECT 1 FROM users WHERE email = 'cli@example.com'") == []
    assert accounts.main(["delete", "cli@example.com", "--yes"]) == 1


def test_deletion_clears_the_invitation_name_and_redacts_stripe_events(client, world, monkeypatch):
    import accounts
    import auth
    import db
    import invites
    monkeypatch.setattr(auth, "ALLOW_REGISTRATION", False)       # invitation-only, as in the pilot
    (iid, token), = invites.issue(note="Pat (seller)")
    client.post("/api/auth/register", json={"email": "pat@example.com", "password": "testpass123",
                                            "invite": token})
    uid = q("SELECT id FROM users WHERE email = 'pat@example.com'")[0]["id"]
    assert q("SELECT used_by_user_id FROM invitations WHERE id = ?", (iid,))[0]["used_by_user_id"] == uid
    with db.session() as c:
        c.execute("INSERT INTO stripe_webhook_events(stripe_event_id, type, payload) VALUES (?,?,?)",
                  ("evt_pat", "customer.updated", '{"email": "pat@example.com"}'))
        c.execute("INSERT INTO stripe_webhook_events(stripe_event_id, type, payload) VALUES (?,?,?)",
                  ("evt_other", "customer.updated", '{"email": "patXexample.com"}'))
    accounts.delete("pat@example.com", confirm=True)
    assert q("SELECT note, used_by_user_id FROM invitations WHERE id = ?", (iid,)) == [
        {"note": None, "used_by_user_id": None}]
    events = {r["stripe_event_id"]: r["payload"] for r in q("SELECT * FROM stripe_webhook_events")}
    assert events["evt_pat"] == '{"redacted": true}'          # event id kept for idempotency
    assert "patXexample.com" in events["evt_other"]           # "_"/"%" are not wildcards here
    assert q("SELECT 1 FROM sessions WHERE user_id = ?", (uid,)) == []
