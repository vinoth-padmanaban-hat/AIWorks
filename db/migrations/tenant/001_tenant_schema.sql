-- Tenant database schema (v1).
-- Applied to EACH tenant's dedicated Postgres database — NOT the control plane DB.
-- Tables have NO tenant_id columns; isolation is at the database boundary.
--
-- Apply to a tenant DB:
--   psql <tenant_db_url> -f db/migrations/tenant/001_tenant_schema.sql
--
-- For the PoC, the seed script handles this automatically.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ═══════════════════════════════════════════════════════════════
-- Ingestion configuration: sources, tags, templates
-- ═══════════════════════════════════════════════════════════════

-- Web sources to scrape for this tenant.
-- max_depth        : how many link-levels to follow from the root URL.
-- same_domain_only : when following links, restrict to the root URL's domain.
-- include_patterns : optional URL substrings — only follow links matching at least one.
CREATE TABLE IF NOT EXISTS tenant_sources (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    url               TEXT        NOT NULL UNIQUE,
    type              TEXT        NOT NULL CHECK (type IN ('rss', 'html')),
    active            BOOLEAN     NOT NULL DEFAULT TRUE,
    last_scraped_at   TIMESTAMPTZ,
    last_etag         TEXT,
    last_content_hash TEXT,
    max_depth         INT         NOT NULL DEFAULT 2,
    same_domain_only  BOOLEAN     NOT NULL DEFAULT TRUE,
    include_patterns  TEXT[]      NOT NULL DEFAULT '{}',
    max_child_links_per_page INT NOT NULL DEFAULT 4,
    max_links_to_scrape INT      NOT NULL DEFAULT 25,
    exclude_patterns  TEXT[]    NOT NULL DEFAULT '{}',
    min_text_chars    INT        NOT NULL DEFAULT 40,
    require_title     BOOLEAN    NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tag taxonomy for this tenant.
CREATE TABLE IF NOT EXISTS tenant_tags (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL UNIQUE,
    description TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Article format templates — defines how normalized articles are shaped for output.
-- template_json describes field mappings; formatted_articles.formatted_json
-- is the result of applying the template to a normalized article.
CREATE TABLE IF NOT EXISTS tenant_article_format_templates (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT        NOT NULL,
    template_json JSONB       NOT NULL,
    is_default    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════
-- Content: normalized articles and tenant-specific formatted view
-- ═══════════════════════════════════════════════════════════════

-- Canonical normalized articles — implementation-agnostic.
-- img_url and summary are new fields extracted/generated during ingestion.
-- The UNIQUE constraint on url prevents duplicate ingestion.
CREATE TABLE IF NOT EXISTS articles (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id     UUID        NOT NULL REFERENCES tenant_sources(id) ON DELETE CASCADE,
    url           TEXT        NOT NULL UNIQUE,
    canonical_url TEXT,
    title         TEXT        NOT NULL DEFAULT '',
    author        TEXT,
    published_at  TIMESTAMPTZ,
    img_url       TEXT,           -- featured/hero image URL extracted from the page
    summary       TEXT,           -- short auto-generated or extracted summary
    text          TEXT            NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- Article ↔ tag join with confidence scores.
CREATE TABLE IF NOT EXISTS article_tags (
    article_id UUID    NOT NULL REFERENCES articles(id)     ON DELETE CASCADE,
    tag_id     UUID    NOT NULL REFERENCES tenant_tags(id)  ON DELETE CASCADE,
    confidence NUMERIC NOT NULL DEFAULT 1.0,
    PRIMARY KEY (article_id, tag_id)
);

-- Tenant-specific formatted view of an article.
-- formatted_json is the result of applying tenant_article_format_templates.template_json
-- to the normalized article. Each tenant defines their own output schema here.
CREATE TABLE IF NOT EXISTS formatted_articles (
    article_id         UUID        PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    format_template_id UUID        NOT NULL REFERENCES tenant_article_format_templates(id),
    formatted_json     JSONB       NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════
-- Ingestion execution tracking and cost logs
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ingestion_executions (
    execution_id UUID        PRIMARY KEY,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT        NOT NULL DEFAULT 'RUNNING'
                             CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'ERROR')),
    summary_json JSONB
);

CREATE TABLE IF NOT EXISTS ingestion_log_entries (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id UUID        NOT NULL REFERENCES ingestion_executions(execution_id) ON DELETE CASCADE,
    source_id    UUID,
    article_id   UUID,
    step_name    TEXT        NOT NULL,
    status       TEXT        NOT NULL CHECK (status IN ('SUCCESS', 'NO_CHANGE', 'ERROR')),
    details_json JSONB,
    tokens_in    INT         NOT NULL DEFAULT 0,
    tokens_out   INT         NOT NULL DEFAULT 0,
    cost_usd     NUMERIC     NOT NULL DEFAULT 0,
    duration_ms  INT         NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════
-- Indexes
-- ═══════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_articles_source      ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_url         ON articles(url);
CREATE INDEX IF NOT EXISTS idx_log_execution        ON ingestion_log_entries(execution_id);
CREATE INDEX IF NOT EXISTS idx_sources_active       ON tenant_sources(active);
CREATE INDEX IF NOT EXISTS idx_sources_last_scraped ON tenant_sources(last_scraped_at);
