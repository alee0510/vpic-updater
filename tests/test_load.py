"""Integration tests for the Load stage. Requires live Postgres containers
(see docker-compose.yml: control-db, target-db). Run with:

    TEST_TARGET_PG_HOST=localhost TEST_TARGET_PG_PORT=5434 \\
    TEST_CONTROL_PG_HOST=localhost TEST_CONTROL_PG_PORT=5433 \\
    TEST_TARGET_PG_PASSWORD=<pw> TEST_CONTROL_PG_PASSWORD=<pw> \\
    uv run pytest tests/test_load.py -v -m integration

Tests are auto-skipped if these env vars aren't set.
"""

import subprocess
from pathlib import Path

import pytest

from vpic_updater.core.db import DatabaseDSN, connect, with_dbname
from vpic_updater.stages.load import (
    LoadError,
    acquire_lock,
    create_database,
    db_name_for,
    drop_database_if_exists,
    get_last_deployed_version,
    grant_app_access,
    promote,
    record_history,
    release_lock,
    restore_dump,
    validate_database,
)

pytestmark = pytest.mark.integration


def _build_sample_dump(target_admin_dsn: DatabaseDSN, tmp_path: Path) -> Path:
    """Build a tiny real custom-format dump we can restore in tests,
    mimicking the real vPIC dump's shape (schema 'vpic', table 'vin')
    without needing the actual 190MB file."""
    seed_db = "vpic_seed_source"
    drop_database_if_exists(target_admin_dsn, seed_db)
    create_database(target_admin_dsn, seed_db)

    seed_dsn = with_dbname(target_admin_dsn, seed_db)
    conn = connect(seed_dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA vpic")
            cur.execute("CREATE TABLE vpic.vin (id SERIAL PRIMARY KEY, vin_number TEXT)")
            cur.executemany(
                "INSERT INTO vpic.vin (vin_number) VALUES (%s)",
                [(f"VIN{i:06d}",) for i in range(150_000)],
            )
    finally:
        conn.close()

    dump_path = tmp_path / "sample.backup"
    cmd = [
        "pg_dump", "--host", target_admin_dsn.host, "--port", str(target_admin_dsn.port),
        "--username", target_admin_dsn.user, "--dbname", seed_db,
        "--format=custom", "--file", str(dump_path),
    ]
    env = {"PGPASSWORD": target_admin_dsn.password.get_secret_value()}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr

    drop_database_if_exists(target_admin_dsn, seed_db)
    return dump_path


class TestLockingBehavior:
    def test_acquire_and_release(self, control_conn):
        assert acquire_lock(control_conn) is True
        release_lock(control_conn)

    def test_second_acquire_fails_while_held(self, control_admin_dsn, control_conn):
        assert acquire_lock(control_conn) is True

        other_conn = connect(control_admin_dsn)
        try:
            assert acquire_lock(other_conn) is False
        finally:
            other_conn.close()

        release_lock(control_conn)


class TestCreateDatabase:
    def test_creates_new_database(self, target_admin_dsn):
        db_name = "vpic_test_create_new"
        drop_database_if_exists(target_admin_dsn, db_name)
        try:
            create_database(target_admin_dsn, db_name)
            conn = connect(with_dbname(target_admin_dsn, db_name))
            conn.close()  # connecting successfully proves it exists
        finally:
            drop_database_if_exists(target_admin_dsn, db_name)

    def test_refuses_to_overwrite_existing_database(self, target_admin_dsn):
        db_name = "vpic_test_existing"
        drop_database_if_exists(target_admin_dsn, db_name)
        create_database(target_admin_dsn, db_name)
        try:
            with pytest.raises(LoadError, match="already exists"):
                create_database(target_admin_dsn, db_name)
        finally:
            drop_database_if_exists(target_admin_dsn, db_name)


class TestFullRestoreCycle:
    def test_restore_validate_grant_promote(
        self, target_admin_dsn, control_conn, tmp_path
    ):
        dump_path = _build_sample_dump(target_admin_dsn, tmp_path)
        db_name = db_name_for("2026_08")
        drop_database_if_exists(target_admin_dsn, db_name)

        try:
            create_database(target_admin_dsn, db_name)
            restore_dump(dump_path, target_admin_dsn, db_name)

            row_count = validate_database(
                target_admin_dsn, db_name, min_row_count=100_000
            )
            assert row_count == 150_000

            # grant_app_access requires the role to pre-exist; create it
            # here to mirror the one-time manual setup step
            admin_conn = connect(target_admin_dsn, autocommit=True)
            with admin_conn.cursor() as cur:
                cur.execute(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vpic_user') THEN "
                    "CREATE ROLE vpic_user LOGIN PASSWORD 'test'; END IF; END $$;"
                )
            admin_conn.close()

            grant_app_access(target_admin_dsn, db_name)

            promote(control_conn, db_name, version="4.08", released_on="2026-08-15")
            record_history(
                control_conn, version="4.08", released_on="2026-08-15",
                status="success", db_name=db_name,
            )

            assert get_last_deployed_version(control_conn) == "4.08"

            with control_conn.cursor() as cur:
                cur.execute("SELECT db_name, version FROM current_deployment WHERE id=1")
                row = cur.fetchone()
            assert row == (db_name, "4.08")

        finally:
            drop_database_if_exists(target_admin_dsn, db_name)

    def test_validate_rejects_low_row_count(self, target_admin_dsn, tmp_path):
        db_name = "vpic_test_low_rowcount"
        drop_database_if_exists(target_admin_dsn, db_name)
        try:
            create_database(target_admin_dsn, db_name)
            conn = connect(with_dbname(target_admin_dsn, db_name), autocommit=True)
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA vpic")
                cur.execute("CREATE TABLE vpic.vin (id SERIAL PRIMARY KEY)")
                cur.execute("INSERT INTO vpic.vin DEFAULT VALUES")  # only 1 row
            conn.close()

            with pytest.raises(LoadError, match="row count too low"):
                validate_database(target_admin_dsn, db_name, min_row_count=100_000)
        finally:
            drop_database_if_exists(target_admin_dsn, db_name)


class TestFailureLeavesProductionUntouched:
    def test_restore_failure_does_not_promote(
        self, target_admin_dsn, control_conn, tmp_path
    ):
        """The core acceptance criterion: a failed restore must never reach
        promote(), and current_deployment must be unchanged."""
        # First, establish a "prior successful deployment" baseline
        promote(control_conn, "vpic_2026_07", version="4.07", released_on="2026-07-18")
        record_history(
            control_conn, version="4.07", released_on="2026-07-18",
            status="success", db_name="vpic_2026_07",
        )

        bad_dump = tmp_path / "corrupt.backup"
        bad_dump.write_bytes(b"not a real pg_dump file")

        db_name = db_name_for("2026_08")
        drop_database_if_exists(target_admin_dsn, db_name)
        try:
            create_database(target_admin_dsn, db_name)
            with pytest.raises(LoadError):
                restore_dump(bad_dump, target_admin_dsn, db_name)

            # simulate orchestrator recording the failure
            record_history(
                control_conn, version="4.08", released_on="2026-08-15",
                status="failure", error="pg_restore failed", db_name=db_name,
            )

            # current_deployment must still point at 4.07, not 4.08
            assert get_last_deployed_version(control_conn) == "4.07"
            with control_conn.cursor() as cur:
                cur.execute("SELECT db_name, version FROM current_deployment WHERE id=1")
                row = cur.fetchone()
            assert row == ("vpic_2026_07", "4.07")
        finally:
            drop_database_if_exists(target_admin_dsn, db_name)