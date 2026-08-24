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
import psycopg2
from pathlib import Path
from psycopg2.extensions import connection as PGConnection

from vpic_updater.core.config import ADVISORY_LOCK_KEY, CORE_TABLE_MIN_ROWS, DEFAULT_SCHEMA, REQUIRED_FUNCTIONS, SMOKE_TEST_VIN
from vpic_updater.core.db import DatabaseDSN, connect, with_dbname
from vpic_updater.models.load import LoadError
from vpic_updater.models.validation import ValidationResult

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
    target_admin_dsn: DatabaseDSN,
    db_name: str,
    schema: str = DEFAULT_SCHEMA,
    min_rows: dict[str, int] | None = None,
    run_smoke_test: bool = True,
) -> ValidationResult:
    """Validate a freshly restored database before allowing promotion.

    Checks, in order (fails fast on the first problem found):
      1. Each core table in CORE_TABLE_MIN_ROWS exists and meets its floor.
      2. Each function in REQUIRED_FUNCTIONS exists in the schema.
      3. (optional) vpic.spvindecode() actually runs against a real VIN
         and returns at least one row -- the strongest signal that the
         restore is functionally complete, not just structurally present.
    """
    min_rows = min_rows if min_rows is not None else CORE_TABLE_MIN_ROWS
    dsn = with_dbname(target_admin_dsn, db_name)
    conn = connect(dsn)
    try:
        row_counts = _check_core_tables(conn, schema, min_rows, db_name)
        _check_required_functions(conn, schema, db_name)

        smoke_test_passed = False
        if run_smoke_test:
            smoke_test_passed = _run_decode_smoke_test(conn, schema, db_name)
    finally:
        conn.close()

    logger.info(
        "Validated %s: %d core tables checked, smoke_test=%s",
        db_name, len(row_counts), smoke_test_passed,
    )
    return ValidationResult(
        db_name=db_name,
        table_row_counts=row_counts,
        smoke_test_passed=smoke_test_passed,
    )


def _check_core_tables(
    conn: PGConnection, schema: str, min_rows: dict[str, int], db_name: str
) -> dict[str, int]:
    row_counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table, floor in min_rows.items():
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (schema, table),
            )
            if cur.fetchone() is None:
                raise LoadError(
                    f"{db_name}: expected table {schema}.{table} not found "
                    f"after restore -- schema may not match expectations"
                )

            cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
            count = cur.fetchone()[0]
            if count < floor:
                raise LoadError(
                    f"{db_name}: {schema}.{table} row count too low "
                    f"({count} < {floor}) -- refusing to promote"
                )
            row_counts[table] = count

    return row_counts


def _check_required_functions(conn: PGConnection, schema: str, db_name: str) -> None:
    with conn.cursor() as cur:
        for func_name in REQUIRED_FUNCTIONS:
            cur.execute(
                "SELECT 1 FROM pg_proc p "
                "JOIN pg_namespace n ON p.pronamespace = n.oid "
                "WHERE n.nspname = %s AND p.proname = %s",
                (schema, func_name),
            )
            if cur.fetchone() is None:
                raise LoadError(
                    f"{db_name}: expected function {schema}.{func_name}() "
                    f"not found after restore"
                )


def _run_decode_smoke_test(conn: PGConnection, schema: str, db_name: str) -> bool:
    """Actually call spvindecode() against a known VIN. Any exception here
    means the restored functions/tables can't perform a real decode, which
    is a harder failure than a missing table -- surface it clearly."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT count(*) FROM "{schema}".spvindecode(%s)',
                (SMOKE_TEST_VIN,),
            )
            result_count = cur.fetchone()[0]
    except psycopg2.Error as exc:
        raise LoadError(
            f"{db_name}: VIN decode smoke test raised an error calling "
            f"{schema}.spvindecode(): {exc}"
        ) from exc

    if result_count == 0:
        raise LoadError(
            f"{db_name}: VIN decode smoke test returned zero rows for "
            f"a known-valid VIN ({SMOKE_TEST_VIN}) -- decode pipeline "
            f"appears non-functional after restore"
        )

    return True

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