-- Tenant DB migration 005: Generic execution tracking (not ingestion-specific).
-- Apply to EACH tenant database:
--   psql $TENANT_DB_URL -f db/migrations/tenant/005_executions.sql

CREATE TABLE IF NOT EXISTS executions (
    execution_id UUID        PRIMARY KEY,
    skill_id     TEXT        NOT NULL,
    persona_id   UUID,
    goal         TEXT        NOT NULL DEFAULT '',
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       TEXT        NOT NULL DEFAULT 'RUNNING'
                             CHECK (status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'ERROR')),
    plan_json    JSONB,
    result_json  JSONB,
    cost_json    JSONB
);

CREATE TABLE IF NOT EXISTS execution_steps (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id UUID        NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    step_id      UUID        NOT NULL,
    skill_id     TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'PENDING'
                             CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'ERROR')),
    input_json   JSONB,
    output_json  JSONB,
    cost_json    JSONB,
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Newsletter / curated articles output (linked to articles + products)
CREATE TABLE IF NOT EXISTS newsletter_articles (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id     UUID        NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    article_id       UUID        REFERENCES articles(id) ON DELETE SET NULL,
    title            TEXT        NOT NULL,
    summary          TEXT        NOT NULL DEFAULT '',
    body             TEXT        NOT NULL DEFAULT '',
    product_refs     JSONB       NOT NULL DEFAULT '[]',
    tags             TEXT[]      NOT NULL DEFAULT '{}',
    source_url       TEXT,
    status           TEXT        NOT NULL DEFAULT 'draft'
                                 CHECK (status IN ('draft', 'approved', 'published', 'rejected')),
    reviewed_by      TEXT,
    reviewed_at      TIMESTAMPTZ,
    published_at     TIMESTAMPTZ,
    publish_channel  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_executions_status     ON executions(status);
CREATE INDEX IF NOT EXISTS idx_exec_steps_execution  ON execution_steps(execution_id);
CREATE INDEX IF NOT EXISTS idx_newsletter_execution  ON newsletter_articles(execution_id);
CREATE INDEX IF NOT EXISTS idx_newsletter_status     ON newsletter_articles(status);
