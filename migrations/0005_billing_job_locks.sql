-- 0005_billing_job_locks.sql
-- One-row-per-job table that doubles as a transactional advisory lock.
-- A reconciliation invocation acquires the lock by setting the row's
-- holder + acquired_at inside a single transaction; any concurrent
-- attempt either finds it already held (and exits) or steals it after
-- a stale-timeout has passed.
--
-- We use a regular table (not Postgres-only advisory locks or SQLite-
-- only file locks) so the same code path works in dev and prod.

CREATE TABLE IF NOT EXISTS billing_job_locks (
    job_name     TEXT PRIMARY KEY,
    holder       TEXT,                -- short opaque token of current holder
    acquired_at  TEXT,
    expires_at   TEXT,                -- holder must finish or refresh before this
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed the row so acquire is a pure UPDATE-with-WHERE — no INSERT race.
INSERT INTO billing_job_locks(job_name, holder, acquired_at, expires_at)
VALUES ('reconcile_billing', NULL, NULL, NULL)
ON CONFLICT(job_name) DO NOTHING;
