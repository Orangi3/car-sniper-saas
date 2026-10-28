-- 0008_durable_jobs.postgres.sql — see the .sql twin for rationale.

CREATE TABLE IF NOT EXISTS jobs (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,
    dedupe_key    TEXT NOT NULL UNIQUE,
    payload       TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',
    priority      INTEGER NOT NULL DEFAULT 100,
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 6,
    run_after     TIMESTAMPTZ NOT NULL,
    locked_by     TEXT,
    locked_until  TIMESTAMPTZ,
    last_error    TEXT,
    result        TEXT,
    user_id       BIGINT,
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_jobs_ready  ON jobs(status, run_after, priority);
CREATE INDEX IF NOT EXISTS idx_jobs_kind   ON jobs(kind, status);
CREATE INDEX IF NOT EXISTS idx_jobs_user   ON jobs(user_id, kind, created_at);

CREATE TABLE IF NOT EXISTS marketplace_inbox (
    id           TEXT PRIMARY KEY,
    platform     TEXT NOT NULL,
    payload      TEXT NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL,
    user_id      BIGINT
);
