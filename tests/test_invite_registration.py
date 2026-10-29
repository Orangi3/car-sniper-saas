"""
tests/test_invite_registration.py — invite-only pilot: with
ALLOW_REGISTRATION=0 an account can be created only with a single-use,
expiring, revocable invitation; each person chooses their own password.
Runs on SQLite and (SNIPER_TEST_PG_URL) Postgres.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_matching_and_history import q


@pytest.fixture()
def closed(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "ALLOW_REGISTRATION", False)


def _register(client, email, invite=None):
    body = {"email": email, "password": "pilot-pass-123"}
    if invite is not None:
        body["invite"] = invite
    return client.post("/api/auth/register", json=body)


def _users():
    return [r["email"] for r in q("SELECT email FROM users ORDER BY id")]


def test_closed_without_or_with_a_bad_invitation(client, closed):
    import invites
    invites.issue(1)
    for bad in (None, "", "not-a-real-token", 12345):
        r = _register(client, "x@example.com", bad)
        assert r.status_code == 403, bad
    assert _users() == []


def test_invitation_creates_an_ordinary_account_with_own_password(client, closed):
    import invites
    (iid, token), = invites.issue(1, note="Pat (seller)")
    r = _register(client, "pat@example.com", token)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["user"]["role"] == "user"
    client.post("/api/auth/logout", json={})
    assert client.post("/api/auth/login", json={"email": "pat@example.com",
                                                "password": "pilot-pass-123"}).status_code == 200
    row, = q("SELECT used_at, used_by_user_id FROM invitations")
    assert row["used_at"] and row["used_by_user_id"]


def test_token_is_single_use(client, closed):
    import invites
    (_, token), = invites.issue(1)
    assert _register(client, "first@example.com", token).status_code == 200
    client.post("/api/auth/logout", json={})
    r = _register(client, "second@example.com", token)
    assert r.status_code == 403 and "already used" in r.get_json()["error"]
    assert _users() == ["first@example.com"]


def test_expired_and_revoked_tokens_are_refused(client, closed):
    import invites
    (_, expired), (rid, revoked) = invites.issue(2)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    import db
    with db.session() as c:
        c.execute("UPDATE invitations SET expires_at = ? WHERE id != ?", (past, rid))
    assert invites.revoke(rid) is True
    assert invites.revoke(rid) is False                  # already revoked
    assert _register(client, "a@example.com", expired).status_code == 403
    assert _register(client, "b@example.com", revoked).status_code == 403
    assert _users() == []


def test_failed_registration_does_not_burn_the_invitation(client, closed):
    """Taken email: the account insert fails, the claim rolls back with it."""
    import auth
    import invites
    auth.create_user("taken@example.com", "pilot-pass-123")
    (_, token), = invites.issue(1)
    assert _register(client, "taken@example.com", token).status_code == 400
    assert q("SELECT used_at FROM invitations")[0]["used_at"] is None
    assert _register(client, "new@example.com", token).status_code == 200


def test_used_invitation_cannot_be_revoked(client, closed):
    import invites
    (iid, token), = invites.issue(1)
    assert _register(client, "u@example.com", token).status_code == 200
    assert invites.revoke(iid) is False


def test_only_hashes_are_stored(client, closed):
    import invites
    issued = invites.issue(3)
    stored = {r["token_hash"] for r in q("SELECT token_hash FROM invitations")}
    for _, token in issued:
        assert token not in stored and len(token) >= 40       # 256-bit, urlsafe
    assert len({t for _, t in issued}) == 3


def test_concurrent_registrations_with_one_token_create_one_account(app, closed):
    import invites
    (_, token), = invites.issue(1)
    results, barrier = [], threading.Barrier(6)

    def go(i):
        c = app.test_client()
        barrier.wait()
        results.append(_register(c, f"race{i}@example.com", token).status_code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [200] + [403] * 5, results
    assert len(_users()) == 1


def test_cli_prints_each_link_once_and_lists_status(client, closed, monkeypatch, capsys):
    import invites
    monkeypatch.setenv("BILLING_ORIGIN", "https://app.example.test")
    assert invites._main(["issue", "--count", "2", "--note", "pilot"]) == 0
    links = [l.split()[-1] for l in capsys.readouterr().out.splitlines() if "#invite=" in l]
    assert len(links) == 2 and all(l.startswith("https://app.example.test/login#invite=") for l in links)
    assert _register(client, "cli@example.com", links[0].split("#invite=")[1]).status_code == 200
    assert invites._main(["revoke", "2"]) == 0
    assert invites._main(["list"]) == 0
    out = capsys.readouterr().out
    assert "used by cli@example.com" in out and "revoked" in out
    assert links[0].split("#invite=")[1] not in out          # never shown again
