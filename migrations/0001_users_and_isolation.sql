-- 0001_users_and_isolation.sql
-- Multi-user accounts + per-user data isolation.
--
-- Schema choices:
--   * users.role  in ('user','admin')  — admin gets settings + scrape triggers
--   * users.plan  in ('free','starter','pro') — feature gating
--   * email is unique, lowercase normalized
--   * sessions are opaque random tokens stored server-side (cookie carries token only)
--   * saved gets a user_id; PK becomes (user_id, composite_id) so two users
--     can independently save the same listing
--   * user_alerts is a per-user view onto the shared `alerts` deal feed,
--     tracking read state so each user has their own alert tray.

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    plan            TEXT NOT NULL DEFAULT 'free',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS sessions (
    token           TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    user_agent      TEXT,
    ip              TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- Migrate `saved` to be user-scoped. SQLite can't DROP a PRIMARY KEY in place
-- without a table rewrite — recreate it.
CREATE TABLE IF NOT EXISTS saved_v2 (
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    composite_id    TEXT NOT NULL REFERENCES listings(composite_id),
    note            TEXT,
    status          TEXT NOT NULL DEFAULT 'saved',
    saved_at        TEXT NOT NULL,
    updated_at      TEXT,
    PRIMARY KEY (user_id, composite_id)
);

CREATE INDEX IF NOT EXISTS idx_saved_v2_user    ON saved_v2(user_id);
CREATE INDEX IF NOT EXISTS idx_saved_v2_saved_at ON saved_v2(user_id, saved_at DESC);

-- Saved searches: user-named filter presets that can optionally alert.
CREATE TABLE IF NOT EXISTS saved_searches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    filter_json     TEXT NOT NULL,
    notify_enabled  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_alert_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_saved_searches_user ON saved_searches(user_id);

-- Per-user alert tray (view onto the shared `alerts` deal feed).
CREATE TABLE IF NOT EXISTS user_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    alert_id        INTEGER NOT NULL REFERENCES alerts(id),
    saved_search_id INTEGER REFERENCES saved_searches(id) ON DELETE SET NULL,
    seen            INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, alert_id)
);

CREATE INDEX IF NOT EXISTS idx_user_alerts_user
    ON user_alerts(user_id, created_at DESC);
