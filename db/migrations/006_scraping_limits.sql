-- Migration 006: add scraping_limits_json to tenant_policies
-- Run: psql -h localhost -p 5432 -d aiworks -f db/migrations/006_scraping_limits.sql

ALTER TABLE tenant_policies
    ADD COLUMN IF NOT EXISTS scraping_limits_json JSONB NOT NULL DEFAULT '{
        "max_depth": 2,
        "max_links_per_page": 30,
        "max_total_links": 100,
        "allow_external_domains": false,
        "allow_subdomains": true,
        "allowed_domains": [],
        "blocked_domains": [],
        "max_concurrent_requests": 3,
        "request_delay_ms": 500
    }'::jsonb;

COMMENT ON COLUMN tenant_policies.scraping_limits_json IS
    'Per-tenant crawl quotas enforced by the Scraper MCP server on every crawl request.';
