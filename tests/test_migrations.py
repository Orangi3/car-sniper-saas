"""
tests/test_migrations.py — migrations apply cleanly, are idempotent, and
upgrade a populated pre-0008 database without losing data.
Runs on SQLite, and on Postgres when SNIPER_TEST_PG_URL is set.
"""
from __future__ import annotations

import sys


def _fresh_runner():
    for m in ("db", "migrations.runner", "migrations"):
        sys.modules.pop(m, None)
    from migrations import runner
    return runner


def test_all_migrations_apply_and_rerun_is_noop(temp_db_path):
    runner = _fresh_runner()
    first = runner.run_pending(verbose=False)
    assert first == [m[0] for m in runner._list_migrations()]
    assert first[0] == "0000_legacy_schema" and len(first) >= 12
    assert runner.run_pending(verbose=False) == []


def test_upgrade_from_0007_keeps_data_and_backfills(temp_db_path, monkeypatch):
    runner = _fresh_runner()
    allm = runner._list_migrations()
    monkeypatch.setattr(runner, "_list_migrations",
                        lambda: [m for m in allm if m[0] < "0008"])
    runner.run_pending(verbose=False)
    import db
    with db.session() as c:
        c.execute("INSERT INTO listings(composite_id, source, source_id, url, title, "
                  "price, year, make, model, first_seen_at) VALUES "
                  "('cl:old','craigslist','old','https://x','2012 Honda Civic',5000,2012,"
                  "'Honda','Civic','2026-01-01T00:00:00+00:00')")
    monkeypatch.setattr(runner, "_list_migrations", lambda: allm)
    applied = runner.run_pending(verbose=False)
    assert applied == [m[0] for m in allm if m[0] >= "0008"]
    with db.session() as c:
        r = dict(c.execute("SELECT price, last_seen_at, needs_work FROM listings "
                           "WHERE composite_id = 'cl:old'").fetchone())
    assert r["price"] == 5000
    assert r["last_seen_at"].startswith("2026-01-01")
    assert r["needs_work"] == 0
