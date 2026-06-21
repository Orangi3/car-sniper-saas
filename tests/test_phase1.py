"""
tests/test_phase1.py — Phase 1 SaaS security & isolation tests.

Covers (per the principal-engineer brief):
  D. focused tests for authorization, user-data isolation, public poll
     blocking, and normal cached-listing access.

Run from sniper/:
    python3 -m pytest tests/test_phase1.py -v
"""
from __future__ import annotations

from tests.conftest import (register, login, logout, make_admin,
                            set_plan, insert_listing)


# ---------- Auth flow ----------------------------------------------------

def test_register_login_logout_me_cycle(client):
    r = register(client, "alice@example.com")
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["plan"] == "free"
    assert body["user"]["role"] == "user"
    # Cookie set
    assert any("sniper_session" in (h[1] or "") for h in r.headers.items()
               if h[0].lower() == "set-cookie")

    # /api/me now returns the user
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.get_json()["user"]["email"] == "alice@example.com"

    # Logout clears the cookie; /api/me now 401
    r = logout(client)
    assert r.status_code == 200
    r = client.get("/api/me")
    assert r.status_code == 401


def test_register_rejects_duplicate_email(client):
    register(client, "dup@example.com")
    logout(client)
    r = register(client, "dup@example.com")
    assert r.status_code == 400
    assert "already" in r.get_json()["error"].lower()


def test_register_rejects_weak_password(client):
    r = client.post("/api/auth/register",
                    json={"email": "weak@example.com", "password": "short"})
    assert r.status_code == 400
    assert "8" in r.get_json()["error"]


def test_login_wrong_password_generic_error(client):
    register(client, "carol@example.com")
    logout(client)
    r = client.post("/api/auth/login",
                    json={"email": "carol@example.com", "password": "wrong-one"})
    assert r.status_code == 401
    # Generic — must not reveal whether the email existed
    msg = r.get_json()["error"].lower()
    assert "invalid email or password" == msg


def test_login_nonexistent_email_same_error(client):
    r = client.post("/api/auth/login",
                    json={"email": "nobody@example.com", "password": "anything12345"})
    assert r.status_code == 401
    assert r.get_json()["error"].lower() == "invalid email or password"


# ---------- Per-user data isolation -------------------------------------

def test_saved_deals_are_isolated_between_users(client):
    insert_listing("test:car1", "2018 Honda Civic", 9500)
    insert_listing("test:car2", "2019 Toyota Camry", 11000)

    # Alice (starter) saves car1
    register(client, "alice@example.com")
    set_plan("alice@example.com", "starter")
    # Re-login to refresh the session's view of the plan
    logout(client); login(client, "alice@example.com")
    r = client.post("/api/save", json={"composite_id": "test:car1"})
    assert r.status_code == 200, r.get_data(as_text=True)

    r = client.get("/api/saved")
    assert r.status_code == 200
    cids = [x["composite_id"] for x in r.get_json()]
    assert cids == ["test:car1"]

    # Bob (starter) saves car2 — must NOT see Alice's save
    logout(client)
    register(client, "bob@example.com")
    set_plan("bob@example.com", "starter")
    logout(client); login(client, "bob@example.com")
    r = client.post("/api/save", json={"composite_id": "test:car2"})
    assert r.status_code == 200

    r = client.get("/api/saved")
    cids = [x["composite_id"] for x in r.get_json()]
    assert cids == ["test:car2"]
    assert "test:car1" not in cids

    # Bob can't delete Alice's save — DELETE is scoped to his user_id
    r = client.post("/api/unsave", json={"composite_id": "test:car1"})
    assert r.status_code == 200  # idempotent, "ok"
    # Switch back to Alice — her save is still there
    logout(client); login(client, "alice@example.com")
    r = client.get("/api/saved")
    assert [x["composite_id"] for x in r.get_json()] == ["test:car1"]


def test_saved_searches_are_isolated(client):
    register(client, "alice@example.com")
    set_plan("alice@example.com", "starter")
    logout(client); login(client, "alice@example.com")

    r = client.post("/api/saved_searches",
                    json={"name": "Hondas under 10k",
                          "filter": {"make": "honda", "max_price": 10000}})
    assert r.status_code == 200
    alice_search_id = r.get_json()["id"]

    r = client.get("/api/saved_searches")
    names = [s["name"] for s in r.get_json()]
    assert names == ["Hondas under 10k"]

    logout(client)
    register(client, "bob@example.com")
    set_plan("bob@example.com", "starter")
    logout(client); login(client, "bob@example.com")
    r = client.get("/api/saved_searches")
    assert r.get_json() == []  # Bob sees nothing — isolated

    # Bob tries to delete Alice's search — DELETE silently no-ops
    # because the WHERE clause includes user_id
    r = client.delete(f"/api/saved_searches/{alice_search_id}")
    assert r.status_code == 200

    logout(client); login(client, "alice@example.com")
    r = client.get("/api/saved_searches")
    assert len(r.get_json()) == 1  # Alice's search survived Bob's attempt


# ---------- Public poll blocking ----------------------------------------

def test_anonymous_cannot_trigger_poll(client):
    # No session cookie at all
    r = client.post("/api/admin/poll")
    assert r.status_code == 401

# NOTE — `test_regular_user_cannot_trigger_poll` and
# `test_admin_can_trigger_poll` were retired in Phase 2C.1 when
# /api/admin/poll was removed as a public scrape trigger. The current
# coverage is in tests/test_scheduler_isolation.py:
#   - test_server_module_has_no_polling_route          (route is gone)
#   - test_scheduler_runs_one_tick_and_exits           (internal CLI works)
#   - test_scheduler_second_instance_exits_2_when_lock_held
# The old anonymous-cannot-trigger check below still holds: any /api/*
# without a session 401s in _enforce_route_policy regardless of whether
# the path resolves to a real route.


def test_legacy_poll_endpoints_are_dropped(client):
    """/api/poll and /api/poll-all from the personal-tool days were the
    biggest scrape-trigger surface. The SaaS build must not expose them."""
    register(client, "alice@example.com")
    r = client.post("/api/poll")
    assert r.status_code in (401, 404, 405)
    r = client.post("/api/poll-all")
    assert r.status_code in (401, 404, 405)


# `test_admin_can_trigger_poll` retired in Phase 2C.1 — see note above.


# ---------- Normal cached-listing access (free plan can browse) ---------

def test_free_plan_can_browse_listings(client):
    insert_listing("test:carA", "2020 Subaru Outback", 14500)
    register(client, "freeuser@example.com")
    # Default plan is "free" — should still be able to read the cache
    r = client.get("/api/listings?limit=50")
    assert r.status_code == 200
    titles = [x["title"] for x in r.get_json()]
    assert "2020 Subaru Outback" in titles


def test_anonymous_cannot_browse_listings(client):
    insert_listing("test:carA", "2020 Subaru Outback", 14500)
    r = client.get("/api/listings")
    assert r.status_code == 401


def test_free_plan_blocked_from_save(client):
    insert_listing("test:carA", "2020 Subaru Outback", 14500)
    register(client, "freeuser@example.com")  # plan=free
    r = client.post("/api/save", json={"composite_id": "test:carA"})
    assert r.status_code == 402  # "upgrade required"
    assert r.get_json()["required_plan"] == "starter"


def test_starter_plan_can_save(client):
    insert_listing("test:carA", "2020 Subaru Outback", 14500)
    register(client, "starter@example.com")
    set_plan("starter@example.com", "starter")
    logout(client); login(client, "starter@example.com")
    r = client.post("/api/save", json={"composite_id": "test:carA"})
    assert r.status_code == 200


def test_starter_blocked_from_vin_check(client):
    register(client, "starter@example.com")
    set_plan("starter@example.com", "starter")
    logout(client); login(client, "starter@example.com")
    r = client.post("/api/vin/check", json={"vin": "1HGCM82633A004352"})
    assert r.status_code == 402
    assert r.get_json()["required_plan"] == "pro"


# ---------- Admin-only source health -----------------------------------

def test_non_admin_cannot_see_source_health(client):
    register(client, "user@example.com")
    r = client.get("/api/admin/sources/health")
    assert r.status_code == 403


def test_admin_can_see_source_health(client):
    register(client, "admin@example.com")
    make_admin("admin@example.com")
    logout(client); login(client, "admin@example.com")
    r = client.get("/api/admin/sources/health")
    assert r.status_code == 200
    body = r.get_json()
    assert "sources" in body
    assert "scheduler" in body
    assert "scorer" in body
    assert isinstance(body["sources"], list)


# ---------- Settings stay admin-only (no upgrade-bypass via /api/settings) ----

def test_non_admin_cannot_change_settings(client):
    register(client, "user@example.com")
    set_plan("user@example.com", "pro")  # even pro doesn't get settings
    logout(client); login(client, "user@example.com")
    r = client.post("/api/settings", json={"zip": "00000"})
    assert r.status_code == 403


def test_no_client_field_can_self_assign_admin(client):
    """Sanity: even if a client tries to send role/plan/is_admin at register
    time, the server must ignore them and seat the user as 'user'/'free'."""
    r = client.post("/api/auth/register",
                    json={"email": "sneaky@example.com",
                          "password": "abcd1234abcd",
                          "role": "admin", "plan": "pro", "is_admin": True})
    assert r.status_code == 200
    body = r.get_json()
    assert body["user"]["role"] == "user"
    assert body["user"]["plan"] == "free"
    assert body["user"]["is_admin"] is False


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
