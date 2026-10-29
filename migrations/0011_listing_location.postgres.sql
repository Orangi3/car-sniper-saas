-- 0011_listing_location.postgres.sql — see the .sql twin.
-- Real ZIP/radius matching. Coordinates come ONLY from the provider's
-- structured fields or from a ZIP-centroid lookup of a provider-supplied ZIP
-- (never parsed from free text). location_source records which.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS zip             TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS latitude        DOUBLE PRECISION;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS longitude       DOUBLE PRECISION;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS location_source TEXT;
CREATE INDEX IF NOT EXISTS idx_listings_latlon ON listings(latitude, longitude);
-- Each user's buying territory (home ZIP + radius) used by matching/feeds.
ALTER TABLE users ADD COLUMN IF NOT EXISTS home_zip   TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS radius_mi  INTEGER;
