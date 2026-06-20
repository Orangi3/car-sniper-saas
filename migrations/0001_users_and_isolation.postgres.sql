-- 0001_users_and_isolation.postgres.sql
-- Postgres variant — same shape as the SQLite version but uses native
-- BIGSERIAL, TIMESTAMPTZ, BOOLEAN, ON DELETE CASCADE, and CITEXT for
-- case-insensitive email lookups.

CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    email           CITEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    plan            TEXT NOT NULL DEFAULT 'free',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS sessions (
    token           TEXT PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_agent      TEXT,
    ip              TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS saved_v2 (
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    composite_id    TEXT NOT NULL REFERENCES listings(composite_id),
    note            TEXT,
    status          TEXT NOT NULL DEFAULT 'saved',
    saved_at        TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ,
    PRIMARY KEY (user_id, composite_id)
);

CREATE INDEX IF NOT EXISTS idx_saved_v2_user     ON saved_v2(user_id);
CREATE INDEX IF NOT EXISTS idx_saved_v2_saved_at ON saved_v2(user_id, saved_at DESC);

CREATE TABLE IF NOT EXISTS saved_searches (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    filter_json     TEXT NOT NULL,
    notify_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_alert_id BIGINT
);

CREATE INDEX IF NOT EXISTS idx_saved_searches_user ON saved_searches(user_id);

CREATE TABLE IF NOT EXISTS user_alerts (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    alert_id        BIGINT NOT NULL REFERENCES alerts(id),
    saved_search_id BIGINT REFERENCES saved_searches(id) ON DELETE SET NULL,
    seen            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, alert_id)
);

CREATE INDEX IF NOT EXISTS idx_user_alerts_user
    ON user_alerts(user_id, created_at DESC);
