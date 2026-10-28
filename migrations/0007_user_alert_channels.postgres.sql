-- 0007_user_alert_channels.postgres.sql — see the .sql twin for rationale.

ALTER TABLE users ADD COLUMN IF NOT EXISTS ntfy_topic    TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS alert_webhook TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_phone  TEXT;

CREATE INDEX IF NOT EXISTS idx_saved_searches_notify
    ON saved_searches(notify_enabled);
