# CUI // SP-CTI
"""Pytest suite — migration_canvas + security_canvas PG placeholder sweep (cvx-sql-01).

These two canvases are PostgreSQL-primary (``MC_STORAGE_BACKEND`` /
``SC_STORAGE_BACKEND`` default ``postgresql``) and connect through
``StorageConnection`` (``get_connection`` / ``get_canvas_connection``). Runtime
SQL must be authored PG-native (``%s``) per the CLAUDE.md pgp-tx-03 guardrail —
``translate_sql`` is an init-only SQLite fallback, not a load-bearing runtime
shim. Bare ``?`` placeholders in these StorageConnection call sites emit a
"bare ? placeholder detected" warning and mix dialects within a single
statement (e.g. an f-string that built ``SET {k}=?`` but ended ``updated_at=%s``).

This suite guards three things:

  1. Every ``execute()`` / ``executemany()`` SQL literal in the converted files
     is PG-native (no bare ``?``) — with ONE documented exception: the migration
     canvas blueprint queries ``nc_hardware_profiles`` through the raw ``sqlite3``
     ``_nc_conn()`` helper (a self-contained local SQLite catalog), where ``?`` is
     the correct sqlite3 placeholder and there is NO translate wrapper.

  2. The dynamically-built SQL fragments (set-clauses, IN-list placeholder
     builders, appended WHERE clauses) render ``%s``, not ``?`` — these live in
     ``.append()`` / ``.join()`` calls the AST-over-``execute()`` scan cannot see.

  3. A representative converted statement round-trips correctly through both
     dialects via ``translate_sql`` (PG keeps ``%s``; the SQLite fallback yields
     ``?``), proving the ``%s`` source is valid on both backends.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import translate_sql  # noqa: E402


# Files converted to PG-native %s on their StorageConnection paths (cvx-sql-01).
_CONVERTED = [
    "tools/migration_canvas/blueprint.py",
    "tools/migration_canvas/server_migration.py",
    "tools/migration_canvas/sops.py",
    "tools/security_canvas/blueprint.py",
    "tools/security_canvas/sops.py",
    "tools/security_canvas/attack_graph_db.py",
]

# The sole legitimate bare-? in the converted files: migration_canvas/blueprint.py
# runs two catalog lookups against nc_hardware_profiles through the raw sqlite3
# _nc_conn() helper. Any execute() SQL literal mentioning this table is exempt.
_RAW_SQLITE_TABLE = "nc_hardware_profiles"


def _sql_literal(node: ast.AST) -> str | None:
    """Reconstruct the literal text of a SQL argument.

    Handles plain string constants, ``a + b`` concatenation, and f-strings
    (substitution sites collapse to a sentinel; the surrounding literal text is
    what we inspect for placeholder style).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("\x00")  # substitution sentinel
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_literal(node.left)
        right = _sql_literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _iter_execute_sql(source: str):
    """Yield (lineno, sql_literal) for every execute()/executemany() call."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("execute", "executemany")
            and node.args
        ):
            sql = _sql_literal(node.args[0])
            if sql is not None:
                yield node.lineno, sql


@pytest.mark.parametrize("rel_path", _CONVERTED)
def test_execute_sql_is_pg_native(rel_path):
    """Every execute() SQL literal is PG-native (%s), except the raw-sqlite3
    nc_hardware_profiles catalog queries."""
    source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    offenders = [
        (lineno, sql)
        for lineno, sql in _iter_execute_sql(source)
        if "?" in sql and _RAW_SQLITE_TABLE not in sql
    ]
    assert not offenders, (
        f"{rel_path}: execute() SQL still contains bare ? placeholders: "
        + "; ".join(f"line {ln}: {sql!r}" for ln, sql in offenders)
    )


# Dynamically-built SQL fragments (set-clauses / IN-list builders / appended
# WHERE clauses) live in .append()/.join() calls, invisible to the execute()
# AST scan above. Assert their bare-? source forms are gone.
_FORBIDDEN_BUILDER_PATTERNS = {
    "tools/migration_canvas/blueprint.py": [
        'f"{k}=?"',            # set_clause builders (fields + updates)
        '"?" * len(fields)',   # INSERT placeholders builder
        'session_id=?',        # app-inventory filter appends
        'criticality=?',
        'environment=?',
    ],
    "tools/security_canvas/blueprint.py": [
        'f"{key}=?"',          # security_designs update builder
        '"graph_json=?"',
        '"updated_at=?"',
        'pillar_slug=?',       # zig capability/activity filter appends
        'implementation_status=?',
        'a.capability_id=?',
    ],
    "tools/migration_canvas/server_migration.py": [
        'f"{col}=?"',
        '"provider = ?"',
        '"provider=?"',
        '"vcpus >= ?"',
        '"eol_status = ?"',
    ],
    "tools/security_canvas/attack_graph_db.py": [
        '"asset_id = ?"',
        '"src_node_id = ?"',
        '"ttp_id = ?"',
        'LIMIT ?',
    ],
}


def test_no_bare_qmark_builder_fragments():
    """No converted file still assembles SQL fragments with bare ? placeholders."""
    violations = []
    for rel_path, patterns in _FORBIDDEN_BUILDER_PATTERNS.items():
        source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        for pat in patterns:
            if pat in source:
                violations.append(f"{rel_path}: still contains {pat!r}")
    assert not violations, "bare-? SQL builders survived:\n" + "\n".join(violations)


def test_nc_hardware_profiles_raw_sqlite_keeps_qmark():
    """The raw sqlite3 _nc_conn() catalog queries MUST keep ? (no translate
    wrapper on that connection) — proving the sweep did not over-convert."""
    source = (_REPO_ROOT / "tools/migration_canvas/blueprint.py").read_text(
        encoding="utf-8"
    )
    assert "LOWER(vendor)=LOWER(?)" in source
    assert "WHERE device_type=? ORDER BY vendor, model" in source


@pytest.mark.parametrize(
    "sql",
    [
        # migration_canvas — sops filter (get_all_sops)
        "SELECT * FROM mc_sops WHERE sop_type = %s AND approval_status = %s "
        "ORDER BY updated_at DESC",
        # migration_canvas — dynamic UPDATE set-clause shape
        "UPDATE mc_app_inventory SET name=%s, updated_at=%s WHERE id=%s",
        # security_canvas — sdc sops filter
        "SELECT * FROM sdc_sops WHERE sop_type = %s ORDER BY updated_at DESC",
        # security_canvas — attack_graph_db list with LIMIT
        "SELECT * FROM attack_graph_edges WHERE ttp_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
    ],
)
def test_converted_sql_round_trips_both_dialects(sql):
    """A converted %s statement is psycopg2-valid on PG and translates back to
    ? for the SQLite init-fallback — the whole point of authoring PG-native."""
    pg = translate_sql(sql, "postgresql")
    assert "%s" in pg and "?" not in pg, f"PG render not psycopg2-valid: {pg!r}"

    lite = translate_sql(sql, "sqlite")
    assert "?" in lite and "%s" not in lite, f"SQLite fallback not valid: {lite!r}"
    # Placeholder count is preserved across the round-trip.
    assert lite.count("?") == sql.count("%s")
