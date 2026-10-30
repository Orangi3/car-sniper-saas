-- 0020_listings_lower_ymm_index.postgres.sql — see the .sql twin.
CREATE INDEX IF NOT EXISTS idx_listings_lower_ymm ON listings(LOWER(make), LOWER(model), year);
