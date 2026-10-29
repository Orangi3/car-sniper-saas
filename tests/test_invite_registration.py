"""
tests/test_invite_registration.py — invite-only pilot: with
ALLOW_REGISTRATION=0 only holders of the owner's invite link can create an
account (choosing their own password); everyone else is refused.
"""
from __future__ import annotations

import pytest

CODE = "0123456789abcdef0123456789abcdef"


@pytest.fixture()
def closed(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "ALLOW_REGISTRATION", False)
    monkeypatch.setenv("SNIPER_INVITE_CODE", CODE)


def _register(client, email, **extra):
    return client.post("/api/auth/register",
                       json={"email": email, "password": "pilot-pass-123", **extra})


def test_closed_registration_refuses_without_the_invite(client, closed):
    r = _register(client, "a@example.com")
    assert r.status_code == 403 and "invitation" in r.get_json()["error"]
    assert _register(client, "b@example.com", invite_code="wrong").status_code == 403


def test_invite_code_creates_an_ordinary_account_with_own_password(client, closed):
    r = _register(client, "seller@example.com", invite_code=CODE)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["user"]["role"] == "user"
    client.post("/api/auth/logout", json={})
    ok = client.post("/api/auth/login",
                     json={"email": "seller@example.com", "password": "pilot-pass-123"})
    assert ok.status_code == 200


@pytest.mark.parametrize("configured", ["", "short-code"])
def test_unset_or_short_code_means_no_invites(client, monkeypatch, configured):
    import auth
    monkeypatch.setattr(auth, "ALLOW_REGISTRATION", False)
    monkeypatch.setenv("SNIPER_INVITE_CODE", configured)
    for sent in ("", configured, None):
        assert _register(client, "x@example.com", invite_code=sent).status_code == 403
