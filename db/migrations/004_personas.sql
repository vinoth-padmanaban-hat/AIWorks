-- Persona Store (control plane). Each tenant can have N personas.
-- Run: psql $CONTROL_PLANE_DATABASE_URL -f db/migrations/004_personas.sql

CREATE TABLE IF NOT EXISTS personas (
    persona_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    display_name       TEXT        NOT NULL,
    slug               TEXT        NOT NULL DEFAULT '',
    role_description   TEXT        NOT NULL DEFAULT '',
    tone_style         TEXT        NOT NULL DEFAULT '',
    goals              JSONB       NOT NULL DEFAULT '[]'::jsonb,
    constraints        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    default_skills     TEXT[]      NOT NULL DEFAULT '{}',
    guardrail_profile  TEXT        NOT NULL DEFAULT '',
    active             BOOLEAN     NOT NULL DEFAULT TRUE,
    is_default         BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, slug)
);

-- At most one default persona per tenant (for ingestion when persona_id omitted).
CREATE UNIQUE INDEX IF NOT EXISTS idx_personas_one_default_per_tenant
    ON personas (tenant_id)
    WHERE is_default = TRUE;

CREATE INDEX IF NOT EXISTS idx_personas_tenant ON personas (tenant_id);
