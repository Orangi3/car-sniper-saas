-- 0004_billing_hardening.postgres.sql

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_event_at TIMESTAMPTZ;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_event_id TEXT;

CREATE TABLE IF NOT EXISTS billing_anomalies (
    id                     BIGSERIAL PRIMARY KEY,
    type                   TEXT NOT NULL,
    severity               TEXT NOT NULL DEFAULT 'warn',
    user_id                BIGINT REFERENCES users(id) ON DELETE SET NULL,
    stripe_event_id        TEXT,
    stripe_customer_id     TEXT,
    stripe_subscription_id TEXT,
    details                TEXT NOT NULL DEFAULT '{}',
    resolved               BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_anomalies_unresolved
    ON billing_anomalies(resolved, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_anomalies_user
    ON billing_anomalies(user_id);

CREATE TABLE IF NOT EXISTS billing_reconciliation_runs (
    id                  BIGSERIAL PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ,
    checked             INTEGER NOT NULL DEFAULT 0,
    mismatches_found    INTEGER NOT NULL DEFAULT 0,
    corrections_applied INTEGER NOT NULL DEFAULT 0,
    errors              INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    triggered_by        TEXT NOT NULL DEFAULT 'cron'
);

CREATE INDEX IF NOT EXISTS idx_recon_started
    ON billing_reconciliation_runs(started_at DESC);
