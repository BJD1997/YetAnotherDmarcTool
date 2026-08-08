#!/usr/bin/env bash
# Runs once, automatically, on first `db` container start (empty data
# directory only — the official postgres image only executes
# /docker-entrypoint-initdb.d/* the very first time).
#
# POSTGRES_USER (set via docker-compose environment) is created as a
# superuser by the base image and is used ONLY for running Alembic
# migrations (see the `migrate` service and alembic/env.py). It therefore
# bypasses row-level security, which is fine for schema management but
# would defeat RLS entirely if the running api/worker containers connected
# as it. This script creates a second, deliberately non-superuser role that
# they connect as instead — FORCE ROW LEVEL SECURITY (set per-table in
# 0001_initial_schema.py) only binds a non-owner, non-superuser role, so
# this role is what actually makes RLS take effect.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'dmarc_app') THEN
            CREATE ROLE dmarc_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '${DMARC_APP_DB_PASSWORD}';
        ELSE
            ALTER ROLE dmarc_app WITH PASSWORD '${DMARC_APP_DB_PASSWORD}';
        END IF;
    END
    \$\$;

    GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO dmarc_app;
EOSQL
