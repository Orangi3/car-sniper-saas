"""
tests/test_deals_api.py — profit model (P4), deals API / mobile backend
(P5), plan limits + usage metering (P6).
"""
from __future__ import annotations

import json

import pytest

import profit
from tests.conftest import login, logout, make_admin, register, set_plan
from tests.test_matching_and_history import L, make_user, q, score_all, world  # noqa: F401


# ---------- profit model --------------------------------------------------

def test_tax_and_title_not_assumed_until_user_sets_them():
    p = profit.compute(8000, 9500, 10000, 10500, "confirmed_sales", None)
    assert "sales_tax_pct" not in p["costs"] and "title_registration" not in p["costs"]
    assert any("sales tax" in x for x in p["not_included"])
    # defaults: mechanical 500 + cosmetic 250 + selling fees 100 + holding $5 × 21 days
    assert p["total_costs"] == 955
    assert p["scenarios"]["base"]["profit"] == 10000 - 8000 - 955
    p2 = profit.compute(8000, 9500, 10000, 10500, "confirmed_sales",
                        {"sales_tax_pct": 4, "title_registration": 50})
    assert p2["costs"]["sales_tax_pct"] == 320 and p2["not_included"] == []


def test_asking_haircut_only_for_non_sales_evidence_and_is_labelled():
    sold = profit.compute(8000, 9000, 10000, 11000, "confirmed_sales", None)
    ask = profit.compute(8000, 9000, 10000, 11000, "asking_comparables", None)
    assert sold["scenarios"]["base"]["resale"] == 10000
    assert ask["scenarios"]["base"]["resale"] == 9500
    assert any(a["key"] == "asking_to_sale_pct" for a in ask["assumptions"])
    assert not any(a["key"] == "asking_to_sale_pct" for a in sold["assumptions"])
    assert ask["scenarios"]["conservative"]["profit"] < ask["scenarios"]["optimistic"]["profit"]


def test_scenarios_vary_purchase_price_and_time_to_sell():
    p = profit.compute(8000, 9500, 10000, 10500, "confirmed_sales", {"negotiation_pct": 5})
    c, b, o = (p["scenarios"][k] for k in ("conservative", "base", "optimistic"))
    assert (c["purchase"], b["purchase"], o["purchase"]) == (8000, 7600, 7200)
    assert (c["days_to_sell"], b["days_to_sell"], o["days_to_sell"]) == (31.5, 21, 10.5)
    assert c["holding"] > b["holding"] > o["holding"]
    assert c["profit"] < b["profit"] < o["profit"]
    assert abs(b["annualized_roi_pct"] - b["roi_pct"] * 365 / 21) < 0.5
    assert any("Scenarios:" in x for x in p["explanation"])
    lite = profit.compute(8000, 9500, 10000, 10500, "confirmed_sales", None, scenarios=False)
    assert set(lite["scenarios"]) == {"base"}


def test_legacy_recon_setting_is_the_whole_repair_allowance():
    old = profit.merged({"recon": 2000})
    assert old["recon"] == 2000 and old["mechanical"] == 0
    new = profit.merged({"recon": 300, "mechanical": 900})
    assert (new["recon"], new["mechanical"]) == (300, 900)
    assert profit.merged(None)["mechanical"] == 500


def test_user_assumptions_are_validated_and_clamped():
    c = profit.clean_settings({"recon": "-5", "negotiation_pct": 500, "bogus": 1,
                               "sales_tax_pct": ""})
    assert c == {"recon": 0.0, "negotiation_pct": 60.0, "sales_tax_pct": None}


def test_no_valuation_no_profit():
    assert profit.compute(8000, None, None, None, "insufficient", None) is None


# ---------- deals API -----------------------------------------------------

def test_feeds_detail_and_actions(client, world):
    uid = make_user(client, "u@example.com", searches=[("c", {"model": "camry"})])
    world.ingest([L("deal", 8100), L("salv", 6000, desc="rebuilt title")])
    score_all()
    login(client, "u@example.com")
    mine = client.get("/api/deals?feed=mine").get_json()
    assert [d["composite_id"] for d in mine] == ["craigslist:deal"]
    d = mine[0]
    assert d["evidence_class"] == "asking_comparables" and d["value_low"] < d["value_high"]
    assert set(d["profit"]) == {"base"}                       # Lite: base only
    trusted = {x["composite_id"] for x in client.get("/api/deals?feed=trusted").get_json()}
    risk = {x["composite_id"]: x for x in client.get("/api/deals?feed=risk").get_json()}
    assert "craigslist:deal" in trusted and "craigslist:salv" not in trusted
    assert "branded_title" in risk["craigslist:salv"]["risk_flags"]

    det = client.get("/api/deals/craigslist:deal").get_json()
    assert det["confidence_reasons"] and det["evidence"]["comps"]
    assert all(c["composite_id"] != "craigslist:deal" for c in det["evidence"]["comps"])
    assert det["price_history"][0]["event"] == "first_seen"
    assert det["profit_detail"]["disclaimer"].startswith("Estimated")

    assert client.post("/api/deals/craigslist:deal/action",
                       json={"action": "save"}).get_json()["status"] == "saved"
    client.post("/api/deals/craigslist:deal/action", json={"action": "dismiss"})
    assert client.get("/api/deals?feed=mine").get_json() == []
    assert client.post("/api/deals/craigslist:nope/action",
                       json={"action": "save"}).status_code == 404


def test_pro_gets_profit_scenarios(client, world):
    make_user(client, "p@example.com", plan="pro", searches=[("c", {"model": "camry"})])
    world.ingest([L("deal", 8100)])
    score_all()
    login(client, "p@example.com")
    d = client.get("/api/deals?feed=mine").get_json()[0]
    assert set(d["profit"]) == {"conservative", "base", "optimistic"}


def test_search_preview_shows_current_matches_and_is_private(client, world):
    world.ingest([L("deal", 8100)])
    score_all()
    make_user(client, "a@example.com", searches=[("c", {"model": "camry"})])
    login(client, "a@example.com")
    sid = client.get("/api/saved_searches").get_json()[0]["id"]
    rows = client.get(f"/api/deals?feed=search&search_id={sid}").get_json()
    assert [r["composite_id"] for r in rows] == ["craigslist:deal"]
    logout(client)
    make_user(client, "b@example.com")
    login(client, "b@example.com")
    assert client.get(f"/api/deals?feed=search&search_id={sid}").status_code == 404
    assert client.get("/api/deals?feed=mine").get_json() == []


def test_free_plan_can_browse_but_not_act_or_customize(client, world):
    world.ingest([L("deal", 8100)])
    score_all()
    register(client, "f@example.com")
    login(client, "f@example.com")
    assert client.get("/api/deals?feed=trusted").status_code == 200
    assert client.post("/api/deals/craigslist:deal/action",
                       json={"action": "save"}).status_code == 402
    assert client.post("/api/me/profit_settings", json={"recon": 500}).status_code == 402


def test_profit_settings_change_profit_not_valuation(client, world):
    make_user(client, "u@example.com", searches=[("c", {"model": "camry"})])
    world.ingest([L("deal", 8100)])
    score_all()
    login(client, "u@example.com")
    before = client.get("/api/deals/craigslist:deal").get_json()
    r = client.post("/api/me/profit_settings", json={"recon": 2000, "sales_tax_pct": 4})
    assert r.status_code == 200 and r.get_json()["settings"]["recon"] == 2000
    after = client.get("/api/deals/craigslist:deal").get_json()
    assert after["value_mid"] == before["value_mid"]
    assert after["profit"]["base"] < before["profit"]["base"]
    assert "sales_tax_pct" in after["profit_detail"]["costs"]


# ---------- usage metering + SMS cap --------------------------------------

def test_sends_are_metered_and_sms_capped(client, world, monkeypatch):
    import jobqueue, notifications
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t")
    monkeypatch.setenv("TWILIO_FROM", "+15550000000")
    monkeypatch.setenv("PLAN_LIMITS_JSON", json.dumps({"pro": {"sms_per_month": 1}}))
    sent = []

    class R:
        status_code = 201
    monkeypatch.setattr(notifications.requests, "post",
                        lambda url, **kw: sent.append(url) or R())
    uid = make_user(client, "s@example.com", plan="pro",
                    searches=[("c", {"model": "camry"})], topic=False)
    login(client, "s@example.com")
    assert client.post("/api/me/alert_channels",
                       json={"notify_phone": "205-555-0100"}).status_code == 200
    world.ingest([L("d1", 8100, odo=120000)])
    score_all()
    jobqueue.drain({"notify": notifications.deliver_job})
    assert len(sent) == 1 and "twilio" in sent[0]
    usage = client.get("/api/me/usage").get_json()
    assert usage["sends_this_month"] == {"sms": 1}
    world.ingest([L("d2", 8000, odo=125000)])           # cap reached → no SMS job
    score_all()
    jobs = [json.loads(j["payload"])["channel"] for j in
            q("SELECT payload FROM jobs WHERE kind='notify' AND user_id = ?", (uid,))]
    assert jobs == ["twilio"]


def test_admin_usage_requires_admin(client, app):
    register(client, "x@example.com")
    login(client, "x@example.com")
    assert client.get("/api/admin/usage").status_code == 403
    make_admin("x@example.com")
    login(client, "x@example.com")
    body = client.get("/api/admin/usage").get_json()
    assert "rows" in body and "jobs" in body


def test_plans_endpoint_lists_lite_and_pro(client, app):
    register(client, "y@example.com")
    login(client, "y@example.com")
    names = {p["plan"]: p["name"] for p in client.get("/api/plans").get_json()["plans"]}
    assert names == {"free": "Free", "starter": "Lite", "pro": "Pro"}
