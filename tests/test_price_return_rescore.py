"""
tests/test_price_return_rescore.py — a price that returns to an earlier value
is valued again (sniper.price_change_reason).

Score jobs were keyed by (listing, price), and a key is kept for 14 days. So
"$8,100" mistyped as "$810" and then corrected was never re-scored at $8,100:
the car kept the typo's valuation — "92% below market", risk feed,
too_good_to_be_true — at its real price, and backfill_unscored() could not
repair it (same key). Each price change now has its own key.

SYNTHETIC fixtures only: no provider is called, nothing here is live data.
"""
from __future__ import annotations

import json

from tests.test_matching_and_history import L, make_user, q, score_all, world  # noqa: F401


def _nm(cid):
    return q("SELECT price_at_score, discount_pct, trust, risk_flags FROM near_misses "
             "WHERE composite_id = ?", (cid,))[0]


def test_corrected_price_typo_is_valued_again(world):
    world.ingest([L("ty", 8100)])
    score_all()
    world.ingest([L("ty", 810)])                  # "$810": a typo
    score_all()
    assert _nm("craigslist:ty")["trust"] == "risk"
    world.ingest([L("ty", 8100)])                 # corrected
    score_all()
    nm = _nm("craigslist:ty")
    assert nm["price_at_score"] == 8100
    assert 15 < nm["discount_pct"] < 30           # not the typo's 92%
    assert nm["trust"] == "trusted"
    assert "too_good_to_be_true" not in json.loads(nm["risk_flags"])


def test_reverted_price_rise_is_valued_again_without_repeating_alerts(client, world):
    """$8,100 → $9,500 → $8,100: the valuation follows the price. A buyer
    already told about this car at $8,100 is not told again; a plan with
    price-drop alerts hears about the drop from $9,500, once."""
    starter = make_user(client, "s@example.com", searches=[("c", {"model": "camry"})])
    pro = make_user(client, "p@example.com", plan="pro", searches=[("c", {"model": "camry"})])
    world.ingest([L("rv", 8100)])
    score_all()
    world.ingest([L("rv", 9500)])
    score_all()
    assert _nm("craigslist:rv")["price_at_score"] == 9500
    world.ingest([L("rv", 8100)])
    score_all()
    assert _nm("craigslist:rv")["price_at_score"] == 8100
    assert q("SELECT event, price FROM user_matches WHERE user_id = ?", (starter,)) \
        == [{"event": "new", "price": 8100}]
    assert q("SELECT event, price FROM user_matches WHERE user_id = ? ORDER BY id", (pro,)) \
        == [{"event": "new", "price": 8100}, {"event": "price_drop", "price": 8100}]
    world.ingest([L("rv", 8100)])                 # unchanged re-poll: nothing new
    score_all()
    assert len(q("SELECT 1 FROM user_matches")) == 3


def test_backfill_repairs_a_listing_left_on_an_old_price(world):
    """A listing stuck by the old keying (price back at $8,100, valuation
    still at $810) is repaired by the backfill that runs at server start."""
    import db
    import sniper
    from datetime import datetime, timezone
    world.ingest([L("bf", 8100)])
    score_all()
    world.ingest([L("bf", 810)])
    score_all()
    with db.session() as c:                       # what the old release left behind
        c.execute("UPDATE listings SET price = 8100, previous_price = 810, "
                  "price_changed_at = ? WHERE composite_id = 'craigslist:bf'",
                  (datetime.now(timezone.utc).isoformat(),))
    assert _nm("craigslist:bf")["price_at_score"] == 810
    assert sniper.backfill_unscored() == 1
    score_all()
    assert _nm("craigslist:bf")["price_at_score"] == 8100
    assert sniper.backfill_unscored() == 0        # repaired: nothing left to queue


def test_backfill_rebuilds_the_key_ingest_wrote(world):
    """Same key from the written timestamp and the stored one (Postgres
    TIMESTAMPTZ comes back reformatted): no duplicate re-score."""
    import sniper
    world.ingest([L("k1", 8100)])
    score_all()
    world.ingest([L("k1", 7600)])                 # re-score queued, not yet run
    row = q("SELECT price_changed_at FROM listings WHERE composite_id = 'craigslist:k1'")[0]
    keys = {r["dedupe_key"] for r in q("SELECT dedupe_key FROM jobs WHERE kind = 'score' "
                                       "AND status = 'pending'")}
    assert keys == {"score:craigslist:k1:7600:"
                    + sniper.price_change_reason(row["price_changed_at"])}
    assert sniper.backfill_unscored() == 0


def test_change_key_is_utc_whatever_the_offset():
    import sniper
    assert sniper.price_change_reason("2026-11-07T16:40:00.123456+00:00") \
        == sniper.price_change_reason("2026-11-07T10:40:00.123456-06:00") \
        == "changed:20261107T164000.123456"
