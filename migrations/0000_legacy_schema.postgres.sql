-- 0000_legacy_schema.postgres.sql
-- Postgres variant of the legacy bootstrap.

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
    posted_at       TIMESTAMPTZ,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    is_dealer       BOOLEAN DEFAULT FALSE,
    is_salvage      BOOLEAN DEFAULT FALSE,
    is_auction      BOOLEAN DEFAULT FALSE,
    auction_end_at  TIMESTAMPTZ,
    bid_count       INTEGER,
    buy_now_price   INTEGER,
    is_cash_only    BOOLEAN DEFAULT FALSE,
    accepts_financing BOOLEAN DEFAULT FALSE,
    scam_score      INTEGER DEFAULT 0,
    scam_reasons    TEXT,
    image_urls      TEXT,
    extras          TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id              BIGSERIAL PRIMARY KEY,
    composite_id    TEXT NOT NULL REFERENCES listings(composite_id),
    comp_avg        DOUBLE PRECISION,
    comp_median     DOUBLE PRECISION,
    comp_n          INTEGER,
    discount_pct    DOUBLE PRECISION,
    score           DOUBLE PRECISION,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
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

CREATE TABLE IF NOT EXISTS saved (
    composite_id TEXT PRIMARY KEY REFERENCES listings(composite_id),
    note         TEXT,
    saved_at     TIMESTAMPTZ NOT NULL,
    status       TEXT DEFAULT 'saved',
    updated_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS near_misses (
    composite_id  TEXT PRIMARY KEY REFERENCES listings(composite_id),
    discount_pct  DOUBLE PRECISION,
    score         DOUBLE PRECISION,
    comp_avg      DOUBLE PRECISION,
    note          TEXT,
    refreshed_at  TIMESTAMPTZ NOT NULL,
    comp_n        INTEGER,
    basis         TEXT
);
