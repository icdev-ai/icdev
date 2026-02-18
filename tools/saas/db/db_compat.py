#!/usr/bin/env python3
"""ICDEV SaaS - Database Compatibility Layer.
CUI // SP-CTI

Provides a unified interface for both SQLite and PostgreSQL connections.
Tools can use this adapter to work with either backend transparently.
"""

import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.db.compat")

SAAS_MODE = os.environ.get("ICDEV_SAAS_MODE", "false").lower() in ("true", "1", "yes")


class DBAdapter:
    """Wraps either SQLite or PostgreSQL connection with a unified interface."""

    def __init__(self, conn: Any, engine: str = "sqlite"):
        self._conn = conn
        self.engine = engine

    @property
    def connection(self) -> Any:
        """Return the underlying raw database connection."""
        return self._conn

    @staticmethod
    def _translate_sql(sql: str) -> str:
        """Translate SQLite SQL to PostgreSQL SQL."""
        sql = sql.replace("?", "%s")
        sql = sql.replace("datetime('now')", "now()")
        return sql

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute SQL with automatic placeholder translation."""
        if self.engine == "postgresql":
            sql = self._translate_sql(sql)
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        return cursor

    def executemany(self, sql: str, params_list: list) -> Any:
        """Execute SQL for each parameter set."""
        if self.engine == "postgresql":
            sql = self._translate_sql(sql)
        cursor = self._conn.cursor()
        cursor.executemany(sql, params_list)
        return cursor

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Execute a query and return a single row as a dict."""
        cursor = self.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        if self.engine == "sqlite":
            return dict(row)
        return dict(row)

    def fetchall(self, sql: str, params: tuple = ()) -> List[dict]:
        """Execute a query and return all rows as a list of dicts."""
        cursor = self.execute(sql, params)
        rows = cursor.fetchall()
        if self.engine == "sqlite":
            return [dict(r) for r in rows]
        return [dict(r) for r in rows]

    def commit(self) -> None:
        """Commit the current transaction."""
        self._conn.commit()

    def rollback(self) -> None:
        """Roll back the current transaction."""
        self._conn.rollback()

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> "DBAdapter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()

    def __repr__(self) -> str:
        return f"<DBAdapter engine={self.engine}>"


def get_sqlite_connection(db_path: str) -> DBAdapter:
    """Create a SQLite connection wrapped in DBAdapter."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return DBAdapter(conn, engine="sqlite")


def get_pg_connection(db_url: str) -> DBAdapter:
    """Create a PostgreSQL connection wrapped in DBAdapter."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise ImportError(
            "psycopg2 is required for PostgreSQL connections. "
            "Install with: pip install psycopg2-binary"
        )
    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    return DBAdapter(conn, engine="postgresql")


def get_connection(
    db_path: Optional[str] = None,
    db_url: Optional[str] = None,
) -> DBAdapter:
    """Get a database connection - auto-detects engine."""
    if db_url:
        return get_pg_connection(db_url)

    if db_path:
        return get_sqlite_connection(db_path)

    if SAAS_MODE:
        tenant_url = os.environ.get("TENANT_DB_URL")
        if tenant_url:
            return get_pg_connection(tenant_url)

    default_path = str(BASE_DIR / "data" / "icdev.db")
    return get_sqlite_connection(default_path)
