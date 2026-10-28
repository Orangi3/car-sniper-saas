"""
tests/test_matching_and_history.py — personalized matching (P2) and price
history / price drops / relists (P3), end to end through ingest(), the
durable score job, user_matches and notify jobs. Sold-comp scraping is
stubbed (no network); valuations come from ingested asking comparables.
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import register, login, logout, set_plan

PEER_PRICES = [10000, 10400, 9800, 10200, 10600, 9900, 10100, 10300, 10500, 9700]


def L(sid, price, title="2015 Toyota Camry SE", odo=130000, source="craigslist",
      desc="clean title, runs great", **kw):
    from sources.base import NormalizedListing
    return NormalizedListing(
        source=source, source_id=sid, url=f"https://example.com/{source}/{sid}",
        title=f"{title} - ${price:,}", price=price, year=2015, make="Toyota",
        model="Camry", odometer=odo, description=desc, is_cash_only=True,
        state="AL", **kw)


@pytest.fixture()
def world(app, monkeypatch):
    import comps
    import sniper
    monkeypatch.setattr(comps, "raw_sold", lambda y, mk, md: ({}, 0))
    monkeypatch.setattr(sniper._config_mod, "reload", lambda: None)
    for k in ("NTFY_TOPIC", "PUSHOVER_TOKEN", "TWILIO_ACCOUNT_SID",
              "DISCORD_WEBHOOK_URL", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setitem(sniper.CONFIG, "notify_text_enabled", True)
    monkeypatch.setitem(sniper.CONFIG, "notify_min_score", 0)
    peers = [L(f"peer{i}", p, odo=100000 + i * 7000) for i, p in enumerate(PEER_PRICES)]
    sniper.ingest(peers)
    score_all()
    return sniper


def score_all():
    import jobqueue, sniper
    return jobqueue.drain({"score": sniper.score_job})


def make_user(client, email, plan="starter", searches=(), topic=True):
    register(client, email)
    set_plan(email, plan)
    login(client, email)
    if topic:
        assert client.post("/api/me/alert_channels",
                           json={"ntfy_topic": "generate"}).status_code == 200
    for name, f in searches:
        r = client.post("/api/saved_searches",
                        json={"name": name, "notify_enabled": True, "filter": f})
        assert r.status_code == 200, r.get_json()
    import auth
    uid = auth.get_user_by_email(email).id
    logout(client)
    return uid


def q(sql, params=()):
    import db
    with db.session() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def user_jobs(uid):
    return [json.loads(j["payload"]) for j in
            q("SELECT payload FROM jobs WHERE kind='notify' AND user_id = ?", (uid,))]


# ---------- P2: personalized matching ------------------------------------

def test_user_alert_independent_of_owner_global_threshold(client, world, monkeypatch):
    monkeypatch.setitem(world.CONFIG, "deal_threshold_pct", 40)   # owner: very strict
    uid = make_user(client, "u@example.com", searches=[
        ("camrys 12%+", {"make": "toyota", "model": "camry", "min_discount_pct": 12})])
    world.ingest([L("t1", 8100)])                                  # ~20% under
    r = score_all()[-1]
    assert r["status"] == "done"
    assert q("SELECT * FROM alerts WHERE composite_id = 'craigslist:t1'") == []
    m = q("SELECT * FROM user_matches WHERE user_id = ?", (uid,))
    assert len(m) == 1 and m[0]["event"] == "new" and m[0]["notified"] == 1
    assert [j["channel"] for j in user_jobs(uid)] == ["ntfy"]
    login(client, "u@example.com")
    tray = client.get("/api/me/alerts").get_json()
    assert tray[0]["composite_id"] == "craigslist:t1"
    assert tray[0]["saved_search_name"] == "camrys 12%+"


def test_users_only_see_their_own_matches(client, world):
    ua = make_user(client, "a@example.com", searches=[("camry", {"model": "camry"})])
    ub = make_user(client, "b@example.com", searches=[("hondas", {"make": "honda"})])
    world.ingest([L("t1", 8100)])
    score_all()
    assert len(q("SELECT * FROM user_matches WHERE user_id = ?", (ua,))) == 1
    assert q("SELECT * FROM user_matches WHERE user_id = ?", (ub,)) == []
    login(client, "b@example.com")
    assert client.get("/api/me/alerts").get_json() == []
    me = client.get("/api/me/alert_channels").get_json()
    a_topic = q("SELECT ntfy_topic FROM users WHERE id = ?", (ua,))[0]["ntfy_topic"]
    assert me["ntfy_topic"] != a_topic


def test_two_matching_searches_one_notification_and_no_repeat(client, world):
    uid = make_user(client, "u@example.com", searches=[
        ("toyotas", {"make": "toyota"}), ("camrys", {"model": "camry"})])
    world.ingest([L("t1", 8100)])
    score_all()
    world.ingest([L("t1", 8100)])          # unchanged re-poll
    score_all()
    from sniper import _score_one_listing
    _score_one_listing("craigslist:t1")    # forced re-score of same price
    assert len(q("SELECT * FROM user_matches WHERE user_id = ?", (uid,))) == 1
    assert len(user_jobs(uid)) == 1


def test_lapsed_plan_and_deleted_search_get_nothing(client, world):
    uid = make_user(client, "u@example.com", searches=[("camry", {"model": "camry"})])
    set_plan("u@example.com", "free")                  # subscription ended
    world.ingest([L("t1", 8100)])
    score_all()
    assert q("SELECT * FROM user_matches WHERE user_id = ?", (uid,)) == []
    set_plan("u@example.com", "starter")
    login(client, "u@example.com")
    sid = client.get("/api/saved_searches").get_json()[0]["id"]
    client.delete(f"/api/saved_searches/{sid}")
    world.ingest([L("t2", 8000)])
    score_all()
    assert q("SELECT * FROM user_matches WHERE user_id = ?", (uid,)) == []


def test_lite_saved_search_limit_and_aggressive_gate(client, app):
    register(client, "lite@example.com")
    set_plan("lite@example.com", "starter")
    login(client, "lite@example.com")
    for i in range(3):
        assert client.post("/api/saved_searches", json={
            "name": f"s{i}", "filter": {}}).status_code == 200
    r = client.post("/api/saved_searches", json={"name": "s4", "filter": {}})
    assert r.status_code == 402 and r.get_json()["limit"] == 3
    set_plan("lite@example.com", "pro")
    login(client, "lite@example.com")
    assert client.post("/api/saved_searches", json={
        "name": "agg", "filter": {"risk_mode": "aggressive"}}).status_code == 200
    set_plan("lite@example.com", "starter")
    login(client, "lite@example.com")
    r = client.post("/api/saved_searches", json={
        "name": "agg2", "filter": {"risk_mode": "aggressive"}})
    assert r.status_code == 402


def test_conservative_skips_risky_aggressive_pro_gets_it_labeled(client, world):
    cons = make_user(client, "c@example.com", searches=[("c", {"model": "camry"})])
    aggr = make_user(client, "g@example.com", plan="pro",
                     searches=[("g", {"model": "camry", "risk_mode": "aggressive"})])
    world.ingest([L("dealer1", 8100, desc="dealer special, financing available")])
    score_all()
    nm = q("SELECT trust, risk_flags FROM near_misses WHERE composite_id = 'craigslist:dealer1'")[0]
    assert "dealer" in json.loads(nm["risk_flags"])
    # dealer alone isn't blocking; make it risky with a scam-ish claim instead
    # scam score 55: risky (>=50) but below the 80 auto-hide line
    world.ingest([L("risky", 8100, desc="currently deployed, title in the mail")])
    score_all()
    nm = q("SELECT trust FROM near_misses WHERE composite_id = 'craigslist:risky'")[0]
    assert nm["trust"] == "risk"
    got_c = {m["composite_id"] for m in q("SELECT composite_id FROM user_matches WHERE user_id=?", (cons,))}
    got_g = {m["composite_id"] for m in q("SELECT composite_id, trust FROM user_matches WHERE user_id=?", (aggr,))}
    assert "craigslist:risky" not in got_c
    assert "craigslist:risky" in got_g
    assert q("SELECT trust FROM user_matches WHERE user_id=? AND composite_id='craigslist:risky'",
             (aggr,))[0]["trust"] == "risk"


def test_daily_cap_keeps_tray_but_stops_notifying(client, world, monkeypatch):
    monkeypatch.setenv("PLAN_LIMITS_JSON", json.dumps({"starter": {"alerts_per_day": 1}}))
    uid = make_user(client, "u@example.com", searches=[("c", {"model": "camry"})])
    world.ingest([L("t1", 8100, odo=120000), L("t2", 8000, odo=125000)])
    score_all()
    m = q("SELECT notified FROM user_matches WHERE user_id = ? ORDER BY id", (uid,))
    assert [x["notified"] for x in m] == [1, 0]
    assert len(user_jobs(uid)) == 1


# ---------- P3: price history, drops, relists ----------------------------

def test_price_drop_9000_to_6500_is_an_opportunity(client, world, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "sniper-owner-topic-123")
    monkeypatch.setitem(world.CONFIG, "deal_threshold_pct", 25)
    pro = make_user(client, "pro@example.com", plan="pro", searches=[
        ("camry", {"model": "camry", "min_discount_pct": 15})])
    lite = make_user(client, "lite@example.com", searches=[
        ("camry", {"model": "camry", "min_discount_pct": 15})])
    world.ingest([L("drop", 9000)])                 # ~11% under: below 15%
    score_all()
    assert q("SELECT * FROM user_matches") == []

    res = world.ingest([L("drop", 6500)])           # same listing, new price
    assert [l.composite_id() for l in res["price_changed"]] == ["craigslist:drop"]
    row = q("SELECT price, previous_price, price_changed_at FROM listings "
            "WHERE composite_id = 'craigslist:drop'")[0]
    assert (row["price"], row["previous_price"]) == (6500, 9000)
    hist = q("SELECT price, event FROM listing_prices WHERE composite_id = 'craigslist:drop' "
             "ORDER BY id")
    assert [(h["price"], h["event"]) for h in hist] == [(9000, "first_seen"),
                                                       (6500, "price_change")]
    r = score_all()
    assert r and r[-1]["status"] == "done"
    pm = q("SELECT event, price, previous_price FROM user_matches WHERE user_id = ?", (pro,))
    assert pm == [{"event": "price_drop", "price": 6500, "previous_price": 9000}]
    lm = q("SELECT event FROM user_matches WHERE user_id = ?", (lite,))
    assert lm == [{"event": "new"}]                 # newly qualifies; no drop feature
    owner = [json.loads(j["payload"]) for j in
             q("SELECT payload FROM jobs WHERE kind='notify' AND user_id IS NULL")]
    assert owner and owner[0]["deal"]["event"] == "price_drop"
    assert owner[0]["deal"]["previous_price"] == 9000


def test_unchanged_repoll_is_not_rescored(world):
    world.ingest([L("same", 9000)])
    score_all()
    n_jobs = len(q("SELECT id FROM jobs WHERE kind='score'"))
    before = q("SELECT last_seen_at FROM listings WHERE composite_id='craigslist:same'")[0]
    res = world.ingest([L("same", 9000)])
    assert res["new"] == [] and res["price_changed"] == [] and res["seen"] == 1
    assert len(q("SELECT id FROM jobs WHERE kind='score'")) == n_jobs
    after = q("SELECT last_seen_at FROM listings WHERE composite_id='craigslist:same'")[0]
    assert after["last_seen_at"] >= before["last_seen_at"]


def _age_listing(cid, hours):
    import db
    from datetime import datetime, timedelta, timezone
    with db.session() as c:
        c.execute("UPDATE listings SET last_seen_at = ? WHERE composite_id = ?",
                  ((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(), cid))


def test_relisted_cheaper_is_detected_as_price_drop(world):
    world.ingest([L("tuscaloosa:111", 9000, title="2015 Toyota Camry SE one owner",
                    odo=142345)])
    _age_listing("craigslist:tuscaloosa:111", 24)      # old post taken down
    world.ingest([L("tuscaloosa:222", 7900, title="2015 Toyota Camry SE one owner",
                    odo=142345)])
    row = q("SELECT relist_of, previous_price FROM listings "
            "WHERE composite_id = 'craigslist:tuscaloosa:222'")[0]
    assert row == {"relist_of": "craigslist:tuscaloosa:111", "previous_price": 9000}
    assert q("SELECT event FROM listing_prices WHERE composite_id = "
             "'craigslist:tuscaloosa:222'") == [{"event": "relist"}]


def test_two_live_identical_posts_are_not_a_relist(world):
    world.ingest([L("tuscaloosa:1", 9000, title="2015 Toyota Camry SE", odo=128000),
                  L("tuscaloosa:2", 8500, title="2015 Toyota Camry SE", odo=128000)])
    rows = q("SELECT relist_of FROM listings WHERE composite_id LIKE 'craigslist:tuscaloosa:%'")
    assert all(r["relist_of"] is None for r in rows)


def test_lookalike_cars_are_not_merged(world):
    world.ingest([L("x1", 9000, title="2015 Toyota Camry SE", odo=None),
                  L("x2", 9000, title="2015 Toyota Camry LE", odo=None),
                  L("x3", 9000, title="2015 Toyota Camry SE", odo=150000),
                  L("x4", 9000, title="2015 Toyota Camry SE", odo=90000)])
    rows = q("SELECT relist_of, vehicle_key FROM listings WHERE composite_id LIKE 'craigslist:x%'")
    assert all(r["relist_of"] is None and r["vehicle_key"] is None for r in rows)


def test_same_vin_on_two_sources_notifies_once(client, world):
    uid = make_user(client, "u@example.com", searches=[("c", {"model": "camry"})])
    vin = "1HGCM82633A004352"
    world.ingest([L("v1", 8100, desc=f"VIN {vin} clean title")])
    score_all()
    world.ingest([L("v2", 8100, source="ebay", desc=f"vin: {vin}")])
    score_all()
    keys = q("SELECT vehicle_key FROM listings WHERE composite_id IN "
             "('craigslist:v1','ebay:v2')")
    assert {k["vehicle_key"] for k in keys} == {f"vin:{vin}"}
    assert len(q("SELECT * FROM user_matches WHERE user_id = ?", (uid,))) == 1


def test_invalid_vin_like_tokens_are_ignored(world):
    world.ingest([L("nv", 8100, desc="stock ABCDEFGH123456789 ask for 1HGCM82633A004353")])
    assert q("SELECT vin FROM listings WHERE composite_id='craigslist:nv'") == [{"vin": None}]
