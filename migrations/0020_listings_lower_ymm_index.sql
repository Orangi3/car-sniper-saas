-- 0020_listings_lower_ymm_index.sql
-- valuation.load_peers (Discover scoring and Analyze) filters on
-- LOWER(make), LOWER(model) and a year range, which the plain (make, model,
-- year) index can't serve. Measured on 50,000 SIMULATED listings: the query
-- went from 23 ms to 12 ms on SQLite and from 39 ms to 26 ms on PostgreSQL.
CREATE INDEX IF NOT EXISTS idx_listings_lower_ymm ON listings(LOWER(make), LOWER(model), year);
