"""
tests/test_safe_http.py — outbound webhook SSRF protection (P0.4).
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import safe_http
from tests.conftest import register, login, set_plan


def _fake_dns(monkeypatch, mapping):
    def gai(host, port, *a, **k):
        ips = mapping[host]
        ips = ips if isinstance(ips, list) else [ips]
        fam = lambda ip: socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam(ip), socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]
    monkeypatch.setattr(safe_http.socket, "getaddrinfo", gai)


@pytest.mark.parametrize("url,err", [
    ("http://hooks.example.com/x", "https"),
    ("ftp://hooks.example.com/x", "https"),
    ("https://user:pw@hooks.example.com/x", "credentials"),
    ("https://hooks.example.com:22/x", "port"),
    ("https:///nohost", "no host"),
    ("https://" + "a" * 3000 + ".com", "too long"),
])
def test_static_url_rules(url, err):
    with pytest.raises(safe_http.UnsafeDestination, match=err):
        safe_http.check_url(url)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.5", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1",
    "::ffff:169.254.169.254", "224.0.0.1",
])
def test_private_and_metadata_addresses_blocked(monkeypatch, ip):
    _fake_dns(monkeypatch, {"evil.example.com": ip})
    with pytest.raises(safe_http.UnsafeDestination):
        safe_http.resolve_checked("evil.example.com", 443)
    assert safe_http.validate_destination("https://evil.example.com/hook")


def test_any_private_answer_poisons_the_name(monkeypatch):
    _fake_dns(monkeypatch, {"mixed.example.com": ["93.184.216.34", "10.0.0.7"]})
    with pytest.raises(safe_http.UnsafeDestination):
        safe_http.resolve_checked("mixed.example.com", 443)


def test_ip_literal_urls_checked_too():
    with pytest.raises(safe_http.UnsafeDestination):
        safe_http.post_json("https://169.254.169.254/latest/meta-data", {})


def test_dns_rebinding_between_save_and_send_is_blocked(monkeypatch):
    calls = []
    monkeypatch.setattr(safe_http.urllib3.HTTPSConnectionPool, "urlopen",
                        lambda *a, **k: calls.append(1))
    _fake_dns(monkeypatch, {"rebind.example.com": "93.184.216.34"})
    assert safe_http.validate_destination("https://rebind.example.com/h") is None
    _fake_dns(monkeypatch, {"rebind.example.com": "127.0.0.1"})   # attacker flips DNS
    with pytest.raises(safe_http.UnsafeDestination):
        safe_http.post_json("https://rebind.example.com/h", {"a": 1})
    assert calls == []                                            # never connected


def test_connection_is_pinned_to_the_validated_ip(monkeypatch):
    seen = {}

    class FakePool:
        def __init__(self, host, **kw):
            seen["host"], seen["sni"] = host, kw.get("server_hostname")

        def urlopen(self, method, path, **kw):
            seen["headers"], seen["redirect"] = kw["headers"], kw["redirect"]

            class R:
                status = 204
                def read(self, n): return b""
                def release_conn(self): pass
            return R()

        def close(self):
            pass

    monkeypatch.setattr(safe_http.urllib3, "HTTPSConnectionPool", FakePool)
    _fake_dns(monkeypatch, {"hooks.example.com": "93.184.216.34"})
    assert safe_http.post_json("https://hooks.example.com/x?y=1", {"a": 1}) == 204
    assert seen["host"] == "93.184.216.34"          # connect to the checked IP
    assert seen["sni"] == "hooks.example.com"       # TLS still verifies the name
    assert seen["headers"]["Host"] == "hooks.example.com"
    assert seen["redirect"] is False


def test_oversized_payload_rejected(monkeypatch):
    _fake_dns(monkeypatch, {"hooks.example.com": "93.184.216.34"})
    with pytest.raises(safe_http.UnsafeDestination, match="exceeds"):
        safe_http.post_json("https://hooks.example.com/x", {"big": "x" * 20000})


class _Redirector(BaseHTTPRequestHandler):
    hits: list = []

    def do_POST(self):
        _Redirector.hits.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(302)
        self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
        self.end_headers()

    def log_message(self, *a):
        pass


def test_redirect_to_metadata_service_is_not_followed():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Redirector.hits.clear()
    try:
        url = f"http://localhost:{srv.server_address[1]}/hook"
        # test-only policy lets us reach the local server; the redirect target
        # must still never be requested.
        with pytest.raises(safe_http.DeliveryError) as ei:
            safe_http.post_json(url, {"a": 1}, allow_http=True,
                                policy=lambda ip: True)
        assert ei.value.retryable is False and ei.value.status == 302
        assert _Redirector.hits == ["/hook"]
    finally:
        srv.shutdown()


# ---------- end-to-end through the notify job handler ---------------------

def _user_with_webhook(client, email, url, monkeypatch):
    register(client, email)
    set_plan(email, "pro")
    login(client, email)
    r = client.post("/api/me/alert_channels", json={"alert_webhook": url})
    assert r.status_code == 200, r.get_json()
    import auth
    return auth.get_user_by_email(email).id


def test_user_webhook_rebound_to_private_is_dead_lettered(client, monkeypatch):
    import db, jobqueue, notifications
    _fake_dns(monkeypatch, {"a.example.com": "93.184.216.34"})
    uid = _user_with_webhook(client, "a@example.com", "https://a.example.com/h", monkeypatch)
    with db.session() as c:
        notifications.enqueue_user(c, {"composite_id": "x:1", "title": "t"},
                                   "alert1", uid, ["webhook"])
    _fake_dns(monkeypatch, {"a.example.com": "10.0.0.5"})     # rebinding
    done = jobqueue.drain({"notify": notifications.deliver_job})
    assert done[0]["status"] == "dead"
    assert "unsafe destination" in jobqueue.dead_letters()[0]["last_error"]


def test_jobs_deliver_only_to_their_own_users_destination(client, monkeypatch):
    import db, jobqueue, notifications
    _fake_dns(monkeypatch, {"a.example.com": "93.184.216.34",
                            "b.example.com": "93.184.216.35"})
    ua = _user_with_webhook(client, "a@example.com", "https://a.example.com/h", monkeypatch)
    client.post("/api/auth/logout")
    ub = _user_with_webhook(client, "b@example.com", "https://b.example.com/h", monkeypatch)
    sent = []
    monkeypatch.setattr(notifications.safe_http, "post_json",
                        lambda url, payload, **k: sent.append((url, payload)) or 200)
    with db.session() as c:
        notifications.enqueue_user(c, {"composite_id": "x:1", "title": "A's deal",
                                       "description": "secret seller phone 555"},
                                   "alert1", ua, ["webhook"])
    jobqueue.drain({"notify": notifications.deliver_job})
    assert [u for u, _ in sent] == ["https://a.example.com/h"]
    payload = json.dumps(sent[0][1])
    assert "secret seller phone" not in payload           # minimized payload
    assert "b.example.com" not in payload and str(ub) not in json.dumps(sent[0][1]["deal"])


def test_removed_channel_skips_delivery(client, monkeypatch):
    import db, jobqueue, notifications
    _fake_dns(monkeypatch, {"a.example.com": "93.184.216.34"})
    uid = _user_with_webhook(client, "a@example.com", "https://a.example.com/h", monkeypatch)
    with db.session() as c:
        notifications.enqueue_user(c, {"composite_id": "x:1"}, "alert1", uid, ["webhook"])
    client.post("/api/me/alert_channels", json={"alert_webhook": ""})
    sent = []
    monkeypatch.setattr(notifications.safe_http, "post_json",
                        lambda url, payload, **k: sent.append(url) or 200)
    done = jobqueue.drain({"notify": notifications.deliver_job})
    assert done[0]["status"] == "done" and sent == []
