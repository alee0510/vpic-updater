"""
Shared PostgreSQL connection helpers.

Two logically separate connection targets are used throughout the
pipeline:
  - control DSN  -> the stable `vpic_meta` database (locking, promotion
                     pointer, update_history audit log)
  - target DSN   -> the Postgres server/instance where per-release
                     databases (vpic_2026_08, ...) are created and restored
"""

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PGConnection
from pydantic import BaseModel, SecretStr

logger = logging.getLogger("vpic_updater.db")


class DatabaseDSN(BaseModel):
    host: str
    port: int = 5432
    user: str
    password: SecretStr
    dbname: str

    model_config = {"frozen": True}

    def as_psycopg2_kwargs(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password.get_secret_value(),
            "dbname": self.dbname,
        }


def connect(dsn: DatabaseDSN, autocommit: bool = False) -> PGConnection:
    conn = psycopg2.connect(**dsn.as_psycopg2_kwargs())
    conn.autocommit = autocommit
    return conn


@contextmanager
def connection_scope(dsn: DatabaseDSN, autocommit: bool = False) -> Iterator[PGConnection]:
    """Context-managed connection that always closes, even on error.
    Does NOT auto-commit/rollback for you when autocommit=False --
    callers remain responsible for transaction boundaries."""
    conn = connect(dsn, autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()


def with_dbname(dsn: DatabaseDSN, dbname: str) -> DatabaseDSN:
    """Return a copy of `dsn` pointed at a different database on the same
    server -- used to connect into a freshly created vpic_{year_month} db
    without needing separate config entries per release."""
    return dsn.model_copy(update={"dbname": dbname})