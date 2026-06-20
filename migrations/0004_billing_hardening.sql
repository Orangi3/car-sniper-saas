-- 0004_billing_hardening.sql
-- Phase 2B — subscription lifecycle hardening.
--
-- Adds:
--   * subscriptions.last_event_at   — newest event timestamp APPLIED to
--                                     this row. Out-of-order events that
--                                     arrive with an older `created` are
--                                     refused (and recorded as an anomaly).
--   * subscriptions.last_event_id   — Stripe event id of the last applied
--                                     event, purely audit trail.
--   * billing_anomalies             — append-only log of things that need
--                                     human review: unknown price IDs,
--                                     partial refunds we can't link to a
--                                     subscription, disputes against
--                                     unknown subs, out-of-order events
--                                     that were ignored, reconciliation
--                                     corrections, etc.
--   * billing_reconciliation_runs   — one row per nightly Stripe-backed
--                                     reconciliation pass. Powers the
--                                     admin /api/admin/billing/health view.

ALTER TABLE subscriptions ADD COLUMN last_event_at TEXT;
ALTER TABLE subscriptions ADD COLUMN last_event_id TEXT;

CREATE TABLE IF NOT EXISTS billing_anomalies (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    type                   TEXT NOT NULL,    -- 'unknown_price' | 'out_of_order_event' | 'partial_refund_unlinked' | 'dispute_unknown_sub' | 'reconciliation_correction' | 'subscription_orphan'
    severity               TEXT NOT NULL DEFAULT 'warn',  -- 'info' | 'warn' | 'critical'
    user_id                INTEGER REFERENCES users(id) ON DELETE SET NULL,
    stripe_event_id        TEXT,             -- not FK so cleanup of webhook events doesn't lose history
    stripe_customer_id     TEXT,
    stripe_subscription_id TEXT,
    details                TEXT NOT NULL DEFAULT '{}',    -- JSON, NEVER includes raw card/auth data
    resolved               INTEGER NOT NULL DEFAULT 0,
    resolved_at            TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_anomalies_unresolved
    ON billing_anomalies(resolved, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_anomalies_user
    ON billing_anomalies(user_id);

CREATE TABLE IF NOT EXISTS billing_reconciliation_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    checked             INTEGER NOT NULL DEFAULT 0,
    mismatches_found    INTEGER NOT NULL DEFAULT 0,
    corrections_applied INTEGER NOT NULL DEFAULT 0,
    errors              INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    triggered_by        TEXT NOT NULL DEFAULT 'cron'      -- 'cron' | 'admin'
);

CREATE INDEX IF NOT EXISTS idx_recon_started
    ON billing_reconciliation_runs(started_at DESC);
