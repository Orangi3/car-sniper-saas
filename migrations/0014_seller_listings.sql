-- 0014_seller_listings.sql
-- Seller-submitted listings: a vehicle owner lists their own car with
-- their consent and an attestation of authority. The vehicle itself lives
-- in `listings` (source = 'direct', source_id = this id) so the normal
-- valuation/matching/alert pipeline handles it; this table holds who
-- submitted it, what they attested, how buyers may contact them, and its
-- lifecycle. On removal the listing and everything derived from it are
-- deleted; this row keeps only an audit stub (no vehicle or contact data).
CREATE TABLE IF NOT EXISTS seller_listings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'active',     -- active | removed
    vin             TEXT,
    contact_email   TEXT,
    attested_at     TEXT NOT NULL,
    attestation     TEXT NOT NULL,                      -- JSON: what was affirmed
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    removed_at      TEXT,
    removed_by      TEXT                                -- 'seller' | 'admin'
);
CREATE INDEX IF NOT EXISTS idx_seller_listings_user ON seller_listings(user_id, status);
CREATE INDEX IF NOT EXISTS idx_seller_listings_vin  ON seller_listings(vin, status);
