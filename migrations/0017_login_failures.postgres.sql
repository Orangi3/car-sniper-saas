-- 0017_login_failures.postgres.sql — see the .sql twin.
CREATE TABLE IF NOT EXISTS login_failures (
    rate_key  TEXT NOT NULL,
    at        DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_failures_key_at ON login_failures(rate_key, at);
CREATE INDEX IF NOT EXISTS idx_login_failures_at ON login_failures(at);
