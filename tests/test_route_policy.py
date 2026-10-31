"""
tests/test_route_policy.py — every route has a declared access level, and
the server (not the UI) enforces it for anonymous, Free, Lite, Pro and
admin users. A new route fails test_every_route_has_a_policy until it is
added to POLICY, so access is always a deliberate decision.

Levels: public · page (signed-out visitors are redirected to /login) ·
user (any signed-in account) · starter (Lite+) · pro · admin · gone.
Runs on SQLite and (SNIPER_TEST_PG_URL) Postgres.
"""
from __future__ import annotations

import pytest

from tests.test_matching_and_history import L, make_user, score_all, world  # noqa: F401

POLICY = {
    ("GET", "/"): "page", ("GET", "/m"): "page",
    ("GET", "/billing/success"): "page", ("GET", "/billing/cancel"): "page",
    ("GET", "/login"): "public", ("GET", "/health"): "public", ("GET", "/favicon.ico"): "public",
    ("POST", "/api/auth/login"): "public", ("POST", "/api/auth/register"): "public",
    ("POST", "/api/auth/logout"): "public", ("POST", "/api/billing/webhook"): "public",
    # any signed-in account
    ("GET", "/api/me"): "user", ("GET", "/api/plans"): "user", ("GET", "/api/diagnostics"): "user",
    ("GET", "/api/deals"): "user", ("GET", "/api/deals/<path:cid>"): "user",
    ("GET", "/api/saved"): "user", ("GET", "/api/saved_searches"): "user",
    ("GET", "/api/me/alerts"): "user", ("GET", "/api/me/alert_channels"): "user",
    ("GET", "/api/me/profit_settings"): "user", ("GET", "/api/me/usage"): "user",
    ("GET", "/api/me/territory"): "user", ("POST", "/api/me/territory"): "user",
    ("GET", "/api/seller/contact"): "user", ("POST", "/api/seller/contact"): "user",
    # Analyze and the private workspace: every plan (daily analysis cap per plan)
    ("POST", "/api/analyze"): "user", ("POST", "/api/analyze/vin"): "user",
    ("GET", "/api/opportunities"): "user", ("POST", "/api/opportunities"): "user",
    ("GET", "/api/opportunities/<int:oid>"): "user", ("PATCH", "/api/opportunities/<int:oid>"): "user",
    ("DELETE", "/api/opportunities/<int:oid>"): "user",
    ("POST", "/api/seller/contact/verify"): "user",
    ("GET", "/api/seller/listings"): "user", ("POST", "/api/seller/listings"): "user",
    ("PATCH", "/api/seller/listings/<int:sid>"): "user",
    ("DELETE", "/api/seller/listings/<int:sid>"): "user",
    ("POST", "/api/seller/listings/<int:sid>/renew"): "user",
    ("POST", "/api/seller/listings/import"): "user",          # approved dealers only (403 otherwise)
    ("POST", "/api/deals/<path:cid>/report"): "user",
    ("GET", "/api/billing/me"): "user", ("POST", "/api/billing/checkout"): "user",
    ("POST", "/api/billing/portal"): "user",
    # Lite and up
    ("POST", "/api/deals/<path:cid>/action"): "starter",
    ("POST", "/api/save"): "starter", ("POST", "/api/unsave"): "starter",
    ("POST", "/api/saved/note"): "starter", ("POST", "/api/saved/status"): "starter",
    ("POST", "/api/saved_searches"): "starter", ("DELETE", "/api/saved_searches/<int:sid>"): "starter",
    ("POST", "/api/me/alert_channels"): "starter", ("POST", "/api/me/alert_channels/test"): "starter",
    ("POST", "/api/me/profit_settings"): "starter", ("POST", "/api/comps"): "starter",
    # Pro
    ("POST", "/api/vin"): "pro", ("POST", "/api/vin/check"): "pro",
    ("GET", "/api/vin/decode/<vin>"): "pro", ("GET", "/api/vin/report/<vin>"): "pro",
    ("GET", "/api/vin/provider-status"): "pro", ("GET", "/api/diagnostics/vin"): "pro",
    # owner / operator
    ("GET", "/admin/ops"): "admin", ("GET", "/api/admin/ops"): "admin",
    ("GET", "/api/admin/usage"): "admin", ("GET", "/api/admin/eval/report"): "admin",
    ("GET", "/api/admin/billing/health"): "admin", ("GET", "/api/admin/sources/health"): "admin",
    ("GET", "/debug/status"): "admin",
    ("GET", "/api/alerts"): "admin", ("GET", "/api/listings"): "admin",
    ("GET", "/api/listings/closing_soon"): "admin", ("GET", "/api/fresh_listings"): "admin",
    ("GET", "/api/near_misses"): "admin", ("GET", "/api/stats"): "admin",
    ("GET", "/api/facets"): "admin", ("GET", "/api/score_progress"): "admin",
    ("GET", "/api/sources"): "admin",
    ("GET", "/api/settings"): "admin", ("POST", "/api/settings"): "admin",
    ("POST", "/api/threshold"): "admin",
    ("GET", "/api/share_settings"): "admin", ("POST", "/api/share_settings"): "admin",
    ("GET", "/api/notify_settings"): "admin", ("POST", "/api/notify_settings"): "admin",
    ("POST", "/api/notify_test"): "admin", ("POST", "/api/vin_provider"): "admin",
    ("POST", "/api/import"): "admin", ("POST", "/api/import_email"): "admin",
    # removed in the SaaS build (data collection is the scheduler's job)
    ("POST", "/api/poll"): "gone", ("POST", "/api/poll-all"): "gone",
}

RANK = {"user": 0, "free": 0, "starter": 1, "pro": 2, "admin": 3}   # levels and roles
ROLES = ("free", "starter", "pro", "admin")
# reached only to prove refusal: allowing them would call outside services or
# change settings, which the feature tests cover with their own fakes
NO_ALLOWED_CALL = {k for k in POLICY if k[0] != "GET"} | {
    ("GET", "/api/vin/decode/<vin>"), ("GET", "/api/vin/report/<vin>"),
    ("GET", "/api/vin/provider-status"), ("GET", "/api/diagnostics/vin")}


def _url(rule):
    return (rule.replace("<path:cid>", "test:abc").replace("<int:sid>", "1").replace("<int:oid>", "1")
                .replace("<vin>", "1HGCM82633A004352"))


def _call(c, method, rule):
    return c.open(_url(rule), method=method, json=None if method == "GET" else {}).status_code


@pytest.fixture()
def clients(app):
    import auth
    out = {"anon": app.test_client()}
    for role in ROLES:
        email = f"{role}@example.com"
        u = auth.create_user(email, "role-pass-123")
        if role == "admin":
            auth.set_role(u.id, "admin")
        else:
            auth.set_plan(u.id, role)
        c = app.test_client()
        assert c.post("/api/auth/login", json={"email": email, "password": "role-pass-123"}).status_code == 200
        out[role] = c
    return out


def test_every_route_has_a_policy(app):
    routes = {(m, r.rule) for r in app.url_map.iter_rules() if r.endpoint != "static"
              for m in r.methods - {"HEAD", "OPTIONS"}}
    assert routes - set(POLICY) == set(), "new route(s) need an access level in POLICY"
    assert set(POLICY) - routes == set(), "POLICY lists routes that no longer exist"


def test_the_server_enforces_every_policy(clients):
    wrong = []
    for (method, rule), level in sorted(POLICY.items()):
        if level == "public":
            continue
        anon = _call(clients["anon"], method, rule)
        if anon != (302 if level == "page" else 401):
            wrong.append(f"anon {method} {rule}: {anon}")
        if level in ("page", "gone"):
            continue
        for role in ROLES:
            got = _call(clients[role], method, rule) if (
                RANK[role] < RANK[level] or (method, rule) not in NO_ALLOWED_CALL) else None
            if got is None:
                continue
            if RANK[role] < RANK[level]:
                want = 403 if level == "admin" else 402
                if got != want:
                    wrong.append(f"{role} {method} {rule}: {got}, want {want}")
            elif got in (401, 402, 403):
                wrong.append(f"{role} {method} {rule}: refused ({got}) but allowed by policy")
    assert wrong == []


def test_removed_routes_stay_removed(clients):
    for role in ROLES:
        assert _call(clients[role], "POST", "/api/poll") == 404
        assert _call(clients[role], "POST", "/api/poll-all") == 404


# ---------- plan limits hold on direct API calls -------------------------------

def test_profit_scenarios_are_pro_only_in_every_deal_response(client, world):
    world.ingest([L("deal", 8100)]); score_all()
    seen = {}
    for plan in ("free", "starter", "pro"):
        make_user(client, f"{plan}-s@example.com", plan=plan, topic=False)
        from tests.conftest import login
        login(client, f"{plan}-s@example.com")
        rows = client.get("/api/deals?feed=trusted").get_json()
        cid = next(d["composite_id"] for d in rows if d["composite_id"].endswith(":deal"))
        detail = client.get(f"/api/deals/{cid}").get_json()
        seen[plan] = (set(next(d for d in rows if d["composite_id"] == cid)["profit"]),
                      set(detail["profit_detail"]["scenarios"]))
        client.post("/api/auth/logout", json={})
    assert seen["free"] == seen["starter"] == ({"base"}, {"base"})
    assert seen["pro"] == ({"conservative", "base", "optimistic"},) * 2


def test_free_and_lite_limits_are_enforced_by_the_server(clients):
    free, lite = clients["free"], clients["starter"]
    assert free.post("/api/saved_searches", json={"name": "x", "filter": {}}).status_code == 402
    assert free.post("/api/me/profit_settings", json={"misc": 1}).status_code == 402
    assert free.post("/api/me/alert_channels", json={"webhook_url": "https://example.com/h"}).status_code == 402
    aggressive = {"name": "x", "filter": {"risk_mode": "aggressive"}}
    assert lite.post("/api/saved_searches", json=aggressive).status_code == 402
    for i in range(3):
        assert lite.post("/api/saved_searches", json={"name": f"s{i}", "filter": {}}).status_code == 200
    assert lite.post("/api/saved_searches", json={"name": "s4", "filter": {}}).status_code == 402


def test_customers_see_source_status_only(clients):
    rows = clients["free"].get("/api/diagnostics").get_json()
    assert rows and all(set(r) == {"id", "name", "status"} for r in rows)
    assert "requests_ok" in clients["admin"].get("/api/diagnostics").get_json()[0]


# ---------- one account never reaches another's data ---------------------------

def test_accounts_cannot_reach_each_others_data(clients, world):
    world.ingest([L("deal", 8100)]); score_all()
    a, b = clients["pro"], clients["starter"]
    cid = next(d["composite_id"] for d in a.get("/api/deals?feed=trusted").get_json()
               if d["composite_id"].endswith(":deal"))
    sid = a.post("/api/saved_searches", json={"name": "A's search", "filter": {}}).get_json()["id"]
    assert a.post(f"/api/deals/{cid}/action", json={"action": "save"}).status_code == 200
    assert a.post("/api/saved/note", json={"composite_id": cid, "note": "A's private note"}).status_code in (200, 204)
    # B can't list, use or delete A's search, nor see A's saved deals and notes
    assert all(s["id"] != sid for s in b.get("/api/saved_searches").get_json())
    assert b.get(f"/api/deals?feed=search&search_id={sid}").status_code == 404
    assert b.delete(f"/api/saved_searches/{sid}").status_code == 200        # no-op for B
    assert any(s["id"] == sid for s in a.get("/api/saved_searches").get_json())
    assert b.get("/api/deals?feed=saved").get_json() == []
    detail_b = b.get(f"/api/deals/{cid}").get_json()
    assert detail_b.get("user_status") is None and not detail_b.get("user_note")
    assert "A's private note" not in b.get(f"/api/deals/{cid}").get_data(as_text=True)
    assert a.get(f"/api/deals/{cid}").get_json()["user_note"] == "A's private note"
