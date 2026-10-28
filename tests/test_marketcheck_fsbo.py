"""
tests/test_marketcheck_fsbo.py — MarketCheck private-party adapter.

Network-free: requests are routed to a SYNTHETIC fixture shaped after
MarketCheck's documented schema (tests/fixtures/marketcheck_fsbo_page.json).
Nothing here proves the live endpoint behaves this way.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

FIX = json.loads((Path(__file__).parent / "fixtures" / "marketcheck_fsbo_page.json").read_text())


class R:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body or {})
        self.headers = {"Content-Type": "application/json"}
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


@pytest.fixture()
def mc(app, monkeypatch):
    import sniper
    monkeypatch.setenv("MARKETCHECK_API_KEY", "test-key")
    monkeypatch.setattr(sniper._config_mod, "reload", lambda: None)
    calls = []
    state = {"resp": lambda params: R(200, FIX)}

    def get(url, params=None, **kw):
        calls.append((url, dict(params or {})))
        return state["resp"](params or {})
    monkeypatch.setattr(requests, "get", get)
    return sniper, calls, state


def _row(cid):
    import db
    with db.session() as c:
        r = c.execute("SELECT * FROM listings WHERE composite_id = ?", (cid,)).fetchone()
        return dict(r) if r else None


def test_normalizes_private_party_records_with_provenance(mc):
    import sources.marketcheck_fsbo as fsbo
    ls = [fsbo.normalize(x) for x in FIX["listings"]]
    a, b, bad = ls
    assert bad is None                                    # no URL -> unusable
    assert (a.price, a.year, a.make, a.model, a.odometer) == (7900, 2014, "Toyota", "Camry", 128000)
    assert a.is_dealer is False and a.url.startswith("https://")
    assert (a.zip, a.latitude, a.longitude) == ("35401", 33.2098, -87.5692)
    assert a.extras["origin_marketplace"] == "example-marketplace.test"
    assert a.extras["provider"] == "marketcheck" and a.extras["seller_type"] == "fsbo"
    assert b.latitude is None and b.zip == "35473"        # no coordinates invented


def test_poll_uses_documented_endpoint_territory_and_budget(mc):
    sniper, calls, _ = mc
    out = sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    assert out["source_status"]["marketcheck_fsbo"]["status"] == "healthy"
    url, params = calls[0]
    assert url == "https://api.marketcheck.com/v2/search/car/fsbo/active"
    assert params["zip"] and params["radius"] and params["price_range"] == "2000-15000"
    assert params["rows"] == 50 and params["car_type"] == "used"
    assert len(calls) == 1                                # short page: no 2nd call
    row = _row("marketcheck_fsbo:fsbo-1001")
    assert row["is_dealer"] == 0 and row["location_source"] == "provider"
    import provider_budget
    assert provider_budget.used("marketcheck_fsbo") == 1


def test_second_page_only_when_first_page_is_all_new(mc, monkeypatch):
    import sources.marketcheck_fsbo as fsbo
    sniper, calls, state = mc
    monkeypatch.setattr(fsbo, "ROWS", 2)
    full = {"listings": FIX["listings"][:2]}
    state["resp"] = lambda p: R(200, full if p["start"] == 0 else {"listings": []})
    sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    assert [c[1]["start"] for c in calls] == [0, 2]
    calls.clear()
    # already known -> caught up after page 1
    sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    assert [c[1]["start"] for c in calls] == [0]


def test_budget_exhausted_stops_calls_and_backs_off_to_next_month(mc, monkeypatch):
    sniper, calls, _ = mc
    monkeypatch.setenv("MARKETCHECK_FSBO_MONTHLY_CALL_BUDGET", "0")
    out = sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    assert calls == []
    assert out["source_status"]["marketcheck_fsbo"]["status"] == "rate_limited"
    import db
    with db.session() as c:
        nxt = c.execute("SELECT next_poll_after FROM source_health "
                        "WHERE source_id='marketcheck_fsbo'").fetchone()[0]
    import provider_budget
    from datetime import datetime, timezone
    wait = (datetime.fromisoformat(nxt) - datetime.now(timezone.utc)).total_seconds()
    assert wait > provider_budget.seconds_to_next_month() - 120   # ~1st of next month


def test_quota_exhausted_429_is_rate_limited_until_next_month(mc):
    sniper, calls, state = mc
    state["resp"] = lambda p: R(429, text='{"message":"Monthly API quota exhausted"}')
    sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    import db, provider_budget
    with db.session() as c:
        r = dict(c.execute("SELECT status, next_poll_after FROM source_health "
                           "WHERE source_id='marketcheck_fsbo'").fetchone())
    assert r["status"] == "rate_limited"
    from datetime import datetime, timezone
    wait = (datetime.fromisoformat(r["next_poll_after"]) - datetime.now(timezone.utc)).total_seconds()
    assert wait > provider_budget.seconds_to_next_month() - 120


def test_auth_failure_and_malformed_reported(mc):
    sniper, calls, state = mc
    state["resp"] = lambda p: R(401, text='{"message":"invalid api key"}')
    out = sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    assert out["source_status"]["marketcheck_fsbo"]["status"] == "auth_config"


def test_malformed_payload_is_degraded(mc):
    sniper, calls, state = mc
    state["resp"] = lambda p: R(200, text='{"unexpected": true}')
    out = sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    st = out["source_status"]["marketcheck_fsbo"]
    assert st["status"] in ("degraded", "unverified") and "malformed" in st["reason"]


def test_missing_key_is_auth_config_and_no_call(mc, monkeypatch):
    sniper, calls, _ = mc
    monkeypatch.delenv("MARKETCHECK_API_KEY")
    out = sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    assert out["source_status"]["marketcheck_fsbo"]["status"] == "auth_config" and calls == []


def test_fail_closed_in_commercial_mode_without_agreement(mc, monkeypatch):
    sniper, calls, _ = mc
    monkeypatch.setitem(sniper.CONFIG, "commercial_mode", True)
    monkeypatch.delenv("MARKETCHECK_COMMERCIAL_AGREEMENT", raising=False)
    out = sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    st = out["source_status"]["marketcheck_fsbo"]
    assert st["status"] == "disabled" and "written commercial agreement" in st["reason"]
    assert calls == []


def test_price_change_flows_into_history_and_rescoring(mc):
    sniper, calls, state = mc
    sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    cheaper = json.loads(json.dumps(FIX))
    cheaper["listings"][0]["price"] = 6900
    state["resp"] = lambda p: R(200, cheaper)
    sniper.poll_once(verbose=False, only_sources=["marketcheck_fsbo"], force_all=True)
    row = _row("marketcheck_fsbo:fsbo-1001")
    assert (row["price"], row["previous_price"]) == (6900, 7900)
    import db
    with db.session() as c:
        keys = {r[0] for r in c.execute("SELECT dedupe_key FROM jobs WHERE kind='score'")}
    assert "score:marketcheck_fsbo:fsbo-1001:6900" in keys
