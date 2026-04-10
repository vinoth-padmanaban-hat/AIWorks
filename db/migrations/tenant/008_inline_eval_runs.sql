-- Tenant DB migration 008: Inline (online) LLM-judge evaluation runs per execution step.
-- Apply to EACH tenant database:
--   psql $TENANT_DB_URL -f db/migrations/tenant/008_inline_eval_runs.sql

CREATE TABLE IF NOT EXISTS inline_eval_runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id     UUID        NOT NULL,
    step_id          UUID        NOT NULL,
    skill_id         TEXT        NOT NULL,
    attempt_index    INT         NOT NULL,
    passed           BOOLEAN     NOT NULL,
    score            DOUBLE PRECISION,
    threshold        DOUBLE PRECISION,
    metric_name      TEXT        NOT NULL DEFAULT '',
    reason           TEXT,
    judge_model      TEXT,
    output_snippet   TEXT,
    details_json     JSONB       NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inline_eval_execution
    ON inline_eval_runs (execution_id);
CREATE INDEX IF NOT EXISTS idx_inline_eval_step
    ON inline_eval_runs (execution_id, step_id);
