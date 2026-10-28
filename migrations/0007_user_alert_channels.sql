-- 0007_user_alert_channels.sql
-- Per-user delivery targets for saved-search alerts. Each user picks where
-- their matches go; the owner-level channels in notifications.py stay as-is.
--   ntfy_topic     free instant phone push (ntfy app, subscribe to topic)
--   alert_webhook  JSON POST target (Discord/Slack/Zapier/n8n)
--   notify_phone   SMS via Twilio when the server has Twilio configured

ALTER TABLE users ADD COLUMN ntfy_topic    TEXT;
ALTER TABLE users ADD COLUMN alert_webhook TEXT;
ALTER TABLE users ADD COLUMN notify_phone  TEXT;

CREATE INDEX IF NOT EXISTS idx_saved_searches_notify
    ON saved_searches(notify_enabled);
