"""
tests/test_alert_pipeline.py — title parsing, ingestion normalization, the
alert gate, estimate-based alerts, multi-channel notifications and
saved-search fan-out. Everything here is network-free: comps and outbound
HTTP are monkeypatched.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import register, login, set_plan


# ---------- Title / price parsing -----------------------------------------

@pytest.mark.parametrize("title,year,make,model,price,miles", [
    ("2014 Chevy Silverado 1500 LT 4x4 - $14,500", 2014, "Chevrolet", "Silverado", 14500, None),
    ("2012 F-150 XLT crew cab $11,000", 2012, "Ford", "F-150", 11000, None),
    ("05 Silverado 2500HD Duramax $13,500", 2005, "Chevrolet", "Silverado", 13500, None),
    ("2010 Land Rover Range Rover Sport $9,000", 2010, "Land Rover", "Range Rover", 9000, None),
    ("2018 Toyota Tacoma TRD Off Road $27k", 2018, "Toyota", "Tacoma", 27000, None),
    ("2020 Toyota Camry SE $2,500 down $18,900", 2020, "Toyota", "Camry", 18900, None),
    ("2008 Honda Accord 180k miles $4,200", 2008, "Honda", "Accord", 4200, 180000),
    ("2016 Honda Odyssey minivan $9,000", 2016, "Honda", "Odyssey", 9000, None),
    ("99 Civic hatch $3,000", 1999, "Honda", "Civic", 3000, None),
    ("2009 Honda CR-V EX $6,500", 2009, "Honda", "CR-V", 6500, None),
])
def test_title_parsing(title, year, make, model, price, miles):
    from sources.base import (extract_make_model, extract_miles, extract_price,
                              extract_year)
    assert extract_year(title) == year
    assert extract_make_model(title) == (make, model)
    assert extract_price(title) == price
    assert extract_miles(title) == miles


def test_payment_only_price_is_not_a_price():
    from sources.base import extract_price
    assert extract_price("2016 Toyota Corolla $299/mo") is None
    assert extract_price("2016 Toyota Corolla $1,500 down drive today") is None


@pytest.mark.parametrize("text,salvage,needs_work", [
    ("2014 Nissan Altima rebuilt title $5,000", True, False),
    ("2017 Mustang GT clean title, not rebuilt, never salvaged", False, False),
    ("2011 Accord for parts only", True, False),
    ("2013 Jeep Wrangler needs motor $4,000", False, True),
    ("2015 Camry runs and drives great", False, False),
])
def test_condition_flags(text, salvage, needs_work):
    from sources.base import looks_needs_work, looks_salvage
    assert looks_salvage(text) is salvage
    assert looks_needs_work(text) is needs_work


def test_rss_dates_get_the_freshness_bonus():
    import sniper
    from email.utils import format_datetime
    recent = format_datetime(datetime.now(timezone.utc) - timedelta(minutes=10))
    assert sniper._freshness_factor(recent) == 1.20
    assert sniper._freshness_factor("not a date") == 0.85


# ---------- End-to-end scoring --------------------------------------------

def _listing(sid, price, title=None, year=2015, make="Toyota", model="Tacoma",
             source="craigslist", desc=""):
    from sources.base import NormalizedListing
    return NormalizedListing(
        source=source, source_id=sid, url=f"https://example.com/{sid}",
        title=title or f"{year} {make} {model} - ${price:,}", price=price,
        year=year, make=make, model=model, description=desc,
        posted_at=datetime.now(timezone.utc).isoformat(), is_cash_only=True)


@pytest.fixture()
def pipeline(app, monkeypatch):
    """sniper wired to the test DB, verified comps forced unavailable, and
    every outbound notification captured instead of sent."""
    import sniper
    import comps
    import notifications
    monkeypatch.setattr(comps, "comps_for_listing", lambda **kw: None)
    # Score synchronously in the test; no background scorer threads (they'd
    # race these calls and leak into other tests' thread checks).
    monkeypatch.setattr(sniper, "ensure_scorer_started", lambda: None)
    sent = []
    monkeypatch.setattr(notifications, "notify_deal",
                        lambda deal, **kw: sent.append(("owner", deal)) or ["test"])
    monkeypatch.setattr(notifications, "notify_user_deal",
                        lambda deal, **kw: sent.append(("user", deal, kw)) or ["test"])
    monkeypatch.setitem(sniper.CONFIG, "notify_text_enabled", True)
    monkeypatch.setitem(sniper.CONFIG, "notify_min_score", 0)
    monkeypatch.setitem(sniper.CONFIG, "quiet_start_hour", None)
    monkeypatch.setitem(sniper.CONFIG, "quiet_end_hour", None)
    monkeypatch.setattr(sniper._config_mod, "reload", lambda: None)
    # Ten local peers around $25k → estimate ≈ $23k
    peers = [_listing(f"peer{i}", 24000 + i * 200) for i in range(10)]
    sniper.store_new(peers)
    return sniper, sent


def _alerts():
    import sniper
    with sniper.db() as c:
        return {r["composite_id"]: dict(r) for r in
                c.execute("SELECT * FROM alerts").fetchall()}


def test_estimate_alert_fires_when_sold_comps_unavailable(pipeline):
    sniper, sent = pipeline
    sniper.store_new([_listing("steal", 14000)])
    sniper._score_one_listing("craigslist:steal")
    a = _alerts()
    assert "craigslist:steal" in a
    assert a["craigslist:steal"]["basis"] == "estimate"
    assert any(s[0] == "owner" and s[1]["composite_id"] == "craigslist:steal" for s in sent)


def test_small_estimate_discount_does_not_alert(pipeline):
    sniper, _ = pipeline
    sniper.store_new([_listing("meh", 19000)])          # ~18% under estimate
    sniper._score_one_listing("craigslist:meh")
    assert "craigslist:meh" not in _alerts()


def test_salvage_never_alerts(pipeline):
    sniper, _ = pipeline
    sniper.store_new([_listing("salv", 12000,
                               title="2015 Toyota Tacoma rebuilt title - $12,000")])
    sniper._score_one_listing("craigslist:salv")
    assert "craigslist:salv" not in _alerts()


def test_needs_work_never_alerts(pipeline):
    sniper, _ = pipeline
    sniper.store_new([_listing("blown", 12000, desc="blown head gasket, tow it away")])
    sniper._score_one_listing("craigslist:blown")
    assert "craigslist:blown" not in _alerts()


def test_price_band_is_enforced(pipeline, monkeypatch):
    sniper, _ = pipeline
    monkeypatch.setitem(sniper.CONFIG, "min_price", 13000)
    sniper.store_new([_listing("cheap", 12000)])
    sniper._score_one_listing("craigslist:cheap")
    assert "craigslist:cheap" not in _alerts()


def test_too_good_to_be_true_does_not_alert(pipeline):
    sniper, _ = pipeline
    sniper.store_new([_listing("fake", 4000)])          # ~83% under
    sniper._score_one_listing("craigslist:fake")
    assert "craigslist:fake" not in _alerts()


# ---------- Saved-search fan-out ------------------------------------------

def test_saved_search_match_lands_in_user_tray_and_pushes(client, pipeline):
    sniper, sent = pipeline
    register(client, "flip@example.com")
    set_plan("flip@example.com", "starter")
    login(client, "flip@example.com")
    r = client.post("/api/me/alert_channels", json={"ntfy_topic": "generate"})
    assert r.status_code == 200 and r.get_json()["ntfy_topic"].startswith("sniper-")
    client.post("/api/saved_searches", json={
        "name": "cheap tacos", "notify_enabled": True,
        "filter": {"make": "toyota", "model": "tacoma", "max_price": 16000}})
    client.post("/api/saved_searches", json={
        "name": "hondas", "notify_enabled": True, "filter": {"make": "honda"}})

    sniper.store_new([_listing("taco", 14000)])
    sniper._score_one_listing("craigslist:taco")

    tray = client.get("/api/me/alerts").get_json()
    assert [t["composite_id"] for t in tray] == ["craigslist:taco"]
    assert tray[0]["saved_search_name"] == "cheap tacos"
    user_pushes = [s for s in sent if s[0] == "user"]
    assert len(user_pushes) == 1
    assert user_pushes[0][2]["ntfy_topic"].startswith("sniper-")


def test_saved_search_filter_matching():
    from saved_search_alerts import matches
    deal = {"make": "Toyota", "model": "Tacoma", "price": 14000, "year": 2015,
            "source": "craigslist", "discount_pct": 35, "est_profit": 4000,
            "title": "2015 Toyota Tacoma TRD", "state": "AL"}
    assert matches({"make": "toyota,ford", "max_price": 15000}, deal)
    assert matches({"min_discount_pct": 30, "search": "trd"}, deal)
    assert not matches({"min_profit": 5000}, deal)
    assert not matches({"state": "GA"}, deal)
    assert not matches({"source": "ebay"}, deal)


def test_webhook_must_be_public_https(client, app):
    register(client, "hook@example.com")
    set_plan("hook@example.com", "starter")
    login(client, "hook@example.com")
    for bad in ("http://example.com/hook", "https://127.0.0.1/hook",
                "https://localhost/hook"):
        r = client.post("/api/me/alert_channels", json={"alert_webhook": bad})
        assert r.status_code == 400, bad


def test_free_plan_cannot_set_alert_channels(client, app):
    register(client, "free@example.com")
    login(client, "free@example.com")
    r = client.post("/api/me/alert_channels", json={"ntfy_topic": "generate"})
    assert r.status_code == 402


# ---------- Notification outbox -------------------------------------------

def test_outbox_delivers_every_alert_without_dropping(monkeypatch):
    import notifications
    calls = []

    class _R:
        ok, status_code = True, 200

    monkeypatch.setenv("NTFY_TOPIC", "sniper-test-topic-123")
    for k in ("PUSHOVER_TOKEN", "TWILIO_ACCOUNT_SID", "DISCORD_WEBHOOK_URL",
              "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(notifications.requests, "post",
                        lambda url, **kw: calls.append(kw.get("json")) or _R())
    for i in range(5):
        chans = notifications.notify_deal(
            {"composite_id": f"x:{i}", "year": 2015, "make": "Toyota",
             "model": "Tacoma", "price": 14000, "discount_pct": 35,
             "comp_avg": 21500, "comp_n": 9, "basis": "estimate",
             "url": f"https://example.com/{i}"})
        assert chans == ["ntfy"]
    deadline = time.time() + 5
    while len(calls) < 5 and time.time() < deadline:
        time.sleep(0.05)
    assert len(calls) == 5
    assert calls[0]["topic"] == "sniper-test-topic-123"
    assert calls[0]["click"] == "https://example.com/0"
