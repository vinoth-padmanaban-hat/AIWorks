#!/bin/sh
# Apply control-plane and per-tenant Postgres migrations (tracked; safe to re-run).
# Mount repo db/migrations at /migrations. Env: PGHOST, PGUSER, PGPASSWORD, PGPORT.

set -eu

PGPORT="${PGPORT:-5432}"
export PGPASSWORD="${PGPASSWORD:?PGPASSWORD required}"

wait_for_postgres() {
  i=0
  while [ "$i" -lt 60 ]; do
    if psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -c '\q' 2>/dev/null; then
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "init-db: Postgres at $PGHOST:$PGPORT not reachable" >&2
  exit 1
}

ensure_log_table() {
  db="$1"
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$db" -v ON_ERROR_STOP=1 -c \
    "CREATE TABLE IF NOT EXISTS _docker_init_migrations (
      name text PRIMARY KEY,
      applied_at timestamptz NOT NULL DEFAULT now()
    );"
}

migration_applied() {
  db="$1"
  name="$2"
  r="$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$db" -tAc \
    "SELECT COUNT(*) FROM _docker_init_migrations WHERE name = '$name';")"
  [ "$r" = "1" ]
}

record_migration() {
  db="$1"
  name="$2"
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$db" -v ON_ERROR_STOP=1 -c \
    "INSERT INTO _docker_init_migrations (name) VALUES ('$name');"
}

apply_one() {
  db="$1"
  name="$2"
  file="$3"
  if migration_applied "$db" "$name"; then
    echo "init-db: skip $name (already applied on $db)"
    return 0
  fi
  if [ ! -f "$file" ]; then
    echo "init-db: missing $file" >&2
    exit 1
  fi
  echo "init-db: apply $name on $db"
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$db" -v ON_ERROR_STOP=1 -f "$file"
  record_migration "$db" "$name"
}

create_tenant_db_if_missing() {
  db="$1"
  exists="$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$db'")" || true
  if [ "$exists" != "1" ]; then
    echo "init-db: CREATE DATABASE $db"
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
      -c "CREATE DATABASE \"$db\" OWNER \"$PGUSER\";"
  fi
}

wait_for_postgres

ensure_log_table aiworks

apply_one aiworks cp_001_schema /migrations/001_schema.sql
apply_one aiworks cp_002_tenant_policies /migrations/002_tenant_policies.sql
apply_one aiworks cp_003_tenant_db_connections /migrations/003_tenant_db_connections.sql
apply_one aiworks cp_004_personas /migrations/004_personas.sql
apply_one aiworks cp_006_scraping_limits /migrations/006_scraping_limits.sql
apply_one aiworks cp_007_policy_allowed_skill_ids /migrations/007_policy_allowed_skill_ids.sql

for db in aiworks_t001 aiworks_t002 aiworks_t003 aiworks_t004; do
  create_tenant_db_if_missing "$db"
  ensure_log_table "$db"
  apply_one "$db" t_001_tenant_schema /migrations/tenant/001_tenant_schema.sql
  apply_one "$db" t_002_tenant_sources_article_rules /migrations/tenant/002_tenant_sources_article_rules.sql
  apply_one "$db" t_003_ingestion_persona /migrations/tenant/003_ingestion_persona.sql
  apply_one "$db" t_004_product_catalog /migrations/tenant/004_product_catalog.sql
  apply_one "$db" t_005_executions /migrations/tenant/005_executions.sql
  apply_one "$db" t_006_visit_strategy /migrations/tenant/006_visit_strategy.sql
  apply_one "$db" t_007_newsletter_media /migrations/tenant/007_newsletter_media.sql
  apply_one "$db" t_008_inline_eval_runs /migrations/tenant/008_inline_eval_runs.sql
done

echo "init-db: done"
