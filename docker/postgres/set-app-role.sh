#!/bin/bash
# Runs once on target-db's first init. Creates the read-only application
# role that grant_app_access() (in stages/load.py) grants per-database
# access to on every successful deployment. Password comes from the
# VPIC_USER_PASSWORD environment variable -- never hardcoded.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vpic_user') THEN
            CREATE ROLE vpic_user LOGIN PASSWORD '${VPIC_USER_PASSWORD}';
        END IF;
    END
    \$\$;
EOSQL

echo "[set-app-role.sh] vpic_user role ensured"