-- 0013_source_probe_runs.postgres.sql — see the .sql twin.
CREATE TABLE IF NOT EXISTS source_probe_runs (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL,
    checked_at    TIMESTAMPTZ NOT NULL,
    territory_zip TEXT,
    radius_mi     INTEGER,
    status        TEXT NOT NULL,
    verified      BOOLEAN NOT NULL DEFAULT FALSE,
    listings      INTEGER NOT NULL DEFAULT 0,
    summary       TEXT NOT NULL,
    origin        TEXT NOT NULL DEFAULT 'cli',
    created_at    TIMESTAMPTZ NOT NULL,
    UNIQUE(source, checked_at)
);
CREATE INDEX IF NOT EXISTS idx_probe_source_time ON source_probe_runs(source, checked_at);
