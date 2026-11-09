"""
tests/test_seller_verification_caps.py — the seller "confirm your email"
message can't be aimed at a third party over and over (SEC-4).

Any signed-in account may name a contact address, and FlipFirst emails it a
verification link: that is how a seller proves the address is theirs. The
per-account limits (5 a day, 60 s apart) stay as they were; these tests pin
the two limits that do not depend on how many accounts someone controls:
  * one address gets at most 5 verification emails a day, whichever
    accounts ask for them;
  * the whole site sends at most SELLER_MAX_VERIFY_EMAILS_PER_DAY a day.
A seller verifying their own address is unaffected. SYNTHETIC accounts;
mail is captured, nothing is sent. SQLite and PostgreSQL.
"""
from __future__ import annotations

from tests.conftest import login, logout, register
from tests.test_matching_and_history import world  # noqa: F401
from tests.test_seller_listings import car, mail, token_from  # noqa: F401

VICTIM = "victim@third-party.example"


def _ask(client, email, to):
    register(client, email)
    login(client, email)
    r = client.post("/api/seller/contact", json={"email": to})
    logout(client)
    return r.status_code


def test_one_address_gets_at_most_five_verification_emails_a_day(client, world, mail):
    codes = [_ask(client, f"spam{i}@example.com", VICTIM) for i in range(7)]
    assert codes == [200] * 5 + [429, 429]
    assert [to for to, _ in mail] == [VICTIM] * 5


def test_the_recipient_limit_ignores_letter_case(client, world, mail):
    codes = [_ask(client, f"case{i}@example.com", VICTIM.upper() if i % 2 else VICTIM)
             for i in range(6)]
    assert codes == [200] * 5 + [429]


def test_site_wide_daily_cap_stops_verification_emails(client, world, mail, monkeypatch):
    import seller_listings as sl
    monkeypatch.setattr(sl, "MAX_VERIFY_EMAILS_PER_DAY", 3, raising=False)
    codes = [_ask(client, f"many{i}@example.com", f"person{i}@third-party.example")
             for i in range(4)]
    assert codes == [200, 200, 200, 429]
    assert len(mail) == 3


def test_a_seller_still_verifies_their_own_address_and_publishes(client, world, mail):
    register(client, "seller@example.com")
    login(client, "seller@example.com")
    assert client.post("/api/seller/listings", json=car()).status_code == 201
    assert client.post("/api/seller/contact", json={}).status_code == 200
    r = client.post("/api/seller/contact/verify",
                    json={"token": token_from(mail, "seller@example.com")})
    assert r.status_code == 200 and r.get_json()["verified"] is True
    assert r.get_json()["published"] == 1


def test_plus_tags_and_gmail_dots_count_as_one_inbox(client, world, mail):
    """victim+1@gmail.com, v.ictim@gmail.com and VICTIM@googlemail.com all
    reach one Gmail inbox; counting them apart would let the per-recipient
    limit be walked around by varying the address."""
    variants = ["victim+1@gmail.com", "v.ictim@gmail.com", "VICTIM+x@googlemail.com",
                "vic.tim+2@gmail.com", "victim@gmail.com", "victim+again@gmail.com"]
    codes = [_ask(client, f"alias{i}@example.com", to) for i, to in enumerate(variants)]
    assert codes == [200] * 5 + [429]


def test_dots_still_matter_outside_gmail(client, world, mail):
    codes = [_ask(client, f"dots{i}@example.com", to)
             for i, to in enumerate(["a.b@example.org", "ab@example.org"] * 3)]
    assert codes == [200] * 6                     # two different inboxes, 3 each
