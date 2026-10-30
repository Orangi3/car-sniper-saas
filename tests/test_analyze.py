"""
tests/test_analyze.py — FlipFirst Analyze and the private deal workspace.

Comparables are the SIMULATED 2015 Camry peers from the `world` fixture
(asking prices around $10,100); VIN decodes are stubbed. SQLite and
PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import login, logout, register
from tests.test_matching_and_history import L, make_user, q, score_all, world  # noqa: F401

CAMRY = {"year": 2015, "make": "Toyota", "model": "Camry", "trim": "SE", "mileage": 130000,
         "asking_price": 6500, "zip": "35401", "title_status": "clean",
         "condition": "runs_drives", "description": "clean title, runs great, one owner"}


def vin_with_check(base: str) -> str:
    import vin
    return base[:8] + vin.vin_check_digit(base) + base[9:]


CAMRY_VIN = vin_with_check("4T1BF1FK0FU123456")
CAMRY_DECODE = {"ModelYear": "2015", "Make": "TOYOTA", "Model": "Camry", "Trim": "SE",
                "BodyClass": "Sedan/Saloon", "DriveType": "FWD/Front-Wheel Drive"}


def run(payload, **kw):
    import analyze
    result, errs = analyze.analyze(payload, **kw)
    assert errs == [], errs
    return result


# ---------- the analysis ----------------------------------------------------

def test_market_backed_strong_candidate_with_transparent_math(client, world):
    r = run(CAMRY)
    assert r["valuation_basis"] == "market" and r["evidence_class"] == "asking_comparables"
    assert r["comp_n"] >= 3 and r["evidence"]["comps"] and r["freshness"]["newest"]
    base, cons = r["profit_detail"]["scenarios"]["base"], r["profit_detail"]["scenarios"]["conservative"]
    assert r["break_even_resale"] == r["investment"]["total"] == base["invested"]
    assert abs(base["profit"] - (base["resale"] - base["invested"])) <= 1
    assert abs(r["margin_of_safety_pct"] - 100 * base["profit"] / base["resale"]) <= 0.1
    assert r["decision"]["class"] == "strong_candidate", r["decision"]
    assert cons["profit"] >= 1000 and r["risk_flags"] == []


def test_max_offer_leaves_exactly_the_target_profit(client, world):
    import analyze
    r = run({**CAMRY, "target_profit": 1500, "costs": {"sales_tax_pct": 4, "transport": 150}})
    offer = r["max_offer"]["expected"]
    again = run({**CAMRY, "offer_price": offer, "target_profit": 1500,
                 "costs": {"sales_tax_pct": 4, "transport": 150}})
    assert abs(again["profit_detail"]["scenarios"]["base"]["profit"] - 1500) <= 2
    assert r["max_offer"]["conservative"] < offer          # a lower resale allows less
    assert analyze.DEFAULT_TARGET_PROFIT == run(CAMRY)["target_profit"]


def test_no_comparables_means_no_value_and_no_profit_never_an_invented_one(client, world):
    r = run({**CAMRY, "make": "Ford", "model": "Fusion", "trim": None})
    assert r["valuation_basis"] == "none" and r["value_mid"] is None and r["profit"] is None
    assert r["decision"]["class"] == "insufficient_evidence"
    assert "insufficient_data" in r["risk_flags"]
    assert {m["field"] for m in r["missing"]} >= {"resale_estimate"}
    assert r["break_even_resale"] > CAMRY["asking_price"]    # still useful without data


def test_own_resale_estimate_is_labeled_and_never_strong(client, world):
    r = run({**CAMRY, "make": "Ford", "model": "Fusion", "resale_estimate": 12000})
    assert r["valuation_basis"] == "user_estimate" and r["value_mid"] is None
    base = r["profit_detail"]["scenarios"]["base"]
    assert base["resale"] == 12000                        # no asking-to-sale haircut on a sale price
    assert any("your own" in x for x in r["profit_detail"]["explanation"])
    assert r["decision"]["class"] == "worth_investigating"
    assert any("not a market value" in x for x in r["decision"]["reasons"])


@pytest.mark.parametrize("change, flag", [
    ({"title_status": "salvage"}, "branded_title"),
    ({"title_status": "flood"}, "flood_damage"),
    ({"title_status": "missing"}, "title_missing"),
    ({"condition": "not_running"}, "not_running"),
    ({"description": "minor frame damage from a fender bender, drives fine"}, "structural_damage"),
    ({"faults": "water damage in the cabin"}, "flood_damage"),
    ({"condition": "needs_work"}, "repairs_not_estimated"),
    ({"year": 2012, "mileage": 18000}, "mileage_suspect"),
    ({"asking_price": 3000}, "too_good_to_be_true"),
])
def test_blocking_risks_make_it_high_risk(client, world, change, flag):
    r = run({**CAMRY, **change})
    assert flag in r["risk_flags"], r["risk_flags"]
    assert r["decision"]["class"] == "high_risk"


def test_negated_damage_and_priced_repairs_are_not_blocking(client, world):
    r = run({**CAMRY, "description": "No accidents, no frame damage, never flooded. Clean title."})
    assert not {"structural_damage", "flood_damage", "branded_title"} & set(r["risk_flags"])
    r = run({**CAMRY, "condition": "needs_work", "faults": "transmission slipping",
             "costs": {"mechanical": 1800}})
    assert "needs_work" in r["risk_flags"] and "repairs_not_estimated" not in r["risk_flags"]
    assert r["decision"]["class"] != "high_risk"
    assert r["investment"]["costs"]["mechanical"] == 1800


def test_missing_information_is_listed(client, world):
    r = run({"year": 2015, "make": "Toyota", "model": "Camry", "asking_price": 8100})
    missing = {m["field"] for m in r["missing"]}
    assert {"vin", "mileage", "title_status", "condition", "description", "zip"} <= missing
    assert "mileage_unknown" in r["risk_flags"] and "description_missing" in r["risk_flags"]


def test_vin_fills_identity_and_mismatches_are_flagged(client, world, monkeypatch):
    import vin
    monkeypatch.setattr(vin, "decode_vin", lambda v, force_refresh=False: dict(CAMRY_DECODE))
    r = run({"vin": CAMRY_VIN.lower(), "asking_price": 6500, "mileage": 130000})
    assert r["identity"]["vin"]["status"] == "decoded"
    assert r["identity"]["used"] == {"year": 2015, "make": "Toyota", "model": "Camry", "trim": "SE"}
    assert set(r["identity"]["filled_from_vin"]) == {"year", "make", "model", "trim"}
    assert r["valuation_basis"] == "market"
    r = run({**CAMRY, "vin": CAMRY_VIN, "make": "Honda", "model": "Accord"})
    assert "vin_mismatch" in r["risk_flags"] and r["decision"]["class"] == "high_risk"


def test_bad_or_unreachable_vin_still_analyzes(client, world, monkeypatch):
    import vin
    bad = CAMRY_VIN[:8] + ("X" if CAMRY_VIN[8] != "X" else "1") + CAMRY_VIN[9:]
    r = run({**CAMRY, "vin": bad})
    assert r["identity"]["vin"]["status"] == "invalid" and "vin_invalid" in r["risk_flags"]

    def down(v, force_refresh=False):
        raise OSError("network down")
    monkeypatch.setattr(vin, "decode_vin", down)
    r = run({**CAMRY, "vin": CAMRY_VIN})
    assert r["identity"]["vin"]["status"] == "unavailable" and r["valuation_basis"] == "market"


@pytest.mark.parametrize("payload, needle", [
    ({**CAMRY, "asking_price": None}, "Asking price is required"),
    ({**CAMRY, "asking_price": "abc"}, "Asking price must be a number"),
    ({**CAMRY, "zip": "00000"}, "ZIP"),
    ({**CAMRY, "title_status": "great"}, "Title status"),
    ({**CAMRY, "make": "<script>"}, "Make may only contain"),
    ({"asking_price": 5000}, "year, make and model"),
])
def test_input_validation(client, world, payload, needle):
    import analyze
    result, errs = analyze.analyze(payload)
    assert result is None and any(needle in e for e in errs), errs


def test_only_comparables_allowed_on_screen_are_used(client, world):
    import analyze
    assert analyze.analyze(CAMRY, peer_filter=lambda p: False)[0]["valuation_basis"] == "none"


# ---------- API ---------------------------------------------------------------

def test_analyze_api_free_plan_daily_limit_and_login(client, world, monkeypatch):
    assert client.post("/api/analyze", json=CAMRY).status_code == 401
    make_user(client, "free@example.com", plan="free", topic=False)
    login(client, "free@example.com")
    r = client.post("/api/analyze", json=CAMRY)
    assert r.status_code == 200 and r.get_json()["decision"]["class"] == "strong_candidate"
    assert r.get_json()["usage"] == {"analyses_today": 1, "analyses_per_day": 20}
    assert client.post("/api/analyze", json={"asking_price": 1}).status_code == 400
    monkeypatch.setenv("PLAN_LIMITS_JSON", '{"free": {"analyses_per_day": 2}}')
    assert client.post("/api/analyze", json=CAMRY).status_code == 200
    assert client.post("/api/analyze", json=CAMRY).status_code == 429
    assert client.post("/api/analyze", json=CAMRY,
                       headers={"Origin": "https://evil.example"}).status_code == 403


def test_stale_ebay_comparables_are_not_used(client, world):
    import db
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    with db.session() as c:
        c.execute("UPDATE listings SET source = 'ebay', last_seen_at = ? "
                  "WHERE composite_id LIKE 'craigslist:peer%'", (old,))
    make_user(client, "u@example.com", plan="free", topic=False)
    login(client, "u@example.com")
    assert client.post("/api/analyze", json=CAMRY).get_json()["valuation_basis"] == "none"


def test_vin_decode_endpoint(client, world, monkeypatch):
    import vin
    monkeypatch.setattr(vin, "decode_vin", lambda v, force_refresh=False: dict(CAMRY_DECODE))
    make_user(client, "u@example.com", plan="free", topic=False)
    login(client, "u@example.com")
    r = client.post("/api/analyze/vin", json={"vin": CAMRY_VIN})
    assert r.status_code == 200 and r.get_json()["decoded"]["make"] == "Toyota"
    assert client.post("/api/analyze/vin", json={"vin": "123"}).status_code == 400


def test_workspace_is_private_and_records_outcomes(client, world):
    make_user(client, "a@example.com", plan="free", topic=False)
    make_user(client, "b@example.com", plan="free", topic=False)
    login(client, "a@example.com")
    r = client.post("/api/opportunities", json={"input": CAMRY, "notes": "saw it Tuesday"})
    assert r.status_code == 201, r.get_json()
    oid = r.get_json()["id"]
    assert r.get_json()["analysis"]["decision"]["class"] == "strong_candidate"
    assert r.get_json()["input"]["asking_price"] == 6500
    p = client.patch(f"/api/opportunities/{oid}", json={
        "status": "sold", "notes": "tires worn; negotiated", "purchase_price": 6200,
        "purchased_on": "2026-10-01", "repair_spent": 400, "fees_spent": 150,
        "sale_price": 8900, "sold_on": "2026-10-20"})
    assert p.status_code == 200, p.get_json()
    real = p.get_json()["realized"]
    assert real == {"invested": 6750, "profit": 2150, "roi_pct": 31.9, "holding_days": 19,
                    "recorded_by": "you (not verified by FlipFirst)"}
    lst = client.get("/api/opportunities").get_json()
    assert lst["history"]["completed_flips"] == 1 and lst["history"]["realized_profit"] == 2150
    assert lst["history"]["avg_estimate_error"] == 2150 - lst["items"][0]["est_profit"]
    re = client.patch(f"/api/opportunities/{oid}", json={"input": {**CAMRY, "asking_price": 9900}})
    assert re.get_json()["analysis"]["price"] == 9900 and re.get_json()["status"] == "sold"
    logout(client)

    login(client, "b@example.com")                         # another user sees nothing
    assert client.get("/api/opportunities").get_json()["items"] == []
    assert client.get(f"/api/opportunities/{oid}").status_code == 404
    assert client.patch(f"/api/opportunities/{oid}", json={"notes": "x"}).status_code == 404
    assert client.delete(f"/api/opportunities/{oid}").status_code == 404
    logout(client)
    login(client, "a@example.com")
    assert client.delete(f"/api/opportunities/{oid}").status_code == 200
    assert q("SELECT 1 FROM opportunities") == []


@pytest.mark.parametrize("body, needle", [
    ({"status": "flipped"}, "status must be one of"),
    ({"sale_price": -5}, "between 0 and 1,000,000"),
    ({"sold_on": "2999-01-01"}, "in the future"),
    ({"purchased_on": "2026-10-10", "sold_on": "2026-10-01"}, "before the purchase"),
])
def test_workspace_validation(client, world, body, needle):
    make_user(client, "a@example.com", plan="free", topic=False)
    login(client, "a@example.com")
    oid = client.post("/api/opportunities", json={"input": CAMRY}).get_json()["id"]
    r = client.patch(f"/api/opportunities/{oid}", json=body)
    assert r.status_code == 400 and needle in r.get_json()["error"], r.get_json()


def test_workspace_plan_limit_and_account_deletion(client, world, monkeypatch):
    import accounts
    monkeypatch.setenv("PLAN_LIMITS_JSON", '{"free": {"opportunities": 1}}')
    make_user(client, "a@example.com", plan="free", topic=False)
    login(client, "a@example.com")
    assert client.post("/api/opportunities", json={"input": CAMRY}).status_code == 201
    assert client.post("/api/opportunities", json={"input": CAMRY}).status_code == 402
    logout(client)
    accounts.delete("a@example.com", confirm=True)
    assert q("SELECT 1 FROM opportunities") == []
