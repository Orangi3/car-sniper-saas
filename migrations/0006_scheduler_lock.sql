-- 0006_scheduler_lock.sql
-- Bring the source-polling scheduler under the same DB-backed advisory
-- lock that already gates billing reconciliation. The web tier MUST NOT
-- own the scheduler — exactly one process holds the 'scheduler' lock
-- at a time, and the rest stand by.
--
-- Also widen scheduler_state (seeded in 0002) with audit columns so the
-- admin health endpoint can surface which host is currently running the
-- scheduler, when it last succeeded, and what its most recent error was.

INSERT INTO billing_job_locks(job_name, holder, acquired_at, expires_at)
VALUES ('scheduler', NULL, NULL, NULL)
ON CONFLICT(job_name) DO NOTHING;

ALTER TABLE scheduler_state ADD COLUMN holder           TEXT;
ALTER TABLE scheduler_state ADD COLUMN last_success_at  TEXT;
ALTER TABLE scheduler_state ADD COLUMN last_error       TEXT;
ALTER TABLE scheduler_state ADD COLUMN last_error_at    TEXT;
