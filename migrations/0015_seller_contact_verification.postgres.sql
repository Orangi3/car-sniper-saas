-- 0015_seller_contact_verification.postgres.sql — see the .sql twin.
CREATE TABLE IF NOT EXISTS seller_contacts (
    user_id      BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    verified_at  TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS contact_verifications (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_contact_verif_user ON contact_verifications(user_id, created_at);
ALTER TABLE seller_listings ADD COLUMN IF NOT EXISTS payload TEXT;
ALTER TABLE seller_listings ADD COLUMN IF NOT EXISTS last_confirmed_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS uq_seller_listings_live_vin
    ON seller_listings(vin) WHERE vin IS NOT NULL AND status IN ('pending', 'active');
