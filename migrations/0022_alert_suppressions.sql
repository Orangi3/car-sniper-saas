-- 0022_alert_suppressions.sql — one row per subscriber alert that was NOT
-- sent because that subscriber had already been alerted to the same vehicle
-- through another listing (saved_search_alerts: same VIN, or the
-- conservative no-VIN identity in vehicle_identity.py). Audit record only:
-- listings, matches and relist history are untouched, and no decision reads
-- this table. Rows go with the user (ON DELETE CASCADE, accounts.py).
CREATE TABLE IF NOT EXISTS alert_suppressions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    composite_id  TEXT NOT NULL,          -- the listing not alerted
    duplicate_of  TEXT NOT NULL,          -- the listing already alerted
    reason        TEXT NOT NULL,          -- same_vin | same_vehicle_no_vin
    evidence      TEXT,                   -- JSON: the attributes that matched
    created_at    TEXT NOT NULL,
    UNIQUE (user_id, composite_id)
);
CREATE INDEX IF NOT EXISTS idx_alert_suppressions_created ON alert_suppressions(created_at);
