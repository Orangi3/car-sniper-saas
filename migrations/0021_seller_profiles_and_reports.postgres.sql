-- 0021_seller_profiles_and_reports.postgres.sql — see the .sql twin.
CREATE TABLE IF NOT EXISTS seller_profiles (
    user_id        BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL DEFAULT 'dealer',
    business_name  TEXT NOT NULL,
    max_active     INTEGER NOT NULL,
    approved_note  TEXT,
    approved_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listing_reports (
    id            BIGSERIAL PRIMARY KEY,
    composite_id  TEXT NOT NULL,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL,
    note          TEXT,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT,
    resolution    TEXT,
    UNIQUE (composite_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_listing_reports_open ON listing_reports(resolved_at, created_at);
