-- 0018_hashed_session_tokens.postgres.sql
-- sessions.token now holds the SHA-256 (hex) of the cookie value, not the
-- value itself (auth.create_session), so a copy of the database can't be
-- replayed as anyone's session. Existing rows are converted in place:
-- nobody is signed out. A raw token (43 base64url characters) is never
-- 64 lowercase hex characters, so re-running this is a no-op.
UPDATE sessions
   SET token = encode(sha256(convert_to(token, 'UTF8')), 'hex')
 WHERE token !~ '^[0-9a-f]{64}$';
