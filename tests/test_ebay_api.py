"""
tests/test_ebay_api.py — eBay Browse adapter behaviour against documented
API rules (auth failures, token expiry, pagination, currency, seller type)
and the live-probe aggregates. Network-free, SYNTHETIC fixtures only.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

EBAY = json.loads((Path(__file__).parent / "fixtures" / "ebay_browse_page.json").read_text())


class R:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.text = json.dumps(body if body is not None else {})
        self.headers = {"Content-Type": "application/json"}
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


@pytest.fixture()
def eb(app, monkeypatch):
    import sources.ebay_motors as eb
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(eb, "_TOKEN", {"value": None, "expires_at": 0})
    st = {"token": [R(200, {"access_token": "t1", "expires_in": 7200})],
          "pages": [R(200, EBAY)], "gets": [], "posts": 0}

    def post(*a, **k):
        st["posts"] += 1
        return st["token"].pop(0) if len(st["token"]) > 1 else st["token"][0]

    def get(url, params=None, headers=None, **k):
        st["gets"].append({"params": dict(params or {}), "headers": dict(headers or {})})
        return st["pages"].pop(0) if len(st["pages"]) > 1 else st["pages"][0]
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    return eb, st


def _poll(eb):
    from sources import health
    rep = health.begin("ebay")
    out = eb.poll({"zip": "35401", "radius_mi": 150})
    return out, rep, health.classify(rep, len(out))


def test_rejected_credentials_are_auth_config(eb):
    mod, st = eb
    st["token"] = [R(401, {"error": "invalid_client"})]
    out, rep, (status, _) = _poll(mod)
    assert out == [] and status == "auth_config" and st["gets"] == []


def test_token_endpoint_outage_is_not_reported_as_bad_credentials(eb):
    mod, st = eb
    st["token"] = [R(503, {})]
    _, _, (status, reason) = _poll(mod)
    assert status == "degraded" and "http_error" in reason


def test_expired_token_is_dropped_and_replaced_next_poll(eb):
    mod, st = eb
    st["token"] = [R(200, {"access_token": "t1", "expires_in": 7200}),
                   R(200, {"access_token": "t2", "expires_in": 7200})]
    st["pages"] = [R(401, {}), R(200, EBAY)]
    _, _, (status, _) = _poll(mod)
    assert status == "auth_config" and mod._TOKEN["value"] is None
    out, _, (status, _) = _poll(mod)
    assert status == "healthy" and len(out) == 3
    assert st["gets"][-1]["headers"]["Authorization"] == "Bearer t2"


def test_request_shape_matches_documented_browse_usage(eb):
    mod, st = eb
    _poll(mod)
    g = st["gets"][0]
    f = g["params"]["filter"]
    for part in ("deliveryOptions:{SELLER_ARRANGED_LOCAL_PICKUP}", "pickupCountry:US",
                 "pickupPostalCode:35401", "pickupRadius:150", "pickupRadiusUnit:mi"):
        assert part in f                      # pickup filters only work together
    assert g["params"]["offset"] == 0 and g["params"]["limit"] == 200
    assert g["headers"]["X-EBAY-C-ENDUSERCTX"] == "contextualLocation=country%3DUS%2Czip%3D35401"
    assert g["headers"]["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"


def _page(start, n, next_=True):
    base = EBAY["itemSummaries"][0]
    items = [{**base, "itemId": f"v1|{start + i}|0",
              "itemWebUrl": f"https://www.ebay.test/itm/{start + i}"} for i in range(n)]
    return R(200, {"total": 999, "itemSummaries": items, **({"next": "x"} if next_ else {})})


def test_full_new_page_fetches_one_more_and_dedups_repeats(eb, monkeypatch):
    mod, st = eb
    monkeypatch.setattr(mod, "MAX_PAGES", 3)
    p2 = _page(199, 200, next_=False)               # overlaps item 199 of page 1
    st["pages"] = [_page(0, 200), p2]
    out, _, (status, _) = _poll(mod)
    assert [g["params"]["offset"] for g in st["gets"]] == [0, 200]
    assert len(out) == 399 and len({x.source_id for x in out}) == 399
    assert status == "healthy"


def test_paging_stops_when_caught_up(eb, monkeypatch):
    import sniper
    mod, st = eb
    monkeypatch.setattr(mod, "MAX_PAGES", 3)
    st["pages"] = [_page(0, 200)]
    first, _, _ = _poll(mod)
    sniper.store_new(first[:5])                     # some already ingested
    st["gets"].clear()
    _poll(mod)
    assert len(st["gets"]) == 1                     # no second page


def test_non_usd_price_is_not_used(eb):
    mod, _ = eb
    it = copy.deepcopy(EBAY["itemSummaries"][0])
    it["price"] = {"value": "7900.00", "currency": "CAD"}
    n = mod.normalize(it)
    assert n.price is None and n.extras["currency"] == "CAD"
    it["price"] = {"currency": "USD"}               # missing value
    assert mod.normalize(it).price is None


def test_us_listings_without_account_type_are_unknown_not_private(app):
    import valuation as V
    from datetime import timedelta
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    seen = (now - timedelta(days=1)).isoformat()

    def peer(i, price, **kw):
        return {"composite_id": f"x:{i}", "source": "ebay", "title": "2015 Toyota Camry",
                "price": price, "year": 2015, "make": "Toyota", "model": "Camry",
                "odometer": None, "last_seen_at": seen, **kw}
    unk = [peer(i, 12000 + i * 100, extras='{"seller_type": "unknown"}') for i in range(4)]
    priv = [peer(10 + i, 9000 + i * 100) for i in range(3)]
    tgt = {"composite_id": "t", "title": "2015 Toyota Camry", "price": 7000, "year": 2015,
           "make": "Toyota", "model": "Camry", "odometer": None}
    v = V.value_listing(tgt, unk + priv, now=now)
    assert v.value_mid < 10000                       # private peers used, not unknowns
    assert v.segments["unknown_seller_asking"]["n"] == 4
    assert v.segments["private_asking"]["n"] == 3
    only_unk = V.value_listing(tgt, unk, now=now)
    assert only_unk.dealer_share == 1.0              # discounted like retail
    assert any("unknown-seller" in r for r in only_unk.reasons)
    import sniper
    _, flags = sniper.assess_risk({**tgt, "extras": '{"seller_type": "unknown"}',
                                   "odometer": 1}, only_unk, 0.1)
    assert "seller_type_unknown" in flags and "dealer" not in flags


def test_probe_reports_territory_metrics_and_records_aggregates(eb):
    import verify_sources as vs
    import db
    mod, st = eb
    rep = vs.run(["ebay"], zip_code="35401", radius=150)
    r = rep["results"][0]
    assert r["unique_vehicles"] == 3 and r["duplicate_rate_pct"] == 0.0
    # fixture distances 3 and 41 mi kept; 180 mi dropped by the 150 mi radius
    assert r["distance_mi"] == {"le_50": 2, "le_100": 0, "le_150": 0, "gt_150": 0}
    assert r["listing_age_hours"] is not None and r["elapsed_s"] >= 0
    assert r["rate_limited"] is False
    assert vs.record(rep, "test") == 1 and vs.record(rep, "test") == 0   # idempotent
    with db.session() as c:
        row = dict(c.execute("SELECT * FROM source_probe_runs").fetchone())
    assert row["source"] == "ebay" and row["listings"] == 3 and row["origin"] == "test"
    assert "ebay.test" not in row["summary"]          # aggregates only


def test_api_base_selection(monkeypatch):
    import sources.ebay_motors as eb
    monkeypatch.delenv("EBAY_API_BASE", raising=False)
    monkeypatch.delenv("EBAY_ENVIRONMENT", raising=False)
    assert eb._api_base() == "https://api.ebay.com"
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    assert eb._api_base() == "https://api.sandbox.ebay.com"
    monkeypatch.setenv("EBAY_API_BASE", "http://127.0.0.1:9999/")
    assert eb._api_base() == "http://127.0.0.1:9999"
    monkeypatch.setenv("EBAY_API_BASE", "http://evil.example.com")
    with pytest.raises(ValueError):
        eb._api_base()               # credentials never go over plain http remotely


def test_sandbox_probe_is_labeled_not_real_market_data(eb, monkeypatch):
    import verify_sources as vs
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    r = vs.run(["ebay"])["results"][0]
    assert r["provider_environment"] == "sandbox" and r["real_market_data"] is False


def _err(status, error_id, message):
    return R(status, {"errors": [{"errorId": error_id, "domain": "ACCESS",
                                  "category": "REQUEST", "message": message}]})


def test_insufficient_permissions_is_an_access_problem_with_a_clear_reason(eb):
    mod, st = eb
    st["pages"] = [_err(403, 1100, "Insufficient permissions to fulfill the request.")]
    _, rep, (status, reason) = _poll(mod)
    assert status == "auth_config"                      # not "blocked"
    assert "Partner Network" in rep.attempts[-1]["detail"]


def test_provider_error_text_is_kept_for_diagnosis(eb):
    mod, st = eb
    st["pages"] = [_err(400, 12001, "The value of the 'pickupRadius' filter is invalid.")]
    _, rep, (status, reason) = _poll(mod)
    assert status == "degraded"
    assert "pickupRadius" in rep.attempts[-1]["detail"]


def test_a_real_block_stays_blocked(eb):
    mod, st = eb
    st["pages"] = [R(403, {"errors": [{"errorId": 1, "message": "Forbidden"}]})]
    _, _, (status, _) = _poll(mod)
    assert status == "blocked"


def test_epn_campaign_adds_affiliate_context_and_links(eb, monkeypatch):
    mod, st = eb
    monkeypatch.setenv("EBAY_EPN_CAMPAIGN_ID", "5338000000")
    page = copy.deepcopy(EBAY)
    page["itemSummaries"][0]["itemAffiliateWebUrl"] = "https://www.ebay.test/itm/1001?mkcid=1&campid=5338000000"
    st["pages"] = [R(200, page)]
    out, _, _ = _poll(mod)
    ctx = st["gets"][0]["headers"]["X-EBAY-C-ENDUSERCTX"]
    assert ctx.startswith("affiliateCampaignId=5338000000,contextualLocation=")
    by = {x.source_id: x for x in out}
    assert "campid=5338000000" in by["v1|1001|0"].url
    assert by["v1|1002|0"].url == "https://www.ebay.test/itm/1002"   # no affiliate url given
    monkeypatch.setenv("EBAY_EPN_CAMPAIGN_ID", "abc;evil")         # non-numeric ignored
    assert "affiliateCampaignId" not in mod._enduser_ctx("35401")
