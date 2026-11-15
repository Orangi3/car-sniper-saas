"""
tests/test_entitlement_gates.py — two Pro-only features stay Pro-only.
Both were found by the 2026-11-08 security verification and reproduced
before they were fixed.

SEC-1  Aggressive mode. The save gate compared the exact text "aggressive",
       and the matcher treated ANY risk_mode other than "conservative" as
       aggressive. So "aggressive " (trailing space), "AGGRESSIVE" or junk let
       a Lite subscriber see, and be alerted to, risk-flagged deals.
SEC-2  SMS. The "send a test alert" button sent to every configured channel,
       including Twilio for Lite (sms_per_month = 0), to any number, without
       counting the message. Real alerts already apply the plan's channels
       and monthly cap; the test button now does the same.

SYNTHETIC data. Every outbound request is captured; Twilio is never called.
"""
from __future__ import annotations

import json

import pytest

from tests.conftest import register, set_plan

RISKY = "test:risky"


def _seed_risk_flagged_deal():
    """30% under market, but the valuation is not trusted (trust='risk',
    low confidence): only an entitled aggressive search may surface it."""
    import db
    now = db.now_utc_iso()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO listings(composite_id, source, source_id, url, title, price, year, "
            " make, model, odometer, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (RISKY, "test", "risky", "https://example.com/x", "2016 Honda Civic LX", 7000, 2016,
             "Honda", "Civic", 90000, now, now))
        conn.execute(
            "INSERT INTO near_misses(composite_id, discount_pct, score, comp_avg, refreshed_at, "
            " comp_n, basis, evidence_class, value_low, value_high, confidence, trust, risk_flags) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (RISKY, 30.0, 60.0, 10000, now, 6, "asking", "asking_comparables", 9000, 11000,
             "low", "risk", json.dumps(["low_confidence"])))


def _opp():
    return {"composite_id": RISKY, "event": "new", "source": "test", "price": 7000,
            "year": 2016, "make": "Honda", "model": "Civic", "odometer": 90000,
            "discount_pct": 30.0, "value_low": 9000, "value_mid": 10000, "value_high": 11000,
            "evidence_class": "asking_comparables", "confidence": "low", "trust": "risk",
            "risk_flags": ["low_confidence"], "score": 60.0}


def _user(client, email, plan):
    assert register(client, email).status_code == 200
    set_plan(email, plan)


# ---------- SEC-1: aggressive mode ----------------------------------------------------

@pytest.mark.parametrize("spelling", ["aggressive", "aggressive ", " Aggressive", "AGGRESSIVE"])
def test_lite_cannot_save_aggressive_mode_in_any_spelling(client, spelling):
    _user(client, "lite@example.com", "starter")
    r = client.post("/api/saved_searches", json={"name": "a", "filter": {"risk_mode": spelling}})
    assert r.status_code == 402, r.get_json()


def test_unknown_risk_mode_is_refused_and_valid_ones_are_stored_normalized(client):
    from tests.test_matching_and_history import q
    _user(client, "pro@example.com", "pro")
    assert client.post("/api/saved_searches",
                       json={"name": "x", "filter": {"risk_mode": "yolo"}}).status_code == 400
    assert client.post("/api/saved_searches",
                       json={"name": "a", "filter": {"risk_mode": " Aggressive "}}).status_code == 200
    assert client.post("/api/saved_searches",
                       json={"name": "c", "filter": {"risk_mode": "conservative"}}).status_code == 200
    assert client.post("/api/saved_searches", json={"name": "d", "filter": {}}).status_code == 200
    stored = [json.loads(r["filter_json"]).get("risk_mode")
              for r in q("SELECT filter_json FROM saved_searches ORDER BY id")]
    assert stored == ["aggressive", "conservative", None]


def test_matcher_treats_anything_but_entitled_aggressive_as_conservative():
    import plans
    import saved_search_alerts as ssa
    refused = (False, "risk-flagged (conservative mode)")
    for mode in ("aggressive", "aggressive ", "AGGRESSIVE", "yolo", "", None):
        assert ssa.criteria_match({"risk_mode": mode}, _opp(), plans.limits("starter")) == refused
    for mode in ("yolo", "", None, "conservative"):
        assert ssa.criteria_match({"risk_mode": mode}, _opp(), plans.limits("pro")) == refused
    for mode in ("aggressive", " Aggressive "):
        assert ssa.criteria_match({"risk_mode": mode}, _opp(), plans.limits("pro")) == (True, "")


def test_a_lite_search_saved_before_the_fix_gets_no_risk_flagged_deal(client):
    """A filter stored with a variant spelling stays harmless: no preview hit,
    no match, no notification."""
    import db
    import saved_search_alerts as ssa
    _user(client, "lite2@example.com", "starter")
    assert client.post("/api/me/alert_channels", json={"ntfy_topic": "generate"}).status_code == 200
    r = client.post("/api/saved_searches", json={"name": "b", "notify_enabled": True,
                                                 "filter": {"risk_mode": "conservative"}})
    sid = r.get_json()["id"]
    with db.session() as conn:                       # as written before the fix
        conn.execute("UPDATE saved_searches SET filter_json = ?",
                     (json.dumps({"risk_mode": "aggressive "}),))
    _seed_risk_flagged_deal()
    preview = [d["composite_id"] for d in
               client.get(f"/api/deals?feed=search&search_id={sid}").get_json()]
    assert RISKY not in preview
    with db.session() as conn:
        assert ssa.evaluate(conn, _opp()) == []
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'notify'").fetchone()[0] == 0


# ---------- SEC-2: SMS through the test button -----------------------------------------

class _Resp:
    status_code = 201


@pytest.fixture()
def outbound(monkeypatch):
    """Twilio configured (fake credentials); every POST captured."""
    import notifications
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest0000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "dummy-token")
    monkeypatch.setenv("TWILIO_FROM", "+15550000000")
    sent = []

    def fake_post(url, **kw):
        sent.append(url)
        return _Resp()
    monkeypatch.setattr(notifications, "_post", fake_post)
    return sent


def _sms(sent):
    return [u for u in sent if "api.twilio.com" in u]


def _sms_usage():
    from tests.test_matching_and_history import q
    return q("SELECT COUNT(*) AS n FROM usage_events WHERE kind = 'sms'")[0]["n"]


def test_lite_test_alert_never_sends_sms(client, outbound):
    _user(client, "lite3@example.com", "starter")
    assert client.post("/api/me/alert_channels",
                       json={"notify_phone": "205-555-0147", "ntfy_topic": "generate"}).status_code == 200
    r = client.post("/api/me/alert_channels/test")
    assert r.status_code == 200, r.get_json()
    assert "twilio" not in r.get_json()["channels"]
    assert _sms(outbound) == [] and len(outbound) == 1          # the push only


def test_lite_with_only_a_phone_gets_no_test_send_at_all(client, outbound):
    _user(client, "lite4@example.com", "starter")
    assert client.post("/api/me/alert_channels", json={"notify_phone": "2055550147"}).status_code == 200
    r = client.post("/api/me/alert_channels/test")
    assert r.status_code == 400 and outbound == []


def test_pro_test_sms_is_counted_and_stops_at_the_monthly_cap(client, outbound, monkeypatch):
    monkeypatch.setenv("PLAN_LIMITS_JSON", json.dumps({"pro": {"sms_per_month": 1}}))
    _user(client, "pro2@example.com", "pro")
    assert client.post("/api/me/alert_channels",
                       json={"notify_phone": "2055550147", "ntfy_topic": "generate"}).status_code == 200
    first = client.post("/api/me/alert_channels/test").get_json()
    assert first["channels"]["twilio"]["ok"] is True and len(_sms(outbound)) == 1
    assert _sms_usage() == 1                                    # metered like a real alert
    second = client.post("/api/me/alert_channels/test").get_json()
    assert "twilio" not in second["channels"] and len(_sms(outbound)) == 1   # cap reached
