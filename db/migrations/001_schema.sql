-- Full schema for AIWorks PoC
-- Run once: psql $DATABASE_URL -f db/migrations/001_schema.sql

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ═══════════════════════════════════════════════════════════════
-- CONTROL PLANE: Skill Registry
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS skill_registry (
    skill_id      TEXT        PRIMARY KEY,
    name          TEXT        NOT NULL,
    description   TEXT        NOT NULL DEFAULT '',
    domain        TEXT        NOT NULL DEFAULT '',
    tags          TEXT[]      NOT NULL DEFAULT '{}',
    input_schema  JSONB       NOT NULL DEFAULT '{}',
    output_schema JSONB       NOT NULL DEFAULT '{}',
    active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════
-- CONTROL PLANE: Agent Registry
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS agent_registry (
    agent_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name  TEXT        NOT NULL,
    description   TEXT        NOT NULL DEFAULT '',
    version       TEXT        NOT NULL DEFAULT '1.0.0',
    -- Endpoint the Execution Engine calls: POST {endpoint}/invoke
    endpoint      TEXT        NOT NULL,
    protocol      TEXT        NOT NULL DEFAULT 'http_json',
    health_status TEXT        NOT NULL DEFAULT 'OK'
                              CHECK (health_status IN ('OK', 'DEGRADED', 'OFFLINE')),
    active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_supported_skills (
    agent_id      UUID    NOT NULL REFERENCES agent_registry(agent_id) ON DELETE CASCADE,
    skill_id      TEXT    NOT NULL REFERENCES skill_registry(skill_id) ON DELETE CASCADE,
    quality_score NUMERIC NOT NULL DEFAULT 1.0,
    cost_profile  TEXT    NOT NULL DEFAULT 'standard',
    PRIMARY KEY (agent_id, skill_id)
);

-- ═══════════════════════════════════════════════════════════════
-- DATA PLANE: Tenant config (sources, tags, templates)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS tenant_sources (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID        NOT NULL,
    url               TEXT        NOT NULL,
    type              TEXT        NOT NULL CHECK (type IN ('rss', 'html')),
    active            BOOLEAN     NOT NULL DEFAULT TRUE,
    last_scraped_at   TIMESTAMPTZ,
    last_etag         TEXT,
    last_content_hash TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, url)
);

CREATE TABLE IF NOT EXISTS tenant_tags (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID        NOT NULL,
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS tenant_article_format_templates (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID        NOT NULL,
    name          TEXT        NOT NULL,
    template_json JSONB       NOT NULL,
    is_default    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════
-- DATA PLANE: Articles
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS articles (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID        NOT NULL,
    source_id     UUID        NOT NULL REFERENCES tenant_sources(id),
    url           TEXT        NOT NULL,
    canonical_url TEXT,
    title         TEXT        NOT NULL DEFAULT '',
    author        TEXT,
    published_at  TIMESTAMPTZ,
    text          TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, url)
);

CREATE TABLE IF NOT EXISTS article_tags (
    article_id UUID    NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag_id     UUID    NOT NULL REFERENCES tenant_tags(id) ON DELETE CASCADE,
    confidence NUMERIC NOT NULL DEFAULT 1.0,
    PRIMARY KEY (article_id, tag_id)
);

CREATE TABLE IF NOT EXISTS formatted_articles (
    article_id         UUID        PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    tenant_id          UUID        NOT NULL,
    format_template_id UUID        NOT NULL REFERENCES tenant_article_format_templates(id),
    formatted_json     JSONB       NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ═══════════════════════════════════════════════════════════════
-- DATA PLANE: Ingestion execution log
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ingestion_executions (
    execution_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID        NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT        NOT NULL DEFAULT 'RUNNING'
                             CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'ERROR')),
    summary_json JSONB
);

CREATE TABLE IF NOT EXISTS ingestion_log_entries (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id UUID        NOT NULL REFERENCES ingestion_executions(execution_id),
    tenant_id    UUID        NOT NULL,
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

-- Useful indexes
CREATE INDEX IF NOT EXISTS idx_articles_tenant_source ON articles(tenant_id, source_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_execution ON ingestion_log_entries(execution_id);
CREATE INDEX IF NOT EXISTS idx_agent_supported_skills_skill ON agent_supported_skills(skill_id);
