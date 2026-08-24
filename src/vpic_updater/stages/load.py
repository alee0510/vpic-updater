"""
Stage 3: Load.

Creates a fresh per-release database, restores the dump into it, validates
the result, grants application access, and promotes it as the current
deployment via the vpic_meta control database. Structured so that any
failure before promote() leaves all existing databases and the promotion
pointer completely untouched.
"""

import logging
import subprocess
from pathlib import Path

from psycopg2.extensions import connection as PGConnection

from vpic_updater.core.config import ADVISORY_LOCK_KEY, DEFAULT_APP_ROLE, DEFAULT_SCHEMA, DEFAULT_MIN_ROW_COUNT
from vpic_updater.core.db import DatabaseDSN, connect, with_dbname
from vpic_updater.model.load import LoadError

logger = logging.getLogger("vpic_updater.load")


# Step-1: Locking
def acquire_lock(control_conn: PGConnection) -> bool:
    """Attempt to acquire the pipeline's advisory lock on the control
    connection. Non-blocking -- returns False immediately if another
    process already holds it, rather than queuing."""
    with control_conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        acquired = cur.fetchone()[0]
    if acquired:
        logger.info("Acquired advisory lock (key=%s)", ADVISORY_LOCK_KEY)
    else:
        logger.warning(
            "Advisory lock already held by another process (key=%s) -- "
            "an update is already in progress",
            ADVISORY_LOCK_KEY,
        )
    return acquired


def release_lock(control_conn: PGConnection) -> None:
    """Release the advisory lock. Safe to call even if the lock was never
    held by this connection (returns False internally, we just log it)."""
    with control_conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        released = cur.fetchone()[0]
    if released:
        logger.info("Released advisory lock (key=%s)", ADVISORY_LOCK_KEY)
    else:
        logger.warning(
            "Attempted to release advisory lock (key=%s) that was not held",
            ADVISORY_LOCK_KEY,
        )


# Step-2: Create database + restore
def db_name_for(year_month: str) -> str:
    """e.g. '2026_08' -> 'vpic_2026_08', matching the ops team's existing
    naming convention from the manual runbook."""
    return f"vpic_{year_month}"


def create_database(target_admin_dsn: DatabaseDSN, db_name: str) -> None:
    """Create a brand-new, empty database. Refuses to proceed if a database
    of this name already exists, rather than silently reusing/overwriting
    it -- an existing db_name most likely means a prior run partially
    completed and needs investigation, not a silent retry."""
    conn = connect(target_admin_dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone():
                raise LoadError(
                    f"Database '{db_name}' already exists -- refusing to "
                    f"overwrite. Investigate before retrying (a prior run "
                    f"may have failed partway through)."
                )
            cur.execute(f'CREATE DATABASE "{db_name}"')
        logger.info("Created database: %s", db_name)
    finally:
        conn.close()


def drop_database_if_exists(target_admin_dsn: DatabaseDSN, db_name: str) -> None:
    """Used only for explicit cleanup/retry tooling (e.g. a manual re-run
    after investigating a failed partial create) -- never called
    automatically by the happy-path pipeline."""
    conn = connect(target_admin_dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        logger.info("Dropped database: %s", db_name)
    finally:
        conn.close()


def restore_dump(
    dump_path: Path,
    target_admin_dsn: DatabaseDSN,
    db_name: str,
    timeout: int = 900,
) -> None:
    """Run pg_restore against the freshly created database. The dump
    creates its own 'vpic' schema inside db_name (per NHTSA's documented
    restore instructions)."""
    if not dump_path.exists():
        raise LoadError(f"Dump file does not exist: {dump_path}")

    cmd = [
        "pg_restore",
        "--host", target_admin_dsn.host,
        "--port", str(target_admin_dsn.port),
        "--username", target_admin_dsn.user,
        "--dbname", db_name,
        "--no-owner",
        "--no-privileges",
        "--verbose",
        str(dump_path),
    ]
    env = {"PGPASSWORD": target_admin_dsn.password.get_secret_value()}

    logger.info("Starting pg_restore into %s from %s", db_name, dump_path)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LoadError(f"pg_restore timed out after {timeout}s for {db_name}") from exc

    if result.returncode != 0:
        logger.error("pg_restore failed for %s: %s", db_name, result.stderr[-2000:])
        raise LoadError(
            f"pg_restore failed for {db_name} (exit {result.returncode}): "
            f"{result.stderr[-500:]}"
        )

    logger.info("pg_restore completed successfully for %s", db_name)

# Step-3: Validate + grant access
def validate_database(
    target_dsn: DatabaseDSN,
    db_name: str,
    min_row_count: int = DEFAULT_MIN_ROW_COUNT,
    schema: str = DEFAULT_SCHEMA,
    table: str = "vin",
) -> int:
    """Connect into the newly restored database and confirm it looks like
    a real, complete dataset before we allow promotion.

    NOTE: table name 'vin' is a placeholder based on the vPIC docs'
    reference to a VIN-decoding stored procedure/function -- confirm the
    actual primary table name once a real dump has been restored locally,
    and adjust here.
    """
    dsn = with_dbname(target_dsn, db_name)
    conn = connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (schema, table),
            )
            if cur.fetchone() is None:
                raise LoadError(
                    f"Expected table {schema}.{table} not found in {db_name} "
                    f"after restore -- schema may not match expectations"
                )

            cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
            row_count = cur.fetchone()[0]
    finally:
        conn.close()

    if row_count < min_row_count:
        raise LoadError(
            f"{db_name}.{schema}.{table} row count too low "
            f"({row_count} < {min_row_count}) -- refusing to promote"
        )

    logger.info("Validated %s: %s.%s has %d rows", db_name, schema, table, row_count)
    return row_count


def grant_app_access(
    target_admin_dsn: DatabaseDSN,
    db_name: str,
    app_role: str = DEFAULT_APP_ROLE,
    schema: str = DEFAULT_SCHEMA,
) -> None:
    """Per-database GRANTs -- the safe, automatable substitute for editing
    pg_hba.conf on every run (agreed: pg_hba gets one permanent generic
    rule set up manually, access control happens here instead)."""
    admin_conn = connect(target_admin_dsn, autocommit=True)
    try:
        with admin_conn.cursor() as cur:
            cur.execute(
                f'GRANT CONNECT, TEMPORARY ON DATABASE "{db_name}" TO {app_role}'
            )
    finally:
        admin_conn.close()

    scoped_dsn = with_dbname(target_admin_dsn, db_name)
    conn = connect(scoped_dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO {app_role}')
            cur.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO {app_role}')
            cur.execute(
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
                f'GRANT SELECT ON TABLES TO {app_role}'
            )
    finally:
        conn.close()

    logger.info("Granted %s access to %s.%s", app_role, db_name, schema)

# Step-4: Promotion + history
def promote(control_conn: PGConnection, db_name: str, version: str, released_on) -> None:
    """Point the application at the new database by updating the singleton
    row in current_deployment. This is the single moment at which
    "production" actually changes -- everything before this point can fail
    without affecting what's live."""
    with control_conn.cursor() as cur:
        cur.execute(
            "UPDATE current_deployment "
            "SET db_name = %s, version = %s, released_on = %s, promoted_at = now() "
            "WHERE id = 1",
            (db_name, version, released_on),
        )
    control_conn.commit()
    logger.info("Promoted %s (version %s) to current_deployment", db_name, version)


def get_last_deployed_version(control_conn: PGConnection) -> str | None:
    """Read the version of the most recent **successful** deployment from the
    audit log -- used by Stage 0 (check) to decide if there's anything new.
    Deliberately reads from update_history (status='success'), not from
    current_deployment, so this stays correct even if current_deployment
    is ever manually repointed for a rollback."""
    with control_conn.cursor() as cur:
        cur.execute(
            "SELECT version FROM update_history "
            "WHERE status = 'success' "
            "ORDER BY finished_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    return row[0] if row else None


def record_history(
    control_conn: PGConnection,
    version: str,
    released_on,
    status: str,
    db_name: str | None = None,
    error: str | None = None,
) -> None:
    """Insert an audit row for this run. Called for both success and
    failure outcomes by the orchestrator."""
    with control_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO update_history "
            "(version, released_on, db_name, status, error, finished_at) "
            "VALUES (%s, %s, %s, %s, %s, now())",
            (version, released_on, db_name, status, error),
        )
    control_conn.commit()
    logger.info("Recorded update_history: version=%s status=%s", version, status)