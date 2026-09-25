# vpic-updater

Automated pipeline to check for, download, validate, and deploy new NHTSA vPIC (Vehicle Identification Number Decoder) database releases to PostgreSQL with zero-downtime database promotion and Slack notifications.

---

## Overview

The `vpic-updater` service automates the retrieval and ingestion of official NHTSA vPIC database updates published at `https://vpic.nhtsa.dot.gov/downloads`. It monitors the published version, downloads the PostgreSQL custom-format database dump (`.custom.zip`), restores it into a dedicated release database, runs strict verification checks, and atomically updates the production deployment pointer.

### Key Features & Safety Guarantees
- **Version Detection**: Scrapes the NHTSA downloads page to check for new release versions. Exits quietly if no update is required.
- **Stream Download with Temp Safety**: Downloads files into `.part` temporary files with size floor validation ($\ge 10\text{ MB}$) before renaming, preventing incomplete downloads.
- **Archive Integrity Check**: Validates zip CRC checksums and checks dump file size before attempting database restoration.
- **Isolated Release Databases**: Restores each dump into a versioned database named `vpic_{year_month}` (e.g., `vpic_2026_08`).
- **Comprehensive Validation**:
  - Validates minimum row count floors across core reference tables (e.g. `wmiyearvalidchars`, `pattern`, `make`, `model`).
  - Verifies presence of required decode stored procedures (`spvindecode`, `spvindecode_core`).
  - Runs a live VIN decode smoke test using a known valid VIN (`1FTEW1E4XKFC98434`).
- **Zero-Downtime Pointer Promotion**: Application traffic is routed to the active database via `current_deployment` pointer in `vpic_meta`. Atomic updates ensure target data remains untouched until validation passes.
- **Advisory Lock Protection**: Uses PostgreSQL advisory lock (`78123456`) on the control database to prevent concurrent update executions.
- **Forensic Retention on Failure**: Partially restored databases created during a failed run are preserved for inspection and prevent accidental overwrites.
- **Custom PostgreSQL Image Security**: Uses a custom PostgreSQL container image ([docker/postgres/Dockerfile](file:///docker/postgres/Dockerfile)) to automate `vpic_user` read-only role creation ([docker/postgres/set-app-role.sh](file:///docker/postgres/set-app-role.sh)) and restrict container network connections ([docker/postgres/set-hba.sh](file:///docker/postgres/set-hba.sh)) based on `DOCKER_SUBNET`.
- **Systemd Timer Execution**: Managed on the VPS host via systemd timer ([etc/systemd/system/vpic-updater.timer](file:///etc/systemd/system/vpic-updater.timer) & [etc/systemd/system/vpic-updater.service](file:///etc/systemd/system/vpic-updater.service)), triggering one-shot container executions without running long-lived cron daemons inside containers.
- **Slack Alerts & Logging**: Posts status reports to Slack webhooks and writes structured logs with unique execution run IDs (`run_id`).

---

## Architecture & Pipeline Stages

### Database Split
The system utilizes a two-database architecture:
1. **Control Database (`control-db` / `vpic_meta`)**:
   - Runs on port `5433` by default (published as `15433` in production).
   - Holds `current_deployment` (singleton table containing active `db_name`, `version`, `released_on`, `promoted_at`).
   - Holds `update_history` (audit log of every execution attempt).
   - Manages single-job execution via `pg_advisory_lock(78123456)`.
2. **Target Database Server (`target-db`)**:
   - Runs on port `5434` by default (published as `15434` in production).
   - Hosts per-release databases (`vpic_YYYY_MM`).
   - Automated `vpic_user` role creation via [docker/postgres/set-app-role.sh](file:///docker/postgres/set-app-role.sh) on database initialization.
   - Grants read-only permissions (`SELECT`, `USAGE`, default privileges) to application role `vpic_user` on promoted release databases.

### Pipeline Lifecycle
The updater process (`vpic_updater.core.orchestrator`) executes the following sequential stages:

```
[0. Check] ──> [1. Extract] ──> [2. Transform] ──> [3. Load] ──> [4. Notify] ──> [5. Cleanup]
```

1. **Stage 0: Check** (`vpic_updater.stages.check`): Scrapes `https://vpic.nhtsa.dot.gov/downloads` for version string (e.g., `Version: 4.08 last updated on 8/15/2026`) and `.custom.zip` download URL. Compares against the last successful deployment in `update_history`.
2. **Stage 1: Extract** (`vpic_updater.stages.extract`): Stream downloads the `.custom.zip` file into `data/downloads/`. Enforces content-length and minimum file size checks.
3. **Stage 2: Transform** (`vpic_updater.stages.transform`): Extracts zip archive into `data/extracted/{year_month}` and verifies existence of the single `.backup` dump file.
4. **Stage 3: Load** (`vpic_updater.stages.load`):
   - Acquires control DB advisory lock.
   - Creates database `vpic_{year_month}` (refuses to proceed if database already exists).
   - Restores dump using `pg_restore --no-owner --no-privileges`.
   - Runs schema validation, table floor count checks, and live VIN decode smoke test.
   - Grants read privileges to `vpic_user` role.
   - Atomically updates `current_deployment` table and appends `update_history`.
5. **Stage 4: Notify** (`vpic_updater.stages.notify`): Posts execution summary to Slack webhook URL. (Notification failure does not roll back deployment).
6. **Stage 5: Cleanup** (`vpic_updater.stages.cleanup`): In a `finally` block, deletes downloaded zip files and extracted dump folders. Releases advisory lock.

---

## Configuration

Settings are managed via Pydantic (`vpic_updater.core.config.Settings`) reading from environment variables or a `.env` file.

| Environment Variable | Default Value | Description |
| :--- | :--- | :--- |
| `DOCKER_SUBNET` | `172.28.0.0/24` | Subnet CIDR for Docker network & `pg_hba.conf` restriction |
| `REGISTRY` | `your-registry.example.com/yourorg` | Container registry prefix / namespace |
| `IMAGE_TAG` | `latest` | Tag for the `vpic-updater` container image |
| `CONTROL_DB_HOST` | `control-db` | Hostname for `vpic_meta` control database |
| `CONTROL_DB_PORT` | `5432` | Port for `vpic_meta` control database (internal network) |
| `CONTROL_DB_USER` | `vpic_admin` | Admin username for control database |
| `CONTROL_DB_PASSWORD` | *(Required)* | Admin password for control database |
| `CONTROL_DB_NAME` | `vpic_meta` | Database name for control database |
| `TARGET_DB_HOST` | `target-db` | Hostname for target PostgreSQL server |
| `TARGET_DB_PORT` | `5432` | Port for target PostgreSQL server (internal network) |
| `TARGET_DB_USER` | `vpic_admin` | Admin username for target PostgreSQL server |
| `TARGET_DB_PASSWORD` | *(Required)* | Admin password for target PostgreSQL server |
| `TARGET_DB_NAME` | `postgres` | Maintenance/admin database name on target server |
| `VPIC_USER_PASSWORD` | *(Required)* | Password for read-only application role `vpic_user` |
| `APP_ROLE` | `vpic_user` | Application user role granted read access |
| `SLACK_WEBHOOK_URL` | *(Required)* | Slack Incoming Webhook URL for alerts |
| `DOWNLOAD_DIR` | `data/downloads` | Temporary download working directory |
| `EXTRACT_DIR` | `data/extracted` | Temporary extraction working directory |
| `LOG_DIR` | `logs` | Directory for log files |

An example environment file is provided at [.env.example](file:///.env.example).

---

## Database Initialization & Custom PostgreSQL Setup

Database roles and permissions are initialized automatically:

1. **Control DB Schema** ([migrations/001_init_vpic_meta.sql](file:///migrations/001_init_vpic_meta.sql)):
   - Executed on initial startup of `control-db`.
   - Creates `current_deployment` singleton table and `update_history` audit table.
2. **Custom PostgreSQL Image** ([docker/postgres/Dockerfile](file:///docker/postgres/Dockerfile)):
   - Builds custom PostgreSQL images for `control-db` and `target-db`.
   - [docker/postgres/set-app-role.sh](file:///docker/postgres/set-app-role.sh): Creates application role `vpic_user` with `VPIC_USER_PASSWORD` on `target-db` initialization. Supersedes `migrations/002_init_target_db_role.sql`.
   - [docker/postgres/set-hba.sh](file:///docker/postgres/set-hba.sh): Renders `pg_hba.conf` from template using `DOCKER_SUBNET` for network-level security.
   - Password authentication is `md5` (not the PostgreSQL 17 default `scram-sha-256`): `pg_hba.conf.template` uses `md5` on every `host` line, and `docker-compose.prod.yaml` starts both databases with `password_encryption=md5`. Both only take effect on a fresh data volume -- see [Migrating existing volumes to md5](#migrating-existing-volumes-to-md5).

---

## Requirements & Local Setup

### System Prerequisites
- **Python**: `>= 3.12, < 3.13`
- **Package Manager**: [uv](https://github.com/astral-sh/uv)
- **PostgreSQL Client Tools**: `pg_restore` (installed via `postgresql-client`)
- **Docker & Docker Compose**: For containerized runs and testing

### Installation
1. Clone the repository and copy the environment template:
   ```bash
   cp .env.example .env
   ```
2. Update `.env` with your database credentials and Slack webhook URL.
3. Install dependencies using `uv`:
   ```bash
   uv sync
   ```

### Local Execution
Run the updater CLI entry point:
```bash
uv run vpic-update
```
*(Alternatively: `uv run python -m vpic_updater.core.orchestrator`)*

---

## Docker Compose Configurations

The repository provides modular Compose configurations for local development, production deployment, and verification:

| File | Purpose | Key Details |
| :--- | :--- | :--- |
| [docker-compose.yaml](file:///docker-compose.yaml) | Base local/dev setup | Uses `build:` for local `updater` image building |
| [docker-compose.dev.yaml](file:///docker-compose.dev.yaml) | Dev override | Mounts `./src` and `./tests` for live editing, executes integration test suite |
| [docker-compose.prod.yaml](file:///docker-compose.prod.yaml) | VPS production setup | Uses pre-built registry `image:`, no `build:` block for `updater` |
| [docker-compose.verify.yaml](file:///docker-compose.verify.yaml) | Local registry verification | Overrides `updater` service to test registry images locally before deployment |

### Local Development & Integration Testing Setup
To launch database containers and execute integration tests in a dev container:
```bash
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d control-db target-db
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml run --rm updater
```

---

## Deployment Guide

## Prerequisites checklist

- VPS with Docker + Docker Compose plugin confirmed
- A local machine with Docker installed, capable of building the `prod` image
- A Docker registry account (Docker Hub, GHCR, or self-hosted) and a repository/namespace created for `vpic-updater`
- SSH access to the VPS
- A Slack incoming webhook URL

---

## Step 1 — Confirm Docker Compose is available on the VPS

```bash
ssh <user>@<vps-ip>
docker --version
docker compose version
```

If missing:

```bash
sudo apt-get update
sudo apt-get install -y docker-compose-plugin
docker compose version
```

## Step 2 — Get the deployment files onto the VPS

The VPS no longer needs to build anything, but it still needs `docker-compose.prod.yaml`, `migrations/`, and `docker/postgres/*` (for database init) — simplest to keep a full checkout in sync:

```bash
sudo mkdir -p /opt/vpic-updater
sudo chown $USER:$USER /opt/vpic-updater
cd /opt/vpic-updater
git clone <your-repo-url> .
```

## Step 3 — Repo cleanup (confirm superseded files are gone)

```bash
rm -f migrations/002_init_target_db_role.sql   # superseded by docker/postgres/set-app-role.sh
rm -rf docker/cron/                            # superseded by systemd scheduling
find docker -type f
# expected:
#   docker/entrypoint.sh
#   docker/postgres/pg_hba.conf.template
#   docker/postgres/set-hba.sh
#   docker/postgres/set-app-role.sh
#   docker/scripts/check_subnet_collision.sh
```

## Step 4 — Confirm you have two separate compose files

```
docker-compose.yaml         # local/dev -- has build: for the updater service
docker-compose.dev.yaml     # local/dev override -- test target, bind mounts
docker-compose.prod.yaml    # VPS -- has image:, no build: at all
```

## Step 5 — Generate secrets and create `.env` on the VPS

```bash
cp .env.example .env
openssl rand -base64 24   # run 3x
nano .env

# Example of random generated string -base64
# 2W9y39iZF+rYppmHdbfBIcrNyMtCviT7
# 2ZhqQhaUgDekENJPtgtnRRLmS7K40Q0U
# u4kZ5iPX1eohjGDmLLGNytp9bhQh8Sua
```

```bash
REGISTRY=your-registry.example.com/yourorg   # e.g. ghcr.io/yourorg or docker.io/yourusername
IMAGE_TAG=latest

CONTROL_DB_PASSWORD=<generated>
TARGET_DB_PASSWORD=<generated>
VPIC_USER_PASSWORD=<generated>

CONTROL_DB_HOST=control-db
CONTROL_DB_PORT=5432
CONTROL_DB_USER=vpic_admin
CONTROL_DB_NAME=vpic_meta

TARGET_DB_HOST=target-db
TARGET_DB_PORT=5432
TARGET_DB_USER=vpic_admin
TARGET_DB_NAME=postgres

APP_ROLE=vpic_user
SLACK_WEBHOOK_URL=<your real webhook>

DOWNLOAD_DIR=data/downloads
EXTRACT_DIR=data/extracted
LOG_DIR=logs
```

```bash
chmod 600 .env
```

## Step 6 — Firewall confirmation

control-db and target-db are published on `0.0.0.0` (open to all external
requests), not loopback-only. Traffic is unencrypted after authentication
(no TLS) and `pg_hba.conf` allows `0.0.0.0/0`. Confirm the ports are
actually reachable as intended:

```bash
sudo ufw status
sudo ss -tlnp | grep -E '15433|15434' # should show 0.0.0.0:xxxx
```

## Step 7 — Build and push the image (on your local machine, not the VPS)

```bash
cd /path/to/vpic-updater   # your local clone

REGISTRY=<your-docker-hub-registry-url>
docker build --target prod \
  -t ${REGISTRY}/vpic-updater:latest \
  -t ${REGISTRY}/vpic-updater:$(git rev-parse --short HEAD) \
  .

docker push ${REGISTRY}/vpic-updater:latest
docker push ${REGISTRY}/vpic-updater:$(git rev-parse --short HEAD)
```

## Step 8 — Authenticate the VPS to the registry (one-time)

```bash
# Docker Hub:
docker login -u <username>

# GHCR:
echo "<PAT with read:packages>" | docker login ghcr.io -u <github-username> --password-stdin

# self-hosted:
docker login your-registry.example.com
```

## Step 9a — Pre-flight subnet collision check. Must pass before proceeding

```bash
set -a; source .env; set +a
./docker/scripts/check_subnet_collision.sh || { echo "Fix DOCKER_SUBNET in .env and retry."; exit 1; }
```

## Step 9b — Bring up the databases, confirm correct initialization

```bash
cd /opt/vpic-updater
docker compose -f docker-compose.prod.yaml build control-db target-db
docker compose -f docker-compose.prod.yaml up -d control-db target-db
docker compose -f docker-compose.prod.yaml ps   # both "healthy" after ~10-15s
```

```bash
# pg_hba.conf check
docker compose -f docker-compose.prod.yaml exec target-db cat /var/lib/postgresql/data/pg_hba.conf
```

### Migrating existing volumes to md5

`pg_hba.conf` and stored password hashes are written once, when a data
volume is first initialized. On volumes created before the switch to md5,
rebuilding the image is not enough -- run this once per database (never
`docker compose down -v`, which deletes `vpic_meta` and every release db):

```bash
DC="docker compose -f docker-compose.prod.yaml"
$DC up -d --build control-db target-db

# target-db (repeat with control-db / -d vpic_meta / CONTROL_DB_PASSWORD)
$DC exec -u postgres target-db cp /var/lib/postgresql/data/pg_hba.conf /var/lib/postgresql/data/pg_hba.conf.bak
$DC exec -u postgres target-db sed -i '/^host/ s/scram-sha-256/md5/' /var/lib/postgresql/data/pg_hba.conf
$DC exec target-db psql -h 127.0.0.1 -U vpic_admin -d postgres
```

Inside psql, re-set each password to its existing `.env` value so it is
re-hashed as md5 (control-db only has `vpic_admin`):

```
SHOW password_encryption;   -- md5
\password vpic_admin
\password vpic_user
SELECT rolname, left(rolpassword, 6) FROM pg_authid WHERE rolpassword IS NOT NULL;   -- all md5...
SELECT pg_reload_conf();
SELECT line_number, database, user_name, address, auth_method, error FROM pg_hba_file_rules;
```

```bash
# vpic_user role check
docker compose -f docker-compose.prod.yaml exec target-db \
  psql -h 127.0.0.1 -U vpic_admin -d postgres -c "\du vpic_user"

# vpic_meta schema check
docker compose -f docker-compose.prod.yaml exec control-db \
  psql -h 127.0.0.1 -U vpic_admin -d vpic_meta -c "\dt"
```

## Step 10 — Pull the updater image

```bash
docker compose -f docker-compose.prod.yaml pull updater
```

## Step 11 — One manual seed deployment

```bash
docker compose -f docker-compose.prod.yaml run --rm updater vpic-update
```

Verify:

```bash
# current_deployment check
docker compose -f docker-compose.prod.yaml exec control-db \
  psql -h 127.0.0.1 -U vpic_admin -d vpic_meta -c "SELECT * FROM current_deployment;"

# update_history check
docker compose -f docker-compose.prod.yaml exec control-db \
  psql -h 127.0.0.1 -U vpic_admin -d vpic_meta -c "SELECT * FROM update_history;"

# database list check
docker compose -f docker-compose.prod.yaml exec target-db \
  psql -h 127.0.0.1 -U vpic_admin -d postgres -c "\l" | grep vpic_
```

Check Slack for the notification.

## Step 12 — Install and enable the systemd timer

```ini
# /etc/systemd/system/vpic-updater.service
[Unit]
Description=vpic-updater weekly check/deploy job
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/vpic-updater
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yaml run --rm updater vpic-update
TimeoutStartSec=1800
TimeoutStopSec=30
KillMode=mixed

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/vpic-updater.timer
[Unit]
Description=Weekly trigger for vpic-updater.service

[Timer]
OnCalendar=Mon *-*-* 06:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

```bash
sudo cp ./etc/systemd/system/vpic-updater.service ./etc/systemd/system/vpic-updater.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vpic-updater.timer
systemctl list-timers vpic-updater.timer
```

**Optional** immediate test run:

```bash
sudo systemctl start vpic-updater.service
journalctl -u vpic-updater.service -f
```

## Step 13 — Confirm log output

```bash
ls -la /opt/vpic-updater/logs/
tail -f /opt/vpic-updater/logs/vpic_updater.log
journalctl -u vpic-updater.service --since "1 week ago"
systemctl status vpic-updater.service
```

## Step 14 — Reboot survival check

```bash
sudo reboot
```

After reconnecting:

```bash
docker compose -f docker-compose.prod.yaml ps   # control-db, target-db back up
systemctl list-timers vpic-updater.timer        # timer re-armed
```

## Step 15 — Future redeploys

Local machine:

```bash
docker build --target prod \
  -t ${REGISTRY}/vpic-updater:latest \
  -t ${REGISTRY}/vpic-updater:$(git rev-parse --short HEAD) \
  .
docker push ${REGISTRY}/vpic-updater:latest
docker push ${REGISTRY}/vpic-updater:$(git rev-parse --short HEAD)
```

VPS:

```bash
cd /opt/vpic-updater
git pull origin main   # syncs docker-compose.prod.yaml / migrations / docker/postgres if changed
docker compose -f docker-compose.prod.yaml pull updater
```

No restart step needed — `updater` is one-shot, the next timer-triggered (or manual) run uses the newly pulled image automatically.

## Rollback

```bash
# on the VPS:
cd /opt/vpic-updater
nano .env   # set IMAGE_TAG=<previous-known-good-short-sha>
docker compose -f docker-compose.prod.yaml pull updater
docker compose -f docker-compose.prod.yaml run --rm updater vpic-update   # optional: verify immediately
```

Since every push tags both `latest` and an immutable short-SHA (Step 7/15), rollback is always available as long as you keep note of a previously-known-good SHA.

---

## Operational reference

```bash
# manual on-demand run
docker compose -f docker-compose.prod.yaml run --rm updater vpic-update

# current promoted version
docker compose -f docker-compose.prod.yaml exec control-db \
  psql -h 127.0.0.1 -U vpic_admin -d vpic_meta -c "SELECT * FROM current_deployment;"

# deployment history
docker compose -f docker-compose.prod.yaml exec control-db \
  psql -h 127.0.0.1 -U vpic_admin -d vpic_meta -c "SELECT * FROM update_history;"

# all vpic_* databases (retention/growth check)
docker compose -f docker-compose.prod.yaml exec target-db \
  psql -h 127.0.0.1 -U vpic_admin -d postgres -c "\l" | grep vpic_

# live logs during a run
journalctl -u vpic-updater.service -f

# pause scheduling (e.g. maintenance window)
sudo systemctl stop vpic-updater.timer
sudo systemctl start vpic-updater.timer   # resume
```

---

## Testing

### Unit Tests
Run the test suite locally using `uv`:
```bash
uv run pytest
```

### Integration Tests
Integration tests require live Postgres instances (configured via environment variables `TEST_CONTROL_PG_*` and `TEST_TARGET_PG_*`).

To execute integration tests locally against active database containers:
```bash
uv run pytest -v -m integration
```

---

## Logging & Monitoring

- **Console & File Output**: Logs are written simultaneously to standard output and rotating log files under `logs/vpic_updater.log` (10 MB file cap, 5 backup rotations).
- **Run Tracking**: Every log entry is tagged with a unique hex ID (`[run=xxxxxxxx]`) for contextual filtering across modules.
- **Audit Log**: Query `update_history` in `vpic_meta` control DB to review historical update results and failure error trace messages.
- **Host Journal Logs**: On VPS deployments, execution logs from systemd timer runs can be inspected via `journalctl -u vpic-updater.service`.