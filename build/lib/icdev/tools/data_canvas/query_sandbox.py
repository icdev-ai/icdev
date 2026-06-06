from __future__ import annotations
# CUI // SP-CTI — ICDEV Data Design Canvas — SQL Sandbox
"""Read-only SQL sandbox for the DDC Query tab.

Pure functions — no Flask, no LLM dependency.
Validates SQL (SELECT only), executes against a connected DB,
returns results with classification stamping. Hard limit: 1000 rows.

Supported backends: sqlite, postgresql (psycopg2), duckdb.
"""

import re
import time
from datetime import datetime, timezone

from tools.data_canvas.constants import DS_QUERY_MAX_ROWS
from tools.data_canvas.data_profiler import _open_connection

try:
    from tools.data_canvas.pii_scanner import scan_result as pii_scan
except ImportError:
    pii_scan = None

# ── Query validation ──────────────────────────────────────────────────────────

# Strip SQL comments and leading whitespace before checking
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)

_ALLOWED_STARTS = {"select", "with", "explain"}

# Dangerous keywords that must not appear anywhere in the query
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|replace|merge|call|exec|execute|"
    r"grant|revoke|attach|detach|pragma|vacuum|reindex|analyze)\b",
    re.I,
)


def validate_query(sql: str) -> dict:
    """Return {valid: bool, error: str|None}.

    Accepts SELECT, WITH (CTEs), EXPLAIN. Rejects DDL/DML.
    """
    if not sql or not sql.strip():
        return {"valid": False, "error": "Query is empty"}

    clean = _COMMENT_RE.sub(" ", sql).strip()
    first_word = clean.split()[0].lower() if clean.split() else ""

    if first_word not in _ALLOWED_STARTS:
        return {
            "valid": False,
            "error": f"Only SELECT/WITH/EXPLAIN queries are allowed (got: {first_word.upper()})",
        }

    forbidden = _FORBIDDEN_KEYWORDS.search(clean)
    if forbidden:
        return {
            "valid": False,
            "error": f"Forbidden keyword detected: {forbidden.group(0).upper()}",
        }

    return {"valid": True, "error": None}


# ── Query execution ───────────────────────────────────────────────────────────


def execute_query(sql: str, conn_params: dict, classification: str = "CUI // SP-CTI") -> dict:
    """Execute a read-only SQL query against the connected DB.

    Returns:
        {columns, rows, row_count, exec_ms, classification, profiled_at, error?}
    Rows are list-of-lists (values serialised to str for JSON safety).
    Never raises — returns error dict on failure.
    """
    _empty_pii = {"warnings": [], "has_warnings": False}

    validation = validate_query(sql)
    if not validation["valid"]:
        return {
            "error": validation["error"],
            "columns": [],
            "rows": [],
            "row_count": 0,
            "exec_ms": 0,
            "classification": classification,
            "pii_warnings": _empty_pii,
        }

    t0 = time.monotonic()
    try:
        conn, db_kind = _open_connection(conn_params)

        if db_kind == "postgresql":
            cur = conn.cursor()
            cur.execute(sql)
            col_names = [d[0] for d in cur.description] if cur.description else []
            raw = cur.fetchmany(DS_QUERY_MAX_ROWS + 1)
            truncated = len(raw) > DS_QUERY_MAX_ROWS
            raw = raw[:DS_QUERY_MAX_ROWS]
            rows = [[_safe_str(v) for v in row] for row in raw]
        elif db_kind == "duckdb":
            rel = conn.execute(sql)
            col_names = [d[0] for d in rel.description] if rel.description else []
            raw = rel.fetchmany(DS_QUERY_MAX_ROWS + 1)
            truncated = len(raw) > DS_QUERY_MAX_ROWS
            raw = raw[:DS_QUERY_MAX_ROWS]
            rows = [[_safe_str(v) for v in row] for row in raw]
        else:
            cur = conn.execute(sql)
            col_names = [d[0] for d in cur.description] if cur.description else []
            raw = cur.fetchmany(DS_QUERY_MAX_ROWS + 1)
            truncated = len(raw) > DS_QUERY_MAX_ROWS
            raw = raw[:DS_QUERY_MAX_ROWS]
            rows = [[_safe_str(v) for v in row] for row in raw]

        conn.close()
        exec_ms = int((time.monotonic() - t0) * 1000)
        pii_warnings = (
            pii_scan(col_names, rows, classification)
            if pii_scan is not None
            else _empty_pii
        )
        return {
            "columns": col_names,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "exec_ms": exec_ms,
            "classification": classification,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "pii_warnings": pii_warnings,
        }

    except Exception as exc:
        exec_ms = int((time.monotonic() - t0) * 1000)
        return {
            "error": str(exc),
            "columns": [],
            "rows": [],
            "row_count": 0,
            "exec_ms": exec_ms,
            "classification": classification,
            "pii_warnings": _empty_pii,
        }


def _safe_str(val) -> str:
    if val is None:
        return ""
    return str(val)
