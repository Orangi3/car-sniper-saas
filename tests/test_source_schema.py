"""
tests/test_source_schema.py — provider schema checks, eBay normalization and
the bounded live-source probe (verify_sources.py).

Network-free: HTTP is routed to SYNTHETIC fixtures shaped after the
providers' documented schemas. Nothing here proves a live endpoint behaves
this way — that is what `python -m verify_sources` is for.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import requests

FIXDIR = Path(__file__).parent / "fixtures"
EBAY = json.loads((FIXDIR / "ebay_browse_page.json").read_text())
FSBO = json.loads((FIXDIR / "marketcheck_fsbo_page.json").read_text())


class R:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.text = json.dumps(body if body is not None else {})
        self.headers = {"Content-Type": "application/json"}
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


# ---------- schema.py -------------------------------------------------------

def test_check_items_reports_missing_wrong_type_and_coverage():
    from sources import schema
    spec = {"id": str, "price.value": schema.NUM, "zip?": str}
    items = [{"id": "a", "price": {"value": "10"}, "zip": "35401"},
             {"id": "b", "price": {"value": "n/a"}},
             {"price": {"value": 5}}]
    r = schema.check_items(items, spec)
    assert r["n"] == 3 and r["bad_items"] == 2
    assert r["problems"] == {"price.value: wrong type": 1, "id: missing": 1}
    assert r["coverage"] == {"id": 66.7, "price.value": 100.0, "zip": 33.3}
    assert not schema.drifted(r, threshold=0.7) and schema.drifted(r)
    assert "2/3 items fail schema" in schema.describe(r)


def test_optional_fields_never_fail_an_item():
    from sources import schema
    r = schema.check_items([{"id": "a"}], {"id": str, "zip?": str})
    assert r["bad_items"] == 0 and not schema.drifted(r)
    assert schema.check_items([], {"id": str})["n"] == 0


# ---------- eBay normalization ---------------------------------------------

def test_ebay_seller_type_comes_only_from_account_type():
    import sources.ebay_motors as eb
    a, b, c, _ = (eb.normalize(x) for x in EBAY["itemSummaries"])
    assert a.is_dealer is False and a.extras["seller_type"] == "individual"
    assert b.is_dealer is True and b.extras["seller_type"] == "business"
    # no account type -> unknown, NOT guessed from feedback score
    assert c.is_dealer is False and c.extras["seller_type"] == "unknown"
    assert (a.price, a.year, a.make, a.zip) == (7900, 2014, "Toyota", "35401")
    assert b.zip is None                              # masked postal code is not a ZIP
    assert c.is_auction and c.buy_now_price == 11500


def test_ebay_drops_items_beyond_radius_by_ebays_distance():
    import sources.ebay_motors as eb
    items = EBAY["itemSummaries"]
    assert eb.normalize(items[2], radius=100) is None   # 180 mi away
    assert eb.normalize(items[2], radius=200) is not None
    assert eb.normalize(items[3], radius=10) is not None  # no distance given: kept
    assert eb.normalize({"title": "x", "itemWebUrl": "u"}) is None


@pytest.fixture()
def ebay(app, monkeypatch):
    import sources.ebay_motors as eb
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(eb, "_TOKEN", {"value": None, "expires_at": 0})
    state = {"body": EBAY, "status": 200, "calls": []}
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: R(200, {"access_token": "t", "expires_in": 7200}))

    def get(url, params=None, **kw):
        state["calls"].append((url, dict(params or {})))
        return R(state["status"], state["body"])
    monkeypatch.setattr(requests, "get", get)
    return eb, state


def test_ebay_poll_healthy_on_valid_page(ebay):
    from sources import health
    eb, state = ebay
    rep = health.begin("ebay")
    out = eb.poll({"zip": "35401", "radius_mi": 100})
    assert [x.source_id for x in out] == ["v1|1001|0", "v1|1002|0", "v1|1004|0"]
    assert health.classify(rep, len(out))[0] == health.HEALTHY
    assert eb.LAST_RESULTS["schema"]["bad_items"] == 0
    assert "pickupPostalCode:35401" in state["calls"][0][1]["filter"]


def test_ebay_poll_flags_schema_drift(ebay):
    from sources import health
    eb, state = ebay
    drifted = copy.deepcopy(EBAY)
    for it in drifted["itemSummaries"]:              # provider renames price
        it["currentPrice"] = it.pop("price")
    state["body"] = drifted
    rep = health.begin("ebay")
    out = eb.poll({"zip": "35401", "radius_mi": 100})
    status, reason = health.classify(rep, len(out))
    assert status == health.DEGRADED
    assert any("price.value: missing" in a["detail"] for a in rep.attempts)


# ---------- MarketCheck FSBO schema --------------------------------------

def test_fsbo_schema_drift_is_reported_as_malformed(app, monkeypatch):
    import sources.marketcheck_fsbo as fsbo
    from sources import health
    monkeypatch.setenv("MARKETCHECK_API_KEY", "k")
    bad = {"num_found": 2, "listings": [{"listing_id": "1", "url": "u", "asking": 1}] * 2}
    monkeypatch.setattr(requests, "get", lambda *a, **k: R(200, bad))
    rep = health.begin(fsbo.SOURCE_ID)
    out = fsbo.poll({"zip": "35401", "radius_mi": 50})
    assert out == []
    assert rep.attempts[-1]["outcome"] == health.MALFORMED
    assert "id: missing" in rep.attempts[-1]["detail"]


def test_fsbo_fixture_passes_schema():
    import sources.marketcheck_fsbo as fsbo
    from sources import schema
    r = schema.check_items(FSBO["listings"], fsbo.ITEM_SPEC)
    assert r["bad_items"] == 1 and not schema.drifted(r)    # the URL-less record


# ---------- verify_sources probe -----------------------------------------

def test_probe_reports_aggregates_only_and_verifies(ebay, monkeypatch):
    import verify_sources as vs
    monkeypatch.delenv("MARKETCHECK_API_KEY", raising=False)
    eb, state = ebay
    rep = vs.run(zip_code="35401", radius=100)
    by = {r["source"]: r for r in rep["results"]}
    assert by["marketcheck_fsbo"]["status"] == "no_credentials"
    e = by["ebay"]
    assert e["verified"] and e["status"] == "healthy" and e["listings"] == 3
    assert e["seller_type"] == {"private": 1, "dealer": 1, "unknown": 1}
    assert e["price"] == {"min": 3100, "median": 6400, "max": 7900}
    assert e["in_price_band"] == 3 and e["states"] == {"AL": 2}
    assert len(state["calls"]) == 1                   # bounded: one search call
    blob = json.dumps(rep)
    for leaked in ("ebay.test", "Camry", "v1|1001|0", "Tuscaloosa"):
        assert leaked not in blob                     # no listing content
    assert vs.exit_code(rep) == 0


def test_probe_exit_codes(ebay, monkeypatch):
    import verify_sources as vs
    eb, state = ebay
    monkeypatch.delenv("EBAY_CLIENT_ID")
    monkeypatch.delenv("MARKETCHECK_API_KEY", raising=False)
    assert vs.exit_code(vs.run()) == 2                # nothing configured
    monkeypatch.setenv("EBAY_CLIENT_ID", "id")
    state["status"] = 403
    rep = vs.run(["ebay"])
    assert rep["results"][0]["status"] == "blocked" and vs.exit_code(rep) == 1


def test_probe_limits_marketcheck_to_one_page(app, monkeypatch):
    import sources.marketcheck_fsbo as fsbo
    import verify_sources as vs
    monkeypatch.setenv("MARKETCHECK_API_KEY", "k")
    monkeypatch.setattr(fsbo, "MAX_PAGES", 2)
    full = {"num_found": 500, "listings": [
        {**FSBO["listings"][0], "id": f"x{i}"} for i in range(fsbo.ROWS)]}
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, params=None, **k: (calls.append(params), R(200, full))[1])
    rep = vs.run(["marketcheck_fsbo"])
    assert len(calls) == 1 and rep["results"][0]["listings"] == fsbo.ROWS
    assert rep["results"][0]["verified"]
