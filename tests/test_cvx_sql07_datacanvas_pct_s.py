# CUI // SP-CTI
"""cvx-sql-07: data_canvas %s-on-raw-sqlite3 hybrid-branch sweep.

Sibling of cvx-sql-06. That task fixed ``:name`` dict inserts on the HYBRID
``get_connection``. This one fixes the OPPOSITE defect on the same connection:
``%s`` placeholders that break on the raw-sqlite3 SQLite branch.

  * SQLite branch -> raw ``sqlite3.connect`` (NO translate wrapper). Raw sqlite3
    accepts ``?`` but NOT ``%s`` -> ``sqlite3.OperationalError``.
  * PG branch     -> ``StorageConnection`` whose ``translate_sql`` rewrites
    ``?`` -> ``%s``. So POSITIONAL ``?`` is the only form valid on BOTH branches.

Two baseline tests failed purely from this defect:
  * ``test_data_lineage_route`` — ``/data/lineage?classification=CUI`` filtered
    with ``WHERE classification=%s``.
  * ``test_iqe_ext_governance`` — ``check_ext_access`` INSERTed into
    ``dm_policy_audit_log`` with ``VALUES (%s, ...)``.

This suite guards the fix, adds a static ``%s``-free assertion over every
canvas-path ``execute()`` SQL literal, and drives the three dynamic-UPDATE
f-strings (contract/domain/product) whose trailing ``updated_at=%s WHERE id=%s``
also broke on raw sqlite3.

User-DB modules are intentionally EXCLUDED from the static guard:
``data_profiler.py`` / ``query_sandbox.py`` connect to arbitrary CALLER databases
via psycopg2/sqlite adapters, and ``quality_engine.py::run_rule`` issues one
PG-regex ``~ %s`` against that external DB. Those ``%s`` target the caller's
driver, not the canvas hybrid connection.
"""
from __future__ import annotations

import ast
import importlib
import os
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import translate_sql  # noqa: E402

# ── Static-guard scope ────────────────────────────────────────────────────────
_DATA_CANVAS_DIR = _REPO_ROOT / "tools" / "data_canvas"
# Modules that talk to a CALLER-supplied external DB (not the canvas hybrid conn).
_USERDB_FILES = {"data_profiler.py", "query_sandbox.py"}
# Per-literal markers of a user-DB query that legitimately keeps %s.
_USERDB_MARKERS = ("information_schema", " ~ %s", "~ %s")


def _canvas_py_files():
    return [
        p
        for p in sorted(_DATA_CANVAS_DIR.rglob("*.py"))
        if p.name not in _USERDB_FILES
    ]


def _sql_literal(node: ast.AST):
    """Reconstruct a SQL argument literal (const / a+b / f-string)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("\x00")  # opaque interpolation placeholder
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_literal(node.left)
        right = _sql_literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _shared_helper_receivers(scope: ast.AST) -> set:
    """Names bound to ``get_canvas_connection(...)`` within ONE function.

    That helper is NOT the data_canvas hybrid: it returns a StorageConnection on
    both backends, so its SQL is translated either way and ``%s`` is correct.
    Only ``data_canvas.db.init_db.get_connection`` has the raw-sqlite3 branch
    this guard exists for — see the module docstring.

    Scoped per function, never per module: `conn` is bound to the shared helper
    in one function and to the hybrid ``get_connection`` in another throughout
    this package. A module-wide name set conflates the two and silently disarms
    the guard — verified by injecting a hybrid-connection ``%s`` call, which a
    module-wide version failed to catch.
    """
    bound = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        fn = node.value.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name == "get_canvas_connection":
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    bound.add(tgt.id)
    return bound


def _iter_execute_sql(source: str):
    tree = ast.parse(source)
    scopes = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def innermost(lineno):
        best = None
        for fn in scopes:
            if fn.lineno <= lineno <= (fn.end_lineno or fn.lineno):
                if best is None or fn.lineno > best.lineno:
                    best = fn
        return best

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("execute", "executemany")
            and node.args
        ):
            recv = node.func.value
            if isinstance(recv, ast.Name):
                scope = innermost(node.lineno)
                if scope is not None and recv.id in _shared_helper_receivers(scope):
                    continue
            sql = _sql_literal(node.args[0])
            if sql is not None:
                yield node.lineno, sql


@pytest.mark.parametrize(
    "py_file", _canvas_py_files(), ids=lambda p: str(p.relative_to(_REPO_ROOT))
)
def test_no_percent_s_in_canvas_execute_sql(py_file):
    """No canvas-path execute() SQL literal keeps a %s placeholder.

    %s is invalid on the raw-sqlite3 SQLite branch of the hybrid connection.
    User-DB literals (information_schema / PG-regex ``~ %s``) are exempt.
    """
    source = py_file.read_text(encoding="utf-8")
    offenders = []
    for lineno, sql in _iter_execute_sql(source):
        if any(m in sql for m in _USERDB_MARKERS):
            continue
        if "%s" in sql:
            offenders.append((lineno, sql.replace("\x00", "{…}")[:100]))
    assert not offenders, (
        f"{py_file.name}: execute() SQL still contains %s (breaks raw sqlite3): "
        + "; ".join(f"line {ln}: {txt!r}" for ln, txt in offenders)
    )


# ── Live round-trip fixtures ──────────────────────────────────────────────────
def _pin_sqlite(monkeypatch, tmp_db):
    for var in ("ICDEV_STORAGE_BACKEND", "ICDEV_CANVAS_STORAGE_BACKEND",
                "DDC_STORAGE_BACKEND"):
        monkeypatch.setenv(var, "sqlite")
    sys.modules.pop("tools.data_canvas.db.init_db", None)
    from tools.data_canvas.db import init_db as ddc_init
    monkeypatch.setattr(ddc_init, "_DDC_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(ddc_init, "DB_PATH", Path(tmp_db), raising=False)
    return ddc_init


def _create_schema(ddc_init, tmp_db):
    conn = sqlite3.connect(tmp_db)
    try:
        conn.executescript(ddc_init.SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _raw_sqlite_factory(tmp_db):
    """A get_connection() stand-in mirroring the module's SQLite branch:
    a raw sqlite3 connection (row_factory=Row) — the exact driver that rejects %s.
    """
    def _conn():
        c = sqlite3.connect(tmp_db)
        c.row_factory = sqlite3.Row
        return c
    return _conn


def _fresh_db(monkeypatch, tag):
    tmp_db = os.path.join(tempfile.gettempdir(), f"cvxsql07_{tag}_{uuid.uuid4().hex}.db")
    ddc_init = _pin_sqlite(monkeypatch, tmp_db)
    _create_schema(ddc_init, tmp_db)
    return tmp_db


# ── The two baseline-failing sites, at statement level ────────────────────────
def test_lineage_classification_filter_runs_on_raw_sqlite(monkeypatch):
    """The /data/lineage classification filter (was WHERE classification=%s) runs
    on raw sqlite3 with positional ? and round-trips through translate_sql for PG.
    """
    tmp_db = _fresh_db(monkeypatch, "lineage")
    did = uuid.uuid4().hex
    conn = sqlite3.connect(tmp_db)
    try:
        conn.execute(
            "INSERT INTO data_designs (id, name) VALUES (?, ?)", (did, "d")
        )
        for cls in ("CUI", "SECRET"):
            conn.execute(
                "INSERT INTO dd_lineage (id, design_id, source_node_id, "
                "target_node_id, classification) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, did, "s", "t", cls),
            )
        conn.commit()
        sql = ("SELECT id, classification FROM dd_lineage "
               "WHERE classification=? ORDER BY created_at")
        rows = conn.execute(sql, ("CUI",)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1 and rows[0][1] == "CUI"
    # PG render is all-%s, no surviving ?.
    pg = translate_sql(sql, "postgresql")
    assert "?" not in pg and pg.count("%s") == 1


def test_policy_audit_insert_runs_on_raw_sqlite(monkeypatch):
    """dm_policy_audit_log INSERT (was VALUES (%s,...)) runs on raw sqlite3 and
    renders all-%s for PG — mirrors check_ext_access's audit write.
    """
    tmp_db = _fresh_db(monkeypatch, "audit")
    sql = (
        "INSERT INTO dm_policy_audit_log "
        "(id, policy_id, user, resource, decision, reason, method, classification) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    rid = uuid.uuid4().hex
    conn = sqlite3.connect(tmp_db)
    try:
        conn.execute(sql, (rid, "", "system", "{}", 1, "ok", "local", "CUI // SP-CTI"))
        conn.commit()
        got = conn.execute(
            "SELECT decision FROM dm_policy_audit_log WHERE id=?", (rid,)
        ).fetchone()
    finally:
        conn.close()
    assert got is not None and got[0] == 1
    pg = translate_sql(sql, "postgresql")
    assert "?" not in pg and pg.count("%s") == 8


# ── The three dynamic-UPDATE f-strings, driven end-to-end on raw sqlite3 ───────
_UPDATE_CASES = [
    # (module, create_table, insert_cols, update_fn, field, value)
    ("tools.data_canvas.data_mesh.domain_manager", "dm_domains",
     "update_domain", "description", "updated-desc"),
    ("tools.data_canvas.data_mesh.product_registry", "dm_data_products",
     "update_product", "description", "updated-desc"),
    ("tools.data_canvas.data_mesh.contract_engine", "dm_data_contracts",
     "update_contract", "version", "9.9.9"),
]


@pytest.mark.parametrize("mod_name,table,update_fn,field,value", _UPDATE_CASES)
def test_dynamic_update_runs_on_raw_sqlite(monkeypatch, mod_name, table, update_fn,
                                           field, value):
    """The f-string UPDATEs — whose trailing `updated_at=%s WHERE id=%s` broke on
    raw sqlite3 — now update a real row through the live module function.
    """
    tmp_db = _fresh_db(monkeypatch, table)
    rid = uuid.uuid4().hex
    conn = sqlite3.connect(tmp_db)
    try:
        conn.execute(f"INSERT INTO {table} (id, name) VALUES (?, ?)", (rid, "orig"))
        conn.commit()
    finally:
        conn.close()

    mod = importlib.import_module(mod_name)
    monkeypatch.setattr(mod, "get_connection", _raw_sqlite_factory(tmp_db))
    result = getattr(mod, update_fn)(rid, {field: value})
    assert result is not None, f"{update_fn} returned None (update failed)"

    conn = sqlite3.connect(tmp_db)
    try:
        got = conn.execute(
            f"SELECT {field}, updated_at FROM {table} WHERE id=?", (rid,)
        ).fetchone()
    finally:
        conn.close()
    assert got is not None and got[0] == value, f"{table}.{field} not updated"


def test_dynamic_update_statement_pg_round_trip():
    """The canonical dynamic-UPDATE tail renders all-%s for PG (no surviving ?)."""
    sql = "UPDATE dm_domains SET description=?, updated_at=? WHERE id=?"
    pg = translate_sql(sql, "postgresql")
    assert "?" not in pg and pg.count("%s") == 3
    lite = translate_sql(sql, "sqlite")
    assert lite.count("?") == 3 and "%s" not in lite
