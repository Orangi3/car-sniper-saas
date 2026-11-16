"""Native PostgreSQL only; real locking/HTTP issuance, synthetic mail transport."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.conftest import register, login
from tests.test_seller_listings import mail


def assert_timeout_result(status, body, code, sends, tokens):
    assert code == '55P03'
    assert status == 503
    message = 'verification is busy — please retry shortly'
    assert body == {'error': message, 'errors': [message]}
    assert sends == 0
    assert tokens == 0


@pytest.mark.parametrize('kind', ['lock_timeout', 'unexpected_statement_timeout'])
def test_native_timeout_cleanup_and_retry(app, client, mail, monkeypatch, kind):
    import db
    import seller_listings as sl
    import psycopg2
    assert db.IS_PG, 'This acceptance test must execute on genuine PostgreSQL'
    for email in ('timeout@example.com', 'capacity@example.com'):
        assert register(client, email).status_code in (200, 201)
    monkeypatch.setattr(sl, 'MAX_VERIFY_EMAILS_PER_DAY', 1)
    monkeypatch.setattr(sl, 'MAX_SENDS_PER_RECIPIENT_PER_DAY', 1)

    def count():
        with db.session() as c:
            return c.execute('SELECT COUNT(*) FROM contact_verifications').fetchone()[0]

    assert count() == 0 and len(mail) == 0
    observed = {}
    real_execute = db.Conn.execute
    real_rollback = db.Conn.rollback

    # Observers delegate to the real adapter; the advisory lock is never mocked.
    def observe_execute(conn, sql, params=()):
        if 'pg_advisory_xact_lock' not in sql:
            return real_execute(conn, sql, params)
        observed['pid'] = conn.raw.get_backend_pid()
        observed['raw'] = conn.raw
        observed['isolation'] = real_execute(conn, 'SHOW transaction_isolation').fetchone()[0]
        observed['lock_timeout'] = real_execute(conn, 'SHOW lock_timeout').fetchone()[0]
        if kind == 'unexpected_statement_timeout':
            real_execute(conn, "SET LOCAL statement_timeout = '100ms'")
        start = time.monotonic()
        try:
            return real_execute(conn, sql, params)
        except psycopg2.Error as exc:
            observed['sqlstate'] = exc.pgcode
            observed['wait_seconds'] = time.monotonic() - start
            raise

    def observe_rollback(conn):
        if conn.raw is observed.get('raw'):
            observed['rollback_called'] = True
        return real_rollback(conn)

    def attempt():
        with app.test_client() as worker:
            assert login(worker, 'timeout@example.com').status_code == 200
            response = worker.post('/api/seller/contact', json={})
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.session() as holder:
            sl._lock_email_quota(holder)
            holder_pid = holder.raw.get_backend_pid()
            with monkeypatch.context() as observe:
                observe.setattr(db.Conn, 'execute', observe_execute)
                observe.setattr(db.Conn, 'rollback', observe_rollback)
                pending = pool.submit(attempt)
                if kind == 'lock_timeout':
                    status, body = pending.result(timeout=20)
                    assert_timeout_result(status, body, observed.get('sqlstate'), len(mail), count())
                    assert 4.5 <= observed['wait_seconds'] < 15
                else:
                    # Genuine 57014 is outside the production 55P03 handler.
                    # Flask TESTING propagates it; it must not become success/503.
                    with pytest.raises(psycopg2.errors.QueryCanceled):
                        pending.result(timeout=20)
                    assert observed['sqlstate'] == '57014'
                    assert len(mail) == 0 and count() == 0
                    status, body = None, None
            assert observed['pid'] != holder_pid
            assert observed['isolation'] == 'read committed'
            assert observed['lock_timeout'] == '5s'
            assert observed.get('rollback_called') is True
            assert observed['raw'].closed != 0
            with db.session() as check:
                assert check.execute('SELECT COUNT(*) FROM pg_locks WHERE pid=?', (observed['pid'],)).fetchone()[0] == 0
                assert check.execute('SELECT COUNT(*) FROM pg_stat_activity WHERE pid=?', (observed['pid'],)).fetchone()[0] == 0
            holder.rollback()

    # A fresh HTTP request must consume exactly the one remaining allowance.
    with app.test_client() as retry:
        assert login(retry, 'timeout@example.com').status_code == 200
        response = retry.post('/api/seller/contact', json={})
        assert response.status_code == 200 and response.get_json()['sent'] is True
        assert count() == 1 and len(mail) == 1
    with app.test_client() as exhausted:
        assert login(exhausted, 'capacity@example.com').status_code == 200
        response = exhausted.post('/api/seller/contact', json={})
        assert response.status_code == 429
        assert count() == 1 and len(mail) == 1
    print('TIMEOUT_EVIDENCE ' + json.dumps({
        'case': kind, 'sqlstate': observed['sqlstate'], 'http_status': status,
        'http_body': body, 'wait_seconds': round(observed['wait_seconds'], 3),
        'isolation': observed['isolation'], 'lock_timeout': observed['lock_timeout'],
        'distinct_connections': True, 'rollback_called': True,
        'failed_connection_closed': True, 'failed_connection_locks': 0,
        'timeout_tokens': 0, 'timeout_smtp_calls': 0,
        'retry_status': 200, 'retry_tokens': 1, 'retry_smtp_calls': 1,
        'exhausted_status': 429, 'final_tokens': 1, 'final_smtp_calls': 1,
    }, sort_keys=True))
