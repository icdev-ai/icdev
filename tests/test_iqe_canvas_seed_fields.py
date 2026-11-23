# CUI // SP-CTI
"""Canvas seeds run on the executor and name only fields their collection provides.

ccc, dsoc and pmc seeds named collections that only the canvases' natural-language
``handle_query`` router served -- nothing registered them on the executor, so
/iqe/run could never answer them. ohc seeds selected and filtered on fields
(``model_name``, ``promoted_at``, ``drift_detected``, ...) its adapter never
returns, which the lenient executor turns into silent ``None``: a filter that
matches nothing and a column that is always empty.

Each canvas's real schema (the one its init_db.py creates) is copied column for
column, without constraints, into a scratch in-memory SQLite DB with one row in
every table, so every adapter returns a row whose keys are exactly its columns.
Every field a seed filters or selects must be one of them.
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from tools.iqe import catalog
from tools.iqe.ast_nodes import AttrRef, BinOp, Literal
from tools.iqe.executor import _default
from tools.iqe.parser import parse

SEEDS = Path(__file__).resolve().parents[1] / "context" / "iqe" / "queries"

#: seed directory -> (module holding the canvas schema, schema attribute,
#: attribute of its (pg_sql, sqlite_sql) column migrations, or None). init_db()
#: applies both, so both make up the real schema.
CANVASES = {
    "ccc_canvas": ("tools.ccc_canvas.db.init_db", "SCHEMA_SQLITE", "_CCC_MIGRATIONS"),
    "dsoc_canvas": ("tools.dsoc_canvas.db.init_db", "SCHEMA_SQLITE", None),
    "pmc_canvas": ("tools.pmc_canvas.db.init_db", "SCHEMA_SQLITE", None),
    "ohc": ("tools.ops_hub.db.init_db", "SCHEMA", None),
}


def _loose_db(schema: str, migrations=()) -> sqlite3.Connection:
    """The schema's tables and columns, no constraints, one '1' row in each."""
    real = sqlite3.connect(":memory:")
    loose = sqlite3.connect(":memory:")
    try:
        real.executescript(schema)
        for _pg_sql, sqlite_sql in migrations:
            real.execute(sqlite_sql)
        tables = [r[0] for r in real.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            cols = [r[1] for r in real.execute(f'PRAGMA table_info("{table}")')]
            loose.execute(f'CREATE TABLE "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)})')
            loose.execute(f'INSERT INTO "{table}" VALUES ({", ".join("?" for _ in cols)})',
                          ["1"] * len(cols))
    finally:
        real.close()
    return loose


def _fields(node, var: str) -> set[str]:
    """First attribute name of every var-qualified reference under *node*."""
    out: set[str] = set()
    if isinstance(node, AttrRef):
        parts = node.parts[1:] if node.parts and node.parts[0] == var else node.parts
        if parts:
            out.add(parts[0])
    elif isinstance(node, BinOp):
        for side in (node.left, node.right):
            if side is not None and not isinstance(side, Literal):
                out |= _fields(side, var)
    return out


def _cases():
    for canvas in CANVASES:
        for path in sorted((SEEDS / canvas).glob("*.iqe")):
            yield pytest.param(canvas, path, id=f"{canvas}/{path.name}")


@pytest.fixture(scope="module", autouse=True)
def _adapters():
    catalog.load_all_adapters()


@pytest.mark.parametrize("canvas,path", list(_cases()))
def test_seed_runs_and_every_field_is_provided(canvas, path):
    module, attr, migrations = CANVASES[canvas]
    mod = importlib.import_module(module)
    conn = _loose_db(getattr(mod, attr), getattr(mod, migrations) if migrations else ())
    try:
        ast = parse(path.read_text(encoding="utf-8"))
        name = str(ast.collection)
        assert name in _default._registry, f"{name} is not registered on the executor"
        rows = _default._registry[name](conn)
    finally:
        conn.close()
    assert rows, f"{name} returned no row from a table holding one"
    provided = set(rows[0])
    wanted = set()
    for clause in ast.where_clauses:
        wanted |= _fields(clause.predicate, ast.var)
    for field in ast.select.fields:
        wanted |= _fields(field, ast.var)
    missing = sorted(wanted - provided)
    assert not missing, f"{path.name}: {name} provides no {missing}"


def test_geospatial_seed_runs_over_sg_raw_signals():
    """geospatial.raw_signals (registered by the strategos adapter) serves the mcip_geospatial seed."""
    from tools.iqe.executor import execute_query

    conn = sqlite3.connect(":memory:")
    try:
        # The canonical definition, tools/db/schema/pg_consolidated.sql.
        conn.execute("""CREATE TABLE sg_raw_signals (
            id integer NOT NULL,
            url_hash text NOT NULL,
            title text NOT NULL,
            body text,
            source text,
            signal_date text,
            geo_hint text,
            tier_used text,
            created_at text NOT NULL,
            processed integer DEFAULT 0 NOT NULL,
            classification character varying(50) DEFAULT 'CUI'
        )""")
        conn.executemany(
            "INSERT INTO sg_raw_signals (id, url_hash, title, body, source, created_at) "
            "VALUES (?,?,?,?,?,?)",
            # `contains` is case-sensitive and the seed's keywords are lowercase.
            [(1, "h1", "tanker traffic in the strait of hormuz", "", "feed-a", "2026-11-01"),
             (2, "h2", "Quarterly earnings call", "", "feed-b", "2026-11-02")])
        ast = parse((SEEDS / "mcip_geospatial" / "01_osint_feeds_recent.iqe").read_text(encoding="utf-8"))
        rows = execute_query(ast, conn)
    finally:
        conn.close()
    assert [r["title"] for r in rows] == ["tanker traffic in the strait of hormuz"]


def test_strategos_collections_are_registered():
    """The adapter imported a non-existent tools.iqe.registry and registered nothing."""
    import tools.iqe.adapters.strategos  # noqa: F401  (import-for-side-effect)
    from tools.iqe.executor import list_collections

    assert {"strategos.signals", "strategos.conflict_events", "strategos.leadership_briefs",
            "strategos.sio_assessments", "geospatial.raw_signals"} <= set(list_collections())


@pytest.mark.parametrize("question,ctype", [("show 400g circuits", "400g"), ("list 100g circuits", "100g")])
def test_ccc_circuit_type_question_binds_the_type_as_a_parameter(question, ctype):
    """circuits_adapter built `WHERE circuit_type='{t}'` with an f-string; it binds it now."""
    from unittest.mock import MagicMock

    from tools.iqe.adapters.ccc import circuits_adapter

    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    circuits_adapter(question, conn=conn)
    sql, params = conn.cursor.return_value.execute.call_args.args
    assert ctype not in sql and "?" in sql
    assert params == (ctype,)


def test_ccc_circuit_type_question_still_answers():
    from tools.ccc_canvas.db.init_db import SCHEMA_SQLITE
    from tools.iqe.adapters.ccc import circuits_adapter

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(SCHEMA_SQLITE)
        conn.executemany(
            "INSERT INTO ccc_circuits (circuit_id, circuit_type, carrier, bandwidth_gbps) VALUES (?,?,?,?)",
            [("C-1", "400g", "carrier-a", 400), ("C-2", "100g", "carrier-b", 100)])
        rows = circuits_adapter("show 400g circuits", conn=conn)
    finally:
        conn.close()
    assert [r["circuit_id"] for r in rows] == ["C-1"]
