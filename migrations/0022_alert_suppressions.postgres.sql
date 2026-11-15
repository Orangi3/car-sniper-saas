-- 0022_alert_suppressions.postgres.sql — see the .sql twin.
CREATE TABLE IF NOT EXISTS alert_suppressions (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    composite_id  TEXT NOT NULL,
    duplicate_of  TEXT NOT NULL,
    reason        TEXT NOT NULL,
    evidence      TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (user_id, composite_id)
);
CREATE INDEX IF NOT EXISTS idx_alert_suppressions_created ON alert_suppressions(created_at);
