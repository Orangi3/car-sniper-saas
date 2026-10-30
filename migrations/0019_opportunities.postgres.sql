-- 0019_opportunities.postgres.sql — see the .sql twin.
CREATE TABLE IF NOT EXISTS opportunities (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'analyzing',
    title           TEXT NOT NULL,
    input_json      TEXT NOT NULL,
    analysis_json   TEXT,
    notes           TEXT,
    composite_id    TEXT,
    purchase_price  INTEGER,
    purchased_on    TEXT,
    repair_spent    INTEGER,
    fees_spent      INTEGER,
    sale_price      INTEGER,
    sold_on         TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opportunities_user ON opportunities(user_id, updated_at);
