"""
tests/test_sessions.py — sign-in sessions.

The cookie carries a 256-bit random token; the database keeps only its
SHA-256 (migration 0018), so a copy of the database can't be replayed as a
session. Sessions expire after 30 days, logout ends one, revoke_sessions()
ends all of a user's, and expired rows are cleaned up. Writes from another
site are refused. Runs on SQLite and (SNIPER_TEST_PG_URL) Postgres.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from tests.conftest import register
from tests.test_matching_and_history import q

COOKIE = "sniper_session"


def _signed_in(app, email="pat@example.com", password="pat-pass-123", **kw):
    c = app.test_client()
    r = c.post("/api/auth/register", json={"email": email, "password": password}, **kw)
    if r.status_code != 200:
        r = c.post("/api/auth/login", json={"email": email, "password": password}, **kw)
    assert r.status_code == 200, r.get_json()
    return c, r


def _token(c):
    return c.get_cookie(COOKIE).value


def _me(app, token):
    c = app.test_client()
    if token is not None:
        c.set_cookie(COOKIE, token)
    return c.get("/api/me").status_code


# ---------- storage ----------------------------------------------------------

def test_the_database_keeps_only_a_hash_of_the_session_token(app):
    c, _ = _signed_in(app)
    token = _token(c)
    stored = [r["token"] for r in q("SELECT token FROM sessions")]
    assert token not in stored
    assert stored == [hashlib.sha256(token.encode()).hexdigest()]
    assert _me(app, token) == 200


def test_a_copied_database_row_is_not_a_session(app):
    _signed_in(app)
    stored = q("SELECT token FROM sessions")[0]["token"]
    assert _me(app, stored) == 401


def test_invalid_malformed_and_oversized_tokens_are_refused(app):
    _signed_in(app)
    for bad in ("", "x", secrets.token_urlsafe(32), "' OR 1=1 --", "a" * 3000, "☃" * 10):
        assert _me(app, bad) == 401, bad[:20]


# ---------- lifetime ---------------------------------------------------------

def test_expired_sessions_are_refused_and_cleaned_up(app):
    import db
    c, _ = _signed_in(app)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with db.session() as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (past,))
    assert _me(app, _token(c)) == 401
    _signed_in(app)                                   # the next sign-in sweeps expired rows
    assert len(q("SELECT token FROM sessions")) == 1


def test_sessions_last_thirty_days(app):
    _signed_in(app)
    exp = datetime.fromisoformat(str(q("SELECT expires_at FROM sessions")[0]["expires_at"]))
    exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
    days = (exp - datetime.now(timezone.utc)).total_seconds() / 86400
    assert 29.9 < days <= 30.0


def test_each_sign_in_gets_its_own_session_and_logout_ends_only_that_one(app):
    phone, _ = _signed_in(app)
    laptop, _ = _signed_in(app)
    assert _token(phone) != _token(laptop)
    phone_token = _token(phone)
    assert phone.post("/api/auth/logout", json={}).status_code == 200
    assert _me(app, phone_token) == 401                     # a stolen copy of it is dead too
    assert _me(app, _token(laptop)) == 200


def test_revoke_sessions_signs_a_user_out_everywhere(app):
    import auth
    a1, _ = _signed_in(app)
    a2, _ = _signed_in(app)
    other, _ = _signed_in(app, "sam@example.com", "sam-pass-123")
    uid = auth.get_user_by_email("pat@example.com").id
    assert auth.revoke_sessions(uid) == 2
    assert _me(app, _token(a1)) == _me(app, _token(a2)) == 401
    assert _me(app, _token(other)) == 200


# ---------- cookie -------------------------------------------------------------

def _set_cookie(resp):
    return next(h for h in resp.headers.getlist("Set-Cookie") if h.startswith(COOKIE + "="))


def test_cookie_flags(app, monkeypatch):
    monkeypatch.delenv("BILLING_ORIGIN", raising=False)
    _, r = _signed_in(app)                                         # dev server over http
    h = _set_cookie(r)
    assert "HttpOnly" in h and "SameSite=Lax" in h and "Path=/" in h
    assert f"Max-Age={30 * 86400}" in h and "Secure" not in h
    _, r = _signed_in(app, "sam@example.com", "sam-pass-123", base_url="https://localhost")
    assert "Secure" in _set_cookie(r)                             # arrived over https
    monkeypatch.setenv("BILLING_ORIGIN", "https://app.example.test")
    _, r = _signed_in(app, "kim@example.com", "kim-pass-123")
    assert "Secure" in _set_cookie(r)                             # deployed origin is https


# ---------- migration of sessions stored the old way ----------------------------

def test_migration_0018_converts_or_removes_plaintext_sessions(app):
    import auth
    import db
    from migrations import runner
    _signed_in(app)
    uid = auth.get_user_by_email("pat@example.com").id
    raw = secrets.token_urlsafe(32)                               # how tokens used to be stored
    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    with db.session() as conn:
        conn.execute("INSERT INTO sessions(token, user_id, expires_at, user_agent, ip) "
                     "VALUES (?, ?, ?, '', '')", (raw, uid, future))
    sql = dict(runner._list_migrations())["0018_hashed_session_tokens"].read_text()
    with db.transaction() as conn:
        runner._exec_script(conn, sql)
        runner._exec_script(conn, sql)                            # re-running is a no-op
    stored = {r["token"] for r in q("SELECT token FROM sessions")}
    assert raw not in stored
    if db.IS_PG:   # converted in place: the person stays signed in
        assert hashlib.sha256(raw.encode()).hexdigest() in stored and _me(app, raw) == 200
    else:          # SQLite (local/demo): removed, sign in again
        assert len(stored) == 1 and _me(app, raw) == 401


# ---------- writes from another site ----------------------------------------------

def test_writes_from_another_site_are_refused(app, monkeypatch):
    monkeypatch.setenv("BILLING_ORIGIN", "https://app.example.test")
    c, _ = _signed_in(app)
    body = {"home_zip": "35401", "radius_mi": 100}
    for evil in ("https://evil.example", "null", "http://app.example.test"):
        r = c.post("/api/me/territory", json=body, headers={"Origin": evil})
        assert r.status_code == 403, evil
    assert c.post("/api/auth/logout", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _me(app, _token(c)) == 200                              # the forged logout did nothing
    for good in ("https://app.example.test", "http://localhost", None):
        h = {"Origin": good} if good else {}
        assert c.post("/api/me/territory", json=body, headers=h).status_code == 200, good
    assert c.get("/api/me", headers={"Origin": "https://evil.example"}).status_code == 200
    # Stripe's webhook carries no browser session; its signature is the check
    r = c.post("/api/billing/webhook", data=b"{}", headers={"Origin": "https://evil.example"})
    assert r.status_code != 403 or "cross-site" not in r.get_data(as_text=True)


# ---------- sign-in doesn't reveal which emails have accounts ------------------------

def test_unknown_email_costs_the_same_password_check(client, monkeypatch):
    import auth
    register(client, "pat@example.com", "pat-pass-123")
    client.post("/api/auth/logout", json={})
    checks, real = [], auth.bcrypt.checkpw
    monkeypatch.setattr(auth.bcrypt, "checkpw", lambda p, h: checks.append(1) or real(p, h))
    for email in ("pat@example.com", "nobody@example.com"):
        r = client.post("/api/auth/login", json={"email": email, "password": "wrong-pass-1"})
        assert r.status_code == 401 and r.get_json()["error"] == "invalid email or password"
    assert len(checks) == 2                                         # one bcrypt check each
