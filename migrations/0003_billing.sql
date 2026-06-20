-- 0003_billing.sql
-- Stripe-driven subscription state (Phase 2A, TEST MODE).
--
-- Two tables:
--   * subscriptions          — local mirror of each user's current
--                              Stripe subscription. Source of truth for
--                              entitlement.refresh_for() / users.plan.
--   * stripe_webhook_events  — append-only event log. The
--                              stripe_event_id PK is the idempotency key:
--                              a duplicate INSERT raises a unique-violation
--                              and we know we've already processed it.
--
-- Status values mirror Stripe's lifecycle vocabulary:
--   incomplete, incomplete_expired, trialing, active, past_due,
--   canceled, unpaid, paused
-- We don't constrain them here so we can ingest new Stripe additions
-- without a migration; entitlement.refresh_for treats only
-- {'active','trialing'} as entitling.

CREATE TABLE IF NOT EXISTS subscriptions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stripe_customer_id       TEXT NOT NULL,
    stripe_subscription_id   TEXT NOT NULL UNIQUE,
    stripe_price_id          TEXT NOT NULL,
    plan                     TEXT NOT NULL,
    status                   TEXT NOT NULL,
    current_period_end       TEXT,
    cancel_at_period_end     INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user        ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_customer    ON subscriptions(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_status ON subscriptions(user_id, status);

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    stripe_event_id  TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    payload          TEXT NOT NULL,
    received_at      TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_stripe_events_received ON stripe_webhook_events(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_stripe_events_type     ON stripe_webhook_events(type);
