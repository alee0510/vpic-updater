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
- **Slack Alerts & Logging**: Posts status reports to Slack webhooks and writes structured logs with unique execution run IDs (`run_id`).

---

## Architecture & Pipeline Stages

### Database Split
The system utilizes a two-database architecture:
1. **Control Database (`control-db` / `vpic_meta`)**:
   - Runs on port `5433` by default.
   - Holds `current_deployment` (singleton table containing active `db_name`, `version`, `released_on`, `promoted_at`).
   - Holds `update_history` (audit log of every execution attempt).
   - Manages single-job execution via `pg_advisory_lock(78123456)`.
2. **Target Database Server (`target-db`)**:
   - Runs on port `5434` by default.
   - Hosts per-release databases (`vpic_YYYY_MM`).
   - Grants read-only permissions (`SELECT`, `USAGE`, default privileges) to application role `vpic_user`.

### Pipeline Lifecycle
The updater process (`vpic_updater.core.orchestrator`) executes the following sequential stages:

```
[0. Check] ──> [1. Extract] ──> [2. Transform] ──> [3. Load] ──> [4. Notify] ──> [5. Cleanup]
```

1. **Stage 0: Check** (`vpic_updater.stages.check`)
   Scrapes `https://vpic.nhtsa.dot.gov/downloads` for version string (e.g., `Version: 4.08 last updated on 8/15/2026`) and `.custom.zip` download URL. Compares against the last successful deployment in `update_history`.
2. **Stage 1: Extract** (`vpic_updater.stages.extract`)
   Stream downloads the `.custom.zip` file into `data/downloads/`. Enforces content-length and minimum file size checks.
3. **Stage 2: Transform** (`vpic_updater.stages.transform`)
   Extracts zip archive into `data/extracted/{year_month}` and verifies existence of the single `.backup` dump file.
4. **Stage 3: Load** (`vpic_updater.stages.load`)
   - Acquires control DB advisory lock.
   - Creates database `vpic_{year_month}` (refuses to proceed if database already exists).
   - Restores dump using `pg_restore --no-owner --no-privileges`.
   - Runs schema validation, table floor count checks, and live VIN decode smoke test.
   - Grants read privileges to `vpic_user` role.
   - Atomically updates `current_deployment` table and appends `update_history`.
5. **Stage 4: Notify** (`vpic_updater.stages.notify`)
   Posts execution summary to Slack webhook URL. (Notification failure does not roll back deployment).
6. **Stage 5: Cleanup** (`vpic_updater.stages.cleanup`)
   In a `finally` block, deletes downloaded zip files and extracted dump folders. Releases advisory lock.

---

## Configuration

Settings are managed via Pydantic (`vpic_updater.core.config.Settings`) reading from environment variables or a `.env` file.

| Environment Variable | Default Value | Description |
| :--- | :--- | :--- |
| `CONTROL_DB_HOST` | `localhost` | Hostname for `vpic_meta` control database |
| `CONTROL_DB_PORT` | `5433` | Port for `vpic_meta` control database |
| `CONTROL_DB_USER` | `vpic_admin` | Admin username for control database |
| `CONTROL_DB_PASSWORD` | *(Required)* | Admin password for control database |
| `CONTROL_DB_NAME` | `vpic_meta` | Database name for control database |
| `TARGET_DB_HOST` | `localhost` | Hostname for target PostgreSQL server |
| `TARGET_DB_PORT` | `5434` | Port for target PostgreSQL server |
| `TARGET_DB_USER` | `vpic_admin` | Admin username for target PostgreSQL server |
| `TARGET_DB_PASSWORD` | *(Required)* | Admin password for target PostgreSQL server |
| `TARGET_DB_NAME` | `postgres` | Maintenance/admin database name on target server |
| `APP_ROLE` | `vpic_user` | Application user role granted read access |
| `SLACK_WEBHOOK_URL` | *(Required)* | Slack Incoming Webhook URL for alerts |
| `DOWNLOAD_DIR` | `data/downloads` | Temporary download working directory |
| `EXTRACT_DIR` | `data/extracted` | Temporary extraction working directory |
| `LOG_DIR` | `logs` | Directory for log files |

An example environment file is provided at [.env.example](file:///.env.example).

---

## Database Migrations

Two SQL migration scripts initialize the required database schemas:

1. **[migrations/001_init_vpic_meta.sql](file:///migrations/001_init_vpic_meta.sql)**:
   - Run once against `control-db` (`vpic_meta`).
   - Creates `current_deployment` singleton table and `update_history` audit table with index structures.
2. **[migrations/002_init_target_db_role.sql](file:///migrations/002_init_target_db_role.sql)**:
   - Run once against `target-db` on container initialization.
   - Creates the application role `vpic_user` if it does not already exist.

---

## Requirements & Local Setup

### System Prerequisites
- **Python**: `>= 3.12, < 3.13`
- **Package Manager**: [uv](https://github.com/astral-sh/uv)
- **PostgreSQL Client Tools**: `pg_restore` (installed via `postgresql-client`)
- **Docker & Docker Compose**: (Optional, for containerized run)

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

### Execution
Run the updater CLI entry point:
```bash
uv run vpic-update
```
*(Alternatively: `uv run python -m vpic_updater.core.orchestrator`)*

---

## Running with Docker Compose

### Production Setup
To spin up `control-db`, `target-db`, and the scheduled `updater` container:
```bash
docker compose up -d
```
- `control-db` is exposed on `localhost:5433`.
- `target-db` is exposed on `localhost:5434`.
- `updater` runs the containerized environment using `cron -f`.

### Development & Integration Testing Setup
To launch database containers and execute integration tests in a dev container:
```bash
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml up -d control-db target-db
docker compose -f docker-compose.yaml -f docker-compose.dev.yaml run --rm updater
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