-- Fix: tenants 2–4 use defaultAllow=false with capabilities.allowed listing planner
-- capability names only. SkillRegistry.filter_by_policy matches registry skill_id values
-- (content_ingestion, content_curation), so the allowed list must include those IDs.
--
-- Run against the control-plane DB, e.g.:
--   psql "$DATABASE_URL" -f db/migrations/007_policy_allowed_skill_ids.sql

UPDATE tenant_policies
SET policy_json = jsonb_set(
    policy_json,
    '{capabilities,allowed}',
    COALESCE(policy_json->'capabilities'->'allowed', '[]'::jsonb)
        || '["content_ingestion", "content_curation"]'::jsonb,
    true
),
    updated_at = now()
WHERE tenant_id IN (
    '00000000-0000-0000-0000-000000000002'::uuid,
    '00000000-0000-0000-0000-000000000003'::uuid,
    '00000000-0000-0000-0000-000000000004'::uuid
);
