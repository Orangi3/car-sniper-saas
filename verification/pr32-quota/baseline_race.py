"""Baseline-only reproduction: PASS means the pinned vulnerability was reproduced; never run on patched code."""
import pytest
from tests.conftest import register,logout
from tests.test_matching_and_history import world
from tests.test_seller_listings import mail

def test_postgres_cross_account_cap_snapshot_race(client,world,mail,monkeypatch):
    import db,auth,seller_listings as sl
    if not db.IS_PG:
        pytest.skip('requires disposable PostgreSQL; SQLite globally serializes writes')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier,local
    users=[]
    for i in range(4):
        address=f'pg-cap{i}@example.com'
        register(client,address); users.append(auth.get_user_by_email(address)); logout(client)
    monkeypatch.setattr(sl,'MAX_VERIFY_EMAILS_PER_DAY',2)
    barrier=Barrier(4); state=local(); real=sl._mailbox
    def mailbox(address):
        if not getattr(state,'waited',False):
            state.waited=True
            # _issue_token has just read the site's recent token rows.
            barrier.wait(timeout=10)
        return real(address)
    monkeypatch.setattr(sl,'_mailbox',mailbox)
    def send(user):
        try: sl.send_verification(user,'https://pilot.example.invalid'); return 200
        except sl.SubmissionError as e: return e.status
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(send,users))
    print("BASELINE_RACE cap=2 successes=%d sends=%d" % (results.count(200),len(mail)))
    assert results.count(200)==4
    assert len(mail)==4
