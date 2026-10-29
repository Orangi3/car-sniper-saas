-- 0009_source_health_states.sql
-- Honest source status. last_success_at used to mean "poll() didn't raise",
-- which every source satisfied even when blocked (they swallowed errors).
--   status                 healthy|degraded|blocked|rate_limited|timeout|
--                          auth_config|disabled|unverified
--   last_success_at        last poll with >=1 successful, parsed request
--   last_genuine_success_at last poll that yielded >=1 listing
--   next_poll_after        persisted backoff after blocks / rate limits
--   ingest_latency_s       median (first_seen_at - posted_at) of the poll's
--                          new listings, where the source gives posted_at

ALTER TABLE source_health ADD COLUMN status                  TEXT;
ALTER TABLE source_health ADD COLUMN status_reason           TEXT;
ALTER TABLE source_health ADD COLUMN requests_attempted      INTEGER;
ALTER TABLE source_health ADD COLUMN requests_ok             INTEGER;
ALTER TABLE source_health ADD COLUMN listings_new            INTEGER;
ALTER TABLE source_health ADD COLUMN consecutive_empty       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE source_health ADD COLUMN consecutive_failures    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE source_health ADD COLUMN last_genuine_success_at TEXT;
ALTER TABLE source_health ADD COLUMN next_poll_after         TEXT;
ALTER TABLE source_health ADD COLUMN ingest_latency_s        REAL;
