-- 0006_scheduler_lock.postgres.sql

INSERT INTO billing_job_locks(job_name, holder, acquired_at, expires_at)
VALUES ('scheduler', NULL, NULL, NULL)
ON CONFLICT(job_name) DO NOTHING;

ALTER TABLE scheduler_state ADD COLUMN IF NOT EXISTS holder          TEXT;
ALTER TABLE scheduler_state ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ;
ALTER TABLE scheduler_state ADD COLUMN IF NOT EXISTS last_error      TEXT;
ALTER TABLE scheduler_state ADD COLUMN IF NOT EXISTS last_error_at   TIMESTAMPTZ;
