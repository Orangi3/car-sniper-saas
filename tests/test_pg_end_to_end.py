"""
tests/test_pg_end_to_end.py — the P0.1 acceptance test, on REAL PostgreSQL.

Skipped unless SNIPER_TEST_PG_URL is set. Applies migrations, creates a
user, ingests listings, scores them through the durable job, produces a
qualified owner alert and a saved-search match, reads the match back
through the authenticated API — then checks with a raw psycopg2
connection that every row is in Postgres and no SQLite file was created.
"""
from __future__ import annotations

import os

import pytest

from tests.conftest import PG_ADMIN_URL, login
from tests.test_matching_and_history import L, make_user, score_all

pytestmark = pytest.mark.skipif(not PG_ADMIN_URL, reason="needs SNIPER_TEST_PG_URL")


def test_full_flow_lives_in_one_postgres_database(client, app, monkeypatch, temp_db_path):
    import comps
    import db
    import psycopg2
    import sniper
    assert db.IS_PG
    monkeypatch.setattr(comps, "raw_sold", lambda y, mk, md: ({}, 0))
    monkeypatch.setattr(sniper._config_mod, "reload", lambda: None)
    monkeypatch.setenv("NTFY_TOPIC", "sniper-owner-topic-123")
    monkeypatch.setitem(sniper.CONFIG, "notify_text_enabled", True)
    monkeypatch.setitem(sniper.CONFIG, "notify_min_score", 0)
    monkeypatch.setitem(sniper.CONFIG, "deal_threshold_pct", 15)

    uid = make_user(client, "pg@example.com", searches=[("c", {"model": "camry"})])
    sniper.ingest([L(f"peer{i}", p, odo=100000 + i * 7000) for i, p in
                   enumerate([10000, 10400, 9800, 10200, 10600, 9900, 10100, 10300, 10500])])
    # ~33% under: clears the owner's 30% bar for asking-price evidence
    sniper.ingest([L("deal", 6800)])
    results = score_all()
    assert results and all(r["status"] == "done" for r in results)

    login(client, "pg@example.com")
    tray = client.get("/api/me/alerts").get_json()
    assert [t["composite_id"] for t in tray] == ["craigslist:deal"]

    raw = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        cur = raw.cursor()
        def one(sql, *p):
            cur.execute(sql, p)
            return cur.fetchone()[0]
        assert one("SELECT COUNT(*) FROM listings") == 10
        assert one("SELECT COUNT(*) FROM listing_prices") == 10
        assert one("SELECT COUNT(*) FROM near_misses WHERE evidence_class IS NOT NULL") >= 1
        assert one("SELECT COUNT(*) FROM alerts WHERE composite_id = 'craigslist:deal'") == 1
        assert one("SELECT COUNT(*) FROM user_matches WHERE user_id = %s", uid) == 1
        assert one("SELECT COUNT(*) FROM jobs WHERE kind = 'score' AND status = 'done'") == 10
        assert one("SELECT COUNT(*) FROM jobs WHERE kind = 'notify'") == 2   # owner + user
        assert one("SELECT COUNT(*) FROM users WHERE email = 'pg@example.com'") == 1
    finally:
        raw.close()
    assert not temp_db_path.exists(), "a SQLite file was created in Postgres mode"
