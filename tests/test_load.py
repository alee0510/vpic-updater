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

from vpic_updater.core.config import CORE_TABLE_MIN_ROWS
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
    """Build a real custom-format dump matching the *shape* of a real vPIC
    restore. Uses generate_series bulk inserts (not per-row Python loops)
    so seeding millions of rows stays fast in CI."""
    seed_db = "vpic_seed_source"
    drop_database_if_exists(target_admin_dsn, seed_db)
    create_database(target_admin_dsn, seed_db)

    seed_dsn = with_dbname(target_admin_dsn, seed_db)
    conn = connect(seed_dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA vpic")

            # Table shape only needs to be plausible enough for count() and
            # information_schema checks to pass -- columns beyond id are
            # omitted where the validator doesn't inspect them.
            table_ddls = {
                "wmiyearvalidchars": "id SERIAL PRIMARY KEY, wmi TEXT",
                "pattern": "id SERIAL PRIMARY KEY, keys TEXT",
                "vehiclespecpattern": "id SERIAL PRIMARY KEY",
                "wmi_vinschema": "id SERIAL PRIMARY KEY",
                "model": "id SERIAL PRIMARY KEY, name TEXT",
                "vinschema": "id SERIAL PRIMARY KEY",
                "manufacturer": "id SERIAL PRIMARY KEY, name TEXT",
                "wmi": "id SERIAL PRIMARY KEY, wmi TEXT",
                "make": "id SERIAL PRIMARY KEY, name TEXT",
                "element": "id SERIAL PRIMARY KEY, code TEXT",
            }
            for table, ddl in table_ddls.items():
                cur.execute(f"CREATE TABLE vpic.{table} ({ddl})")

            # Seed each table to comfortably clear its production floor,
            # using set-based generation instead of row-by-row inserts.
            seed_counts = {
                "wmiyearvalidchars": 5_100_000,
                "pattern": 1_100_000,
                "vehiclespecpattern": 110_000,
                "wmi_vinschema": 26_000,
                "model": 21_000,
                "vinschema": 16_000,
                "manufacturer": 16_000,
                "wmi": 9_000,
                "make": 9_000,
                "element": 60,
            }
            for table, n in seed_counts.items():
                cur.execute(f"INSERT INTO vpic.{table} (id) SELECT generate_series(1, {n})")

            # Minimal stand-ins for the two required functions -- see
            # note in validate_database docstring: this fake only needs
            # to exist under the right name and return >=1 row.
            cur.execute("""
                CREATE FUNCTION vpic.spvindecode(v character varying)
                RETURNS TABLE(groupname text, value text) AS $$
                    SELECT 'Make'::text, 'TestMake'::text
                $$ LANGUAGE sql;
            """)
            cur.execute("""
                CREATE FUNCTION vpic.spvindecode_core(pass integer)
                RETURNS TABLE(dummy text) AS $$
                    SELECT 'ok'::text
                $$ LANGUAGE sql;
            """)
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
                target_admin_dsn, db_name, min_rows=100_000
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
                validate_database(target_admin_dsn, db_name, min_rows=100_000)
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


class TestValidateDatabase:
    def test_full_validation_passes(self, target_admin_dsn, tmp_path):
        dump_path = _build_sample_dump(target_admin_dsn, tmp_path)
        db_name = db_name_for("2026_08")
        drop_database_if_exists(target_admin_dsn, db_name)
        try:
            create_database(target_admin_dsn, db_name)
            restore_dump(dump_path, target_admin_dsn, db_name)

            result = validate_database(target_admin_dsn, db_name)

            assert result.table_row_counts["wmiyearvalidchars"] == 5_100_000
            assert result.table_row_counts["pattern"] == 1_100_000
            assert result.smoke_test_passed is True
        finally:
            drop_database_if_exists(target_admin_dsn, db_name)

    def test_raises_when_core_table_missing(self, target_admin_dsn):
        db_name = "vpic_test_missing_table"
        drop_database_if_exists(target_admin_dsn, db_name)
        try:
            create_database(target_admin_dsn, db_name)
            conn = connect(with_dbname(target_admin_dsn, db_name), autocommit=True)
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA vpic")
                # deliberately omit vpic.wmi
            conn.close()

            with pytest.raises(LoadError, match="wmi.*not found"):
                validate_database(target_admin_dsn, db_name)
        finally:
            drop_database_if_exists(target_admin_dsn, db_name)

    def test_raises_when_smoke_test_function_missing(self, target_admin_dsn):
        db_name = "vpic_test_no_decode_fn"
        drop_database_if_exists(target_admin_dsn, db_name)
        try:
            create_database(target_admin_dsn, db_name)
            conn = connect(with_dbname(target_admin_dsn, db_name), autocommit=True)
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA vpic")
                for table, floor in CORE_TABLE_MIN_ROWS.items():
                    cur.execute(f"CREATE TABLE vpic.{table} (id SERIAL PRIMARY KEY)")
                    cur.execute(
                        f"INSERT INTO vpic.{table} (id) SELECT generate_series(1, {floor + 10})"
                    )
                # spvindecode / spvindecode_core deliberately not created
            conn.close()

            with pytest.raises(LoadError, match="spvindecode"):
                validate_database(target_admin_dsn, db_name)
        finally:
            drop_database_if_exists(target_admin_dsn, db_name)