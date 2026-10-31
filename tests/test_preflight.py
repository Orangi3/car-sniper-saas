"""tests/test_preflight.py — readiness levels are earned, not assumed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

KEYS = ("SNIPER_COMMERCIAL_MODE", "BILLING_ORIGIN", "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_STARTER", "STRIPE_PRICE_PRO",
        "B2_BUCKET", "B2_KEY_ID", "B2_APPLICATION_KEY", "AGE_RECIPIENT",
        "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "MARKETCHECK_COMMERCIAL_AGREEMENT",
        "EBAY_COMMERCIAL_AGREEMENT", "SNIPER_TRUSTED_PROXIES")


@pytest.fixture()
def pf(app, monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    from config import CONFIG
    monkeypatch.setitem(CONFIG, "commercial_mode", False)
    import preflight
    return preflight, CONFIG


def _tick(minutes_ago=1):
    import db
    t = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with db.session() as c:
        c.execute("UPDATE scheduler_state SET last_tick_at = ? WHERE id = 1", (t,))


def _healthy(source, hours_ago=1):
    import db
    t = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    with db.session() as c:
        c.execute("INSERT INTO source_health(source_id, status, last_genuine_success_at, "
                  "updated_at) VALUES (?, 'healthy', ?, ?)", (source, t, t))


def test_never_ticked_is_not_ready(pf):
    preflight, _ = pf
    r = preflight.run()
    assert r["ready_for"] == "not_ready"
    assert any("scheduler" in b for b in r["blockers"]["private_internal_testing"])


def test_internal_only_without_licensed_data(pf):
    preflight, _ = pf
    _tick()
    r = preflight.run()
    assert r["ready_for"] == "private_internal_testing"
    free = " ".join(r["blockers"]["controlled_free_beta"])
    assert "commercial mode" in free and "licensed data" in free and "https" in free


def test_unlicensed_healthy_source_does_not_unlock_free_beta(pf, monkeypatch):
    preflight, CONFIG = pf
    _tick(); _healthy("craigslist")
    monkeypatch.setitem(CONFIG, "commercial_mode", True)
    monkeypatch.setenv("BILLING_ORIGIN", "https://beta.example.com")
    r = preflight.run()
    assert r["ready_for"] == "private_internal_testing"
    assert any("craigslist" in c["check"] and c["category"] == "licensing"
               for c in r["checks"])


def test_licensed_fresh_source_unlocks_free_beta_but_not_paid(pf, monkeypatch):
    preflight, CONFIG = pf
    _tick(); _healthy("ebay")
    monkeypatch.setitem(CONFIG, "commercial_mode", True)
    monkeypatch.setenv("EBAY_COMMERCIAL_AGREEMENT", "EPN approval ref")
    monkeypatch.setenv("BILLING_ORIGIN", "https://beta.example.com")
    monkeypatch.setenv("SNIPER_TRUSTED_PROXIES", "127.0.0.1,::1")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc123secretvalue")
    r = preflight.run()
    assert r["ready_for"] == "controlled_free_beta"
    paid = " ".join(r["blockers"]["controlled_paid_beta"])
    assert "TEST key" in paid and "STRIPE_PRICE_PRO" in paid


def test_behind_the_proxy_without_trusted_proxies_is_not_a_free_beta(pf, monkeypatch):
    """Every visitor would share one login-attempt budget (auth.client_ip)."""
    preflight, CONFIG = pf
    _tick(); _healthy("ebay")
    monkeypatch.setitem(CONFIG, "commercial_mode", True)
    monkeypatch.setenv("EBAY_COMMERCIAL_AGREEMENT", "EPN approval ref")
    monkeypatch.setenv("BILLING_ORIGIN", "https://beta.example.com")
    r = preflight.run()
    assert r["ready_for"] == "private_internal_testing"
    assert any("SNIPER_TRUSTED_PROXIES" in b for b in r["blockers"]["controlled_free_beta"])


def test_stale_source_is_unverified(pf, monkeypatch):
    preflight, CONFIG = pf
    _tick(); _healthy("ebay", hours_ago=72)
    monkeypatch.setitem(CONFIG, "commercial_mode", True)
    monkeypatch.setenv("BILLING_ORIGIN", "https://beta.example.com")
    r = preflight.run()
    assert r["ready_for"] == "private_internal_testing"


def test_worker_backlog_fails_infrastructure(pf):
    preflight, _ = pf
    _tick()
    import db, jobqueue
    with db.session() as c:
        jobqueue.enqueue(c, "score", "stuck", {},
                         run_after=datetime.now(timezone.utc) - timedelta(minutes=30))
    r = preflight.run()
    assert r["ready_for"] == "not_ready"
    assert any("jobs.worker" in b for b in r["blockers"]["private_internal_testing"])


def test_secrets_are_never_printed(pf, monkeypatch, capsys):
    preflight, _ = pf
    _tick()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_SUPERSECRET999")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_ALSOSECRET")
    preflight.main([])
    preflight.main(["--json"])
    out = capsys.readouterr().out
    assert "SUPERSECRET999" not in out and "ALSOSECRET" not in out


def test_paid_beta_reachable_only_with_everything(pf, monkeypatch):
    import db
    if not db.IS_PG:
        pytest.skip("paid beta requires PostgreSQL (run with SNIPER_TEST_PG_URL)")
    preflight, CONFIG = pf
    _tick(); _healthy("ebay")
    monkeypatch.setitem(CONFIG, "commercial_mode", True)
    for k, v in {"BILLING_ORIGIN": "https://beta.example.com",
                 "EBAY_COMMERCIAL_AGREEMENT": "EPN approval ref",
                 "SNIPER_TRUSTED_PROXIES": "127.0.0.1,::1",
                 "STRIPE_SECRET_KEY": "sk_live_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
                 "STRIPE_PRICE_STARTER": "price_a", "STRIPE_PRICE_PRO": "price_b",
                 "B2_BUCKET": "b", "B2_KEY_ID": "k", "B2_APPLICATION_KEY": "s",
                 "AGE_RECIPIENT": "age1x"}.items():
        monkeypatch.setenv(k, v)
    r = preflight.run()
    assert r["ready_for"] == "controlled_paid_beta", r["blockers"]
    assert preflight.main(["--target", "paid"]) == 0
