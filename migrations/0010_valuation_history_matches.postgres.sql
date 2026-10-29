-- 0010_valuation_history_matches.postgres.sql — see the .sql twin.
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

ALTER TABLE listings ADD COLUMN IF NOT EXISTS last_seen_at     TIMESTAMPTZ;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS previous_price   INTEGER;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS price_changed_at TIMESTAMPTZ;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS vin              TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS vehicle_key      TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS relist_of        TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS trim             TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS needs_work       BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_listings_vehicle_key ON listings(vehicle_key);
CREATE INDEX IF NOT EXISTS idx_listings_ymm ON listings(make, model, year);

CREATE TABLE IF NOT EXISTS listing_prices (
    id           BIGSERIAL PRIMARY KEY,
    composite_id TEXT NOT NULL REFERENCES listings(composite_id),
    price        INTEGER NOT NULL,
    event        TEXT NOT NULL,
    observed_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listing_prices_cid ON listing_prices(composite_id, observed_at);

ALTER TABLE near_misses ADD COLUMN IF NOT EXISTS evidence_class     TEXT;
ALTER TABLE near_misses ADD COLUMN IF NOT EXISTS value_low          DOUBLE PRECISION;
ALTER TABLE near_misses ADD COLUMN IF NOT EXISTS value_high         DOUBLE PRECISION;
ALTER TABLE near_misses ADD COLUMN IF NOT EXISTS confidence         TEXT;
ALTER TABLE near_misses ADD COLUMN IF NOT EXISTS confidence_reasons TEXT;
ALTER TABLE near_misses ADD COLUMN IF NOT EXISTS evidence           TEXT;
ALTER TABLE near_misses ADD COLUMN IF NOT EXISTS trust              TEXT;
ALTER TABLE near_misses ADD COLUMN IF NOT EXISTS risk_flags         TEXT;
ALTER TABLE near_misses ADD COLUMN IF NOT EXISTS price_at_score     INTEGER;
CREATE INDEX IF NOT EXISTS idx_near_misses_trust ON near_misses(trust, score);

CREATE TABLE IF NOT EXISTS user_matches (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    saved_search_id BIGINT REFERENCES saved_searches(id) ON DELETE SET NULL,
    composite_id    TEXT NOT NULL REFERENCES listings(composite_id),
    event_key       TEXT NOT NULL,
    event           TEXT NOT NULL,
    price           INTEGER,
    previous_price  INTEGER,
    discount_pct    DOUBLE PRECISION,
    value_mid       DOUBLE PRECISION,
    profit_base     DOUBLE PRECISION,
    confidence      TEXT,
    trust           TEXT,
    notified        BOOLEAN NOT NULL DEFAULT FALSE,
    seen            BOOLEAN NOT NULL DEFAULT FALSE,
    dismissed       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL,
    UNIQUE(user_id, event_key)
);
CREATE INDEX IF NOT EXISTS idx_user_matches_user ON user_matches(user_id, created_at DESC);

ALTER TABLE users ADD COLUMN IF NOT EXISTS profit_settings TEXT;

CREATE TABLE IF NOT EXISTS usage_events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT,
    kind        TEXT NOT NULL,
    units       INTEGER NOT NULL DEFAULT 1,
    cost_cents  DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_events(user_id, kind, created_at);

UPDATE listings SET last_seen_at = first_seen_at WHERE last_seen_at IS NULL;
