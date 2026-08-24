"""Shared pytest fixtures. Integration tests require a live Postgres
reachable via TEST_PG_* env vars (matches docker-compose's target-db /
control-db services) and are skipped otherwise."""

import os
import zipfile
from pathlib import Path

import psycopg2
import pytest

from vpic_updater.core.db import DatabaseDSN


def _dsn_from_env(prefix: str, default_port: int) -> DatabaseDSN | None:
    host = os.environ.get(f"{prefix}_HOST")
    if not host:
        return None
    return DatabaseDSN(
        host=host,
        port=int(os.environ.get(f"{prefix}_PORT", default_port)),
        user=os.environ.get(f"{prefix}_USER", "vpic_admin"),
        password=os.environ.get(f"{prefix}_PASSWORD", "postgres"),
        dbname=os.environ.get(f"{prefix}_DBNAME", "postgres"),
    )


@pytest.fixture(scope="session")
def target_admin_dsn() -> DatabaseDSN:
    dsn = _dsn_from_env("TEST_TARGET_PG", default_port=5434)
    if dsn is None:
        pytest.skip("TEST_TARGET_PG_HOST not set -- skipping integration test")
    return dsn


@pytest.fixture(scope="session")
def control_admin_dsn() -> DatabaseDSN:
    dsn = _dsn_from_env("TEST_CONTROL_PG", default_port=5433)
    if dsn is None:
        pytest.skip("TEST_CONTROL_PG_HOST not set -- skipping integration test")
    return dsn


@pytest.fixture
def control_conn(control_admin_dsn: DatabaseDSN):
    conn = psycopg2.connect(**control_admin_dsn.as_psycopg2_kwargs())
    # Ensure schema exists for this test run
    with open(Path(__file__).parent.parent / "migrations" / "001_init_vpic_meta.sql") as f:
        migration_sql = f.read()
    with conn.cursor() as cur:
        cur.execute(migration_sql)
    conn.commit()
    yield conn
    # Reset singleton + clear history between tests
    with conn.cursor() as cur:
        cur.execute("TRUNCATE update_history RESTART IDENTITY")
        cur.execute(
            "UPDATE current_deployment SET db_name=NULL, version=NULL, "
            "released_on=NULL, promoted_at=NULL WHERE id=1"
        )
    conn.commit()
    conn.close()


@pytest.fixture
def sample_backup_file(tmp_path: Path) -> Path:
    """A minimal real pg_dump custom-format file isn't easy to fake by hand
    (it's a binary format) -- so restore-specific tests build their own
    tiny real dump via pg_dump against a scratch schema. This fixture just
    reserves the path; see test_load.py::test_full_restore_cycle for the
    actual pg_dump bootstrap step."""
    return tmp_path / "sample.backup"