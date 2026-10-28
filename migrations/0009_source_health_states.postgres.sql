-- 0009_source_health_states.postgres.sql — see the .sql twin.

ALTER TABLE source_health ADD COLUMN IF NOT EXISTS status                  TEXT;
ALTER TABLE source_health ADD COLUMN IF NOT EXISTS status_reason           TEXT;
ALTER TABLE source_health ADD COLUMN IF NOT EXISTS requests_attempted      INTEGER;
ALTER TABLE source_health ADD COLUMN IF NOT EXISTS requests_ok             INTEGER;
ALTER TABLE source_health ADD COLUMN IF NOT EXISTS listings_new            INTEGER;
ALTER TABLE source_health ADD COLUMN IF NOT EXISTS consecutive_empty       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE source_health ADD COLUMN IF NOT EXISTS consecutive_failures    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE source_health ADD COLUMN IF NOT EXISTS last_genuine_success_at TIMESTAMPTZ;
ALTER TABLE source_health ADD COLUMN IF NOT EXISTS next_poll_after         TIMESTAMPTZ;
ALTER TABLE source_health ADD COLUMN IF NOT EXISTS ingest_latency_s        DOUBLE PRECISION;
