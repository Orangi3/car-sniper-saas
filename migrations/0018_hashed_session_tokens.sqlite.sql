-- 0018_hashed_session_tokens.sqlite.sql
-- sessions.token now holds the SHA-256 (hex) of the cookie value, not the
-- value itself (auth.create_session). SQLite has no SHA-256 function, so
-- sessions stored the old way are removed: on a SQLite (local/demo)
-- database everyone signs in again once. PostgreSQL converts in place.
DELETE FROM sessions
 WHERE length(token) <> 64 OR token GLOB '*[^0-9a-f]*';
