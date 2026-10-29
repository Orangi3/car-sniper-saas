-- 0012_evaluation.postgres.sql — see the .sql twin.
-- Real-world evaluation: one row per listing we judge (alerted or not).
-- Timeline fields are filled from system records; outcome fields only by a
-- human (never inferred). is_fixture marks demo/synthetic rows, which reports
-- exclude unless explicitly requested.
CREATE TABLE IF NOT EXISTS eval_observations (
    id                 BIGSERIAL PRIMARY KEY,
    source             TEXT NOT NULL,
    source_listing_id  TEXT NOT NULL,
    composite_id       TEXT,
    posted_at         TIMESTAMPTZ,
    posted_at_kind     TEXT NOT NULL DEFAULT 'unknown',  -- seller_posted|provider_first_seen|unknown
    first_seen_at     TIMESTAMPTZ,
    detected_at       TIMESTAMPTZ,
    alert_enqueued_at TIMESTAMPTZ,
    notified_at       TIMESTAMPTZ,
    asking_price       INTEGER,
    est_value          DOUBLE PRECISION,
    value_low          DOUBLE PRECISION,
    value_high         DOUBLE PRECISION,
    est_profit         DOUBLE PRECISION,
    evidence_class     TEXT,
    confidence         TEXT,
    alerted            BOOLEAN NOT NULL DEFAULT FALSE,       -- system flagged as opportunity
    in_reference_set   BOOLEAN NOT NULL DEFAULT FALSE,       -- part of a complete reviewed sample
    buyer_action       TEXT,                             -- none|viewed|contacted|negotiated|purchased|passed
    seller_contacted   BOOLEAN NOT NULL DEFAULT FALSE,
    sold_confirmed     BOOLEAN NOT NULL DEFAULT FALSE,
    sale_amount        INTEGER,
    acquisition_costs  INTEGER,
    realized_profit    INTEGER,
    outcome_label      TEXT,
    labeled_by         TEXT,
    notes              TEXT,
    is_fixture         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    UNIQUE(source, source_listing_id)
);
CREATE INDEX IF NOT EXISTS idx_eval_label ON eval_observations(outcome_label);
