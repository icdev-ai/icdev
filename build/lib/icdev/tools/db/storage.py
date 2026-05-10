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
    - LIKE → ILIKE (case-insensitive)
    - GROUP_CONCAT → string_agg
    - GLOB → ~ (regex)
    - last_insert_rowid() → lastval()

Configuration:
    ICDEV_STORAGE_BACKEND=postgresql|sqlite  (default: sqlite)
    ICDEV_PG_HOST=localhost
    ICDEV_PG_PORT=5432
    ICDEV_PG_USER=icdev
    ICDEV_PG_PASSWORD=...
    ICDEV_PG_DATABASE=icdev
    ICDEV_PG_SSLMODE=verify-full       (optional, for mTLS / IL5+)
    ICDEV_PG_SSLCERT=/path/client.crt  (optional, client cert for mTLS)
    ICDEV_PG_SSLKEY=/path/client.key   (optional, client key for mTLS)
    ICDEV_PG_SSLROOTCERT=/path/ca.crt  (optional, CA bundle)
    ICDEV_PG_SSLCRL=/path/crl.pem      (optional, revocation list)
    ICDEV_DB_PATH=data/icdev.db  (SQLite path)
    ICDEV_PG_NO_FALLBACK=true  (optional: crash instead of falling back to SQLite)

Usage:
    from tools.db.storage import get_connection
    conn = get_connection()
    rows = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchall()
    conn.commit()
    conn.close()

    # Works identically for both SQLite and PostgreSQL.
"""

from __future__ import annotations

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


def _default_db_path() -> str:
    # When running from an installed wheel, BASE_DIR lives under site-packages
    # (or sys.prefix). Writing the DB inside the package dir is undesirable —
    # read-only filesystems, stale data on upgrade, multiple projects sharing
    # state. Default to the user's cwd instead.
    import sys

    base = str(BASE_DIR).replace("\\", "/").lower()
    prefix = str(Path(sys.prefix).resolve()).replace("\\", "/").lower()
    if "site-packages" in base or (prefix and base.startswith(prefix)):
        return str(Path.cwd() / "data" / "icdev.db")
    return str(BASE_DIR / "data" / "icdev.db")


DB_PATH = os.environ.get("ICDEV_DB_PATH", _default_db_path())

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
    #    Exception: PRAGMA table_info(X) → information_schema query
    if _RE_PRAGMA.match(sql.strip()):
        pragma_ti = re.match(
            r"\s*PRAGMA\s+table_info\(\s*(\w+)\s*\)",
            sql,
            re.IGNORECASE,
        )
        if pragma_ti:
            table = pragma_ti.group(1)
            return (
                "SELECT ordinal_position AS cid, column_name AS name, "
                "data_type AS type, "
                "CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull, "
                "column_default AS dflt_value, 0 AS pk "
                "FROM information_schema.columns "
                f"WHERE table_schema = 'public' AND table_name = '{table}' "
                "ORDER BY ordinal_position"
            )
        return "SELECT 1"  # No-op for all other PRAGMAs

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
        sql = re.sub(r"\bBLOB\b", "BYTEA", sql)
        # DEFAULT CURRENT_TIMESTAMP is compatible in both
        # BOOLEAN is native in PG
        # TEXT, REAL, INTEGER are compatible

    # 9. Datetime expressions: leave as native PG timestamp (no ::text cast).
    #    PostgreSQL columns are proper TIMESTAMP type after migration from SQLite.
    #    NOW() and NOW() +/- INTERVAL compare natively with timestamp columns.
    #    Previous ::text cast broke timestamp comparisons (operator mismatch).

    # 10. LIKE → ILIKE (SQLite LIKE is case-insensitive; PG LIKE is case-sensitive)
    #     Only in DML contexts (not DDL, not inside string literals)
    if "CREATE TABLE" not in sql.upper() and "CREATE INDEX" not in sql.upper():
        sql = re.sub(r"\bLIKE\b", "ILIKE", sql, flags=re.IGNORECASE)
        # Also handle NOT LIKE → NOT ILIKE (already handled by the above since LIKE is replaced)

    # 11. GROUP_CONCAT → string_agg (PG equivalent)
    def _replace_group_concat(m):
        args = m.group(1)
        parts = [a.strip() for a in args.split(",", 1)]
        col = parts[0]
        sep = parts[1] if len(parts) > 1 else "','"
        return f"string_agg({col}::text, {sep})"

    sql = re.sub(r"\bGROUP_CONCAT\(([^)]+)\)", _replace_group_concat, sql, flags=re.IGNORECASE)

    # 12. GLOB → ~ (PG regex match) — SQLite GLOB uses * and ?
    #     col GLOB 'pattern' → col ~ 'pattern' (with * → .* and ? → . conversion)
    #     Note: This is a best-effort translation; complex GLOB patterns may need manual review
    sql = re.sub(r"\bGLOB\b", "~", sql, flags=re.IGNORECASE)

    # 13. last_insert_rowid() → lastval() (PG equivalent for last auto-generated ID)
    sql = re.sub(r"\blast_insert_rowid\(\)", "lastval()", sql, flags=re.IGNORECASE)

    # 14. sqlite_master → information_schema.tables / pg_tables
    #     SELECT ... FROM sqlite_master WHERE type='table' AND name=?
    #     → SELECT ... FROM information_schema.tables WHERE table_schema='public' AND table_name=%s
    if "sqlite_master" in sql.lower():
        # Pattern: SELECT 1/name/count(*) FROM sqlite_master WHERE type='table' AND name=?
        # Preserve the original SELECT clause so callers using count(*) for `... > 0`
        # checks keep getting an integer back (not a table_name string).
        def _master_named(m):
            select_clause = m.group(1).strip().lower()
            if "count" in select_clause:
                return (
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = %s"
                )
            return (
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s"
            )

        sql = re.sub(
            r"SELECT\s+(1|name|count\(\*\))\s+FROM\s+sqlite_master\s+"
            r"WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*%s",
            _master_named,
            sql,
            flags=re.IGNORECASE,
        )
        # Pattern: SELECT name FROM sqlite_master WHERE type='table'  (list all tables)
        sql = re.sub(
            r"SELECT\s+name\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'",
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'",
            sql,
            flags=re.IGNORECASE,
        )
        # Pattern: SELECT count(*) FROM sqlite_master WHERE type='table'
        sql = re.sub(
            r"SELECT\s+count\(\*\)\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'",
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'",
            sql,
            flags=re.IGNORECASE,
        )

    # 15. json_extract(col, '$.key') → col::jsonb->>'key'
    #     Handles: json_extract(col, '$.key'), json_extract(col, '$.nested.key')
    def _replace_json_extract(m):
        col = m.group(1).strip()
        path = m.group(2).strip().strip("'\"")
        # Remove leading '$.' from JSON path
        path = re.sub(r"^\$\.?", "", path)
        if "." in path:
            # Nested path: $.a.b → col::jsonb->'a'->>'b'
            parts = path.split(".")
            chain = "::jsonb"
            for part in parts[:-1]:
                chain += f"->'{part}'"
            chain += f"->>'{parts[-1]}'"
            return f"{col}{chain}"
        return f"{col}::jsonb->>'{path}'"

    sql = re.sub(
        r"\bjson_extract\(\s*([^,]+?)\s*,\s*(['\"][^'\"]+['\"])\s*\)",
        _replace_json_extract,
        sql,
        flags=re.IGNORECASE,
    )

    # 16. json_array_length(col) → jsonb_array_length(col::jsonb)
    sql = re.sub(
        r"\bjson_array_length\(\s*([^)]+)\s*\)",
        r"jsonb_array_length(\1::jsonb)",
        sql,
        flags=re.IGNORECASE,
    )

    # 17. julianday(X) → (EXTRACT(EPOCH FROM (X)::timestamptz) / 86400.0)
    #     SQLite julianday() returns days since the Julian epoch as a real number.
    #     The math (julianday(A) - julianday(B)) yields days; *86400 yields seconds.
    #     Both forms are preserved by translating each call site individually.
    #     'now' is recognized via the prior datetime('now') → NOW() rewrite, so
    #     we also handle bare julianday('now') and julianday(NOW()) here.
    def _replace_julianday(m):
        inner = m.group(1).strip()
        # Strip surrounding quotes from 'now' and replace with NOW()
        if inner.lower() in ("'now'", '"now"'):
            inner = "NOW()"
        return f"(EXTRACT(EPOCH FROM ({inner})::timestamptz) / 86400.0)"

    # Match julianday(...) where the argument has no nested parens.
    # Nested cases (e.g. julianday(datetime(col, '-1 hour'))) are handled
    # because datetime() is rewritten in rule 2-4 before we reach here.
    sql = re.sub(
        r"\bjulianday\s*\(\s*([^()]+?)\s*\)",
        _replace_julianday,
        sql,
        flags=re.IGNORECASE,
    )

    # 18. DEFAULT (strftime(..., 'now')) in DDL → DEFAULT NOW()
    #     Must run BEFORE the general strftime rule so DDL defaults
    #     get simplified to NOW() instead of to_char(NOW(), ...).
    sql = re.sub(
        r"DEFAULT\s+\(strftime\([^)]+,\s*'now'\)\)",
        "DEFAULT NOW()",
        sql,
        flags=re.IGNORECASE,
    )

    # 19. strftime(format, col) → to_char(col::timestamp, pg_format)
    #     SQLite strftime uses %Y, %m, %d, %H, %M, %S, %W, %w, %j
    #     PG to_char uses YYYY, MM, DD, HH24, MI, SS, IW, D, DDD
    _STRFTIME_MAP = {
        "%Y": "YYYY", "%m": "MM", "%d": "DD",
        "%H": "HH24", "%M": "MI", "%S": "SS",
        "%W": "IW", "%w": "D", "%j": "DDD",
        "%s": "epoch",
    }

    def _replace_strftime(m):
        fmt = m.group(1).strip().strip("'\"")
        col = m.group(2).strip()
        # Handle strftime('%s', col) → EXTRACT(EPOCH FROM col::timestamp)
        if fmt == "%s":
            if col.lower() in ("'now'", '"now"'):
                return "EXTRACT(EPOCH FROM NOW())"
            return f"EXTRACT(EPOCH FROM ({col})::timestamp)"
        # Handle 'now' as column
        if col.lower() in ("'now'", '"now"'):
            col = "NOW()"
        # Convert format string
        pg_fmt = fmt
        for sqlite_tok, pg_tok in _STRFTIME_MAP.items():
            pg_fmt = pg_fmt.replace(sqlite_tok, pg_tok)
        return f"to_char(({col})::timestamp, '{pg_fmt}')"

    sql = re.sub(
        r"\bstrftime\(\s*(['\"][^'\"]+['\"])\s*,\s*(.+?)\s*\)",
        _replace_strftime,
        sql,
        flags=re.IGNORECASE,
    )

    # 20. IFNULL(a, b) → COALESCE(a, b) (PG has no IFNULL)
    sql = re.sub(r"\bIFNULL\(", "COALESCE(", sql, flags=re.IGNORECASE)

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
def _pg_ssl_kwargs() -> dict:
    """Build psycopg2 SSL kwargs from ICDEV_PG_SSL* env vars.

    Supports mTLS for GovCloud/IL5/IL6 deployments. Any unset var is omitted
    so libpq falls back to its default (typically sslmode=prefer).

        ICDEV_PG_SSLMODE        disable|allow|prefer|require|verify-ca|verify-full
        ICDEV_PG_SSLCERT        path to client certificate (PEM)
        ICDEV_PG_SSLKEY         path to client private key (PEM)
        ICDEV_PG_SSLROOTCERT    path to CA bundle used to verify the server
        ICDEV_PG_SSLCRL         path to certificate revocation list (optional)
    """
    mapping = {
        "sslmode": "ICDEV_PG_SSLMODE",
        "sslcert": "ICDEV_PG_SSLCERT",
        "sslkey": "ICDEV_PG_SSLKEY",
        "sslrootcert": "ICDEV_PG_SSLROOTCERT",
        "sslcrl": "ICDEV_PG_SSLCRL",
    }
    return {k: os.environ[v] for k, v in mapping.items() if os.environ.get(v)}


def _get_pg_connection(db_url: str = None):
    """Create a PostgreSQL connection via psycopg2."""
    import psycopg2
    import psycopg2.extras

    ssl_kwargs = _pg_ssl_kwargs()
    if db_url:
        conn = psycopg2.connect(
            db_url, cursor_factory=psycopg2.extras.RealDictCursor, **ssl_kwargs
        )
    else:
        conn = psycopg2.connect(
            host=os.environ.get("ICDEV_PG_HOST", "localhost"),
            port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
            user=os.environ.get("ICDEV_PG_USER", "icdev"),
            password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
            dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
            cursor_factory=psycopg2.extras.RealDictCursor,
            **ssl_kwargs,
        )
    conn.autocommit = False
    return conn


def _get_sqlite_connection(db_path: str = None):
    """Create a SQLite connection with Row factory."""
    path = db_path or os.environ.get("ICDEV_DB_PATH", DB_PATH)
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
    If PostgreSQL is configured but unreachable, falls back to SQLite
    so that operations (task creation, notifications) are not silently
    lost during PG outages.

    Returns a StorageConnection wrapper that transparently handles
    SQL translation between SQLite and PostgreSQL.
    """
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()

    if backend == "postgresql":
        db_url = os.environ.get("ICDEV_DATABASE_URL")
        no_fallback = os.environ.get("ICDEV_PG_NO_FALLBACK", "").lower() in ("true", "1")
        try:
            raw_conn = _get_pg_connection(db_url)
            return StorageConnection(raw_conn, "postgresql")
        except Exception as exc:
            if no_fallback:
                raise ConnectionError(
                    f"PostgreSQL unavailable and ICDEV_PG_NO_FALLBACK=true — "
                    f"refusing to fall back to SQLite. Fix PG or unset the flag. "
                    f"Original error: {exc}"
                ) from exc
            import logging

            logging.getLogger(__name__).warning(
                "PostgreSQL unavailable (%s), falling back to SQLite",
                exc,
            )
            raw_conn = _get_sqlite_connection(db_path)
            return StorageConnection(raw_conn, "sqlite")
    else:
        raw_conn = _get_sqlite_connection(db_path)
        return StorageConnection(raw_conn, "sqlite")


def get_backend() -> str:
    """Return the current storage backend name."""
    return os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()


def is_pg(conn=None) -> bool:
    """Check if the current backend is PostgreSQL.

    Works with or without a connection object:
        - ``is_pg(conn)`` — checks the connection's ``_backend`` attribute
        - ``is_pg()`` — checks the ``ICDEV_STORAGE_BACKEND`` env var

    Use this instead of ad-hoc ``getattr(conn, '_backend', 'sqlite')`` checks.
    """
    if conn is not None:
        return getattr(conn, "_backend", "sqlite") == "postgresql"
    return get_backend() == "postgresql"


def sql_placeholder(conn=None) -> str:
    """Return the correct SQL placeholder for the current backend.

    PostgreSQL uses ``%s``, SQLite uses ``?``.
    """
    return "%s" if is_pg(conn) else "?"


def sql_now(conn=None) -> str:
    """Return the SQL expression for current timestamp per backend.

    PostgreSQL: ``NOW()``, SQLite: ``datetime('now')``.
    """
    return "NOW()" if is_pg(conn) else "datetime('now')"


def sql_date_sub(conn, days: int = 0, hours: int = 0, minutes: int = 0) -> str:
    """Return SQL expression for 'now minus offset' per backend.

    Examples:
        sql_date_sub(conn, days=7)     → "NOW() - INTERVAL '7 days'" (PG)
                                        → "datetime('now', '-7 days')" (SQLite)
        sql_date_sub(conn, minutes=10) → "NOW() - INTERVAL '10 minutes'" (PG)
                                        → "datetime('now', '-10 minutes')" (SQLite)
    """
    if days:
        unit, val = "days", days
    elif hours:
        unit, val = "hours", hours
    elif minutes:
        unit, val = "minutes", minutes
    else:
        return sql_now(conn)

    if is_pg(conn):
        return f"NOW() - INTERVAL '{val} {unit}'"
    return f"datetime('now', '-{val} {unit}')"


def sql_strftime(conn, fmt: str, col: str) -> str:
    """Return SQL expression for date formatting per backend.

    ``fmt`` uses SQLite-style format tokens (``%Y``, ``%m``, ``%d``, etc.).

    Examples:
        sql_strftime(conn, '%Y-%m', 'created_at')
        → "to_char((created_at)::timestamp, 'YYYY-MM')" (PG)
        → "strftime('%Y-%m', created_at)" (SQLite)
    """
    if is_pg(conn):
        _map = {
            "%Y": "YYYY", "%m": "MM", "%d": "DD",
            "%H": "HH24", "%M": "MI", "%S": "SS",
            "%W": "IW", "%w": "D", "%j": "DDD",
        }
        pg_fmt = fmt
        for s_tok, p_tok in _map.items():
            pg_fmt = pg_fmt.replace(s_tok, p_tok)
        return f"to_char(({col})::timestamp, '{pg_fmt}')"
    return f"strftime('{fmt}', {col})"


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
