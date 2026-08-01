from __future__ import annotations
# CUI // SP-CTI — ICDEV Data Design Canvas — SQL Sandbox
"""Read-only SQL sandbox for the DDC Query tab.

Pure functions — no Flask, no LLM dependency.
Validates SQL (single read-only statement only), executes against a connected
DB, returns results with classification stamping. Hard limit: 1000 rows.

Security model (dcpr-sec-01)
----------------------------
User-supplied SQL is untrusted. ``validate_query`` is a **parser-based**
(``sqlparse``) read-only gate, not a first-word regex. It:

  * accepts EXACTLY ONE top-level statement (``sqlparse.split``), rejecting
    stacked statements like ``SELECT 1; COPY x TO PROGRAM 'sh -c id'`` that
    would otherwise be run in full by psycopg2 (RCE);
  * accepts only SELECT / WITH (CTE) / EXPLAIN statement shapes;
  * rejects any DML/DDL keyword and any reference to file/catalog/RCE
    functions and constructs (pg_read_file, COPY … TO PROGRAM, DuckDB
    read_csv_auto/INSTALL/LOAD, information_schema, dblink, …).

``execute_query`` additionally applies a 10s statement timeout on PostgreSQL
(cartesian-join DoS defence) and caps results at 1000 rows.

Defense in depth: the DB connection used here SHOULD authenticate as a
low-privilege, read-only database role (no COPY/superuser, no write grants).
The parser gate is the first line of defence, not the only one.

Supported backends: sqlite, postgresql (psycopg2), duckdb.
"""

import re
import time
from datetime import datetime, timezone

import sqlparse
from sqlparse import tokens as _T

from tools.data_canvas.constants import DS_QUERY_MAX_ROWS
from tools.data_canvas.data_profiler import _open_connection

try:
    from tools.data_canvas.pii_scanner import scan_result as pii_scan
except ImportError:
    pii_scan = None

# ── Query validation ──────────────────────────────────────────────────────────

# Strip SQL comments before scanning for forbidden tokens (a forbidden word
# inside a comment is harmless — do not false-positive on it).
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)

# Statement shapes we accept. ``sqlparse.get_type()`` returns "SELECT" for both
# plain SELECT and WITH/CTE statements; EXPLAIN is reported as "UNKNOWN", so it
# is matched separately via the first keyword.
_ALLOWED_FIRST_KEYWORDS = {"SELECT", "WITH", "EXPLAIN"}

# Dangerous DML/DDL/administrative keywords that must not appear anywhere.
# COPY/SET/RESET/INSTALL/LOAD added for dcpr-sec-01 (COPY TO PROGRAM = RCE,
# COPY TO/FROM = file IO, DuckDB INSTALL/LOAD httpfs, SET/RESET config abuse).
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|replace|merge|call|exec|execute|"
    r"grant|revoke|attach|detach|pragma|vacuum|reindex|analyze|"
    r"copy|set|reset|install|load)\b",
    re.I,
)

# File-read / catalog-read / RCE functions and identifiers that are dangerous
# even inside a plain SELECT (bypass #3). Whole-word, case-insensitive.
_FORBIDDEN_IDENTIFIERS = re.compile(
    r"\b(pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|"
    r"pg_authid|pg_shadow|pg_catalog|information_schema|read_csv_auto|read_parquet|"
    r"dblink)\b",
    re.I,
)

# The `COPY ... TO PROGRAM '...'` construct (RCE via server-side shell).
_TO_PROGRAM = re.compile(r"\bto\s+program\b", re.I)


def _first_keyword(parsed) -> str | None:
    """Return the first significant keyword of a parsed statement, uppercased.

    Skips whitespace, comments, and leading punctuation (e.g. a parenthesised
    ``(SELECT ...)``).
    """
    for tok in parsed.flatten():
        if tok.is_whitespace:
            continue
        if tok.ttype in _T.Comment:
            continue
        if tok.ttype in _T.Punctuation:
            continue
        return tok.normalized.upper()
    return None


def validate_query(sql: str) -> dict:
    """Return {valid: bool, error: str|None}.

    Parser-based read-only gate. Accepts a SINGLE SELECT / WITH (CTE) / EXPLAIN
    statement. Rejects: empty input, multiple (stacked) statements, any DML/DDL
    keyword, and any reference to forbidden file/catalog/RCE identifiers or the
    ``TO PROGRAM`` construct.
    """
    if not sql or not sql.strip():
        return {"valid": False, "error": "Query is empty"}

    try:
        statements = [s for s in sqlparse.split(sql) if s.strip()]
    except Exception as exc:  # pragma: no cover — sqlparse rarely raises
        return {"valid": False, "error": f"Could not parse SQL: {exc}"}

    if not statements:
        return {"valid": False, "error": "Query is empty"}
    if len(statements) > 1:
        return {
            "valid": False,
            "error": "Multiple statements are not allowed (single read-only query only)",
        }

    single = statements[0]
    parsed = sqlparse.parse(single)[0]
    stmt_type = parsed.get_type()  # "SELECT" for SELECT and WITH/CTE
    first_kw = _first_keyword(parsed)

    # Statement-shape gate: SELECT/WITH (type == SELECT) or EXPLAIN (first kw).
    is_select = stmt_type == "SELECT" and first_kw in {"SELECT", "WITH"}
    is_explain = first_kw == "EXPLAIN"
    if not (is_select or is_explain):
        got = first_kw or stmt_type or "?"
        return {
            "valid": False,
            "error": f"Only SELECT/WITH/EXPLAIN queries are allowed (got: {got})",
        }

    # Token gate: scan the comment-stripped statement for forbidden content.
    clean = _COMMENT_RE.sub(" ", single)

    forbidden = _FORBIDDEN_KEYWORDS.search(clean)
    if forbidden:
        return {
            "valid": False,
            "error": f"Forbidden keyword detected: {forbidden.group(0).upper()}",
        }

    ident = _FORBIDDEN_IDENTIFIERS.search(clean)
    if ident:
        return {
            "valid": False,
            "error": f"Forbidden identifier/function detected: {ident.group(0).lower()}",
        }

    if _TO_PROGRAM.search(clean):
        return {
            "valid": False,
            "error": "Forbidden construct detected: TO PROGRAM",
        }

    return {"valid": True, "error": None}


# ── Query execution ───────────────────────────────────────────────────────────

# Statement timeout applied to PostgreSQL queries (cartesian-join DoS defence).
_STATEMENT_TIMEOUT_MS = 10_000


def execute_query(sql: str, conn_params: dict, classification: str = "CUI // SP-CTI") -> dict:
    """Execute a read-only SQL query against the connected DB.

    Returns:
        {columns, rows, row_count, exec_ms, classification, profiled_at, error?}
    Rows are list-of-lists (values serialised to str for JSON safety).
    Never raises — returns error dict on failure.

    Defense in depth: this SHOULD run under a low-privilege, read-only database
    role (no COPY / superuser / write grants). The ``validate_query`` parser
    gate is the primary control; the DB role is a secondary backstop.
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
            # Bound runtime to defend against cartesian-join DoS. SET LOCAL is
            # scoped to the current transaction (psycopg2 opens one implicitly).
            try:
                cur.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT_MS}'")
            except Exception:
                # Fall back to a session-level SET if not inside a txn block.
                try:
                    cur.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT_MS}'")
                except Exception:
                    pass
            cur.execute(sql)
            col_names = [d[0] for d in cur.description] if cur.description else []
            raw = cur.fetchmany(DS_QUERY_MAX_ROWS + 1)
            truncated = len(raw) > DS_QUERY_MAX_ROWS
            raw = raw[:DS_QUERY_MAX_ROWS]
            rows = [[_safe_str(v) for v in row] for row in raw]
        elif db_kind == "duckdb":
            # DuckDB has no portable per-statement timeout across versions; the
            # 1000-row fetch cap bounds result size. Documented no-op timeout.
            rel = conn.execute(sql)
            col_names = [d[0] for d in rel.description] if rel.description else []
            raw = rel.fetchmany(DS_QUERY_MAX_ROWS + 1)
            truncated = len(raw) > DS_QUERY_MAX_ROWS
            raw = raw[:DS_QUERY_MAX_ROWS]
            rows = [[_safe_str(v) for v in row] for row in raw]
        else:
            # SQLite has no statement_timeout; the 1000-row fetch cap bounds
            # result size. Documented no-op timeout.
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
