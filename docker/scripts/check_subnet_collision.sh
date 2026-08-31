#!/bin/bash
# docker/scripts/check_subnet_collision.sh
#
# Checks whether DOCKER_SUBNET (from .env) numerically overlaps any
# existing Docker network OR any subnet already routed on the host
# (VPN interfaces, provider private networking, the existing native
# Postgres host route, etc.). String-matching on the CIDR text is not
# enough -- a /24 can sit entirely inside an existing /16 without ever
# sharing an identical substring. Run this before `docker compose up`
# on a fresh VPS, or any time DOCKER_SUBNET changes.

set -euo pipefail

if [ -z "${DOCKER_SUBNET:-}" ]; then
    echo "ERROR: DOCKER_SUBNET not set. Export it or source .env first:" >&2
    echo "  set -a; source .env; set +a" >&2
    exit 1
fi

echo "Checking for collisions against candidate subnet: ${DOCKER_SUBNET}"
echo

collision_found=0

check_overlap() {
    local candidate="$1"
    local existing="$2"
    local label="$3"

    python3 - "$candidate" "$existing" <<'PYEOF'
import ipaddress
import sys

candidate, existing = sys.argv[1], sys.argv[2]
try:
    c = ipaddress.ip_network(candidate, strict=False)
    e = ipaddress.ip_network(existing, strict=False)
except ValueError:
    sys.exit(2)  # unparsable -- treat as non-match, don't false-alarm

sys.exit(0 if c.overlaps(e) else 1)
PYEOF
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "  COLLISION: ${label} uses ${existing}, overlaps ${candidate}"
        collision_found=1
    fi
}

echo "-- Existing Docker networks --"
while IFS=$'\t' read -r name subnet; do
    [ -z "$subnet" ] && continue
    check_overlap "$DOCKER_SUBNET" "$subnet" "docker network '${name}'"
done < <(docker network ls -q | xargs -r docker network inspect \
    -f '{{.Name}}	{{range .IPAM.Config}}{{.Subnet}}{{end}}')

echo "-- Host routing table --"
while read -r subnet; do
    [ -z "$subnet" ] && continue
    check_overlap "$DOCKER_SUBNET" "$subnet" "host route"
done < <(ip route show | awk '{print $1}' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$' || true)

echo
if [ "$collision_found" -eq 1 ]; then
    echo "RESULT: Collision(s) found. Choose a different DOCKER_SUBNET in .env before deploying."
    exit 1
else
    echo "RESULT: No collisions found. ${DOCKER_SUBNET} is safe to use."
    exit 0
fi