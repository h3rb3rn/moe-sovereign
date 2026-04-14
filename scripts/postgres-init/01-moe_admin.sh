#!/bin/bash
# Runs once on first initialisation of the terra_checkpoints Postgres volume.
# Creates the moe_admin role and moe_userdb database used by the Admin UI
# and the orchestrator's user-management / API-key store.
set -e

if [[ -z "${MOE_USERDB_PASSWORD:-}" ]]; then
  echo "[postgres-init] ERROR: MOE_USERDB_PASSWORD env var is empty — refusing to create moe_admin with empty password." >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'moe_admin') THEN
      CREATE ROLE moe_admin WITH LOGIN PASSWORD '${MOE_USERDB_PASSWORD}';
    ELSE
      ALTER ROLE moe_admin WITH LOGIN PASSWORD '${MOE_USERDB_PASSWORD}';
    END IF;
  END
  \$\$;

  SELECT 'CREATE DATABASE moe_userdb OWNER moe_admin'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'moe_userdb')\gexec

  GRANT ALL PRIVILEGES ON DATABASE moe_userdb TO moe_admin;
EOSQL

echo "[postgres-init] moe_admin role + moe_userdb database ready."
