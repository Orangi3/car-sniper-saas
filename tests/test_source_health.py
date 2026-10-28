"""
tests/test_source_health.py — honest source status (P0.3).

HTTP is simulated (no live endpoints): each test routes the sources'
requests to canned responses and checks what poll_once() records.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from sources import health

RSS_OK = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>2015 Toyota Camry SE - $9,500</title>
<link>https://tuscaloosa.craigslist.org/cto/d/x/7712345678.html</link>
<description>runs great</description></item></channel></rss>"""
RSS_EMPTY = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
</channel></rss>"""
HTML_EMPTY_SEARCH = ('<html><script id="ld_searchpage_results" type="application/ld+json">'
                     '{"itemListElement": []}</script></html>')
HTML_UNKNOWN = "<html><body><div>totally new layout</div></body></html>"
CHALLENGE = "<html><title>Just a moment...</title><div class='cf-chl'>captcha</div></html>"


class FakeResp:
    def __init__(self, status=200, body=b"", ctype="application/rss+xml", headers=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", "replace")
        self.headers = {"Content-Type": ctype, **(headers or {})}

    def json(self):
        import json
        return json.loads(self.text)


@pytest.fixture()
def router(app, monkeypatch):
    """Route every requests.get / Session.get to `routes[predicate]`."""
    import sniper
    import sources.craigslist as cl
    cl._BACKOFF_UNTIL.clear()
    routes: list = []

    def get(*args, **kw):
        url = args[-1] if args and isinstance(args[-1], str) else kw.get("url")
        for pred, action in routes:
            if pred(url):
                if isinstance(action, Exception):
                    raise action
                return action() if callable(action) else action
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(requests, "get", lambda url, **kw: get(url, **kw))
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: get(url, **kw))
    monkeypatch.setitem(sniper.CONFIG["sources"]["craigslist"], "regions", ["tuscaloosa"])
    monkeypatch.setattr(sniper._config_mod, "reload", lambda: None)
    return routes


def _row(sid):
    import sniper
    with sniper.db() as c:
        r = c.execute("SELECT * FROM source_health WHERE source_id = ?", (sid,)).fetchone()
        return dict(r) if r else {}


def _poll(sid, force=True):
    import sniper
    return sniper.poll_once(verbose=False, only_sources=[sid], force_all=force)


def test_blocked_403_is_blocked_not_success_and_backs_off(router):
    router.append((lambda u: "craigslist" in u, FakeResp(403, b"", "text/html")))
    out = _poll("craigslist")
    st = out["source_status"]["craigslist"]
    assert st["status"] == "blocked"
    row = _row("craigslist")
    assert row["status"] == "blocked" and row["last_success_at"] is None
    # one request: after a 403 the region backs off instead of also hitting HTML
    assert row["requests_attempted"] == 1 and row["requests_ok"] == 0
    assert row["next_poll_after"] > datetime.now(timezone.utc).isoformat()
    # even a manual "poll now" respects the backoff
    out2 = _poll("craigslist")
    assert out2["source_status"]["craigslist"].get("skipped")
    assert "backing off" in out2["source_status"]["craigslist"]["reason"]


def test_proxy_refusal_and_challenge_page_are_blocked(router):
    router.append((lambda u: "format=rss" in u,
                   requests.exceptions.ProxyError("Tunnel connection failed: 403")))
    router.append((lambda u: "craigslist" in u, FakeResp(200, CHALLENGE, "text/html")))
    assert _poll("craigslist")["source_status"]["craigslist"]["status"] == "blocked"


def test_rate_limited_honours_retry_after(router):
    router.append((lambda u: True, FakeResp(429, b"", "text/html", {"Retry-After": "900"})))
    _poll("craigslist")
    row = _row("craigslist")
    assert row["status"] == "rate_limited"
    wait = (datetime.fromisoformat(row["next_poll_after"])
            - datetime.now(timezone.utc)).total_seconds()
    assert wait > 880


def test_timeouts_are_timeout(router):
    router.append((lambda u: True, requests.Timeout("read timed out")))
    _poll("craigslist")
    assert _row("craigslist")["status"] == "timeout"


def test_malformed_response_is_degraded(router):
    router.append((lambda u: "format=rss" in u, FakeResp(200, b"<html>not rss", "text/html")))
    router.append((lambda u: "craigslist" in u, FakeResp(200, HTML_UNKNOWN, "text/html")))
    _poll("craigslist")
    row = _row("craigslist")
    assert row["status"] == "degraded"
    assert "malformed" in row["status_reason"]


def test_valid_empty_never_succeeded_is_unverified(router):
    router.append((lambda u: "format=rss" in u, FakeResp(200, RSS_EMPTY)))
    router.append((lambda u: "craigslist" in u, FakeResp(200, HTML_EMPTY_SEARCH, "text/html")))
    _poll("craigslist")
    assert _row("craigslist")["status"] == "unverified"


def test_valid_empty_after_success_is_healthy_then_degraded_when_persistent(router):
    state = {"body": RSS_OK}
    router.append((lambda u: "format=rss" in u, lambda: FakeResp(200, state["body"])))
    router.append((lambda u: "craigslist" in u, FakeResp(200, HTML_EMPTY_SEARCH, "text/html")))
    _poll("craigslist")
    row = _row("craigslist")
    assert row["status"] == "healthy" and row["listings_new"] == 1
    assert row["last_genuine_success_at"] is not None
    state["body"] = RSS_EMPTY
    for i in range(1, health.EMPTY_POLLS_BEFORE_DEGRADED):
        _poll("craigslist")
        assert _row("craigslist")["status"] == "healthy", i
    _poll("craigslist")
    row = _row("craigslist")
    assert row["status"] == "degraded" and "possible format change" in row["status_reason"]
    assert row["last_genuine_success_at"] is not None   # history kept


def test_partial_failure_is_degraded(router, monkeypatch):
    import sniper
    monkeypatch.setitem(sniper.CONFIG["sources"]["craigslist"], "regions",
                        ["tuscaloosa", "bham"])
    router.append((lambda u: "tuscaloosa" in u and "format=rss" in u, FakeResp(200, RSS_OK)))
    router.append((lambda u: "bham" in u, FakeResp(403, b"", "text/html")))
    _poll("craigslist")
    row = _row("craigslist")
    assert row["status"] == "degraded" and "blocked" in row["status_reason"]


def test_missing_credentials_and_disabled(router, monkeypatch):
    import sniper
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.setitem(sniper.CONFIG["sources"]["hemmings"], "enabled", False)
    out = sniper.poll_once(verbose=False, only_sources=["ebay", "hemmings"], force_all=True)
    assert out["source_status"]["ebay"]["status"] == "auth_config"
    assert out["source_status"]["hemmings"]["status"] == "disabled"
    assert _row("ebay")["status"] == "auth_config"


def test_min_interval_prevents_over_polling(router):
    calls = []
    router.append((lambda u: "format=rss" in u, lambda: calls.append(1) or FakeResp(200, RSS_OK)))
    _poll("craigslist", force=False)
    _poll("craigslist", force=False)               # inside the 60s minimum interval
    assert len(calls) == 1


def test_diagnostics_api_reads_persisted_status(client, router):
    from tests.conftest import register, login
    router.append((lambda u: True, FakeResp(403, b"", "text/html")))
    _poll("craigslist")
    register(client, "d@example.com")
    login(client, "d@example.com")
    diag = {d["id"]: d for d in client.get("/api/diagnostics").get_json()}
    assert diag["craigslist"]["status"] == "blocked"
    assert diag["craigslist"]["requests_ok"] == 0
