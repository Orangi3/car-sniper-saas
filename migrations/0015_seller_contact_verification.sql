-- 0015_seller_contact_verification.sql
-- A seller's contact email must be verified before any listing is published
-- or the address is shown to buyers. Tokens are stored only as SHA-256
-- hashes, are single-use and expire. Listings submitted before verification
-- wait in status 'pending' with their validated fields in `payload`;
-- verification publishes them. Active listings must be re-confirmed
-- (last_confirmed_at) to stay discoverable.
CREATE TABLE IF NOT EXISTS seller_contacts (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    verified_at  TEXT,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contact_verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_contact_verif_user ON contact_verifications(user_id, created_at);
ALTER TABLE seller_listings ADD COLUMN payload TEXT;
ALTER TABLE seller_listings ADD COLUMN last_confirmed_at TEXT;
-- one live listing per VIN, enforced by the database (no check-then-insert race)
CREATE UNIQUE INDEX IF NOT EXISTS uq_seller_listings_live_vin
    ON seller_listings(vin) WHERE vin IS NOT NULL AND status IN ('pending', 'active');
