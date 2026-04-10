-- Tenant DB migration 002: per-source crawl + article eligibility (schema-first).
-- Apply to EACH existing tenant database after 001:
--   psql $TENANT_DB_URL -f db/migrations/tenant/002_tenant_sources_article_rules.sql

ALTER TABLE tenant_sources
    ADD COLUMN IF NOT EXISTS max_child_links_per_page INT NOT NULL DEFAULT 4;

ALTER TABLE tenant_sources
    ADD COLUMN IF NOT EXISTS max_links_to_scrape INT NOT NULL DEFAULT 25;

ALTER TABLE tenant_sources
    ADD COLUMN IF NOT EXISTS exclude_patterns TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE tenant_sources
    ADD COLUMN IF NOT EXISTS min_text_chars INT NOT NULL DEFAULT 40;

ALTER TABLE tenant_sources
    ADD COLUMN IF NOT EXISTS require_title BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN tenant_sources.max_child_links_per_page IS
    'Max outgoing links to enqueue from each fetched URL (per-page cap).';
COMMENT ON COLUMN tenant_sources.max_links_to_scrape IS
    'Max distinct URLs to visit for this source root (total crawl cap).';
COMMENT ON COLUMN tenant_sources.exclude_patterns IS
    'URL substrings — do not follow links containing any of these.';
COMMENT ON COLUMN tenant_sources.min_text_chars IS
    'Minimum extracted body length to insert an article row.';
COMMENT ON COLUMN tenant_sources.require_title IS
    'If true, skip article insert when title is empty after extraction.';
