#!/usr/bin/env bash
# Выполняется только на пустом volume официальным postgres entrypoint.
set -euo pipefail
: "${SOPDS_DEV_PASSWORD:?SOPDS_DEV_PASSWORD is required}"
psql --no-psqlrc --username "${POSTGRES_USER:-postgres}" --dbname postgres \
  --set=ON_ERROR_STOP=1 --set=app_password="$SOPDS_DEV_PASSWORD" <<'SQL'
CREATE ROLE sopds_dev WITH LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEROLE CREATEDB NOREPLICATION;
CREATE DATABASE sopds_dev OWNER sopds_dev;
SQL
