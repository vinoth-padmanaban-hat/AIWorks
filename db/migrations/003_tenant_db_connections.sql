-- Migration 003: Add tenant_db_connections table to the CONTROL PLANE DB.
-- This enables the per-tenant database split: one Postgres DB per tenant.
--
-- Run on the control plane DB only:
--   psql $CONTROL_PLANE_DATABASE_URL -f db/migrations/003_tenant_db_connections.sql

-- ═══════════════════════════════════════════════════════════════
-- CONTROL PLANE: Tenant DB connection registry
-- ═══════════════════════════════════════════════════════════════

-- Store the full async connection URL for each tenant's dedicated DB.
-- In production, replace db_url with a secret reference resolved at runtime.
CREATE TABLE IF NOT EXISTS tenant_db_connections (
    tenant_id   UUID        PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    db_url      TEXT        NOT NULL,        -- postgresql+asyncpg://user:pass@host:port/dbname
    db_schema   TEXT        NOT NULL DEFAULT 'public',
    region      TEXT        NOT NULL DEFAULT 'local',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Drop data-plane tables from the control plane DB.
-- These now live exclusively in each tenant's own DB.
-- Only drop if they exist — safe to run multiple times.
DROP TABLE IF EXISTS ingestion_log_entries   CASCADE;
DROP TABLE IF EXISTS ingestion_executions    CASCADE;
DROP TABLE IF EXISTS formatted_articles      CASCADE;
DROP TABLE IF EXISTS article_tags            CASCADE;
DROP TABLE IF EXISTS articles                CASCADE;
DROP TABLE IF EXISTS tenant_article_format_templates CASCADE;
DROP TABLE IF EXISTS tenant_tags             CASCADE;
DROP TABLE IF EXISTS tenant_sources          CASCADE;
