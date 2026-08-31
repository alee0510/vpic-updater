#!/bin/sh
set -e

# No cron here anymore -- scheduling now lives in a systemd timer on the
# VPS host, which invokes `docker compose run --rm updater vpic-update`
# directly. This container is a one-shot execution runtime only; it does
# not stay running and does not manage its own schedule.
exec "$@"