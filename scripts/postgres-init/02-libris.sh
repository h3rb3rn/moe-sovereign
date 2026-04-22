#!/bin/bash
# Runs once on first initialisation of the terra_checkpoints Postgres volume.
# Creates the libris role and libris database used by moe-libris-api.
set -e

if [[ -z "${LIBRIS_DB_PASSWORD:-}" ]]; then
  echo "[postgres-init] WARNING: LIBRIS_DB_PASSWORD not set — skipping libris DB creation." >&2
  exit 0
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'libris') THEN
      CREATE ROLE libris WITH LOGIN PASSWORD '${LIBRIS_DB_PASSWORD}';
    ELSE
      ALTER ROLE libris WITH LOGIN PASSWORD '${LIBRIS_DB_PASSWORD}';
    END IF;
  END
  \$\$;

  SELECT 'CREATE DATABASE libris OWNER libris'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'libris')\gexec

  GRANT ALL PRIVILEGES ON DATABASE libris TO libris;
EOSQL

echo "[postgres-init] libris role + libris database ready."
