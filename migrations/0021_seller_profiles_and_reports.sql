-- 0021_seller_profiles_and_reports.sql
-- seller_profiles: a dealer or wholesaler the owner approved (python -m
-- seller_listings approve-dealer). Approval raises the account's listing
-- limits, labels its cars as dealer listings under the business name, and
-- allows CSV inventory import. Private sellers have no row.
CREATE TABLE IF NOT EXISTS seller_profiles (
    user_id        INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL DEFAULT 'dealer',
    business_name  TEXT NOT NULL,
    max_active     INTEGER NOT NULL,
    approved_note  TEXT,
    approved_at    TEXT NOT NULL
);
-- listing_reports: a buyer's report about a FlipFirst seller listing (scam,
-- already sold, wrong details, offensive, other). One per buyer per listing;
-- the owner reviews open reports (python -m seller_listings reports) and
-- removes listings by hand. Nothing is hidden automatically.
CREATE TABLE IF NOT EXISTS listing_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    composite_id  TEXT NOT NULL,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason        TEXT NOT NULL,
    note          TEXT,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT,
    resolution    TEXT,
    UNIQUE (composite_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_listing_reports_open ON listing_reports(resolved_at, created_at);
