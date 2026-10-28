-- 0011_listing_location.sql
-- Real ZIP/radius matching. Coordinates come ONLY from the provider's
-- structured fields or from a ZIP-centroid lookup of a provider-supplied ZIP
-- (never parsed from free text). location_source records which.
ALTER TABLE listings ADD COLUMN zip             TEXT;
ALTER TABLE listings ADD COLUMN latitude        REAL;
ALTER TABLE listings ADD COLUMN longitude       REAL;
ALTER TABLE listings ADD COLUMN location_source TEXT;
CREATE INDEX IF NOT EXISTS idx_listings_latlon ON listings(latitude, longitude);
-- Each user's buying territory (home ZIP + radius) used by matching/feeds.
ALTER TABLE users ADD COLUMN home_zip   TEXT;
ALTER TABLE users ADD COLUMN radius_mi  INTEGER;
