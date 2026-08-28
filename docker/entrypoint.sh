#!/bin/sh
set -e

# Cron does not inherit the container's environment by default -- capture
# it once here (as the container is started, with .env already applied by
# `env_file:` in docker-compose.yaml) and write it to a file the cron job
# sources before each run.
printenv | sed 's/^\(.*\)$/export \1/' > /app/.cron_env
chmod 600 /app/.cron_env

if [ "$#" -eq 0 ]; then
    # No command passed -- long-running scheduled service. Install the
    # crontab and run cron in the foreground.
    crontab /app/docker/cron/crontab
    echo "$(date -Iseconds) vpic-updater cron container starting. Schedule installed:"
    crontab -l
    exec cron -f
else
    # A command was passed explicitly (e.g. the initial manual seed run,
    # or a future ad-hoc invocation) -- run it directly instead of cron.
    exec "$@"
fi