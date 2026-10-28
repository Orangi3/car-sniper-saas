-- 0008_durable_jobs.sql
-- Durable background work (scoring, notifications) replacing the in-memory
-- Queue + daemon threads, which lost pending work on every restart.
--
--   status: pending → running → done | dead     (dead = retries exhausted)
--   A running job whose lease (locked_until) expires is reclaimable, so a
--   crashed worker's job is picked up again after restart.
--   dedupe_key is UNIQUE: enqueueing the same work twice is a no-op.

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    dedupe_key    TEXT NOT NULL UNIQUE,
    payload       TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',
    priority      INTEGER NOT NULL DEFAULT 100,
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 6,
    run_after     TEXT NOT NULL,
    locked_by     TEXT,
    locked_until  TEXT,
    last_error    TEXT,
    result        TEXT,
    user_id       INTEGER,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_ready  ON jobs(status, run_after, priority);
CREATE INDEX IF NOT EXISTS idx_jobs_kind   ON jobs(kind, status);
CREATE INDEX IF NOT EXISTS idx_jobs_user   ON jobs(user_id, kind, created_at);

-- Browser-extension / email imports used to live in a separate local
-- marketplace_inbox.sqlite file next to the code — invisible to any other
-- host and outside backups. It now lives in the main database.
CREATE TABLE IF NOT EXISTS marketplace_inbox (
    id           TEXT PRIMARY KEY,
    platform     TEXT NOT NULL,
    payload      TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    user_id      INTEGER
);
