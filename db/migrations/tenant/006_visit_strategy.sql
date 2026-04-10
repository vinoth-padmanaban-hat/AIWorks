-- Tenant DB migration 006: Per-source URL visit strategy.
-- Controls whether the BFS crawler re-visits URLs it has seen before.
-- Apply to EACH tenant database:
--   psql $TENANT_DB_URL -f db/migrations/tenant/006_visit_strategy.sql
--
-- Strategies:
--   skip_if_seen     — never re-visit a URL already stored in articles (default, news/blog)
--   revisit_if_changed — re-visit and re-process only when content hash differs (product pages)
--   always_revisit   — always fetch and re-process regardless of prior visits (live data feeds)
--   revisit_after_ttl — re-visit only when last_scraped_at is older than revisit_ttl_hours

ALTER TABLE tenant_sources
    ADD COLUMN IF NOT EXISTS visit_strategy TEXT NOT NULL DEFAULT 'skip_if_seen'
        CHECK (visit_strategy IN (
            'skip_if_seen',
            'revisit_if_changed',
            'always_revisit',
            'revisit_after_ttl'
        )),
    ADD COLUMN IF NOT EXISTS revisit_ttl_hours INT NOT NULL DEFAULT 24;

COMMENT ON COLUMN tenant_sources.visit_strategy IS
    'Controls cross-run URL revisit behaviour. '
    'skip_if_seen: skip URLs already in articles. '
    'revisit_if_changed: re-fetch but only process when content hash differs. '
    'always_revisit: always fetch and re-process. '
    'revisit_after_ttl: re-visit only after revisit_ttl_hours since last_scraped_at.';

COMMENT ON COLUMN tenant_sources.revisit_ttl_hours IS
    'Used by revisit_after_ttl strategy. Re-visit URL only when last_scraped_at '
    'is older than this many hours. Default 24 (daily refresh).';
