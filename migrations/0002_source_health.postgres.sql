-- 0002_source_health.postgres.sql

CREATE TABLE IF NOT EXISTS source_health (
    source_id        TEXT PRIMARY KEY,
    last_attempt_at  TIMESTAMPTZ,
    last_success_at  TIMESTAMPTZ,
    last_error       TEXT,
    last_count       INTEGER,
    last_elapsed_s   DOUBLE PRECISION,
    listings_total   INTEGER,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scheduler_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    last_tick_at    TIMESTAMPTZ,
    last_tick_secs  DOUBLE PRECISION,
    tick_counter    INTEGER NOT NULL DEFAULT 0,
    enabled_sources TEXT
);

INSERT INTO scheduler_state(id, tick_counter)
VALUES (1, 0)
ON CONFLICT(id) DO NOTHING;
