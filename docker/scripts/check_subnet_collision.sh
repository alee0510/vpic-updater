#!/bin/bash
# docker/scripts/check_subnet_collision.sh
#
# Checks whether DOCKER_SUBNET (from .env) numerically overlaps any
# existing Docker network OR any subnet already routed on the host.

set -uo pipefail
# NOTE: -e deliberately omitted (or must be worked around explicitly, see
# check_overlap below) -- with -e enabled, this script would silently
# exit the moment it reached the first NON-colliding entry, since that
# path's python3 helper intentionally returns a non-zero exit code, which
# -e treats as a fatal error rather than "no match, keep checking."

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
    local rc

    # Explicit if/else, not a bare statement followed by $? -- this is
    # what actually protects the call from set -e (even though -e is off
    # above, keeping this defensive means the function is also safe to
    # reuse in a caller that does have -e enabled).
    if python3 - "$candidate" "$existing" <<'PYEOF'
import ipaddress
import sys

candidate, existing = sys.argv[1], sys.argv[2]
try:
    c = ipaddress.ip_network(candidate, strict=False)
    e = ipaddress.ip_network(existing, strict=False)
except ValueError:
    sys.exit(2)

sys.exit(0 if c.overlaps(e) else 1)
PYEOF
    then
        rc=0
    else
        rc=$?
    fi

    if [ "$rc" -eq 0 ]; then
        echo "  COLLISION: ${label} uses ${existing}, overlaps ${candidate}"
        collision_found=1
    elif [ "$rc" -eq 2 ]; then
        echo "  WARN: could not parse '${existing}' for ${label} -- skipped" >&2
    fi
    # rc == 1 -> no overlap, nothing to print, this is the normal/expected case
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