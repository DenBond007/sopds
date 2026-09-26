#!/bin/sh
set -eu
# The official entrypoint runs this only for a NEW, empty PGDATA.
# Read the password inside psql, not from an interpolated SQL string or argv.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-psqlrc \
    --set=ON_ERROR_STOP=1 <<'SQL'
\getenv app_password SOPDS_DB_PASSWORD
BEGIN;
CREATE ROLE sopds LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER DATABASE sopds OWNER TO sopds;
COMMIT;
SQL
