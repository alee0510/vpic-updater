#!/bin/bash
# Runs once via docker-entrypoint-initdb.d, after initdb has created
# $PGDATA. Substitutes the DOCKER_SUBNET env var (from .env, passed
# through by docker-compose) into the pg_hba.conf template before
# installing it -- this is the single point where the subnet value
# actually gets applied, keeping .env as the only place it's defined.
set -e

if [ -z "${DOCKER_SUBNET:-}" ]; then
    echo "[set-hba.sh] ERROR: DOCKER_SUBNET is not set -- refusing to apply an" >&2
    echo "[set-hba.sh] unrestricted pg_hba.conf. Check that .env defines" >&2
    echo "[set-hba.sh] DOCKER_SUBNET and that docker-compose passes it through." >&2
    exit 1
fi

sed "s|__DOCKER_SUBNET__|${DOCKER_SUBNET}|g" \
    /docker-entrypoint-initdb.d/pg_hba.conf.template > "$PGDATA/pg_hba.conf"
chmod 600 "$PGDATA/pg_hba.conf"

echo "[set-hba.sh] custom pg_hba.conf applied to $PGDATA (subnet: ${DOCKER_SUBNET})"