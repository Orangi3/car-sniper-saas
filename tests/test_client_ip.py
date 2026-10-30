"""
tests/test_client_ip.py — the visitor's address behind the Caddy proxy.

The login-attempt limit and the rate limiter work per visitor. Behind Caddy
every connection reaches gunicorn from loopback, so the visitor's address is
read from X-Forwarded-For: only when the connection comes from a trusted
proxy (SNIPER_TRUSTED_PROXIES), and never from a hop a visitor wrote itself.
Runs on SQLite and (SNIPER_TEST_PG_URL) Postgres.
"""
from __future__ import annotations

import logging

import pytest

from tests.conftest import register

ATTACKER, VICTIM = "198.51.100.66", "203.0.113.20"


@pytest.fixture()
def behind_caddy(app, monkeypatch):
    """The deployed configuration: Caddy on this host, SNIPER_TRUSTED_PROXIES=127.0.0.1,::1."""
    import auth
    monkeypatch.setattr(auth, "TRUSTED_PROXIES", auth._parse_proxies("127.0.0.1,::1"))


def seen(app, peer, xff=None):
    import auth
    headers = {"X-Forwarded-For": xff} if xff is not None else {}
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": peer}, headers=headers):
        return auth.client_ip()


def attempt(client, peer, xff=None, email="nobody@example.com", password="wrong-pass-1"):
    headers = {"X-Forwarded-For": xff} if xff is not None else {}
    return client.post("/api/auth/login", json={"email": email, "password": password},
                       environ_base={"REMOTE_ADDR": peer}, headers=headers).status_code


# ---------- who is the visitor? -------------------------------------------

def test_direct_connections_use_the_socket_address_and_ignore_the_header(app):
    assert seen(app, "203.0.113.9") == "203.0.113.9"
    assert seen(app, "203.0.113.9", "198.51.100.1") == "203.0.113.9"      # nothing trusted


def test_a_forged_header_from_an_untrusted_peer_is_ignored(app, behind_caddy):
    assert seen(app, ATTACKER, VICTIM) == ATTACKER
    assert seen(app, "2001:db8::66", "2001:db8::20") == "2001:db8::66"


def test_the_trusted_proxy_forwards_the_visitor_ipv4_and_ipv6(app, behind_caddy):
    assert seen(app, "127.0.0.1", VICTIM) == VICTIM
    assert seen(app, "::1", "2001:db8::5") == "2001:db8::5"
    assert seen(app, "::ffff:127.0.0.1", VICTIM) == VICTIM                # mapped IPv4 peer
    assert seen(app, "127.0.0.1", "[2001:db8::9]:443") == "2001:db8::9"
    assert seen(app, "127.0.0.1", "198.51.100.7:4444") == "198.51.100.7"
    assert seen(app, "127.0.0.1") == "127.0.0.1"                          # no header: the proxy itself


def test_hops_a_visitor_wrote_are_never_trusted(app, behind_caddy):
    # a proxy that appends instead of setting: the visitor's own claim comes first
    assert seen(app, "127.0.0.1", f"{VICTIM}, {ATTACKER}") == ATTACKER
    assert seen(app, "127.0.0.1", f"not-an-ip, {ATTACKER}") == ATTACKER
    # an unreadable right-most hop: trust nothing in the header
    assert seen(app, "127.0.0.1", f"{VICTIM}, not-an-ip") == "127.0.0.1"


def test_only_listed_proxies_are_skipped_in_a_chain(app, monkeypatch):
    import auth
    monkeypatch.setattr(auth, "TRUSTED_PROXIES", auth._parse_proxies("127.0.0.1, 10.0.0.0/8"))
    assert seen(app, "127.0.0.1", f"{VICTIM}, 10.1.2.3") == VICTIM        # internal hop skipped
    assert seen(app, "127.0.0.1", f"{VICTIM}, {ATTACKER}") == ATTACKER     # stops at the first outsider
    assert seen(app, "10.1.2.3", VICTIM) == VICTIM
    assert seen(app, "192.168.1.5", VICTIM) == "192.168.1.5"              # not listed → not trusted


def test_bad_proxy_entries_are_ignored_with_a_warning(caplog):
    import auth
    with caplog.at_level(logging.WARNING, logger="sniper.auth"):
        nets = auth._parse_proxies("127.0.0.1, not-an-ip ::1")
    assert [str(n) for n in nets] == ["127.0.0.1/32", "::1/128"]
    assert "not-an-ip" in caplog.text


def test_ipv6_visitors_share_a_bucket_per_64():
    import auth
    assert auth.rate_key("2001:db8::1") == auth.rate_key("2001:db8::ffff:2") == "2001:db8::/64"
    assert auth.rate_key("2001:db8:0:1::1") != auth.rate_key("2001:db8::1")
    assert auth.rate_key(VICTIM) == VICTIM


# ---------- login-attempt limit -------------------------------------------

def test_one_visitor_cannot_lock_everyone_out(client, behind_caddy):
    register(client, "victim@example.com", "victim-pass-1")
    client.post("/api/auth/logout", json={})
    for _ in range(10):
        assert attempt(client, "127.0.0.1", ATTACKER) == 401
    assert attempt(client, "127.0.0.1", ATTACKER) == 429                   # the attacker is stopped
    assert attempt(client, "127.0.0.1", VICTIM, "victim@example.com", "victim-pass-1") == 200
    assert attempt(client, "::1", "2001:db8:7::1", "victim@example.com", "victim-pass-1") == 200


def test_forged_headers_neither_escape_the_limit_nor_frame_someone_else(client, behind_caddy):
    register(client, "victim@example.com", "victim-pass-1")
    client.post("/api/auth/logout", json={})
    # direct connection rotating forged headers: still one visitor
    for i in range(10):
        assert attempt(client, ATTACKER, f"192.0.2.{i}") == 401
    assert attempt(client, ATTACKER, "192.0.2.99") == 429
    # through Caddy, claiming to be the victim: counted against the attacker
    for _ in range(10):
        attempt(client, "127.0.0.1", f"{VICTIM}, 198.51.100.77")
    assert attempt(client, "127.0.0.1", "198.51.100.77") == 429
    assert attempt(client, "127.0.0.1", VICTIM, "victim@example.com", "victim-pass-1") == 200


def test_an_ipv6_attacker_rotating_inside_a_64_is_still_limited(client, behind_caddy):
    for i in range(1, 11):
        assert attempt(client, "::1", f"2001:db8::{i:x}") == 401
    assert attempt(client, "::1", "2001:db8::ff") == 429
    assert attempt(client, "::1", "2001:db8:1::1") == 401                  # another /64: own budget


def test_the_budget_is_shared_by_every_web_worker(client, behind_caddy):
    """Counted in the database, not in one gunicorn worker's memory: a fresh
    worker process (here: the module reloaded) sees the same failures."""
    import importlib
    import auth
    from tests.test_matching_and_history import q
    for _ in range(10):
        attempt(client, "127.0.0.1", ATTACKER)
    assert q("SELECT COUNT(*) AS n FROM login_failures WHERE rate_key = ?", (ATTACKER,))[0]["n"] == 10
    importlib.reload(auth)
    auth.TRUSTED_PROXIES = auth._parse_proxies("127.0.0.1,::1")
    assert attempt(client, "127.0.0.1", ATTACKER) == 429


def test_a_successful_login_clears_that_visitors_budget(client, behind_caddy):
    register(client, "pat@example.com", "pat-pass-123")
    client.post("/api/auth/logout", json={})
    for _ in range(9):
        attempt(client, "127.0.0.1", VICTIM)
    assert attempt(client, "127.0.0.1", VICTIM, "pat@example.com", "pat-pass-123") == 200
    for _ in range(9):
        assert attempt(client, "127.0.0.1", VICTIM) == 401


def test_sessions_record_the_visitor_not_the_proxy(client, behind_caddy):
    from tests.test_matching_and_history import q
    register(client, "pat@example.com", "pat-pass-123")
    client.post("/api/auth/logout", json={})
    assert attempt(client, "127.0.0.1", VICTIM, "pat@example.com", "pat-pass-123") == 200
    assert [r["ip"] for r in q("SELECT ip FROM sessions ORDER BY created_at")][-1] == VICTIM


def test_the_rate_limiter_counts_per_visitor(app, behind_caddy):
    import server
    def hit(xff):
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"},
                                      headers={"X-Forwarded-For": xff}):
            import auth
            auth.load_user_from_request()
            r = server._rl_or_fail("unit-test", 2)
            return r[1] if r else 200
    assert [hit(ATTACKER) for _ in range(3)] == [200, 200, 429]
    assert hit(VICTIM) == 200


# ---------- misconfiguration is visible -----------------------------------

def test_a_proxy_without_trusted_proxies_is_reported(app, client, caplog, monkeypatch):
    import auth
    import preflight
    with caplog.at_level(logging.WARNING, logger="sniper.auth"):
        assert seen(app, "127.0.0.1", VICTIM) == "127.0.0.1"
    assert "SNIPER_TRUSTED_PROXIES" in caplog.text
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"},
                                  headers={"X-Forwarded-For": VICTIM}):
        assert auth.proxy_misconfigured() is True
    monkeypatch.setenv("BILLING_ORIGIN", "https://app.example.test")
    monkeypatch.delenv("SNIPER_TRUSTED_PROXIES", raising=False)
    chk = {c["check"]: c for c in preflight.run()["checks"]}["client addresses"]
    assert chk["status"] == "fail" and chk["blocks"] == "controlled_free_beta"
    monkeypatch.setenv("SNIPER_TRUSTED_PROXIES", "127.0.0.1,::1")
    assert {c["check"]: c for c in preflight.run()["checks"]}["client addresses"]["status"] == "ok"


def test_the_operations_page_flags_a_misconfigured_proxy(client):
    from tests.conftest import make_admin
    register(client, "owner@example.com", "owner-pass-1")
    make_admin("owner@example.com")
    via_proxy = client.get("/api/admin/ops", environ_base={"REMOTE_ADDR": "127.0.0.1"},
                           headers={"X-Forwarded-For": VICTIM}).get_json()
    assert any(c["check"] == "client addresses (this request)" and c["status"] == "fail"
               for c in via_proxy["system_health"]["checks"])
    direct = client.get("/api/admin/ops").get_json()
    assert not any(c["check"] == "client addresses (this request)"
                   for c in direct["system_health"]["checks"])
