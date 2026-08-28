#!/bin/bash
# Runs once via docker-entrypoint-initdb.d, after initdb has created
# $PGDATA -- copies our hardened template over the image's default,
# permissive pg_hba.conf. The official postgres image automatically
# restarts the server after all initdb.d scripts finish, so this takes
# effect without any extra reload step.
set -e

cp /docker-entrypoint-initdb.d/pg_hba.conf.template "$PGDATA/pg_hba.conf"
chmod 600 "$PGDATA/pg_hba.conf"

echo "[set-hba.sh] custom pg_hba.conf applied to $PGDATA"