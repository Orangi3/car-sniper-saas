-- 0002_source_health.sql
-- Persistent per-source health snapshot for the admin dashboard.
-- The scheduler upserts one row per source on every tick.

CREATE TABLE IF NOT EXISTS source_health (
    source_id        TEXT PRIMARY KEY,
    last_attempt_at  TEXT,
    last_success_at  TEXT,
    last_error       TEXT,
    last_count       INTEGER,
    last_elapsed_s   REAL,
    listings_total   INTEGER,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Single-row table for global scheduler heartbeat (last tick, total ticks,
-- enabled-source list at last tick). Read by /api/admin/sources/health.
CREATE TABLE IF NOT EXISTS scheduler_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    last_tick_at    TEXT,
    last_tick_secs  REAL,
    tick_counter    INTEGER NOT NULL DEFAULT 0,
    enabled_sources TEXT
);

INSERT INTO scheduler_state(id, tick_counter)
VALUES (1, 0)
ON CONFLICT(id) DO NOTHING;
