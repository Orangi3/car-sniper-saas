-- 0010_valuation_history_matches.sql
-- Phase 1-4 data model.
--
-- listings: lifecycle + identity. Listings are now UPSERTED on every poll
--   (previously only unseen ids were inserted, so price changes vanished).
--     last_seen_at, previous_price, price_changed_at   price tracking
--     vin          VIN found in the listing text, check digit verified
--     vehicle_key  'vin:<VIN>' when a reliable VIN exists (cross-source id)
--     relist_of    composite_id of an earlier listing of the same vehicle
--     trim         trim token parsed from the title, when recognised
--     needs_work   runs/drives problems (was only in extras JSON)
-- listing_prices: inspectable price history.
-- near_misses (the per-listing valuation record) gains explicit evidence:
--     evidence_class   confirmed_sales | asking_comparables |
--                      historical_incomplete | model_estimate | insufficient
--     value_low/high, confidence (+ reasons), evidence JSON (comps used),
--     trust (trusted | risk), risk_flags, price_at_score
-- user_matches: personalized opportunities per subscriber (replaces routing
--     subscribers off the owner's global alerts). UNIQUE(user_id, event_key)
--     dedupes repeat notifications; a price drop is a new event_key.
-- users.profit_settings: per-user profit assumptions (JSON).
-- usage_events: per-account metering of billable work (SMS etc.).

ALTER TABLE listings ADD COLUMN last_seen_at     TEXT;
ALTER TABLE listings ADD COLUMN previous_price   INTEGER;
ALTER TABLE listings ADD COLUMN price_changed_at TEXT;
ALTER TABLE listings ADD COLUMN vin              TEXT;
ALTER TABLE listings ADD COLUMN vehicle_key      TEXT;
ALTER TABLE listings ADD COLUMN relist_of        TEXT;
ALTER TABLE listings ADD COLUMN trim             TEXT;
ALTER TABLE listings ADD COLUMN needs_work       INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_listings_vehicle_key ON listings(vehicle_key);
CREATE INDEX IF NOT EXISTS idx_listings_ymm ON listings(make, model, year);

CREATE TABLE IF NOT EXISTS listing_prices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    composite_id TEXT NOT NULL REFERENCES listings(composite_id),
    price        INTEGER NOT NULL,
    event        TEXT NOT NULL,
    observed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listing_prices_cid ON listing_prices(composite_id, observed_at);

ALTER TABLE near_misses ADD COLUMN evidence_class     TEXT;
ALTER TABLE near_misses ADD COLUMN value_low          REAL;
ALTER TABLE near_misses ADD COLUMN value_high         REAL;
ALTER TABLE near_misses ADD COLUMN confidence         TEXT;
ALTER TABLE near_misses ADD COLUMN confidence_reasons TEXT;
ALTER TABLE near_misses ADD COLUMN evidence           TEXT;
ALTER TABLE near_misses ADD COLUMN trust              TEXT;
ALTER TABLE near_misses ADD COLUMN risk_flags         TEXT;
ALTER TABLE near_misses ADD COLUMN price_at_score     INTEGER;
CREATE INDEX IF NOT EXISTS idx_near_misses_trust ON near_misses(trust, score);

CREATE TABLE IF NOT EXISTS user_matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    saved_search_id INTEGER REFERENCES saved_searches(id) ON DELETE SET NULL,
    composite_id    TEXT NOT NULL REFERENCES listings(composite_id),
    event_key       TEXT NOT NULL,
    event           TEXT NOT NULL,
    price           INTEGER,
    previous_price  INTEGER,
    discount_pct    REAL,
    value_mid       REAL,
    profit_base     REAL,
    confidence      TEXT,
    trust           TEXT,
    notified        INTEGER NOT NULL DEFAULT 0,
    seen            INTEGER NOT NULL DEFAULT 0,
    dismissed       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    UNIQUE(user_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_user_matches_user ON user_matches(user_id, created_at DESC);

ALTER TABLE users ADD COLUMN profit_settings TEXT;

CREATE TABLE IF NOT EXISTS usage_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    kind        TEXT NOT NULL,
    units       INTEGER NOT NULL DEFAULT 1,
    cost_cents  REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_events(user_id, kind, created_at);

UPDATE listings SET last_seen_at = first_seen_at WHERE last_seen_at IS NULL;
