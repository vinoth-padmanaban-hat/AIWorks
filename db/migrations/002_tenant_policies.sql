-- Tenant policy table
-- Run: psql -h localhost -p 5432 -d aiworks -f db/migrations/002_tenant_policies.sql

CREATE TABLE IF NOT EXISTS tenant_policies (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID        UNIQUE NOT NULL,
    -- Policy display name for debugging
    name        TEXT        NOT NULL DEFAULT '',
    -- Full policy blob — see AGENTIC_ARCHITECTURE.md §8.3 for schema
    policy_json JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Also add a display_name column to tenant_sources parent concept.
-- We track tenants loosely via their UUIDs; add a lookup table for display names.
CREATE TABLE IF NOT EXISTS tenants (
    id           UUID        PRIMARY KEY,
    display_name TEXT        NOT NULL,
    domain       TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
