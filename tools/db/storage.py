# CUI // SP-CTI
"""Storage abstraction layer (D-DB-21 through D-DB-25).

Dual-backend: PostgreSQL (primary) ↔ SQLite (fallback).
All tools call get_connection() — backend selected by env var.

The StorageConnection wrapper transparently translates SQLite SQL to
PostgreSQL SQL so existing code works without changes:
    - ? placeholder → %s
    - datetime('now') → NOW()
    - datetime('now', '-N days') → NOW() - INTERVAL 'N days'
    - INSERT OR REPLACE → INSERT ... ON CONFLICT DO UPDATE
    - INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    - PRAGMA → no-op (PG handles these natively)
    - INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY (DDL only)
    - conn.row_factory = sqlite3.Row → RealDictCursor (dict-like rows)

Configuration:
    ICDEV_STORAGE_BACKEND=postgresql|sqlite  (default: sqlite)
    ICDEV_PG_HOST=localhost
    ICDEV_PG_PORT=5432
    ICDEV_PG_USER=icdev
    ICDEV_PG_PASSWORD=...
    ICDEV_PG_DATABASE=icdev
    ICDEV_DB_PATH=data/icdev.db  (SQLite path)

Usage:
    from tools.db.storage import get_connection
    conn = get_connection()
    rows = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchall()
    conn.commit()
    conn.close()

    # Works identically for both SQLite and PostgreSQL.
"""

import os
import re
import sqlite3
from pathlib import Path

# Load .env if available (so ICDEV_STORAGE_BACKEND is picked up)
_BASE = Path(__file__).resolve().parent.parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(_BASE / ".env")
except ImportError:
    _env_file = _BASE / ".env"
    if _env_file.exists():
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))

# Backend detection
_BACKEND = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()

# ---------------------------------------------------------------------------
# SQL Translator — converts SQLite SQL to PostgreSQL SQL
# ---------------------------------------------------------------------------
# Regex patterns compiled once at module load
_RE_DATETIME_OFFSET = re.compile(
    r"datetime\(\s*'now'\s*,\s*'(-?\s*\d+)\s+"
    r"(days?|hours?|minutes?|seconds?)'\s*\)",
    re.IGNORECASE,
)
_RE_DATETIME_CONCAT = re.compile(
    r"datetime\(\s*'now'\s*,\s*'-'\s*\|\|\s*\?\s*\|\|\s*'\s*(days?|hours?|minutes?|seconds?)'\s*\)",
    re.IGNORECASE,
)
_RE_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
_RE_INSERT_OR_REPLACE = re.compile(
    r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_RE_INSERT_OR_IGNORE = re.compile(
    r"INSERT\s+OR\s+IGNORE\s+INTO",
    re.IGNORECASE,
)
_RE_PRAGMA = re.compile(r"^\s*PRAGMA\s+", re.IGNORECASE)
_RE_AUTOINCREMENT = re.compile(
    r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
    re.IGNORECASE,
)
_RE_CURRENT_TIMESTAMP_DEFAULT = re.compile(
    r"DEFAULT\s+\(datetime\('now'\)\)",
    re.IGNORECASE,
)


def translate_sql(sql: str, backend: str = "postgresql") -> str:
    """Translate SQLite SQL to PostgreSQL SQL.

    For SQLite backend, returns SQL unchanged.
    """
    if backend == "sqlite":
        return sql

    original = sql

    # 1. PRAGMA → skip entirely (return empty for PG)
    if _RE_PRAGMA.match(sql.strip()):
        return "SELECT 1"  # No-op query

    # 2. datetime('now', '-N days/hours') → NOW() - INTERVAL 'N days'
    def _replace_datetime_offset(m):
        num = m.group(1).replace(" ", "")
        unit = m.group(2)
        sign = "-" if num.startswith("-") else "+"
        num = num.lstrip("-+")
        return f"NOW() {sign} INTERVAL '{num} {unit}'"

    sql = _RE_DATETIME_OFFSET.sub(_replace_datetime_offset, sql)

    # 3. datetime('now', '-' || ? || ' days') → NOW() - (? || ' days')::INTERVAL
    def _replace_datetime_concat(m):
        unit = m.group(1)
        return f"NOW() - (CAST(%s || ' {unit}' AS INTERVAL))"

    sql = _RE_DATETIME_CONCAT.sub(_replace_datetime_concat, sql)

    # 4. datetime('now') → NOW()
    sql = _RE_DATETIME_NOW.sub("NOW()", sql)

    # 5. INSERT OR REPLACE → INSERT ... ON CONFLICT (first_col) DO UPDATE
    def _replace_insert_or_replace(m):
        table = m.group(1)
        cols_str = m.group(2)
        cols = [c.strip() for c in cols_str.split(",")]
        first_col = cols[0]
        update_cols = cols[1:] if len(cols) > 1 else cols
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        result = f"INSERT INTO {table} ({cols_str})"
        # We'll append ON CONFLICT after VALUES clause via post-processing
        return result + f" /*--ON_CONFLICT({first_col})|{set_clause}--*/"

    sql = _RE_INSERT_OR_REPLACE.sub(_replace_insert_or_replace, sql)

    # Post-process: move ON CONFLICT marker after VALUES(...)
    if "/*--ON_CONFLICT" in sql:
        conflict_match = re.search(r"/\*--ON_CONFLICT\((\w+)\)\|(.+?)--\*/", sql)
        if conflict_match:
            pk = conflict_match.group(1)
            set_clause = conflict_match.group(2)
            sql = sql.replace(conflict_match.group(0), "")
            # Find the end of the statement (before any trailing whitespace/semicolon)
            sql = sql.rstrip().rstrip(";")
            sql += f" ON CONFLICT ({pk}) DO UPDATE SET {set_clause}"  # nosec B608 — pk/set_clause from parsed SQL, not user input

    # 6. INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    sql = _RE_INSERT_OR_IGNORE.sub("INSERT INTO", sql)
    if "INSERT INTO" in sql and "ON CONFLICT" not in sql and "OR IGNORE" in original.upper():
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # 7. ? placeholder → %s
    sql = sql.replace("?", "%s")

    # 8. DDL translations (CREATE TABLE / CREATE INDEX)
    if "CREATE TABLE" in sql.upper() or "CREATE INDEX" in sql.upper():
        sql = _RE_AUTOINCREMENT.sub("SERIAL PRIMARY KEY", sql)
        sql = _RE_CURRENT_TIMESTAMP_DEFAULT.sub("DEFAULT NOW()", sql)
        # SQLite BLOB → PG BYTEA
        sql = re.sub(r'\bBLOB\b', 'BYTEA', sql)
        # DEFAULT CURRENT_TIMESTAMP is compatible in both
        # BOOLEAN is native in PG
        # TEXT, REAL, INTEGER are compatible

    # 9. Cast all datetime expressions to text for TEXT column comparisons
    #    SQLite stores dates as TEXT; PG NOW() returns timestamptz.
    #    Wrap datetime expressions in (...)::text so they compare with TEXT columns.
    if "NOW()" in sql and "CREATE TABLE" not in sql.upper():
        # Cast NOW() +/- INTERVAL '...' → (NOW() +/- INTERVAL '...')::text
        sql = re.sub(
            r"NOW\(\)\s*([-+])\s*INTERVAL\s*'([^']+)'",
            r"(NOW() \1 INTERVAL '\2')::text",
            sql,
        )
        # Cast NOW() - (CAST(... AS INTERVAL)) → (NOW() - ...)::text
        sql = re.sub(
            r"NOW\(\)\s*([-+])\s*(\(CAST\([^)]+\)\s*AS\s+INTERVAL\))",
            r"(NOW() \1 \2)::text",
            sql,
        )
        # Cast standalone NOW() → NOW()::text (not already cast, not in arithmetic)
        sql = re.sub(
            r"NOW\(\)(?!::text)(?!\s*[-+])",
            "NOW()::text",
            sql,
        )
        # Don't cast in DEFAULT clauses
        sql = sql.replace("DEFAULT NOW()::text", "DEFAULT NOW()")
        sql = re.sub(
            r"DEFAULT\s+\(NOW\(\)[^)]*\)::text",
            lambda m: m.group(0).replace("::text", ""),
            sql,
        )

    return sql


# ---------------------------------------------------------------------------
# Row wrapper — makes PG dict rows behave like sqlite3.Row
# ---------------------------------------------------------------------------
class DictRow:
    """Wrapper that supports both dict-style and index-style access.

    Makes psycopg2 RealDictRow behave like sqlite3.Row for existing code.
    """

    __slots__ = ("_data", "_keys")

    def __init__(self, data: dict):
        self._data = data
        self._keys = list(data.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._data[self._keys[key]]
        return self._data[key]

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return iter(self._data.values())

    def __len__(self):
        return len(self._data)

    def keys(self):
        return self._keys

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __repr__(self):
        return f"DictRow({self._data})"


# ---------------------------------------------------------------------------
# Cursor wrapper — translates SQL and wraps results
# ---------------------------------------------------------------------------
class StorageCursor:
    """Wraps a database cursor to translate SQL and normalize results."""

    def __init__(self, cursor, backend: str):
        self._cursor = cursor
        self._backend = backend

    def execute(self, sql: str, params=None):
        sql = translate_sql(sql, self._backend)
        if sql.strip() == "SELECT 1" and params:
            params = None  # PRAGMA no-op doesn't need params
        if params is None:
            self._cursor.execute(sql)
        else:
            if isinstance(params, (list, tuple)):
                self._cursor.execute(sql, params)
            else:
                self._cursor.execute(sql, (params,))
        return self

    def executemany(self, sql: str, params_list):
        sql = translate_sql(sql, self._backend)
        self._cursor.executemany(sql, params_list)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if self._backend == "postgresql" and isinstance(row, dict):
            return DictRow(row)
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        if self._backend == "postgresql":
            return [DictRow(r) if isinstance(r, dict) else r for r in rows]
        return rows

    def fetchmany(self, size=None):
        rows = self._cursor.fetchmany(size) if size else self._cursor.fetchmany()
        if self._backend == "postgresql":
            return [DictRow(r) if isinstance(r, dict) else r for r in rows]
        return rows

    @property
    def lastrowid(self):
        return getattr(self._cursor, "lastrowid", None)

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        self._cursor.close()

    def __iter__(self):
        return iter(self.fetchall())


# ---------------------------------------------------------------------------
# Connection wrapper — the main abstraction
# ---------------------------------------------------------------------------
class StorageConnection:
    """Wraps sqlite3.Connection or psycopg2.connection transparently.

    Provides execute(), commit(), close(), context manager, and
    executescript() that work identically for both backends.
    """

    def __init__(self, conn, backend: str):
        self._conn = conn
        self._backend = backend

    def execute(self, sql: str, params=None):
        """Execute SQL with automatic translation."""
        if self._backend == "postgresql":
            cursor = self._conn.cursor()
            sc = StorageCursor(cursor, self._backend)
            return sc.execute(sql, params)
        else:
            # SQLite — use native execute
            translated = translate_sql(sql, "sqlite")  # No-op for sqlite
            if params is None:
                return self._conn.execute(translated)
            return self._conn.execute(translated, params)

    def executemany(self, sql: str, params_list):
        if self._backend == "postgresql":
            cursor = self._conn.cursor()
            sc = StorageCursor(cursor, self._backend)
            return sc.executemany(sql, params_list)
        return self._conn.executemany(sql, params_list)

    def executescript(self, sql: str):
        """Execute multiple SQL statements.

        SQLite has native executescript(). For PG, split on ; and execute each.
        """
        if self._backend == "sqlite":
            return self._conn.executescript(sql)

        # PostgreSQL: split statements and execute each
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        cursor = self._conn.cursor()
        for stmt in statements:
            if not stmt:
                continue
            translated = translate_sql(stmt, self._backend)
            if translated.strip() and translated.strip() != "SELECT 1":
                try:
                    cursor.execute(translated)
                except Exception:
                    # Skip DDL errors (table already exists, etc.)
                    self._conn.rollback()
                    # Re-establish transaction
                    pass
        self._conn.commit()
        return cursor

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def cursor(self):
        if self._backend == "postgresql":
            return StorageCursor(self._conn.cursor(), self._backend)
        return self._conn.cursor()

    @property
    def row_factory(self):
        if self._backend == "sqlite":
            return self._conn.row_factory
        return None

    @row_factory.setter
    def row_factory(self, value):
        if self._backend == "sqlite":
            self._conn.row_factory = value
        # PG uses RealDictCursor — row_factory is a no-op

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------
def _get_pg_connection(db_url: str = None):
    """Create a PostgreSQL connection via psycopg2."""
    import psycopg2
    import psycopg2.extras

    if db_url:
        conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        conn = psycopg2.connect(
            host=os.environ.get("ICDEV_PG_HOST", "localhost"),
            port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
            user=os.environ.get("ICDEV_PG_USER", "icdev"),
            password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
            dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    conn.autocommit = False
    return conn


def _get_sqlite_connection(db_path: str = None):
    """Create a SQLite connection with Row factory."""
    path = db_path or DB_PATH
    # Ensure parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_connection(db_path: str = None) -> StorageConnection:
    """Return a StorageConnection for the configured backend.

    For SQLite: uses db_path or ICDEV_DB_PATH env var.
    For PostgreSQL: uses ICDEV_PG_* env vars or ICDEV_DATABASE_URL.

    Returns a StorageConnection wrapper that transparently handles
    SQL translation between SQLite and PostgreSQL.
    """
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()

    if backend == "postgresql":
        db_url = os.environ.get("ICDEV_DATABASE_URL")
        raw_conn = _get_pg_connection(db_url)
        return StorageConnection(raw_conn, "postgresql")
    else:
        raw_conn = _get_sqlite_connection(db_path)
        return StorageConnection(raw_conn, "sqlite")


def get_backend() -> str:
    """Return the current storage backend name."""
    return os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
def health_check() -> dict:
    """Check backend connectivity and return status."""
    backend = get_backend()
    try:
        conn = get_connection()
        try:
            result = conn.execute("SELECT 1").fetchone()
            return {
                "status": "healthy",
                "backend": backend,
                "connected": result is not None,
            }
        finally:
            conn.close()
    except Exception as e:
        return {
            "status": "unhealthy",
            "backend": backend,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Storage Layer (D-DB-21)")
    parser.add_argument("--health", action="store_true", help="Check backend health")
    parser.add_argument("--info", action="store_true", help="Show backend config")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = health_check()
        print(json.dumps(result, indent=2) if args.json else f"{result['backend']}: {result['status']}")
    elif args.info:
        info = {
            "backend": get_backend(),
            "sqlite_path": DB_PATH,
            "pg_host": os.environ.get("ICDEV_PG_HOST", "localhost"),
            "pg_port": os.environ.get("ICDEV_PG_PORT", "5432"),
            "pg_database": os.environ.get("ICDEV_PG_DATABASE", "icdev"),
        }
        print(json.dumps(info, indent=2) if args.json else str(info))
