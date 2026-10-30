-- 0016_invitations.postgres.sql — see the .sql twin.
CREATE TABLE IF NOT EXISTS invitations (
    id               BIGSERIAL PRIMARY KEY,
    token_hash       TEXT NOT NULL UNIQUE,
    note             TEXT,
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    used_at          TEXT,
    used_by_user_id  BIGINT REFERENCES users(id) ON DELETE SET NULL,
    revoked_at       TEXT
);
