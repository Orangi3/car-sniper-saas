-- 0012_evaluation.sql
-- Real-world evaluation: one row per listing we judge (alerted or not).
-- Timeline fields are filled from system records; outcome fields only by a
-- human (never inferred). is_fixture marks demo/synthetic rows, which reports
-- exclude unless explicitly requested.
CREATE TABLE IF NOT EXISTS eval_observations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source             TEXT NOT NULL,
    source_listing_id  TEXT NOT NULL,
    composite_id       TEXT,
    posted_at          TEXT,
    posted_at_kind     TEXT NOT NULL DEFAULT 'unknown',  -- seller_posted|provider_first_seen|unknown
    first_seen_at      TEXT,
    detected_at        TEXT,
    alert_enqueued_at  TEXT,
    notified_at        TEXT,
    asking_price       INTEGER,
    est_value          REAL,
    value_low          REAL,
    value_high         REAL,
    est_profit         REAL,
    evidence_class     TEXT,
    confidence         TEXT,
    alerted            INTEGER NOT NULL DEFAULT 0,       -- system flagged as opportunity
    in_reference_set   INTEGER NOT NULL DEFAULT 0,       -- part of a complete reviewed sample
    buyer_action       TEXT,                             -- none|viewed|contacted|negotiated|purchased|passed
    seller_contacted   INTEGER NOT NULL DEFAULT 0,
    sold_confirmed     INTEGER NOT NULL DEFAULT 0,
    sale_amount        INTEGER,
    acquisition_costs  INTEGER,
    realized_profit    INTEGER,
    outcome_label      TEXT,
    labeled_by         TEXT,
    notes              TEXT,
    is_fixture         INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE(source, source_listing_id)
);
CREATE INDEX IF NOT EXISTS idx_eval_label ON eval_observations(outcome_label);
