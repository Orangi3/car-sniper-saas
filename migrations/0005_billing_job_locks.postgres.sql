-- 0005_billing_job_locks.postgres.sql

CREATE TABLE IF NOT EXISTS billing_job_locks (
    job_name     TEXT PRIMARY KEY,
    holder       TEXT,
    acquired_at  TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO billing_job_locks(job_name, holder, acquired_at, expires_at)
VALUES ('reconcile_billing', NULL, NULL, NULL)
ON CONFLICT(job_name) DO NOTHING;
