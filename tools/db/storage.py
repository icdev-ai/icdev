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
    - %s → ? (reverse, for SQLite fallback when runtime uses psycopg2 style)

Configuration:
    ICDEV_STORAGE_BACKEND=postgresql|sqlite  (default: postgresql)
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
    rows = conn.execute("SELECT * FROM projects WHERE id = %s", (pid,)).fetchall()
    conn.commit()
    conn.close()

    # Works identically for both SQLite and PostgreSQL.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

#: Tables whose column-masking failure has already been reported (warn once each).
_MASK_FAILURE_WARNED: set[str] = set()
from typing import Any, Optional

from tools.config.core_profile import profile_default

# Regex for table name extraction used by column-level masking.
_RE_FROM_TABLE = re.compile(r"\bFROM\b\s+([\w\"\.]+)", re.IGNORECASE)
_RE_UPDATE_TABLE = re.compile(r"\bUPDATE\b\s+([\w\"\.]+)", re.IGNORECASE)


def _extract_table_name(sql: str) -> Optional[str]:
    """Return the primary table name from a SELECT or UPDATE statement."""
    m = _RE_FROM_TABLE.search(sql) or _RE_UPDATE_TABLE.search(sql)
    if m:
        return m.group(1).strip('"').split(".")[-1]
    return None

# Load .env if available (so ICDEV_STORAGE_BACKEND is picked up)
_BASE = Path(__file__).resolve().parent
while _BASE.name in ("db", "tools", "icdev"):
    _BASE = _BASE.parent
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

BASE_DIR = _BASE


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

# Backend detection — PostgreSQL is the primary backend (PG-primary policy).
# SQLite is an init-only fallback used when PG is unreachable or explicitly pinned.
# Respect the active core profile if no explicit env var is set.
_BACKEND = profile_default("ICDEV_STORAGE_BACKEND", "postgresql").lower()

# ---------------------------------------------------------------------------
# Audit logging flags — disabled by default (overhead on every query).
# Enable per-layer: ICDEV_AUDIT_RLS=1, ICDEV_AUDIT_COLUMN=1
# Or override in tests: import tools.db.storage as s; s.AUDIT_RLS = True
# ---------------------------------------------------------------------------
AUDIT_RLS = os.environ.get("ICDEV_AUDIT_RLS", "").lower() in ("1", "true", "yes")
AUDIT_COLUMN = os.environ.get("ICDEV_AUDIT_COLUMN", "").lower() in ("1", "true", "yes")

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

# FTS5 virtual table registry: table_name → list of full-text columns.
# Used by is_fts5_query() and the translate_sql() FTS5 rules.
FTS5_TABLES: dict[str, list[str]] = {
    "memory_fts": ["content", "type", "tags"],
}


def is_fts5_query(sql: str) -> bool:
    """Return True if SQL contains an FTS5 MATCH clause."""
    return bool(re.search(r"\bMATCH\b", sql, re.IGNORECASE))


def get_fts5_tables() -> dict[str, list[str]]:
    """Return a copy of the FTS5 table registry."""
    return dict(FTS5_TABLES)


def _translate_pg_to_sqlite(sql: str) -> str:
    """Translate PostgreSQL-style %s placeholders to SQLite-style ?.

    Only converts bare %s parameter placeholders outside SQL string literals,
    identifiers, and comments. This lets runtime modules write psycopg2-native
    %s placeholders while keeping the SQLite fallback functional.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # Single-quoted SQL string literal (handle '' escape)
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
            continue
        # Double-quoted SQL identifier (handle "" escape)
        if ch == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if j + 1 < n and sql[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
            continue
        # Line comment
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = i + 2
            while j < n and sql[j] != "\n":
                j += 1
            out.append(sql[i:j])
            i = j
            continue
        # Block comment
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = i + 2
            while j < n - 1 and not (sql[j] == "*" and sql[j + 1] == "/"):
                j += 1
            out.append(sql[i : j + 2])
            i = j + 2
            continue
        # Bare %s parameter placeholder
        if ch == "%" and i + 1 < n and sql[i + 1] == "s":
            out.append("?")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def translate_sql(sql: str, backend: str = "postgresql") -> str:
    """Translate SQL between SQLite and PostgreSQL dialects.

    PostgreSQL path: SQLite → PostgreSQL (?, datetime(), etc.).
    SQLite path:    PostgreSQL → SQLite (%s → ?).
    """
    if backend == "sqlite":
        return _translate_pg_to_sqlite(sql)

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
            _col_query = (
                "SELECT ordinal_position AS cid, column_name AS name, "  # nosec B608 — translate_sql() is a pure syntax translator, never executes SQL; table from \w+ regex (word chars only)
                "data_type AS type, "
                "CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull, "
                "column_default AS dflt_value, 0 AS pk "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = '__TNAME__' "
                "ORDER BY ordinal_position"
            ).replace("__TNAME__", table)
            return _col_query
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

    # 7. Escape bare % inside SQL string literals to %% so psycopg2 does not
    #    misinterpret them as format specifiers (e.g. '%aadc%' in a LIKE clause).
    #    Must happen before ? → %s so we don't accidentally double-escape params.
    def _escape_pct_in_literals(s: str) -> str:
        out: list[str] = []
        in_str = False
        i = 0
        while i < len(s):
            ch = s[i]
            if not in_str:
                if ch == "'":
                    in_str = True
                out.append(ch)
            else:
                if ch == "'":
                    # SQL '' escape sequence stays inside the literal
                    if i + 1 < len(s) and s[i + 1] == "'":
                        out.append("''")
                        i += 1
                    else:
                        in_str = False
                        out.append(ch)
                elif ch == "%":
                    out.append("%%")
                else:
                    out.append(ch)
            i += 1
        return "".join(out)

    sql = _escape_pct_in_literals(sql)

    # 7b. ? placeholder → %s
    # WARNING: this translation masks source code that uses SQLite-style ?
    # placeholders in runtime modules. Log so the silent translation is
    # visible in server logs and doesn't become a hidden load-bearing shim.
    if "?" in sql:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "translate_sql: bare ? placeholder detected in SQL — use %%s for "
            "psycopg2 directly. SQL (first 120 chars): %.120s", sql
        )
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

    # 8b. ALTER TABLE ... ADD COLUMN  →  ADD COLUMN IF NOT EXISTS (PG 9.6+).
    #     Migrations add columns idempotently but guard only
    #     sqlite3.OperationalError ("duplicate column"), which does NOT catch
    #     PostgreSQL's DuplicateColumn. Making ADD COLUMN idempotent at the
    #     dialect layer fixes this uniformly across every migration instead of
    #     patching each one's exception handling.
    if "ALTER TABLE" in sql.upper() and "ADD COLUMN" in sql.upper():
        sql = re.sub(
            r"(ADD\s+COLUMN\s+)(?!IF\s+NOT\s+EXISTS\b)",
            r"\1IF NOT EXISTS ",
            sql,
            flags=re.IGNORECASE,
        )

    # 8c. ALTER TABLE <t>  →  ALTER TABLE IF EXISTS <t> (PG 9.x+).
    #     The migration chain has historical ordering assumptions where some
    #     ALTERs reference a table created by a *later* migration (or by an
    #     older init script). IF EXISTS turns those into safe no-ops on a fresh
    #     PostgreSQL replay instead of "relation does not exist" failures.
    #     (Genuinely-required base tables are created explicitly by their
    #     migrations; this only tolerates out-of-order incremental ALTERs.)
    if re.match(r"\s*ALTER\s+TABLE\s+", sql, re.IGNORECASE) and not re.match(
        r"\s*ALTER\s+TABLE\s+IF\s+EXISTS", sql, re.IGNORECASE
    ):
        sql = re.sub(
            r"(\bALTER\s+TABLE\s+)", r"\1IF EXISTS ", sql, count=1, flags=re.IGNORECASE
        )

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
        #
        # The trailing ``(?!\s+AND\b)`` negative lookahead stops this list-all
        # regex from PREFIX-matching a literal-name query such as
        # ``... WHERE type='table' AND name='foo'``. The named-form regex above
        # handles only the parameterised ``AND name=%s`` shape, so without the
        # lookahead the list-all rule rewrote just the prefix and left a dangling
        # ``AND name='foo'`` that references information_schema's nonexistent
        # ``name`` column (invalid PG). With the lookahead a query carrying an
        # extra ``AND`` condition is left ALONE rather than mangled, while the
        # legitimate list-all shapes (bare, or followed by ORDER BY/GROUP BY)
        # still translate. See tests/test_translate_sql_rule14.py.
        sql = re.sub(
            r"SELECT\s+name\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'(?!\s+AND\b)",
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'",
            sql,
            flags=re.IGNORECASE,
        )
        # Pattern: SELECT count(*) FROM sqlite_master WHERE type='table'
        #          (same anti-prefix-match lookahead as the list-all name shape)
        sql = re.sub(
            r"SELECT\s+count\(\*\)\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'(?!\s+AND\b)",
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

    # 16. json_array_length(X) → jsonb_array_length((X)::jsonb)
    #     X is parenthesized before the ::jsonb cast because :: binds tighter
    #     than ->/->>. When X is a translated json_extract (rule 15 →
    #     graph_json::jsonb->>'nodes'), an un-parenthesized cast would parse as
    #     graph_json::jsonb ->> ('nodes'::jsonb), and 'nodes'::jsonb is invalid
    #     JSON ("invalid input syntax for type json") — the network home/project
    #     500s. (X)::jsonb casts the whole extracted text value instead.
    sql = re.sub(
        r"\bjson_array_length\(\s*([^)]+)\s*\)",
        r"jsonb_array_length((\1)::jsonb)",
        sql,
        flags=re.IGNORECASE,
    )

    # 16b. json_each(col, '$.path') → jsonb_array_elements((col::jsonb)->'path')
    #      SQLite json_each() is a table-valued function that expands a JSON
    #      array into rows; PG's equivalent over a nested array is
    #      jsonb_array_elements() applied to the extracted sub-document. There
    #      was previously NO rule, so json_each() passed through verbatim and
    #      broke on PG. Nested paths ($.a.b) chain -> operators; the no-path
    #      form (json_each(col)) expands the column itself.
    def _replace_json_each(m):
        col = m.group(1).strip()
        raw_path = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
        path = (raw_path or "").strip().strip("'\"")
        path = re.sub(r"^\$\.?", "", path)
        chain = f"({col}::jsonb)"
        if path:
            for part in path.split("."):
                chain += f"->'{part}'"
        return f"jsonb_array_elements({chain})"

    # Two-arg form: json_each(col, '$.path')
    sql = re.sub(
        r"\bjson_each\(\s*([^,]+?)\s*,\s*(['\"][^'\"]+['\"])\s*\)",
        _replace_json_each,
        sql,
        flags=re.IGNORECASE,
    )
    # One-arg form: json_each(col)
    sql = re.sub(
        r"\bjson_each\(\s*([^,()]+?)\s*\)",
        _replace_json_each,
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

    # 21. CREATE VIRTUAL TABLE t USING fts5(col1, col2) → PG table with tsvector column
    if "CREATE VIRTUAL TABLE" in sql.upper():
        def _fts5_to_pg_table(m):
            table = m.group(1).strip()
            cols_raw = m.group(2)
            col_defs = []
            for raw_col in cols_raw.split(","):
                raw_col = raw_col.strip()
                if "=" in raw_col or raw_col.lower().startswith("tokenize"):
                    continue  # Skip FTS5 options
                col_name = re.split(r"\s+", raw_col)[0].strip("\"'")
                if col_name:
                    col_defs.append(f"    {col_name} TEXT")
            col_defs.append("    ts_doc TSVECTOR")
            return f"CREATE TABLE IF NOT EXISTS {table} (\n{chr(10).join(col_defs)}\n)"

        sql = re.sub(
            r"CREATE\s+VIRTUAL\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+USING\s+fts5\s*\(([^)]+)\)",
            _fts5_to_pg_table,
            sql,
            flags=re.IGNORECASE,
        )

    # 22. FTS5 MATCH translations
    if is_fts5_query(sql):
        # 22a. col MATCH 'term' / col MATCH "term" → table.col <@> BM25Query('term')
        #      Resolves col to parent table via FTS5_TABLES registry; falls back to col name.
        #      Skips table-level MATCH (where identifier is a table key in FTS5_TABLES) — those
        #      go to rule 22b which uses plainto_tsquery.
        _fts5_table_names = {t.lower() for t in FTS5_TABLES}

        def _fts5_bm25_match(m):
            col = m.group(1)
            term = m.group(2)
            # Table-level MATCH: leave unchanged so rule 22b can translate it
            if col.lower() in _fts5_table_names:
                return m.group(0)
            table = col
            for tbl, cols in FTS5_TABLES.items():
                if col in cols:
                    table = tbl
                    break
            quoted_term = "'" + term.replace("'", "''") + "'"  # nosec B608
            return table + "." + col + " <@> BM25Query(" + quoted_term + ")"

        sql = re.sub(
            r'(\w+)\s+MATCH\s+[\'"]([^\'"]+)[\'"]',
            _fts5_bm25_match,
            sql,
            flags=re.IGNORECASE,
        )

        # 22b. table MATCH 'query' / table MATCH %s → ts_doc @@ plainto_tsquery(...)
        #      plainto_tsquery is used (not to_tsquery) so embedded literals are safe — B608 nosec
        def _fts5_match(m):
            query_part = m.group(1).strip()
            # nosec B608 — query_part is either %s (parameterized) or a literal
            # from developer-written SQL; plainto_tsquery treats it as plain text
            return f"ts_doc @@ plainto_tsquery('english', {query_part})"  # nosec B608

        sql = re.sub(
            r"\b\w+\s+MATCH\s+(%s|'[^']*'|\"[^\"]*\")",
            _fts5_match,
            sql,
            flags=re.IGNORECASE,
        )

    # 23. snippet(table, col_idx, start, end, ...) → ts_headline(...)
    #     highlight(table, col_idx, start, end) → ts_headline(...)
    if re.search(r"\bsnippet\s*\(", sql, re.IGNORECASE):
        def _snippet_to_pg(m):
            parts = [p.strip() for p in m.group(1).split(",")]
            start_sel = parts[2].strip("'\"") if len(parts) > 2 else "<b>"
            end_sel = parts[3].strip("'\"") if len(parts) > 3 else "</b>"
            return f"ts_headline('english', content, query, 'StartSel={start_sel}, StopSel={end_sel}, MaxFragments=1')"

        sql = re.sub(r"\bsnippet\s*\(([^)]+)\)", _snippet_to_pg, sql, flags=re.IGNORECASE)

    if re.search(r"\bhighlight\s*\(", sql, re.IGNORECASE):
        def _highlight_to_pg(m):
            parts = [p.strip() for p in m.group(1).split(",")]
            start_sel = parts[2].strip("'\"") if len(parts) > 2 else "<b>"
            end_sel = parts[3].strip("'\"") if len(parts) > 3 else "</b>"
            return f"ts_headline('english', content, query, 'StartSel={start_sel}, StopSel={end_sel}')"

        sql = re.sub(r"\bhighlight\s*\(([^)]+)\)", _highlight_to_pg, sql, flags=re.IGNORECASE)

    # 24. ORDER BY rank (FTS5 BM25, negative = better) → ORDER BY rank DESC after translation
    #     FTS5 rank is negative; ts_rank() is positive. Flip direction on translated queries.
    if is_fts5_query(original) and re.search(r"\bORDER\s+BY\s+rank\b", sql, re.IGNORECASE):
        sql = re.sub(
            r"\bORDER\s+BY\s+rank\b(?!\s+DESC)(?!\s+ASC)",
            "ORDER BY rank DESC",
            sql,
            flags=re.IGNORECASE,
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
# Audit write helpers — fire-and-forget via raw sqlite3 (no recursion risk)
# ---------------------------------------------------------------------------

def _write_rls_audit(table_name: str, tenant_id: Optional[str]) -> None:
    """Append one row to rls_audit. Never raises — failures are silently dropped.

    Placeholders are `?`, not `%s`: this opens a RAW ``sqlite3`` connection and
    bypasses ``translate_sql`` entirely, so it must speak sqlite's dialect. With
    `%s` every insert raised, the bare ``except`` below swallowed it, and **no
    RLS audit record was ever written** — an audit trail that reported nothing
    while appearing to be enabled. NIST AU expects the opposite failure mode.
    """
    try:
        import sqlite3 as _sq
        from datetime import datetime, timezone
        _ac = _sq.connect(os.environ.get("ICDEV_DB_PATH", DB_PATH), timeout=5)
        _ac.execute(
            "INSERT INTO rls_audit (table_name, action, tenant_id, details, recorded_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (table_name, "rls_filter", tenant_id, "{}", datetime.now(timezone.utc).isoformat()),
        )
        _ac.commit()
        _ac.close()
    except Exception:
        pass


def _write_column_audit(table_name: str, role: str, masked_cols: list) -> None:
    """Append one row to column_mask_audit. Never raises.

    Raw ``sqlite3`` connection, so `?` placeholders — see _write_rls_audit for
    why `%s` here silently produced an empty audit table.
    """
    try:
        import sqlite3 as _sq
        import json as _js
        from datetime import datetime, timezone
        _ac = _sq.connect(os.environ.get("ICDEV_DB_PATH", DB_PATH), timeout=5)
        _ac.execute(
            "INSERT INTO column_mask_audit (table_name, role, masked_columns, recorded_at)"
            " VALUES (?, ?, ?, ?)",
            (table_name, role, _js.dumps(masked_cols), datetime.now(timezone.utc).isoformat()),
        )
        _ac.commit()
        _ac.close()
    except Exception:
        pass


def _pg_exec_statements(cursor, sql: str, backend: str) -> None:
    """Execute a multi-statement script on PostgreSQL, statement-isolated.

    Each statement runs inside its own SAVEPOINT so one failing statement
    (already-exists DDL, an out-of-order reference, or an untranslatable
    construct) only rolls back ITSELF — never the objects created earlier in
    the script. A bare conn.rollback() here would discard the whole schema and
    cascade "relation ... does not exist" into every dependent statement.
    Comment-only / empty chunks are skipped, and autocommit connections (where
    SAVEPOINT is unavailable) fall back to direct per-statement execution.
    """
    def _is_empty(s: str) -> bool:
        # Strip -- line and /* */ block comments; whitespace/comment-only chunks
        # are not executable statements (PG raises "can't execute an empty query").
        no_line = re.sub(r"--[^\n]*", "", s)
        no_block = re.sub(r"/\*.*?\*/", "", no_line, flags=re.DOTALL)
        return not no_block.strip()

    # Strip -- line comments BEFORE splitting on ';' — a ';' inside a comment
    # would otherwise corrupt the split into a bogus "statement" (prose) whose
    # syntax error would skip a real CREATE/ALTER that follows it on the line.
    for raw in _strip_sql_line_comments(sql).split(";"):
        stmt = raw.strip()
        if not stmt or _is_empty(stmt):
            continue
        translated = translate_sql(stmt, backend)
        if _is_empty(translated) or translated.strip() == "SELECT 1":
            continue
        # Isolate each statement in a SAVEPOINT so one failure (already-exists
        # DDL, an out-of-order reference, an untranslatable construct) cannot
        # roll back the objects created earlier in the script. If the connection
        # is in autocommit mode SAVEPOINT raises ("can only be used in
        # transaction blocks") — then run the statement directly (each is its
        # own txn, so a failure cannot poison the next).
        use_sp = True
        try:
            cursor.execute("SAVEPOINT icdev_es_stmt")
        except Exception:
            use_sp = False
        try:
            cursor.execute(translated)
            if use_sp:
                cursor.execute("RELEASE SAVEPOINT icdev_es_stmt")
        except Exception:  # noqa: BLE001 — skip; keep prior successful statements
            if use_sp:
                try:
                    cursor.execute("ROLLBACK TO SAVEPOINT icdev_es_stmt")
                    cursor.execute("RELEASE SAVEPOINT icdev_es_stmt")
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Cursor wrapper — translates SQL and wraps results
# ---------------------------------------------------------------------------
class StorageCursor:
    """Wraps a database cursor to translate SQL and normalize results."""

    def __init__(self, cursor, backend: str):
        self._cursor = cursor
        self._backend = backend
        self._table_name: Optional[str] = None

    def execute(self, sql: str, params=None):
        self._table_name = _extract_table_name(sql)
        sql, params = self._inject_rls(sql, params)
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

    def executescript(self, sql: str):
        """Run multiple statements (SQLite executescript parity).

        Some migrations call cursor.executescript(...). SQLite cursors have
        this natively; psycopg2 cursors do not. On PostgreSQL, split on ';'
        and run each statement in its own SAVEPOINT so a single failing
        statement does not abort the whole transaction (mirrors
        StorageConnection.executescript). Commit is left to the caller.
        """
        if self._backend == "sqlite":
            return self._cursor.executescript(sql)
        _pg_exec_statements(self._cursor, sql, self._backend)
        return self

    def executemany(self, sql: str, params_list):
        self._table_name = _extract_table_name(sql)
        # Inject RLS once — modified SQL + the extra predicate params.
        # params=None so _inject_rls returns only the RLS extra tuple.
        modified_sql, rls_params = self._inject_rls(sql, None)
        modified_sql = translate_sql(modified_sql, self._backend)

        if rls_params:
            from tools.security.row_security import _RE_UPDATE, _RE_DELETE
            is_write = bool(_RE_UPDATE.match(sql) or _RE_DELETE.match(sql))
            if is_write:
                # UPDATE/DELETE: RLS params go at the END (after SET + WHERE slots)
                augmented = [tuple(row) + tuple(rls_params) for row in params_list]
            else:
                # SELECT (rare in executemany): RLS params go at the START
                augmented = [tuple(rls_params) + tuple(row) for row in params_list]
            self._cursor.executemany(modified_sql, augmented)
        else:
            self._cursor.executemany(modified_sql, params_list)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        row = self._apply_column_masking(row)
        self._maybe_audit_column_mask()
        if self._backend == "postgresql" and isinstance(row, dict):
            return DictRow(row)
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        rows = [self._apply_column_masking(r) for r in rows]
        if rows:
            self._maybe_audit_column_mask()
        if self._backend == "postgresql":
            return [DictRow(r) if isinstance(r, dict) else r for r in rows]
        return rows

    def _apply_column_masking(self, row: Any) -> Any:
        """Apply column-level masking when a security context and table name are set."""
        ctx = getattr(self, "_security_context", None)
        if not ctx or not self._table_name:
            return row
        role = getattr(ctx, "role", "")
        if not role:
            return row
        try:
            from tools.security.column_security import get_column_policies_for_role, mask_columns
            policies = get_column_policies_for_role(self._table_name, role)
            if not policies:
                return row
            # Convert row to dict using cursor description column names.
            desc = self._cursor.description
            if desc is None:
                return row
            col_names = [d[0] for d in desc]
            if isinstance(row, (tuple, list)):
                row_dict = dict(zip(col_names, row))
            elif isinstance(row, dict):
                row_dict = dict(row)
            elif hasattr(row, "keys"):
                row_dict = {k: row[k] for k in row.keys()}
            else:
                return row
            return DictRow(mask_columns(row_dict, policies))
        except Exception:
            # Fail-open by design (a masking error must not break reads), but it
            # must never be silent: an ImportError here previously disabled every
            # column policy in the installed package with no trace. Warn once per
            # table so the leak is visible without flooding per-row.
            table = self._table_name or "?"
            if table not in _MASK_FAILURE_WARNED:
                _MASK_FAILURE_WARNED.add(table)
                get_logger(__name__).exception(
                    "column masking FAILED for table %r (role=%r); returning UNMASKED row",
                    table, role,
                )
            return row

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

    # -----------------------------------------------------------------------
    # Security context integration (Row-Level Security)
    # -----------------------------------------------------------------------
    def set_security_context(self, ctx) -> None:
        """Attach a SecurityContext for auto predicate injection.

        Used by row_security.py to inject tenant/classification filters.
        """
        self._security_context = ctx

    def _inject_rls(self, sql: str, params) -> tuple[str, Any]:
        """Auto-inject row predicates when a security context is set."""
        ctx = getattr(self, "_security_context", None)
        if not ctx:
            return sql, params
        try:
            from tools.security.row_security import inject_row_predicate
            from tools.security.security_context import classifications_dominated_by
            tenant_id = getattr(ctx, "tenant_id", None)
            classification = getattr(ctx, "classification", None)
            # Bell-LaPadula read-down: a caller may read any classification their
            # clearance dominates, so inject `classification IN (<dominated set>)`
            # rather than `classification = ?` (exact match, which wrongly hid a
            # CUI row from a TOP SECRET//SCI caller). The set never includes a
            # label above the caller's clearance, so read-up stays blocked.
            classifications = classifications_dominated_by(classification) or None
            # Derive LAC and COI label sets from the compartments frozenset.
            # LAC_* prefixed tags → label-based access control predicate.
            # COI_* prefixed tags → community-of-interest predicate.
            compartments = getattr(ctx, "compartments", frozenset()) or frozenset()
            lac_labels = {c for c in compartments if c.upper().startswith("LAC_")} or None
            coi_tags = {c for c in compartments if c.upper().startswith("COI_")} or None
            ph = "%s" if getattr(self, "_backend", "sqlite") == "postgresql" else "?"
            new_sql, extra, n_before = inject_row_predicate(
                sql,
                tenant_id=tenant_id,
                classifications=classifications,
                lac_labels=lac_labels,
                coi_tags=coi_tags,
                placeholder=ph,
            )
            if extra:
                # n_before == -1  → UPDATE/DELETE: APPEND extra_params after all existing params.
                # n_before >= 0   → SELECT: INSERT extra_params at position n_before so that
                #                   subquery placeholders before the outer WHERE keep their
                #                   correct positional bindings.
                existing = tuple(params) if params is not None else ()
                if n_before < 0:
                    params = existing + tuple(extra)
                else:
                    # n_before is the count of ? in the original SQL before the injection site.
                    # Insert extra at that index in the existing params tuple.
                    idx = min(n_before, len(existing))
                    params = existing[:idx] + tuple(extra) + existing[idx:]
                if AUDIT_RLS:
                    _write_rls_audit(
                        self._table_name or "unknown",
                        getattr(ctx, "tenant_id", None),
                    )
            return new_sql, params
        except Exception:
            return sql, params

    def _maybe_audit_column_mask(self) -> None:
        """Write one column_mask_audit record if AUDIT_COLUMN is enabled and policies exist."""
        if not AUDIT_COLUMN:
            return
        ctx = getattr(self, "_security_context", None)
        if not ctx or not self._table_name:
            return
        role = getattr(ctx, "role", "")
        if not role:
            return
        try:
            from tools.security.column_security import get_column_policies_for_role
            policies = get_column_policies_for_role(self._table_name, role)
            if policies:
                _write_column_audit(self._table_name, role, list(policies.keys()))
        except Exception:
            pass


def _strip_sql_line_comments(sql: str) -> str:
    """Remove ``--`` line comments outside of single-quoted string literals.

    The PG ``executescript`` path splits a multi-statement script on ``;``.
    A semicolon inside a ``-- ...`` comment would otherwise corrupt that split,
    producing a bogus statement (e.g. ``this table persists ...``) whose syntax
    error aborts the whole transaction and rolls back every prior CREATE TABLE.
    Stripping comments first makes the split robust. Single-quoted strings are
    respected (SQL doubled-quote escaping ``''`` is handled by toggling).
    """
    out = []
    in_string = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if in_string:
            out.append(ch)
            if ch == "'":
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            # Skip to end of line (keep the newline as a statement separator).
            j = sql.find("\n", i)
            if j == -1:
                break
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Reflex connection scope — thread-local leak reclamation (crx-gen-01)
# ---------------------------------------------------------------------------
# A crashing Genesis reflex that opens get_connection() but raises before
# close() leaks a checked-out pool connection (and, pre-idle-timeout, an
# idle-in-transaction session holding ACCESS SHARE locks — the historical
# kanban_tasks lock-storm). idle_in_transaction_session_timeout rolls the
# transaction back server-side, but the connection stays checked OUT of the
# 20-slot pool, so repeated leaks still exhaust it.
#
# reflex_connection_scope() wraps a single reflex execution. Every connection
# opened on the SAME thread while the scope is active is registered; on scope
# exit any that were never closed are rolled back and returned to the pool.
# It is thread-local by construction, so it never touches connections held by
# other threads/processes (the dashboard, a cached compass connection, etc.).
import threading as _threading  # noqa: E402

_conn_scope = _threading.local()


def _register_scoped_connection(conn) -> None:
    """Register a freshly-opened connection with the active thread scope, if any."""
    stack = getattr(_conn_scope, "stack", None)
    if stack:
        # Only the innermost active scope tracks the connection.
        stack[-1].append(conn)


@contextmanager
def reflex_connection_scope():
    """Isolate one unit of work so leaked connections are reclaimed on exit.

    Guarantees that any StorageConnection opened within the ``with`` block on
    this thread and left open (e.g. because the body raised before calling
    ``close()``) is rolled back and returned to the pool. Connections the body
    closed itself are left untouched — reclamation is skipped when ``_closed``
    is already set, so a pooled connection is never returned to the pool twice.

    Reflexes that intentionally keep a shared/cached connection alive are
    unaffected as long as that connection is not opened via get_connection()
    inside the scope (cached helpers hand back the same object without
    re-registering, and are never force-closed here).
    """
    stack = getattr(_conn_scope, "stack", None)
    if stack is None:
        stack = []
        _conn_scope.stack = stack
    tracked: list = []
    stack.append(tracked)
    try:
        yield
    finally:
        stack.pop()
        for conn in tracked:
            if getattr(conn, "_closed", True):
                continue  # already closed by the reflex — do not double-close
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


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
        self._closed = False
        # If a reflex_connection_scope is active on this thread, register self so
        # a leaked (never-closed) connection is reclaimed when the scope exits.
        _register_scoped_connection(self)

    def execute(self, sql: str, params=None):
        """Execute SQL with automatic translation."""
        cursor = self._conn.cursor()
        sc = StorageCursor(cursor, self._backend)
        sc.set_security_context(getattr(self, "_security_context", None))
        return sc.execute(sql, params)

    def executemany(self, sql: str, params_list):
        cursor = self._conn.cursor()
        sc = StorageCursor(cursor, self._backend)
        sc.set_security_context(getattr(self, "_security_context", None))
        return sc.executemany(sql, params_list)

    def executescript(self, sql: str):
        """Execute multiple SQL statements.

        SQLite has native executescript(). For PG, split on ; and execute each.
        """
        if self._backend == "sqlite":
            return self._conn.executescript(sql)

        # PostgreSQL: split statements and execute each, isolating every
        # statement in its own SAVEPOINT. A single failing statement (e.g.
        # already-exists DDL or an untranslatable construct) must only roll
        # back ITSELF — not the whole transaction. A bare conn.rollback()
        # here would discard every object created earlier in the script,
        # making later dependent statements fail with "relation ... does not
        # exist" and cascading the whole baseline schema load to failure.
        cursor = self._conn.cursor()
        _pg_exec_statements(cursor, sql, self._backend)
        self._conn.commit()
        return cursor

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._closed = True
        self._conn.close()

    def cursor(self):
        sc = StorageCursor(self._conn.cursor(), self._backend)
        sc.set_security_context(getattr(self, "_security_context", None))
        return sc

    def set_security_context(self, ctx) -> None:
        """Attach a SecurityContext for RLS predicate injection and PG session vars."""
        self._security_context = ctx
        if self._backend == "postgresql" and ctx:
            try:
                from tools.security.row_security import set_pg_session_vars
                tenant_id = getattr(ctx, "tenant_id", None)
                classification = getattr(ctx, "classification", None)
                set_pg_session_vars(self._conn, tenant_id, classification)
            except Exception:
                pass

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


_pg_pool = None
_pg_pool_lock = None


def _pg_session_options() -> str:
    """libpq `options` string applied to every PostgreSQL connection at startup.

    Two server-enforced guards that prevent the recurring `kanban_tasks` lock
    storm (see memory `kanban-tasks-lock-storm`):
      * idle_in_transaction_session_timeout — a leaked/unclosed transaction (an
        `idle in transaction` connection holding ACCESS SHARE locks) is rolled
        back automatically after this many ms, so it can never accumulate into a
        storm regardless of per-callsite connection hygiene.
      * lock_timeout — a statement blocked waiting on a lock (e.g. a concurrent
        `ALTER TABLE`) fails fast instead of hanging the whole table.
    Both overridable via .env; set to 0 to disable.
    """
    idle_ms = os.environ.get("ICDEV_PG_IDLE_TXN_TIMEOUT_MS", "30000")
    lock_ms = os.environ.get("ICDEV_PG_LOCK_TIMEOUT_MS", "10000")
    parts = []
    if str(idle_ms) != "0":
        parts.append(f"-c idle_in_transaction_session_timeout={idle_ms}")
    if str(lock_ms) != "0":
        parts.append(f"-c lock_timeout={lock_ms}")
    return " ".join(parts)


def _get_pg_pool():
    """Return (or lazily create) a thread-safe PostgreSQL connection pool."""
    global _pg_pool, _pg_pool_lock
    import threading
    if _pg_pool_lock is None:
        _pg_pool_lock = threading.Lock()
    with _pg_pool_lock:
        if _pg_pool is not None:
            return _pg_pool
        import psycopg2.pool
        import psycopg2.extras
        ssl_kwargs = _pg_ssl_kwargs()
        db_url = os.environ.get("ICDEV_DATABASE_URL")
        minconn = int(os.environ.get("ICDEV_PG_POOL_MIN", "2"))
        maxconn = int(os.environ.get("ICDEV_PG_POOL_MAX", "20"))
        _pg_timeout = int(os.environ.get("ICDEV_PG_CONNECT_TIMEOUT", "10"))
        _pg_options = _pg_session_options()
        if db_url:
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn, maxconn, db_url,
                connect_timeout=_pg_timeout, options=_pg_options,
                cursor_factory=psycopg2.extras.RealDictCursor, **ssl_kwargs,
            )
        else:
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn, maxconn,
                host=os.environ.get("ICDEV_PG_HOST", "localhost"),
                port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
                user=os.environ.get("ICDEV_PG_USER", "icdev"),
                password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
                dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
                connect_timeout=_pg_timeout, options=_pg_options,
                cursor_factory=psycopg2.extras.RealDictCursor,
                **ssl_kwargs,
            )
        return _pg_pool


class _PooledPgConnection:
    """Thin wrapper that returns the connection to the pool on close()."""
    def __init__(self, raw_conn, pool):
        self._conn = raw_conn
        self._pool = pool
        self.autocommit = False
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        if self._closed:
            # Idempotent: never putconn() a connection twice — a second putconn
            # of a raw conn the pool may have already re-issued to another caller
            # would corrupt that caller's session (the classic cached/shared-conn
            # poisoning hazard).
            return
        self._closed = True
        try:
            if not self._conn.closed:
                self._conn.rollback()  # return in clean state
            self._pool.putconn(self._conn)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _get_pg_connection(db_url: str = None):
    """Return a PostgreSQL connection from the shared pool."""
    try:
        pool = _get_pg_pool()
        raw = pool.getconn()
        raw.autocommit = False
        return _PooledPgConnection(raw, pool)
    except Exception:
        # Pool exhausted or unavailable — fall back to direct connect
        import psycopg2
        import psycopg2.extras
        ssl_kwargs = _pg_ssl_kwargs()
        _pg_timeout = int(os.environ.get("ICDEV_PG_CONNECT_TIMEOUT", "10"))
        _pg_options = _pg_session_options()
        if db_url:
            conn = psycopg2.connect(
                db_url, connect_timeout=_pg_timeout, options=_pg_options,
                cursor_factory=psycopg2.extras.RealDictCursor, **ssl_kwargs
            )
        else:
            conn = psycopg2.connect(
                host=os.environ.get("ICDEV_PG_HOST", "localhost"),
                port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
                user=os.environ.get("ICDEV_PG_USER", "icdev"),
                password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
                dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
                connect_timeout=_pg_timeout, options=_pg_options,
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


def _attach_flask_security_context(conn: "StorageConnection") -> None:
    """Auto-attach Flask g.security_context to a connection when in a request context.

    Bridges the middleware-set SecurityContext (on Flask's g) to the storage
    layer so that RLS predicate injection fires automatically on every query
    without requiring each route handler to call set_security_context() manually.
    """
    try:
        from flask import g, has_request_context
        if has_request_context():
            ctx = getattr(g, "security_context", None)
            if ctx:
                conn.set_security_context(ctx)
    except ImportError:
        pass


def _resolve_zone_dsn_env() -> str | None:
    """Return the pg_dsn_env value for the active ICDEV_DATA_ZONE, or None.

    When ICDEV_DATA_ZONE is set, look up the zone row in data_residency_zones
    and return its pg_dsn_env column value (the name of an env var that holds
    the zone-specific PostgreSQL DSN).  Returns None if the zone is not found
    or the table does not yet exist (e.g. during initial migration).
    """
    zone_id = os.environ.get("ICDEV_DATA_ZONE")
    if not zone_id:
        return None
    try:
        import psycopg2
        import psycopg2.extras

        db_url = os.environ.get("ICDEV_DATABASE_URL")
        ssl_kwargs = _pg_ssl_kwargs()
        _pg_timeout = int(os.environ.get("ICDEV_PG_CONNECT_TIMEOUT", "10"))
        _pg_options = _pg_session_options()
        if db_url:
            conn = psycopg2.connect(
                db_url, connect_timeout=_pg_timeout, options=_pg_options,
                cursor_factory=psycopg2.extras.RealDictCursor, **ssl_kwargs,
            )
        else:
            conn = psycopg2.connect(
                host=os.environ.get("ICDEV_PG_HOST", "localhost"),
                port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
                user=os.environ.get("ICDEV_PG_USER", "icdev"),
                password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
                dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
                connect_timeout=_pg_timeout, options=_pg_options,
                cursor_factory=psycopg2.extras.RealDictCursor,
                **ssl_kwargs,
            )
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_dsn_env FROM data_residency_zones WHERE id = %s",
                    (zone_id,),
                )
                row = cur.fetchone()
        conn.close()
        if row:
            return row["pg_dsn_env"]
        get_logger(__name__).warning("ICDEV_DATA_ZONE=%r not found in data_residency_zones", zone_id)
    except Exception as exc:  # noqa: BLE001
        get_logger(__name__).warning("Could not resolve data zone DSN (%s)", exc)
    return None


def get_connection(db_path: str = None) -> StorageConnection:
    """Return a StorageConnection for the configured backend.

    For SQLite: uses db_path or ICDEV_DB_PATH env var.
    For PostgreSQL: uses ICDEV_PG_* env vars or ICDEV_DATABASE_URL.
    If PostgreSQL is configured but unreachable, falls back to SQLite
    so that operations (task creation, notifications) are not silently
    lost during PG outages.

    When ICDEV_DATA_RESIDENCY_ENABLED=true and Flask g.tenant_id is set,
    delegates to get_zone_connection() for per-tenant PG routing.

    When ICDEV_DATA_ZONE is set, the zone's pg_dsn_env column names an env
    var that overrides ICDEV_DATABASE_URL for that connection, routing it to
    the zone-specific PostgreSQL instance.

    Returns a StorageConnection wrapper that transparently handles
    SQL translation between SQLite and PostgreSQL. When called inside a
    Flask request context the connection is automatically scoped to the
    authenticated user's tenant and classification via set_security_context.
    """
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()

    # Per-tenant data-residency routing: delegate to zone_router when enabled
    # and a tenant is active in the current Flask request context.
    if os.environ.get("ICDEV_DATA_RESIDENCY_ENABLED", "").lower() in ("true", "1"):
        try:
            from flask import g, has_request_context
            if has_request_context():
                tenant_id = getattr(g, "tenant_id", None)
                if tenant_id:
                    from tools.db.zone_router import get_zone_connection
                    return get_zone_connection(tenant_id)
        except (ImportError, RuntimeError):
            pass

    # Data-residency zone override: when ICDEV_DATA_ZONE is set, the zone row's
    # pg_dsn_env names an env var that holds the zone-specific PG DSN.
    zone_dsn_env = _resolve_zone_dsn_env()
    if zone_dsn_env:
        zone_db_url = os.environ.get(zone_dsn_env)
        if zone_db_url:
            try:
                raw_conn = _get_pg_connection(zone_db_url)
                conn = StorageConnection(raw_conn, "postgresql")
                _attach_flask_security_context(conn)
                return conn
            except Exception as exc:  # noqa: BLE001
                get_logger(__name__).warning(
                    "Zone DSN from %s unreachable (%s), falling back to default", zone_dsn_env, exc
                )

    # A db_path ending in '.db' selects a dedicated SQLite file ONLY when the
    # process backend is pinned to sqlite.  On a PostgreSQL-primary stack the
    # '.db' path is ignored and the connection goes to the shared icdev database
    # (canvas tables are namespaced by table-name prefix to avoid collisions).
    # This removes the old ambiguity where any '.db' path silently forced SQLite
    # even on PG, while a non-'.db' name fell through to shared PG.
    _main_db = os.environ.get("ICDEV_DB_PATH", str(Path.cwd() / "data" / "icdev.db"))
    if (
        backend == "sqlite"
        and db_path
        and str(db_path).endswith(".db")
        and Path(db_path).resolve() != Path(_main_db).resolve()
    ):
        raw_conn = _get_sqlite_connection(db_path)
        conn = StorageConnection(raw_conn, "sqlite")
        # Canvas/auxiliary SQLite DBs have no tenant_id/classification columns
        # — skip RLS so inject_row_predicate doesn't break every query.
        return conn

    if backend == "postgresql":
        db_url = os.environ.get("ICDEV_DATABASE_URL")
        no_fallback = os.environ.get("ICDEV_PG_NO_FALLBACK", "").lower() in ("true", "1")
        try:
            raw_conn = _get_pg_connection(db_url)
            conn = StorageConnection(raw_conn, "postgresql")
            _attach_flask_security_context(conn)
            return conn
        except Exception as exc:
            if no_fallback:
                raise ConnectionError(
                    f"PostgreSQL unavailable and ICDEV_PG_NO_FALLBACK=true — "
                    f"refusing to fall back to SQLite. Fix PG or unset the flag. "
                    f"Original error: {exc}"
                ) from exc

            get_logger(__name__).warning(
                "PostgreSQL unavailable (%s), falling back to SQLite",
                exc,
            )
            raw_conn = _get_sqlite_connection(db_path)
            conn = StorageConnection(raw_conn, "sqlite")
            _attach_flask_security_context(conn)
            return conn
    else:
        raw_conn = _get_sqlite_connection(db_path)
        conn = StorageConnection(raw_conn, "sqlite")
        _attach_flask_security_context(conn)
        return conn


def resolve_canvas_backend(canvas_backend_env_var: str = None) -> str:
    """Resolve the storage backend a canvas should use — PG-primary, no sqlite default.

    Canvases inherit the platform backend rather than hard-coding SQLite.  The
    resolution order is:

        1. The canvas-specific override (e.g. ``NC_STORAGE_BACKEND``), if given
           and set.
        2. ``ICDEV_CANVAS_STORAGE_BACKEND`` — platform-wide canvas override.
        3. ``ICDEV_STORAGE_BACKEND`` — the platform backend.
        4. ``"postgresql"`` — PG is primary; SQLite is an init-only fallback.

    There is intentionally NO hard ``"sqlite"`` default at any step: with every
    backend env var unset this returns ``"postgresql"``.
    """
    candidates = []
    if canvas_backend_env_var:
        candidates.append(canvas_backend_env_var)
    candidates += ["ICDEV_CANVAS_STORAGE_BACKEND", "ICDEV_STORAGE_BACKEND"]
    for var in candidates:
        val = os.environ.get(var)
        if val:
            return val.lower()
    return "postgresql"


def get_canvas_connection(canvas_env_var: str = None) -> "StorageConnection":
    """Return a StorageConnection for canvas tables, RLS disabled.

    Canvas tables (aac_*, dsoc_*, ccc_*, etc.) do not have classification/tenant_id
    columns, so the global RLS predicate injected by _attach_flask_security_context
    would raise UndefinedColumn on every query.  Call this instead of get_connection()
    in any canvas db/init_db.py that connects to canvas-specific tables.

    Policy (PG-primary): on PostgreSQL the canvas tables live in the SHARED icdev
    database, namespaced by table-name prefix — there is no separate per-canvas PG
    database.  The dedicated ``.db`` SQLite file is used ONLY when the resolved
    backend is sqlite.

    Args:
        canvas_env_var: Optional env-var name carrying a SQLite ``.db`` path used
                        only in sqlite-pinned mode (e.g. ``"AAC_DB_PATH"``).  On
                        PostgreSQL it is ignored.

    Returns:
        A StorageConnection with security_context=None (no RLS filtering).
    """
    backend = resolve_canvas_backend()
    if backend == "sqlite":
        # SQLite-pinned: use the dedicated canvas .db file if one is configured.
        db_path = canvas_env_var and os.environ.get(canvas_env_var)
        conn = get_connection(db_path=db_path)
    else:
        # PG-primary: shared icdev database (canvas tables namespaced by prefix).
        conn = get_connection()
    conn.set_security_context(None)
    return conn


def get_backend() -> str:
    """Return the current storage backend name (PG-primary default)."""
    return os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()


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
# Backend-aware schema introspection (translation-independent)
# ---------------------------------------------------------------------------
#
# These helpers replace ad-hoc ``sqlite_master`` / ``PRAGMA table_info`` probes
# that only work on SQLite — or that silently rely on translate_sql rule-14,
# which rewrites only three exact query shapes and lets every other shape bypass
# translation and raise (or silently skip) on PostgreSQL.  They build the correct
# catalogue query per backend DIRECTLY and execute it against the raw DB-API
# connection (bypassing StorageConnection's translating cursor), so they are
# correct regardless of whether translate_sql would have matched the shape.
#
# All three accept either a StorageConnection wrapper OR a raw psycopg2 / sqlite3
# connection, never raise for a missing table/column (they return False / []),
# and validate identifiers with a strict regex before any interpolation.

_INTROSPECT_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _introspect_backend(conn) -> str:
    """Return ``'postgresql'`` or ``'sqlite'`` for a StorageConnection or raw conn.

    Duck-types the ``_backend`` attribute first (StorageConnection / StorageCursor
    / _PooledPgConnection expose it); otherwise sniffs the raw connection's class
    module/name to distinguish psycopg2 from sqlite3.  Unknown connections default
    to ``'sqlite'`` (the ``?`` + ``sqlite_master`` dialect).
    """
    backend = getattr(conn, "_backend", None)
    if isinstance(backend, str) and backend:
        return "postgresql" if backend.lower().startswith("postgre") else "sqlite"
    cls = type(conn)
    ident = f"{getattr(cls, '__module__', '')}.{getattr(cls, '__name__', '')}".lower()
    if "psycopg" in ident:
        return "postgresql"
    if "sqlite" in ident:
        return "sqlite"
    return "sqlite"


def _introspect_raw(conn):
    """Return the underlying DB-API connection.

    For a StorageConnection this returns ``conn._conn`` so introspection SQL runs
    verbatim through the raw cursor, bypassing StorageCursor's translate_sql pass.
    Raw connections are returned unchanged.
    """
    return getattr(conn, "_conn", conn)


def _introspect_scalar_rows(cursor) -> list:
    """Return the first column of every fetched row, tolerant of dict rows.

    psycopg2 RealDictCursor yields dict rows; sqlite3 yields tuples / Row objects.
    """
    out = []
    for row in cursor.fetchall():
        if row is None:
            continue
        if isinstance(row, dict):
            out.append(next(iter(row.values())))
        else:
            out.append(row[0])
    return out


def _validate_identifier(name: str, kind: str = "identifier") -> str:
    """Raise ValueError unless *name* is a bare SQL identifier (``^[A-Za-z_]\\w*$``)."""
    if not isinstance(name, str) or not _INTROSPECT_IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid SQL {kind}: {name!r}")
    return name


def table_exists(conn, table_name: str) -> bool:
    """Return True if *table_name* exists in the connected database.

    Backend-aware and translation-independent:
        * PostgreSQL → ``information_schema.tables`` (table_schema='public')
        * SQLite     → ``sqlite_master`` (type='table')

    Accepts a StorageConnection or a raw psycopg2 / sqlite3 connection.  Never
    raises for a missing table (returns False).  Raises ValueError if
    *table_name* is not a valid SQL identifier.
    """
    _validate_identifier(table_name, "table name")
    backend = _introspect_backend(conn)
    raw = _introspect_raw(conn)
    if backend == "postgresql":
        sql = (
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s LIMIT 1"
        )
    else:
        sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1"
    try:
        cur = raw.cursor()
        cur.execute(sql, (table_name,))
        return cur.fetchone() is not None
    except Exception:
        return False


def list_tables(conn) -> list[str]:
    """Return a sorted list of user table names in the connected database.

    PostgreSQL reads ``information_schema.tables`` (table_schema='public',
    BASE TABLE); SQLite reads ``sqlite_master`` (type='table', excluding internal
    ``sqlite_*`` tables).  Accepts a StorageConnection or raw connection and never
    raises (returns [] on error).
    """
    backend = _introspect_backend(conn)
    raw = _introspect_raw(conn)
    if backend == "postgresql":
        sql = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
    else:
        sql = (
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    try:
        cur = raw.cursor()
        cur.execute(sql)
        return [str(v) for v in _introspect_scalar_rows(cur)]
    except Exception:
        return []


def column_exists(conn, table_name: str, column: str) -> bool:
    """Return True if *table_name* has a column named *column*.

    PostgreSQL → ``information_schema.columns`` (fully parameterized).
    SQLite     → ``PRAGMA table_info(<table>)`` — PRAGMA takes no bind parameters,
                 so the table name is interpolated; it is validated against a
                 strict identifier regex and double-quoted first.

    Accepts a StorageConnection or a raw connection.  Returns False for a missing
    table or column; raises ValueError for an invalid identifier.
    """
    _validate_identifier(table_name, "table name")
    _validate_identifier(column, "column name")
    backend = _introspect_backend(conn)
    raw = _introspect_raw(conn)
    try:
        cur = raw.cursor()
        if backend == "postgresql":
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s "
                "AND column_name = %s LIMIT 1",
                (table_name, column),
            )
            return cur.fetchone() is not None
        # SQLite: PRAGMA accepts no bind params; table_name was validated above
        # and is double-quoted to survive reserved-word identifiers.
        cur.execute(f'PRAGMA table_info("{table_name}")')  # nosec B608 — identifier regex-validated
        for row in cur.fetchall():
            # PRAGMA table_info cols: cid, name, type, notnull, dflt_value, pk
            name = row.get("name") if isinstance(row, dict) else row[1]
            if name == column:
                return True
        return False
    except Exception:
        return False


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
