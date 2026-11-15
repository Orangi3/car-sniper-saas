"""
tests/test_seller_contact_fail_closed.py — a seller's contact address is
shown only to a known paid plan.

seller_listings.contact_for decided with `plan == "free"`, so every other
value counted as paid. A plan column holding anything unexpected — a billing
status such as "canceled", a misspelling, a future plan name — revealed the
seller's verified email address to that account. Every other plan check in
the app already falls back to the free plan for an unknown value; this one
did not.

The rule is now an allowlist: only "starter" and "pro" see the address.
Anything else gets the same upgrade notice as the free plan, and the address
is nowhere in the response. SYNTHETIC sellers and buyers; SQLite and
PostgreSQL.
"""
from __future__ import annotations

import pytest

from tests.conftest import login, logout, register, set_plan
from tests.test_matching_and_history import score_all, world  # noqa: F401
from tests.test_seller_listings import car, mail, seller  # noqa: F401

SELLER = "seller@example.com"
BUYER = "buyer@example.com"
SHOWN = {"available": True, "email": SELLER, "verified_email": True}
UPGRADE = {"available": False, "why": "upgrade to see the seller's contact"}
# values a plan column could come to hold without being a paid plan
NOT_PAID = ["free", "", "canceled", "past_due", "unpaid", "trialing", "expired",
            "Pro", "PRO", "Starter", " starter", "starter ", "lite", "paid", "premium",
            "enterprise", "admin", "true", "1", "starter,pro", "free' OR '1'='1"]


@pytest.fixture()
def listing(client, world, mail):
    """A live seller listing whose contact address is verified."""
    seller(client, box=mail)
    cid = client.post("/api/seller/listings", json=car()).get_json()["composite_id"]
    score_all()
    logout(client)
    return cid


def _force_plan(value: str) -> None:
    """Write a plan value straight to the database, as a manual edit, a bad
    restore or a future billing state could."""
    import db
    with db.session() as conn:
        conn.execute("UPDATE users SET plan = ? WHERE LOWER(email) = LOWER(?)", (value, BUYER))


def _detail(client, cid):
    r = client.get(f"/api/deals/{cid}")
    assert r.status_code == 200, r.get_json()
    return r


@pytest.mark.parametrize("plan", ["starter", "pro"])
def test_paid_plans_see_the_verified_contact(client, listing, plan):
    register(client, BUYER); login(client, BUYER)
    set_plan(BUYER, plan)
    assert _detail(client, listing).get_json()["seller_contact"] == SHOWN


def test_every_other_plan_value_gets_the_upgrade_notice_and_no_address(client, listing):
    register(client, BUYER); login(client, BUYER)
    leaked = []
    for plan in NOT_PAID:
        _force_plan(plan)
        r = _detail(client, listing)
        if r.get_json()["seller_contact"] != UPGRADE or SELLER in r.get_data(as_text=True):
            leaked.append(plan)
    assert leaked == [], f"the seller's address was shown to these plan values: {leaked}"


def test_missing_and_non_text_plan_values_fail_closed(client, listing):
    import db
    import seller_listings as sl
    with db.session() as conn:
        for plan in (None, "", 0, 1, True, False, [], {}, ("pro",), ["pro"], b"pro"):
            assert sl.contact_for(conn, listing, plan) == UPGRADE, repr(plan)
        assert sl.contact_for(conn, listing, "pro") == SHOWN       # the listing itself is live


def test_a_downgrade_hides_the_contact_again(client, listing):
    register(client, BUYER); login(client, BUYER)
    set_plan(BUYER, "pro")
    assert _detail(client, listing).get_json()["seller_contact"] == SHOWN
    set_plan(BUYER, "free")                                  # what a cancelled subscription becomes
    r = _detail(client, listing)
    assert r.get_json()["seller_contact"] == UPGRADE
    assert SELLER not in r.get_data(as_text=True)


def test_signed_out_visitors_get_nothing(client, listing):
    r = client.get(f"/api/deals/{listing}")
    assert r.status_code == 401
    assert SELLER not in r.get_data(as_text=True)
