"""
tests/test_e2e_pipeline.py — the whole chain, not modules in isolation:

  provider response → normalization → dedup → persistence → comparables →
  valuation → profit → opportunity → saved-search match → alert job →
  delivery → deals API

Scenarios not already covered elsewhere (see the coverage table in the
module below). Network-free: the provider is a SYNTHETIC eBay-shaped
response, notifications go to a stubbed HTTP client.
"""
from __future__ import annotations

import json

import pytest
import requests

from tests.conftest import login, logout
from tests.test_matching_and_history import L, make_user, q, score_all, world  # noqa: F401

# Scenario → where it is covered
#  1 new undervalued vehicle ............ test_provider_response_to_dashboard (here)
#  2 no valid price ...................... test_listing_without_price_is_counted_not_stored
#  3 insufficient comparables ........... test_insufficient_evidence_never_matches
#  4 salvage looks profitable ........... test_alert_pipeline::test_salvage_never_alerts
#  5 duplicate across sources ........... test_matching_and_history::test_same_vin_on_two_sources_notifies_once
#  6 material price reduction ........... test_matching_and_history::test_price_drop_9000_to_6500_is_an_opportunity
#  7 outside buying radius .............. test_territory_and_ranking::test_tuscaloosa_buyer_gets_birmingham_not_mobile
#  8 dealer vs private conflict ......... test_valuation + test_ebay_api (unknown sellers)
#  9 high repair costs .................. test_high_repair_allowance_blocks_min_profit_match
# 10 legit deal under threshold ......... test_alert_pipeline::test_small_estimate_discount_does_not_alert
# 11 notification provider timeout ...... test_notification_timeout_retries_then_delivers_once
# 12 worker restart before delivery ..... test_jobqueue::test_pending_notification_survives_worker_kill_and_restart
# 13 malformed source data .............. test_source_health + test_source_schema
# 14 expired / invalid credential ....... test_rejected_credential_is_persisted_as_auth_config
# 15 database interruption .............. test_db_failure_mid_ingest_rolls_back_and_recovers

NEAR = {"city": "Northport", "stateOrProvince": "AL", "postalCode": "35473", "country": "US"}


def _ebay_item(item_id, price, title="2015 Toyota Camry SE 130k miles", seller="INDIVIDUAL"):
    return {"itemId": item_id, "title": title,
            "itemWebUrl": f"https://www.ebay.test/itm/{item_id}",
            "price": {"value": f"{price}.00", "currency": "USD"},
            "buyingOptions": ["FIXED_PRICE"], "itemLocation": NEAR,
            "itemCreationDate": "2026-10-28T12:00:00.000Z",
            "seller": {"sellerAccountType": seller},
            "distanceFromPickupLocation": {"value": "6", "unitOfMeasure": "MILE"}}


class R:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.text = json.dumps(body if body is not None else {})
        self.headers = {"Content-Type": "application/json"}
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


@pytest.fixture()
def ebay_feed(world, monkeypatch):
    """eBay enabled with fake credentials; requests.get serves `state['items']`;
    requests.post serves the OAuth token and records notification posts."""
    import sources.ebay_motors as eb
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(eb, "_TOKEN", {"value": None, "expires_at": 0})
    state = {"items": [], "search_status": 200, "token_status": 200, "pushes": [],
             "push_error": None}

    def get(url, params=None, **kw):
        return R(state["search_status"], {"total": len(state["items"]),
                                          "itemSummaries": state["items"]})

    def post(url, **kw):
        if "oauth2/token" in url:
            return R(state["token_status"], {"access_token": "tok", "expires_in": 7200})
        if state["push_error"]:
            err, state["push_error"] = state["push_error"], None
            raise err
        state["pushes"].append(kw.get("json"))
        return R(200, {})
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "post", post)
    import notifications
    monkeypatch.setattr(notifications.requests, "post", post)
    return state


def _poll():
    import sniper
    return sniper.poll_once(verbose=False, only_sources=["ebay"], force_all=True)


def _deliver():
    import jobqueue
    import notifications
    return jobqueue.drain({"notify": notifications.deliver_job})


def test_provider_response_to_dashboard(client, ebay_feed):
    uid = make_user(client, "buyer@example.com", searches=[("camry", {"max_price": 9000})])
    ebay_feed["items"] = [_ebay_item("v1|500|0", 6900)]
    out = _poll()
    assert out["source_status"]["ebay"]["status"] == "healthy"
    assert out["source_status"]["ebay"]["listings_new"] == 1
    row = q("SELECT * FROM listings WHERE composite_id = 'ebay:v1|500|0'")[0]
    assert (row["zip"], row["location_source"]) == ("35473", "zip_centroid")
    assert json.loads(row["extras"])["seller_type"] == "individual"     # provenance kept
    score_all()
    nm = q("SELECT * FROM near_misses WHERE composite_id = 'ebay:v1|500|0'")[0]
    assert nm["evidence_class"] == "asking_comparables" and nm["confidence"] == "medium"
    assert q("SELECT * FROM user_matches WHERE user_id = ?", (uid,))
    done = _deliver()
    assert any(d["status"] == "done" for d in done) and ebay_feed["pushes"]
    login(client, "buyer@example.com")
    deals = client.get("/api/deals?view=all").get_json()
    d = next(x for x in deals if x["composite_id"] == "ebay:v1|500|0")
    assert d["profit"]["base"] > 0 and d["opportunity"]["risk_adjusted_profit"] > 0
    # re-poll: idempotent — no new listing, no second notification
    n_push = len(ebay_feed["pushes"])
    assert _poll()["source_status"]["ebay"]["listings_new"] == 0
    score_all(); _deliver()
    assert len(ebay_feed["pushes"]) == n_push


def test_listing_without_price_is_counted_not_stored(ebay_feed):
    item = _ebay_item("v1|501|0", 6900)
    item["price"] = {"currency": "USD"}
    ebay_feed["items"] = [item, _ebay_item("v1|502|0", 7100)]
    st = _poll()["source_status"]["ebay"]
    assert st["listings_new"] == 1 and st["listings_dropped_no_price"] == 1
    assert "1 listing(s) without a price skipped" in st["reason"]
    assert not q("SELECT 1 FROM listings WHERE composite_id = 'ebay:v1|501|0'")


def test_insufficient_evidence_never_matches(client, ebay_feed):
    uid = make_user(client, "saab@example.com", searches=[("any", {})])
    ebay_feed["items"] = [_ebay_item("v1|503|0", 2500, title="2006 Saab 9-3 Aero 105k miles")]
    _poll(); score_all()
    nm = q("SELECT evidence_class FROM near_misses WHERE composite_id = 'ebay:v1|503|0'")
    assert nm and nm[0]["evidence_class"] == "insufficient"
    assert not q("SELECT 1 FROM user_matches WHERE user_id = ?", (uid,))
    assert not q("SELECT 1 FROM alerts WHERE composite_id = 'ebay:v1|503|0'")


def test_high_repair_allowance_blocks_min_profit_match(client, ebay_feed):
    careful = make_user(client, "careful@example.com",
                        searches=[("camry", {"min_profit": 1000})])
    login(client, "careful@example.com")
    assert client.post("/api/me/profit_settings",
                       json={"mechanical": 4000}).status_code == 200
    logout(client)
    easy = make_user(client, "easy@example.com", searches=[("camry", {"min_profit": 1000})])
    ebay_feed["items"] = [_ebay_item("v1|504|0", 6900)]
    _poll(); score_all()
    assert q("SELECT 1 FROM user_matches WHERE user_id = ?", (easy,))
    assert not q("SELECT 1 FROM user_matches WHERE user_id = ?", (careful,))


def test_notification_timeout_retries_then_delivers_once(client, ebay_feed):
    import db
    uid = make_user(client, "t@example.com", searches=[("camry", {"max_price": 9000})])
    ebay_feed["items"] = [_ebay_item("v1|505|0", 6900)]
    _poll(); score_all()
    ebay_feed["push_error"] = requests.Timeout("provider timed out")
    first = _deliver()
    assert all(d["status"] != "dead" for d in first)
    job = q("SELECT * FROM jobs WHERE kind='notify' AND user_id = ?", (uid,))[0]
    assert job["status"] == "pending" and job["attempts"] == 1
    with db.session() as c:                        # skip the backoff wait
        c.execute("UPDATE jobs SET run_after = ? WHERE id = ?",
                  ("2000-01-01T00:00:00+00:00", job["id"]))
    _deliver()
    job = q("SELECT * FROM jobs WHERE id = ?", (job["id"],))[0]
    assert job["status"] == "done"
    assert len([p for p in ebay_feed["pushes"] if p and "v1|505|0" in json.dumps(p)]) == 1


def test_rejected_credential_is_persisted_as_auth_config(ebay_feed):
    ebay_feed["token_status"] = 401
    out = _poll()
    assert out["source_status"]["ebay"]["status"] == "auth_config"
    row = q("SELECT status, status_reason FROM source_health WHERE source_id = 'ebay'")[0]
    assert row["status"] == "auth_config" and "rejected" in row["status_reason"]


def test_db_failure_mid_ingest_rolls_back_and_recovers(ebay_feed, monkeypatch):
    import sniper
    ebay_feed["items"] = [_ebay_item("v1|506|0", 6900), _ebay_item("v1|507|0", 7000)]
    real = sniper.enqueue_score
    calls = {"n": 0}

    def flaky(conn, cid, price):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("server closed the connection unexpectedly")
        return real(conn, cid, price)
    monkeypatch.setattr(sniper, "enqueue_score", flaky)
    with pytest.raises(RuntimeError):
        _poll()                                  # the tick fails loudly ...
    assert not q("SELECT 1 FROM listings WHERE source = 'ebay'")   # ... and atomically
    assert not q("SELECT 1 FROM jobs WHERE dedupe_key LIKE 'score:ebay:%'")
    monkeypatch.setattr(sniper, "enqueue_score", real)
    assert _poll()["source_status"]["ebay"]["listings_new"] == 2   # next tick recovers
    assert len(q("SELECT 1 FROM jobs WHERE dedupe_key LIKE 'score:ebay:%'")) == 2
