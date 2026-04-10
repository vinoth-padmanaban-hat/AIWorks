-- Link ingestion runs to a control-plane persona id (opaque UUID in tenant DB).
-- Apply to each tenant database:
--   psql $TENANT_DB_URL -f db/migrations/tenant/003_ingestion_persona.sql

ALTER TABLE ingestion_executions
    ADD COLUMN IF NOT EXISTS persona_id UUID;

COMMENT ON COLUMN ingestion_executions.persona_id IS
    'References personas.persona_id in the control plane DB (no cross-DB FK).';
