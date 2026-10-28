-- 0013_source_probe_runs.sql
-- Aggregate results of bounded live-source checks (verify_sources.py).
-- Real provider observations only — never listing content, never synthetic
-- fixtures. Kept apart from listings/eval tables on purpose.
CREATE TABLE IF NOT EXISTS source_probe_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    checked_at    TEXT NOT NULL,
    territory_zip TEXT,
    radius_mi     INTEGER,
    status        TEXT NOT NULL,
    verified      INTEGER NOT NULL DEFAULT 0,
    listings      INTEGER NOT NULL DEFAULT 0,
    summary       TEXT NOT NULL,          -- JSON aggregate from verify_sources
    origin        TEXT NOT NULL DEFAULT 'cli',
    created_at    TEXT NOT NULL,
    UNIQUE(source, checked_at)
);
CREATE INDEX IF NOT EXISTS idx_probe_source_time ON source_probe_runs(source, checked_at);
