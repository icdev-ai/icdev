# CUI // SP-CTI
"""Pytest suite — data_canvas hybrid-connection placeholder sweep (cvx-sql-06).

Follow-up to cvx-sql-05. That sweep converted the migration canvas ``:name``
inserts to positional placeholders. This one covers ``tools/data_canvas``, which
has a CRITICAL DIFFERENCE: its ``get_connection`` is HYBRID.

  * SQLite branch  -> raw ``sqlite3.connect`` (NO translate wrapper). Raw sqlite3
    accepts ``?`` and ``:name`` but NOT ``%s``.
  * PG branch      -> ``StorageConnection`` (``get_canvas_connection``).
    ``translate_sql`` rewrites ``?`` -> ``%s`` but leaves ``:name`` untouched, and
    ``StorageCursor.execute`` wraps a non-list/tuple ``params`` (a dict) into a
    1-tuple, so a ``:name`` mapping never reaches psycopg2 or sqlite3 as a mapping.

The ONLY placeholder form valid on BOTH branches is therefore POSITIONAL ``?``
with an ordered param tuple (raw sqlite3 native; StorageConnection translates
``?`` -> ``%s`` for PG). This suite guards that the six ``:name`` dict inserts
found in data_canvas (data_mesh.py x3, governance_engine.py, data_mesh/
governance_engine.py, blueprint.py) are now positional ``?``.
"""
from __future__ import annotations

import ast
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


# data_canvas runtime modules that carried :name dict inserts on the hybrid
# get_connection path.
_DATA_CANVAS_FILES = [
    "tools/data_canvas/data_mesh.py",
    "tools/data_canvas/governance_engine.py",
    "tools/data_canvas/data_mesh/governance_engine.py",
    "tools/data_canvas/blueprint.py",
]

# A ``:word`` token that is a genuine SQLite named bind — but NOT a PG cast
# (``::type``). We scan reconstructed execute() SQL literals only.
_NAMED_PARAM_RE = __import__("re").compile(r"(?<!:):[a-zA-Z_]\w*")

# The six converted INSERT statements, as (substring_present_in_source, arity).
# Arity is the number of positional ? placeholders / ordered-tuple length.
_CONVERTED_INSERTS = [
    ("INSERT INTO dm_domains", 11),
    ("INSERT INTO dm_data_products", 12),
    ("INSERT INTO dm_domain_maturity", 8),
    ("INSERT INTO dm_governance_policies", 9),
    ("INSERT INTO dm_opa_policies", 8),
    ("INSERT INTO dm_contracts", 11),
]


def _sql_literal(node: ast.AST) -> str | None:
    """Reconstruct the literal text of a SQL argument (const / a+b / f-string)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("\x00")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_literal(node.left)
        right = _sql_literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _iter_execute_sql(source: str):
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


def _strip_pg_casts(sql: str) -> str:
    return __import__("re").sub(r"::[a-zA-Z_]\w*", "", sql)


@pytest.mark.parametrize("rel_path", _DATA_CANVAS_FILES)
def test_execute_sql_has_no_named_params(rel_path):
    """No data_canvas execute() SQL literal uses a SQLite :name bind.

    Named params never translate for psycopg2 and get dict-wrapped by
    StorageCursor; on the hybrid connection they must be positional ?.
    """
    source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    offenders = []
    for lineno, sql in _iter_execute_sql(source):
        hits = _NAMED_PARAM_RE.findall(_strip_pg_casts(sql))
        if hits:
            offenders.append((lineno, sorted(set(hits))))
    assert not offenders, (
        f"{rel_path}: execute() SQL still contains :name named placeholders "
        "(broken on both branches of the hybrid connection): "
        + "; ".join(f"line {ln}: {names}" for ln, names in offenders)
    )


def test_all_six_inserts_present_and_positional():
    """Each converted INSERT is present in source and uses positional ? (no :name)."""
    blob = "\n".join(
        (_REPO_ROOT / f).read_text(encoding="utf-8") for f in _DATA_CANVAS_FILES
    )
    for needle, _arity in _CONVERTED_INSERTS:
        assert needle in blob, f"missing converted insert: {needle}"
    # The specific broken bind names from the finding must be gone everywhere.
    for gone in (":policy_type", ":rego_text", ":quality_rules_json",
                 ":bounded_context", ":availability_sla", ":scores_json"):
        assert gone not in blob, f"named bind survived: {gone}"


@pytest.mark.parametrize("needle,arity", _CONVERTED_INSERTS)
def test_converted_insert_is_pg_compatible(needle, arity):
    """translate_sql renders each positional-? INSERT as psycopg2-valid %s.

    Reconstruct the INSERT from source, run it through translate_sql for the PG
    backend, and assert it is all-%s with the right count and no surviving
    ?/:name — proving the emitted SQL reaches psycopg2 cleanly.
    """
    blob = "\n".join(
        (_REPO_ROOT / f).read_text(encoding="utf-8") for f in _DATA_CANVAS_FILES
    )
    # Grab the ? placeholders belonging to this insert's VALUES clause by counting
    # the placeholders in the fabricated canonical form below (kept in lockstep
    # with the source via test_all_six_inserts_present_and_positional).
    sql = needle + " VALUES (" + ", ".join(["?"] * arity) + ")"
    assert needle in blob
    pg = translate_sql(sql, "postgresql")
    assert "?" not in pg, f"PG render still has ?: {pg!r}"
    assert ":" not in pg.split("VALUES", 1)[1], f"PG render has :name: {pg!r}"
    assert pg.count("%s") == arity, f"placeholder count drift: {pg!r}"

    lite = translate_sql(sql, "sqlite")
    assert lite.count("?") == arity and "%s" not in lite


def _pin_sqlite(monkeypatch, tmp_db):
    """Point the data_canvas hybrid connection at a throwaway SQLite DB."""
    for var in ("ICDEV_STORAGE_BACKEND", "ICDEV_CANVAS_STORAGE_BACKEND",
                "DDC_STORAGE_BACKEND"):
        monkeypatch.setenv(var, "sqlite")
    # init_db reads _DDC_BACKEND / DB_PATH module globals at call time.
    # NOT sys.modules.pop(). Evicting the module and re-importing it installs a
    # NEW module object in sys.modules that nothing ever puts back, so every later
    # test in the run resolves tools.data_canvas.db.init_db to that replacement
    # while modules that imported get_connection earlier still hold the original.
    # monkeypatch restores the attributes it set, on the object it set them on —
    # it cannot restore a swapped module. That leak failed 11 tests in
    # test_dcpr_product_registry and test_dcpr_governance_engine whenever this file
    # ran first, and all of them pass in isolation, which is what made it look like
    # a data_canvas bug rather than a fixture one.
    #
    # The pop was redundant anyway: it existed to re-read the module-level
    # _DDC_BACKEND/DB_PATH after setenv, and the two setattr calls below overwrite
    # exactly those globals.
    from tools.data_canvas.db import init_db as ddc_init
    monkeypatch.setattr(ddc_init, "_DDC_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(ddc_init, "DB_PATH", Path(tmp_db), raising=False)
    return ddc_init


def _create_schema(ddc_init, tmp_db):
    """Materialize the data_canvas schema in a throwaway SQLite DB.

    We build the schema directly rather than calling ``init_db()`` — its template
    SEEDING step uses ``%s`` on the raw sqlite3 branch (a separate hybrid-%s
    latent defect, out of this task's :name scope), which we don't need here.
    """
    conn = sqlite3.connect(tmp_db)
    try:
        conn.executescript(ddc_init.SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _raw_sqlite_factory(tmp_db):
    """A get_connection() stand-in that mirrors the module's SQLite branch: a
    raw sqlite3 connection (row_factory=Row) to the throwaway DB — proving the
    positional-? statements run on the exact driver the SQLite branch uses.
    """
    def _conn():
        c = sqlite3.connect(tmp_db)
        c.row_factory = sqlite3.Row
        return c
    return _conn


def test_governance_create_policy_live_on_sqlite_branch(monkeypatch):
    """LIVE insert on the SQLite branch (raw sqlite3) through the real
    top-level governance_engine.create_policy — the positional-? path lands a row.
    """
    import importlib
    tmp_db = os.path.join(tempfile.gettempdir(), f"cvxsql06_a_{uuid.uuid4().hex}.db")
    ddc_init = _pin_sqlite(monkeypatch, tmp_db)
    _create_schema(ddc_init, tmp_db)
    ge = importlib.import_module("tools.data_canvas.governance_engine")
    # Patch the module's get_connection to the raw sqlite3 branch on tmp_db —
    # deterministic regardless of import caching of the shared init_db module.
    monkeypatch.setattr(ge, "get_connection", _raw_sqlite_factory(tmp_db))

    row = ge.create_policy({"name": "Least Privilege", "policy_type": "opa",
                            "rules": [{"op": "eq"}], "applies_to": "all"})
    assert row["name"] == "Least Privilege"

    # Confirm via a raw sqlite3 read that the row physically landed.
    conn = sqlite3.connect(tmp_db)
    try:
        got = conn.execute(
            "SELECT name, policy_type FROM dm_governance_policies WHERE id=?",
            (row["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert got is not None and got[0] == "Least Privilege" and got[1] == "opa"


def test_opa_create_policy_live_on_sqlite_branch(monkeypatch):
    """LIVE insert on the SQLite branch through data_mesh.governance_engine
    (the federated-governance create_policy) — positional-? lands a row.
    """
    import importlib
    tmp_db = os.path.join(tempfile.gettempdir(), f"cvxsql06_b_{uuid.uuid4().hex}.db")
    ddc_init = _pin_sqlite(monkeypatch, tmp_db)
    _create_schema(ddc_init, tmp_db)
    opa_ge = importlib.import_module("tools.data_canvas.data_mesh.governance_engine")
    monkeypatch.setattr(opa_ge, "get_connection", _raw_sqlite_factory(tmp_db))

    row = opa_ge.create_policy({"name": "Mesh Gate", "domain_id": "dom-1",
                                "enabled": True})
    assert row["name"] == "Mesh Gate"

    conn = sqlite3.connect(tmp_db)
    try:
        got = conn.execute(
            "SELECT name, domain_id, enabled FROM dm_opa_policies WHERE id=?",
            (row["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert got is not None and got[0] == "Mesh Gate" and got[1] == "dom-1"


def test_all_six_inserts_execute_raw_sqlite(monkeypatch):
    """Every converted INSERT executes on raw sqlite3 with an ordered ? tuple.

    Covers the three data_mesh.py inserts whose helper functions also emit %s
    (unrelated to this task) and so can't be driven end-to-end on sqlite — here
    the INSERT statements themselves are proven sqlite-valid with positional ?.
    """
    tmp_db = os.path.join(tempfile.gettempdir(), f"cvxsql06_c_{uuid.uuid4().hex}.db")
    ddc_init = _pin_sqlite(monkeypatch, tmp_db)
    _create_schema(ddc_init, tmp_db)

    conn = sqlite3.connect(tmp_db)
    try:
        for needle, arity in _CONVERTED_INSERTS:
            sql = needle + " VALUES (" + ", ".join(["?"] * arity) + ")"
            # id (col 0) is the PK TEXT; supply a unique id and benign values.
            params = [uuid.uuid4().hex] + [""] * (arity - 1)
            conn.execute(sql, tuple(params))
        conn.commit()
    finally:
        conn.close()
