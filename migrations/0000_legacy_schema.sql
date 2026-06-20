-- 0000_legacy_schema.sql
-- Brings the legacy "personal tool" tables under the migration runner so a
-- freshly-provisioned DB (test, staging, new VPS) has the same shape a
-- pre-Phase-1 install built up through sniper.db()'s SCHEMA constant.
--
-- All CREATEs are IF NOT EXISTS — running this on the existing prod DB is
-- a complete no-op. The legacy SCHEMA in sniper.py is kept identical so
-- one or the other can bootstrap the schema without divergence.

CREATE TABLE IF NOT EXISTS listings (
    composite_id    TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    price           INTEGER,
    year            INTEGER,
    make            TEXT,
    model           TEXT,
    odometer        INTEGER,
    location        TEXT,
    state           TEXT,
    description     TEXT,
    posted_at       TEXT,
    first_seen_at   TEXT NOT NULL,
    is_dealer       INTEGER DEFAULT 0,
    is_salvage      INTEGER DEFAULT 0,
    is_auction      INTEGER DEFAULT 0,
    auction_end_at  TEXT,
    bid_count       INTEGER,
    buy_now_price   INTEGER,
    is_cash_only    INTEGER DEFAULT 0,
    accepts_financing INTEGER DEFAULT 0,
    scam_score      INTEGER DEFAULT 0,
    scam_reasons    TEXT,
    image_urls      TEXT,
    extras          TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    composite_id    TEXT NOT NULL REFERENCES listings(composite_id),
    comp_avg        REAL,
    comp_median     REAL,
    comp_n          INTEGER,
    discount_pct    REAL,
    score           REAL,
    note            TEXT,
    created_at      TEXT NOT NULL,
    basis           TEXT,
    UNIQUE(composite_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings(first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_source     ON listings(source);
CREATE INDEX IF NOT EXISTS idx_listings_auction    ON listings(is_auction, auction_end_at);
CREATE INDEX IF NOT EXISTS idx_listings_scam       ON listings(scam_score);
CREATE INDEX IF NOT EXISTS idx_listings_make_model ON listings(make, model);
CREATE INDEX IF NOT EXISTS idx_alerts_created      ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_score        ON alerts(score DESC);

-- The legacy `saved` table — replaced by `saved_v2` in migration 0001 for
-- multi-user. Kept as an empty placeholder so the FK chain holds on a fresh
-- bootstrap and prod's existing data is left untouched.
CREATE TABLE IF NOT EXISTS saved (
    composite_id TEXT PRIMARY KEY REFERENCES listings(composite_id),
    note         TEXT,
    saved_at     TEXT NOT NULL,
    status       TEXT DEFAULT 'saved',
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS near_misses (
    composite_id  TEXT PRIMARY KEY REFERENCES listings(composite_id),
    discount_pct  REAL,
    score         REAL,
    comp_avg      REAL,
    note          TEXT,
    refreshed_at  TEXT NOT NULL,
    comp_n        INTEGER,
    basis         TEXT
);
