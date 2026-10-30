-- 0019_opportunities.sql
-- FlipFirst Analyze workspace: each user's private deal research. One row
-- per car the user analyzed and saved: what they entered (input_json), the
-- latest analysis (analysis_json), inspection notes, the stage, and — once
-- they buy and sell it — what they actually paid, spent and sold for.
-- Private: every query is scoped to user_id; nothing here is shown to other
-- users or used in aggregate. Deleted by the user, and with the account
-- (accounts.py). composite_id loosely links a FlipFirst listing it came
-- from (no foreign key: listings expire independently).
CREATE TABLE IF NOT EXISTS opportunities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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
