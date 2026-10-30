"""
tests/test_false_positives.py — listings that looked like trusted deals but
aren't. Each test pins one false-positive path found in the valuation audit:

  * model names shared by different vehicles (Silverado 1500 vs 2500HD,
    Wrangler vs Wrangler Unlimited) pooled as comparables
  * a running auction's current bid scored as if it were a price
  * a high-mileage car valued from much lower-mileage comparables
  * damage or mechanical problems described in words the filters missed
  * a price far below every comparable with no other warning sign
  * a stated trim that no comparable shares (XL valued from Lariats)
  * title-only listings presented as if their condition had been screened
  * asking prices presented as sales; ended auctions still displayed

All rows here are SIMULATED test fixtures, not real listings. These tests
guard behavior; they are not evidence of real-world accuracy.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import login
from tests.test_matching_and_history import L, make_user, q, score_all, world  # noqa: F401

NOW = datetime.now(timezone.utc)


def peer(i, title, price, year=2014, miles=None, **kw):
    """A simulated peer row, parsed the way ingestion parses titles."""
    import valuation as V
    from sources.base import extract_make_model, looks_needs_work, looks_salvage
    make, model = extract_make_model(title)
    row = {"composite_id": f"sim:{i}", "source": "sim", "title": title, "price": price,
           "year": year, "make": make, "model": model, "odometer": miles,
           "is_dealer": False, "is_salvage": looks_salvage(title),
           "needs_work": looks_needs_work(title), "extras": "{}",
           "last_seen_at": (NOW - timedelta(days=1)).isoformat()}
    row.update(kw)
    row["trim"] = V.parse_trim(title, row["model"])
    return row


def judge(target, peers):
    """(valuation, discount, trust, flags) exactly as the scorer computes them."""
    import sniper
    import valuation as V
    v = V.value_listing(target, peers, now=NOW)
    disc = (v.value_mid - target["price"]) / v.value_mid if v.value_mid else 0.0
    trust, flags = sniper.assess_risk(target, v, disc)
    return v, disc, trust, flags


def camry_market(n=8):
    return [peer(i, "2015 Toyota Camry LE", 12000 + 100 * i, year=2015, miles=90000 + 1500 * i)
            for i in range(n)]


# ---------- one model name, different vehicles ---------------------------

@pytest.mark.parametrize("title,variant", [
    ("2014 Chevrolet Silverado 1500 LT", "standard"),
    ("2014 Chevy Silverado LT 4x4", "standard"),
    ("2008 Chevy Silverado LT $2500", "standard"),            # a price, not a 2500
    ("2014 Chevy Silverado 2500HD LT Duramax", "heavy-duty"),
    ("2012 Chevrolet Silverado 2500 HD", "heavy-duty"),
    ("2011 GMC Sierra 3500 Denali dually", "heavy-duty"),
    ("2012 Chevy Silverado 3/4 ton", "heavy-duty"),
    ("2004 Chevrolet Silverado 1500HD", "heavy-duty"),
])
def test_duty_class_is_read_from_the_title(title, variant):
    import valuation as V
    from sources.base import extract_make_model
    make, model = extract_make_model(title)
    assert V.model_variant(title, make, model) == variant


def test_other_models_have_no_variant_split():
    import valuation as V
    assert V.model_variant("2014 Honda Accord EX", "Honda", "Accord") is None
    assert V.model_variant("2016 Nissan Titan XD SV", "Nissan", "Titan") == "XD"
    assert V.model_variant("2012 Jeep Wrangler Unlimited Sahara", "Jeep", "Wrangler") == "Unlimited"
    assert V.model_variant("2012 Jeep Wrangler Sport", "Jeep", "Wrangler") == "standard"


def test_half_ton_is_not_valued_from_heavy_duty_asks():
    """Before: a $9,200 Silverado 1500 scored 39% under 'market' because six
    2500HD asks (~$15.4k) outvoted four 1500 asks (~$10.5k)."""
    hd = [peer(i, "2014 Chevrolet Silverado 2500HD LT", 15000 + 200 * i, miles=115000 + 2000 * i)
          for i in range(6)]
    ld = [peer(10 + i, "2014 Chevrolet Silverado 1500 LT", 10300 + 200 * i, miles=116000 + 2000 * i)
          for i in range(4)]
    target = peer("t", "2014 Chevy Silverado 1500 LT 118k miles", 9200, miles=118000)
    v, disc, trust, _ = judge(target, hd + ld)
    assert v.value_mid < 11000 and disc < 0.20
    assert all("2500HD" not in c["title"] for c in v.comps)
    assert any("6 peer(s) excluded: a different vehicle" in r for r in v.reasons)

    # and the reverse: a heavy-duty truck is valued only from heavy-duty asks
    hd_target = peer("t2", "2014 Chevy Silverado 2500HD LT", 11500, miles=118000)
    v, disc, trust, _ = judge(hd_target, hd + ld)
    assert v.value_mid > 14500 and trust == "trusted" and disc > 0.2


def test_two_door_wrangler_is_not_valued_from_unlimited_asks():
    unl = [peer(i, "2012 Jeep Wrangler Unlimited Sahara", 15000 + 200 * i, year=2012,
                miles=98000 + 2000 * i) for i in range(6)]
    target = peer("t", "2012 Jeep Wrangler 4x4", 10400, year=2012, miles=104000)
    v, _, trust, flags = judge(target, unl)
    assert v.evidence_class == "insufficient" and trust == "risk"


# ---------- auctions ------------------------------------------------------

def test_running_auction_bid_is_never_a_trusted_deal():
    """Before: a $6,000 current bid with 5 days left was a 52%-off trusted deal."""
    target = peer("t", "2015 Toyota Camry LE", 6800, year=2015, miles=95000, is_auction=True,
                  auction_end_at=(NOW + timedelta(days=5)).isoformat(), description="no reserve")
    _, disc, trust, flags = judge(target, camry_market())
    assert disc > 0.4 and "auction_bid" in flags and trust == "risk"


def test_auction_bids_are_not_comparables():
    bids = [peer(20 + i, "2015 Toyota Camry LE", 4000 + 100 * i, year=2015, miles=95000,
                 is_auction=True) for i in range(12)]
    target = peer("t", "2015 Toyota Camry LE", 11800, year=2015, miles=95000)
    v, *_ = judge(target, camry_market(4) + bids)
    assert v.n == 4 and v.value_mid > 11500


def test_ended_auction_is_hidden_and_a_live_one_is_labeled(client, world):
    make_user(client, "auc@example.com")
    world.ingest([
        L("ended", 6500, is_auction=True,
          auction_end_at=(NOW - timedelta(hours=1)).isoformat()),
        L("live", 6500, is_auction=True,
          auction_end_at=(NOW + timedelta(days=2)).isoformat())])
    score_all()
    login(client, "auc@example.com")
    trusted = {d["composite_id"] for d in client.get("/api/deals?feed=trusted").get_json()}
    risk = {d["composite_id"]: d for d in client.get("/api/deals?feed=risk").get_json()}
    assert "craigslist:ended" not in trusted and "craigslist:ended" not in risk
    assert client.get("/api/deals/craigslist:ended").status_code == 410
    assert "craigslist:live" not in trusted
    assert "auction_bid" in risk["craigslist:live"]["risk_flags"]


# ---------- mileage -------------------------------------------------------

def test_mileage_far_beyond_every_comparable_caps_confidence():
    """Before: 248k miles valued from ~100k-mile asks, medium confidence."""
    peers = [peer(i, "2013 Honda Accord EX", 11000 + 250 * i, year=2013,
                  miles=(95000 + 5000 * i) if i < 4 else None) for i in range(8)]
    target = peer("t", "2013 Honda Accord EX 248k miles", 7200, year=2013, miles=248000)
    v, _, trust, flags = judge(target, peers)
    assert v.confidence == "low" and trust == "risk" and "low_confidence" in flags
    assert any("248,000 mi" in r and "capped at low" in r for r in v.reasons)


def test_mileage_inside_the_comparables_range_is_unaffected():
    peers = [peer(i, "2013 Honda Accord EX", 11000 + 250 * i, year=2013, miles=95000 + 5000 * i)
             for i in range(4)]
    target = peer("t", "2013 Honda Accord EX", 8000, year=2013, miles=120000)
    v, *_ = judge(target, peers)
    assert v.confidence == "medium" and not any("capped at low" in r for r in v.reasons)


def test_extrapolated_mileage_adjustment_is_stated():
    peers = [peer(i, "2014 Toyota Corolla LE", 11000 - 400 * i, miles=60000 + 8000 * i)
             for i in range(8)]
    target = peer("t", "2014 Toyota Corolla LE", 5000, miles=200000)
    v, *_ = judge(target, peers)
    assert v.mileage_adjusted
    assert any("mileage adjustment extrapolated" in r for r in v.reasons)


# ---------- disclosed damage and mechanical problems -----------------------

@pytest.mark.parametrize("desc", [
    "front end damage, drives fine", "wrecked on passenger side", "airbags deployed",
    "frame damage", "transmission slipping when cold", "overheats in traffic", "needs work",
    "rod knock", "needs head gasket", "burns oil",
])
def test_damage_wording_marks_needs_work(desc):
    from sources.base import looks_needs_work
    assert looks_needs_work("2013 Honda Accord", desc)


@pytest.mark.parametrize("desc", [
    "never wrecked", "no accident damage", "doesn't overheat", "does not burn oil",
    "new head gasket", "no frame damage, clean title", "hasn't been wrecked",
])
def test_denials_of_damage_are_not_flagged(desc):
    from sources.base import looks_needs_work
    assert not looks_needs_work("2013 Honda Accord", desc)


def test_airbags_deployed_is_damage_not_a_military_story():
    import scam_detector
    score, reasons = scam_detector.score_listing("2013 Honda Accord", "airbags deployed")
    assert score == 0 and reasons == []
    assert scam_detector.score_listing("x", "currently deployed, must sell")[0] == 30


def test_ebay_for_parts_condition_is_needs_work():
    import sources.ebay_motors as eb
    item = {"itemId": "v1|1|0", "title": "2012 Honda Civic LX", "itemWebUrl": "https://e.test/1",
            "price": {"value": "3000.00", "currency": "USD"}, "buyingOptions": ["FIXED_PRICE"],
            "condition": "For parts or not working", "conditionId": "7000"}
    assert eb.normalize(item).extras.get("needs_work") is True
    item.update(condition="Used", conditionId="3000")
    assert "needs_work" not in eb.normalize(item).extras


def test_damage_in_the_description_keeps_a_listing_out_of_trusted(client, world):
    make_user(client, "dmg@example.com")
    world.ingest([L("dmg", 7200, desc="front end damage from a fender bender, drives fine")])
    score_all()
    row = q("SELECT trust, risk_flags FROM near_misses WHERE composite_id = 'craigslist:dmg'")[0]
    assert row["trust"] == "risk" and "needs_work" in json.loads(row["risk_flags"])


# ---------- suspiciously cheap ----------------------------------------------

def test_more_than_half_off_goes_to_the_risk_feed():
    """Before: 56% under every comparable, no scam wording -> trusted."""
    target = peer("t", "2015 Toyota Camry LE", 5500, year=2015, miles=95000,
                  description="clean title, need gone")
    _, disc, trust, flags = judge(target, camry_market())
    assert 0.5 < disc < 0.65 and "too_good_to_be_true" in flags and trust == "risk"
    target["price"] = 8800                                   # ~30% off stays a deal
    _, disc, trust, flags = judge(target, camry_market())
    assert trust == "trusted" and "too_good_to_be_true" not in flags


# ---------- trim ----------------------------------------------------------

def test_stated_trim_that_no_comparable_shares_is_low_confidence():
    """Before: a base F-150 XL valued from Lariat asks, 49% off, trusted."""
    peers = [peer(i, "2012 Ford F-150 Lariat SuperCrew 4x4", 14000 + 200 * i, year=2012,
                  miles=120000 + 3000 * i) for i in range(8)]
    target = peer("t", "2012 Ford F-150 XL Regular Cab 2WD", 7500, year=2012, miles=125000,
                  description="work truck")
    v, _, trust, _ = judge(target, peers)
    assert v.confidence == "low" and trust == "risk"
    assert any("different trim (LARIAT) than this XL" in r for r in v.reasons)


def test_unstated_trims_do_not_cap_confidence():
    peers = [peer(i, "2015 Toyota Camry", 12000 + 100 * i, year=2015, miles=90000 + 1500 * i)
             for i in range(8)]
    target = peer("t", "2015 Toyota Camry LE", 9000, year=2015, miles=95000, description="ok")
    v, _, trust, _ = judge(target, peers)
    assert v.confidence == "medium" and trust == "trusted"


def test_f150_spellings_are_one_model_with_its_trim():
    import valuation as V
    from sources.base import extract_make_model
    assert {extract_make_model(t)[1] for t in ("2009 Ford F150 XLT", "2009 Ford F-150 XLT",
                                                 "2009 Ford F 150 XLT")} == {"F-150"}
    assert V.parse_trim("2009 Ford F150 XLT", "F-150") == "XLT"


# ---------- title-only listings -----------------------------------------------

def test_title_only_listing_is_labeled_unchecked_but_not_blocked():
    target = peer("t", "2015 Toyota Camry LE", 9000, year=2015, miles=95000, description="")
    _, _, trust, flags = judge(target, camry_market())
    assert "description_unchecked" in flags and trust == "trusted"
    target["description"] = "one owner, clean title"
    assert "description_unchecked" not in judge(target, camry_market())[3]


# ---------- asking prices are never presented as sales -------------------------

def test_asking_prices_are_labeled_as_asking_end_to_end(client, world):
    """Without completed-sale evidence the value is an estimate from asking
    prices, every comparable says so, and profit is labeled estimated."""
    make_user(client, "ask@example.com", searches=[("c", {"model": "camry"})])
    world.ingest([L("ask", 8100)])
    score_all()
    login(client, "ask@example.com")
    d = client.get("/api/deals/craigslist:ask").get_json()
    assert d["evidence_class"] == "asking_comparables"
    assert {c["kind"] for c in d["evidence"]["comps"]} == {"asking"}
    assert d["profit_detail"]["disclaimer"].startswith("Estimated")
    assert any("not completed sales" in r for r in d["confidence_reasons"])
    assert q("SELECT basis FROM near_misses WHERE composite_id = 'craigslist:ask'")[0]["basis"] \
        == "estimate"


def test_last_asking_prices_from_sale_feeds_are_not_confirmed_sales():
    """MarketCheck /sales and Hemmings 'sold' report the last asking price of
    a listing that disappeared; they are never confirmed sales."""
    import valuation as V
    recent = (NOW - timedelta(days=20)).date().isoformat()
    raw = {"marketcheck": [{"price": 11000 + i, "title": "2015 Toyota Camry LE",
                            "end_date": recent, "year": 2015} for i in range(5)],
           "hemmings": [{"price": 11500, "title": "2015 Toyota Camry LE", "end_date": recent,
                         "year": 2015}]}
    out = V.classify_sold_items(raw, 2015, "Toyota", "Camry")
    assert out[V.CONFIRMED_SALES] == []
    assert len(out[V.HISTORICAL]) == 6
    assert {r["why_incomplete"] for r in out[V.HISTORICAL]} == {
        "last asking price, not a verified sale"}
