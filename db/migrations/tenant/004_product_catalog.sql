-- Tenant DB migration 004: Product/Service catalog for content-product matching.
-- Apply to EACH tenant database:
--   psql $TENANT_DB_URL -f db/migrations/tenant/004_product_catalog.sql

CREATE TABLE IF NOT EXISTS tenant_products (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    url         TEXT,
    category    TEXT        NOT NULL DEFAULT '',
    tags        TEXT[]      NOT NULL DEFAULT '{}',
    features    TEXT[]      NOT NULL DEFAULT '{}',
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_products_category ON tenant_products(category);
CREATE INDEX IF NOT EXISTS idx_products_active   ON tenant_products(active);

COMMENT ON TABLE tenant_products IS
    'Products/services this tenant offers. Used by content curation agents '
    'to match scraped articles with relevant product references.';
