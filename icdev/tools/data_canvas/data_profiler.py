from __future__ import annotations
# CUI // SP-CTI — ICDEV Data Design Canvas — Data Profiler
"""Classification-aware data profiler for the DDC Explore tab.

Pure functions — no Flask, no LLM dependency.
Profiles a connected database: row counts, null%, cardinality,
min/max, top values per column. Classification is inherited from
the requesting design and stamped on every returned object.

Supported backends: sqlite, postgresql (psycopg2), duckdb.
Gracefully degrades to whichever driver is available.
"""

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from tools.data_canvas.constants import DS_PROFILER_MAX_ROWS

# ── Connection helpers ────────────────────────────────────────────────────────

_NUMERIC_RE = re.compile(r"(int|float|real|numeric|decimal|double|bigint|smallint|money)", re.I)
_DATE_RE = re.compile(r"(date|time|timestamp)", re.I)


def _ident(name: str) -> str:
    """Double-quote a SQL identifier, escaping embedded quotes per SQL-92."""
    return '"' + name.replace('"', '""') + '"'


def _open_connection(conn_params: dict):
    """Return a DB-API 2.0 connection for the given params dict.

    conn_params keys: db_type, host, port, user, password, database, path (sqlite)
    """
    db_type = (conn_params.get("db_type") or "sqlite").lower()

    if db_type == "postgresql":
        try:
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(
                host=conn_params.get("host", "localhost"),
                port=int(conn_params.get("port", 5432)),
                user=conn_params.get("user", ""),
                password=conn_params.get("password", ""),
                dbname=conn_params.get("database", ""),
                connect_timeout=10,
            )
            conn.set_session(readonly=True, autocommit=True)
            return conn, "postgresql"
        except ImportError:
            raise RuntimeError("psycopg2 not installed — cannot connect to PostgreSQL")

    if db_type == "duckdb":
        try:
            import duckdb
            path = conn_params.get("path") or conn_params.get("database") or ":memory:"
            conn = duckdb.connect(path, read_only=True)
            return conn, "duckdb"
        except ImportError:
            raise RuntimeError("duckdb not installed — cannot use DuckDB backend")

    # SQLite (default)
    import sqlite3
    path = conn_params.get("path") or conn_params.get("database") or ":memory:"
    conn = sqlite3.connect(str(path), timeout=10)  # pg-ok: profiles user-provided SQLite data sources, not ICDEV storage
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn, "sqlite"


def _fetchall_dicts(cursor, db_kind: str) -> list[dict]:
    """Normalise rows to list-of-dicts regardless of backend."""
    if db_kind in ("sqlite",):
        return [dict(r) for r in cursor.fetchall()]
    # postgresql / duckdb return tuples; use description
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _scalar(cursor, db_kind: str):
    row = cursor.fetchone()
    if row is None:
        return None
    if db_kind == "sqlite":
        return row[0]
    return row[0]


# ── Table list ────────────────────────────────────────────────────────────────


def list_tables(conn_params: dict) -> dict:
    """Return {"tables": ["name", ...], "db_type": ...} or {"error": ...}."""
    try:
        conn, db_kind = _open_connection(conn_params)
        tables = _get_table_list(conn, db_kind)
        conn.close()
        return {"tables": tables, "db_type": db_kind}
    except Exception as exc:
        return {"error": str(exc), "tables": []}


def _get_table_list(conn, db_kind: str) -> list[str]:
    if db_kind == "sqlite":
        # pg-portability: sqlite-only path — profiles arbitrary EXTERNAL user
        # databases over a raw driver connection keyed by db_kind, not the ICDEV
        # storage backend; the PG branch below is the information_schema equivalent.
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [r[0] for r in cur.fetchall()]
    if db_kind == "postgresql":
        cur = conn.cursor()
        schema = "public"
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name",
            (schema,),
        )
        return [r[0] for r in cur.fetchall()]
    if db_kind == "duckdb":
        cur = conn.execute("SHOW TABLES")
        return [r[0] for r in cur.fetchall()]
    return []


def _get_column_info(conn, db_kind: str, table: str) -> list[dict]:
    """Return [{name, type_str}] for each column in table."""
    if db_kind == "sqlite":
        # pg-portability: sqlite-only path — external DB profiling keyed by db_kind
        # (the PG branch below uses information_schema.columns).
        cur = conn.execute(f"PRAGMA table_info({_ident(table)})")  # nosec B608
        cols = [{"name": r[1], "type_str": r[2] or ""} for r in cur.fetchall()]
        if not cols and table in ("sqlite_master", "sqlite_schema"):
            # Virtual catalog table — PRAGMA returns nothing; use known schema
            cols = [
                {"name": "type", "type_str": "TEXT"},
                {"name": "name", "type_str": "TEXT"},
                {"name": "tbl_name", "type_str": "TEXT"},
                {"name": "rootpage", "type_str": "INTEGER"},
                {"name": "sql", "type_str": "TEXT"},
            ]
        return cols
    if db_kind == "postgresql":
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name=%s AND table_schema='public' ORDER BY ordinal_position",
            (table,),
        )
        return [{"name": r[0], "type_str": r[1] or ""} for r in cur.fetchall()]
    if db_kind == "duckdb":
        cur = conn.execute(f"DESCRIBE {_ident(table)}")  # nosec B608
        rows = cur.fetchall()
        return [{"name": r[0], "type_str": r[1] or ""} for r in rows]
    return []


# ── Column profiler ───────────────────────────────────────────────────────────


@dataclass
class _ProfileCtx:
    conn: Any
    db_kind: str
    table: str
    classification: str


def _fetch_null_stats(ctx: _ProfileCtx, safe_col: str, safe_table: str, row_count: int) -> tuple[int, float]:
    if ctx.db_kind == "postgresql":
        cur = ctx.conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {safe_table} WHERE {safe_col} IS NULL")  # nosec B608
        null_count = cur.fetchone()[0] or 0
    else:
        cur = ctx.conn.execute(f"SELECT COUNT(*) FROM {safe_table} WHERE {safe_col} IS NULL")  # nosec B608
        null_count = _scalar(cur, ctx.db_kind) or 0
    null_pct = round(null_count / row_count * 100, 2) if row_count else 0.0
    return null_count, null_pct


def _fetch_distinct_count(ctx: _ProfileCtx, safe_col: str, safe_table: str) -> int:
    if ctx.db_kind == "postgresql":
        cur = ctx.conn.cursor()
        cur.execute(f"SELECT COUNT(DISTINCT {safe_col}) FROM {safe_table}")  # nosec B608
        return cur.fetchone()[0] or 0
    cur = ctx.conn.execute(f"SELECT COUNT(DISTINCT {safe_col}) FROM {safe_table}")  # nosec B608
    return _scalar(cur, ctx.db_kind) or 0


def _infer_col_type(type_str: str) -> str:
    if _NUMERIC_RE.search(type_str):
        return "numeric"
    if _DATE_RE.search(type_str):
        return "datetime"
    return "string"


def _fetch_min_max(ctx: _ProfileCtx, safe_col: str, safe_table: str) -> tuple[Any, Any]:
    if ctx.db_kind == "postgresql":
        cur = ctx.conn.cursor()
        cur.execute(f"SELECT MIN({safe_col}), MAX({safe_col}) FROM {safe_table}")  # nosec B608
        row = cur.fetchone()
    else:
        cur = ctx.conn.execute(f"SELECT MIN({safe_col}), MAX({safe_col}) FROM {safe_table}")  # nosec B608
        row = cur.fetchone()
    if row:
        return (str(row[0]) if row[0] is not None else None, str(row[1]) if row[1] is not None else None)
    return None, None


def _fetch_top_values(ctx: _ProfileCtx, safe_col: str, safe_table: str) -> list[dict]:
    limit = 10
    if ctx.db_kind == "postgresql":
        cur = ctx.conn.cursor()
        cur.execute(
            f"SELECT {safe_col}, COUNT(*) AS cnt FROM {safe_table} WHERE {safe_col} IS NOT NULL "  # nosec B608
            f"GROUP BY {safe_col} ORDER BY cnt DESC LIMIT %s", (limit,),
        )
    else:
        # sqlite3 and duckdb use the qmark paramstyle ("?"), not "%s".
        # Passing "%s" here raises (swallowed upstream), silently emptying top_values.
        cur = ctx.conn.execute(
            f"SELECT {safe_col}, COUNT(*) AS cnt FROM {safe_table} WHERE {safe_col} IS NOT NULL "  # nosec B608
            f"GROUP BY {safe_col} ORDER BY cnt DESC LIMIT ?", (limit,),
        )
    return [{"value": str(r[0]), "count": r[1]} for r in cur.fetchall()]


def _profile_column(ctx: _ProfileCtx, col: dict, row_count: int) -> dict:
    """Profile a single column. Returns dict with stats."""
    name, type_str = col["name"], col["type_str"]
    safe_col, safe_table = _ident(name), _ident(ctx.table)
    result: dict[str, Any] = {
        "name": name, "type_str": type_str, "classification": ctx.classification,
        "null_count": 0, "null_pct": 0.0, "distinct_count": 0,
        "min": None, "max": None, "top_values": [], "inferred_type": "string",
    }
    if row_count == 0:
        return result
    try:
        result["null_count"], result["null_pct"] = _fetch_null_stats(ctx, safe_col, safe_table, row_count)
        result["distinct_count"] = _fetch_distinct_count(ctx, safe_col, safe_table)
        result["inferred_type"] = _infer_col_type(type_str)
        if result["inferred_type"] in ("numeric", "datetime"):
            result["min"], result["max"] = _fetch_min_max(ctx, safe_col, safe_table)
        if result["distinct_count"] <= 100 or result["inferred_type"] == "string":
            result["top_values"] = _fetch_top_values(ctx, safe_col, safe_table)
    except Exception as exc:
        result["error"] = str(exc)
    return result


# ── Table profiler ────────────────────────────────────────────────────────────


def profile_table(conn_params: dict, table: str, classification: str = "CUI // SP-CTI") -> dict:
    """Profile a single table.

    Returns:
        {name, row_count, columns: [...], classification, profiled_at, error?}
    """
    try:
        conn, db_kind = _open_connection(conn_params)
        safe_table = _ident(table)

        # Row count — safe_table is a properly-quoted identifier from DB metadata
        if db_kind == "postgresql":
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {safe_table}")  # nosec B608
            row_count = cur.fetchone()[0] or 0
        else:
            cur = conn.execute(f"SELECT COUNT(*) FROM {safe_table}")  # nosec B608
            row_count = _scalar(cur, db_kind) or 0

        if row_count > DS_PROFILER_MAX_ROWS:
            row_count_label = f"{row_count:,} (sampled)"
        else:
            row_count_label = f"{row_count:,}"

        cols = _get_column_info(conn, db_kind, table)
        ctx = _ProfileCtx(conn=conn, db_kind=db_kind, table=table, classification=classification)
        column_profiles = [_profile_column(ctx, col, row_count) for col in cols]
        conn.close()
        return {
            "name": table,
            "row_count": row_count,
            "row_count_label": row_count_label,
            "columns": column_profiles,
            "classification": classification,
            "profiled_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {"name": table, "error": str(exc), "classification": classification, "columns": [], "row_count": 0}


# ── Database profiler ─────────────────────────────────────────────────────────


def profile_database(conn_params: dict, classification: str = "CUI // SP-CTI", tables: list[str] | None = None) -> dict:
    """Profile all (or specified) tables in a connected database.

    Returns:
        {db_type, db_name, table_count, tables: [...profile_table results...],
         classification, profiled_at, exec_ms}
    """
    t0 = time.monotonic()
    try:
        conn, db_kind = _open_connection(conn_params)
        all_tables = _get_table_list(conn, db_kind)
        conn.close()
    except Exception as exc:
        return {"error": str(exc), "classification": classification, "tables": [], "exec_ms": 0}

    target_tables = tables if tables else all_tables
    profiled = []
    for tname in target_tables:
        if tables or tname in all_tables:
            profiled.append(profile_table(conn_params, tname, classification))

    exec_ms = int((time.monotonic() - t0) * 1000)
    return {
        "db_type": conn_params.get("db_type", "sqlite"),
        "db_name": conn_params.get("database") or conn_params.get("path") or "unknown",
        "table_count": len(target_tables),
        "tables": profiled,
        "classification": classification,
        "profiled_at": datetime.now(timezone.utc).isoformat(),
        "exec_ms": exec_ms,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import json as _json
    import sys

    ap = argparse.ArgumentParser(description="DDC Data Profiler — profile a database from the CLI")
    ap.add_argument("--db", required=True, help="Path to SQLite DB file (or DSN for postgres)")
    ap.add_argument("--type", dest="db_type", default="sqlite",
                    choices=["sqlite", "postgresql", "duckdb"],
                    help="Database backend (default: sqlite)")
    ap.add_argument("--table", metavar="TABLE", action="append", dest="tables",
                    help="Profile only these tables (repeatable). Omit for all.")
    ap.add_argument("--classification", default="CUI // SP-CTI", help="ATO classification label")
    ap.add_argument("--output-json", action="store_true", help="Emit JSON to stdout")
    ap.add_argument("--list-tables", action="store_true", help="List tables and exit")
    args = ap.parse_args()

    conn_params: dict[str, Any] = {"db_type": args.db_type}
    if args.db_type == "sqlite":
        conn_params["path"] = args.db
    else:
        conn_params["database"] = args.db

    if args.list_tables:
        result = list_tables(conn_params)
        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)
        if args.output_json:
            print(_json.dumps(result, indent=2))
        else:
            for tbl in result.get("tables", []):
                print(tbl)
        return

    result = profile_database(conn_params, classification=args.classification, tables=args.tables)
    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        print(_json.dumps(result, indent=2))
    else:
        print(f"[data_profiler] {result['table_count']} tables profiled in {result['exec_ms']}ms")
        for tbl in result.get("tables", []):
            row_count = tbl.get("row_count", 0)
            col_count = len(tbl.get("columns", []))
            print(f"  {tbl.get('name','?'):40s}  rows={row_count:>8,}  cols={col_count}")


if __name__ == "__main__":
    main()
