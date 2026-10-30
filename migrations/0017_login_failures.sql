-- 0017_login_failures.sql
-- Failed sign-in attempts per visitor (auth.login_throttled). Kept in the
-- database so every gunicorn worker counts against one shared budget and a
-- restart doesn't reset it; an in-memory count per worker multiplied the
-- allowed attempts by the number of workers. rate_key is the visitor's
-- address (an IPv6 /64); at is Unix time. Rows older than the window are
-- deleted as new failures arrive.
CREATE TABLE IF NOT EXISTS login_failures (
    rate_key  TEXT NOT NULL,
    at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_failures_key_at ON login_failures(rate_key, at);
CREATE INDEX IF NOT EXISTS idx_login_failures_at ON login_failures(at);
