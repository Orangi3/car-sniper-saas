-- 0016_invitations.sql
-- Invite-only pilot: one row per invitation, issued by the owner
-- (python -m invites issue). Only the SHA-256 of the token is stored; the
-- token itself exists only in the link the owner sends. A token works once
-- (used_at), until expires_at, and not after revoked_at.
CREATE TABLE IF NOT EXISTS invitations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash       TEXT NOT NULL UNIQUE,
    note             TEXT,
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    used_at          TEXT,
    used_by_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    revoked_at       TEXT
);
