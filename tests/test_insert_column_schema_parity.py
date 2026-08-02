# CUI // SP-CTI
"""Regression guard for the INSERT/schema drift triaged by swp-scan-01.

``tools/lint/insert_column_linter.py`` parses every *static*
``INSERT INTO <table> (<columns>)`` under ``tools/`` and checks the named
columns against the live database schema.  At the start of swp-scan-01 it
reported 126 statements naming a column the table does not have; almost all of
them sat inside a bare ``except Exception: pass``, so they had been failing
silently for as long as the columns had been missing.

Two things have to stay true from here on, and this module asserts both:

1. **No new drift, and no regression of a fix.**  :func:`test_no_new_insert_column_drift`
   re-runs the scanner against the live schema and asserts the surviving
   ``column_absent`` findings are *exactly* :data:`ACCEPTED_COLUMN_ABSENT` — the
   fifteen sites that write to a database other than the icdev instance the
   scanner reads (category iii), plus the one documented intentional skip.
   Adding a column to an INSERT without adding it to the schema fails here, and
   so does reverting any of the fixes.

2. **The rows the migration unblocked actually persist.**
   :func:`test_migration_329_columns_persist_a_row` builds each affected table in
   its *pre*-migration shape on a throwaway SQLite database, applies migration
   329, writes a row naming the added columns, and reads it back.  Before the
   migration that INSERT raises; after it the row round-trips.

The full per-finding classification is in
``docs/database/insert-column-schema-parity.md``.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = (
    _REPO_ROOT / "tools" / "db" / "migrations" / "329_insert_column_schema_parity" / "up.py"
)


# ---------------------------------------------------------------------------
# (1) Scanner regression guard
# ---------------------------------------------------------------------------

# Every ``column_absent`` finding that is expected to survive, keyed by
# "<file>::<table>".  Each is either a scanner false positive (category iii —
# the site does not write to the icdev database the scanner reads) or a
# documented intentional skip.  A finding NOT in this map is new drift; an entry
# here that no longer fires means a site moved and the note is stale.  Both fail.
ACCEPTED_COLUMN_ABSENT: dict[str, str] = {
    # --- category (iii): the site writes to a different database -------------
    "tools/saas/tenant_manager.py::tenants": (
        "get_platform_connection() resolves to data/platform.db, whose tenants "
        "table has compartments."
    ),
    "tools/saas/tenant_manager.py::users": (
        "Same platform connection; platform.db users has compartments."
    ),
    "tools/sg/supply_chain_bridge.py::sg_entities": (
        "_geo_upsert_entity() is handed the apps/geosigint SQLite connection, "
        "which carries the entity_name/theater/lat/lon shape."
    ),
    "tools/sg/supply_chain_bridge.py::sg_kg_nodes": (
        "_geo_upsert_kg_node() writes geosigint.db, which has all ten columns."
    ),
    "tools/ais/ais_importer.py::sg_tracks": (
        "get_connection is imported from apps.geosigint.models, not "
        "tools.db.storage; geosigint.db sg_tracks has the AIS shape."
    ),
    "tools/rag/sqlite_vector_store.py::rag_chunks": (
        "SQLiteVectorStore._get_conn() is deliberately pinned to its own .db "
        "file; sign_bits is a SQLite-only quantisation column."
    ),
    "tools/appforge/reflexes/build.py::kg_edges": (
        "The INSERT lives inside an f-string that is generated child-app "
        "source. It is never executed here; it targets the generated app's DB."
    ),
    # tools/ndc/seed_dewie_demo.py::ni_devices is NOT listed, and deliberately
    # so. It is still a category (iii) site — it opens
    # sqlite3.connect('data/network_canvas.db') at module level and never
    # touches the icdev instance — but it no longer produces a finding to
    # accept. Migration 329 added downstream_count / properties_json /
    # annual_maintenance_cost to the icdev copy of ni_devices, which already
    # carried rack_location and criticality_score, so the icdev table became a
    # superset of what the seed writes and the scanner fell silent. An entry
    # here would assert a finding that no longer fires, which is exactly the
    # rot the expected-minus-actual half of this test exists to catch.
    "tools/playground/seed_data.py::projects": (
        "seed_playground_db() takes an explicit db_path and CREATEs projects "
        "with compliance_score in the same executescript."
    ),
    "tools/playground/seed_data.py::poam_items": (
        "Same playground database; poam_items is created there with finding / "
        "milestone / due_date."
    ),
    "tools/finetune/doc_extractor.py::rag_ingestion_log": (
        "_get_db() takes a caller-supplied db_path and returns a sqlite3 "
        "connection (PRAGMA journal_mode=WAL, INSERT OR IGNORE)."
    ),
    "tools/kanban/seed_dsyn_kanban.py::canvas_events": (
        "Not executable SQL. The text sits inside a kanban task *description* "
        "telling a future session what to write; the scanner's AST walk cannot "
        "tell prose-in-a-string from a query."
    ),
    # --- category (i), resolved as a documented intentional skip -------------
    "tools/trading/db.py::ad_macro_indicators": (
        "One table name, two shapes: tools/trading/db.py CREATEs a run-scoped "
        "table, the live one is the global FathomDesk reference table. "
        "save_macro_indicators() now documents the mismatch and skips; the "
        "per-run values are already persisted in ad_macro_context.context_json, "
        "so nothing is lost."
    ),
}


def _live_backend_is_postgres() -> bool:
    return os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower().startswith("postgres")


@pytest.mark.skipif(
    not _live_backend_is_postgres(),
    reason=(
        "The scanner compares against the live schema. Under the SQLite test "
        "backend the database is a minimal fixture, so every table would report "
        "as absent and the comparison would be meaningless."
    ),
)
# The scan AST-parses every .py file under tools/ — ~25s on a warm filesystem
# cache and over the project-wide 30s budget on a cold one. Without this the
# test is not slow, it is *intermittently red*, which is worse. The ceiling is
# generous on purpose: it exists to catch a hang, not to police the runtime.
@pytest.mark.timeout(300)
def test_no_new_insert_column_drift():
    """Surviving column_absent findings must be exactly the accepted set."""
    from tools.lint.insert_column_linter import load_live_schema, scan

    result = scan(_REPO_ROOT, load_live_schema())
    actual = {
        f"{f['file']}::{f['table']}"
        for f in result["findings"]
        if f["kind"] == "column_absent"
    }
    expected = set(ACCEPTED_COLUMN_ABSENT)

    unexpected = sorted(actual - expected)
    assert not unexpected, (
        "New INSERT/schema drift — these statements name a column the live "
        "table does not have:\n  " + "\n  ".join(unexpected)
    )
    resolved = sorted(expected - actual)
    assert not resolved, (
        "These sites no longer report drift. If that is intentional, drop them "
        "from ACCEPTED_COLUMN_ABSENT so the allowlist cannot rot:\n  "
        + "\n  ".join(resolved)
    )


# ---------------------------------------------------------------------------
# (2) The migration actually lets a row land
# ---------------------------------------------------------------------------

# {table: (pre-329 CREATE, columns written, values)} — the CREATE is the shape
# the live database really had, so the INSERT below genuinely fails before the
# migration runs and genuinely succeeds after it.
ROUND_TRIP: dict[str, tuple[str, tuple[str, ...], tuple]] = {
    # R3 — TTX cost telemetry was never persisted.
    "ttx_api_log": (
        "CREATE TABLE ttx_api_log (id TEXT PRIMARY KEY, endpoint TEXT)",
        ("id", "endpoint", "token_count", "cost_usd"),
        ("ttx-1", "/v1/messages", 1024, 0.0153),
    ),
    # R3 — a TRUST invariant: without these an INTSUM cannot record whether its
    # paragraphs were grounded, so the citation gate had nothing to read at rest.
    "sg_intsums": (
        "CREATE TABLE sg_intsums (id TEXT PRIMARY KEY, title TEXT)",
        ("id", "title", "grounding_status", "grounding_json"),
        ("intsum-1", "Theater summary", "grounded", '{"sources": 3}'),
    ),
    # R4 — migration 067 created the web-request log; migration 213's billing
    # CREATE TABLE IF NOT EXISTS silently no-opped against it.
    "usage_events": (
        "CREATE TABLE usage_events (id TEXT PRIMARY KEY, route TEXT, "
        "method TEXT, status_code INTEGER)",
        ("id", "tenant_id", "event_type", "quantity", "model"),
        ("ue-1", "tenant-a", "llm.completion", 2.0, "claude-opus-5"),
    ),
    # R3 — the DIC AI-provenance and review-gate fields.
    "dic_documents": (
        "CREATE TABLE dic_documents (id TEXT PRIMARY KEY, title TEXT)",
        ("id", "title", "origin", "status"),
        ("dic-1", "Runbook", "ai_generated", "pending_review"),
    ),
    # R4 — the Digital Program Twin's per-dimension columns never landed
    # because the Network Canvas won the CREATE race for this table name.
    # sim_type is nullable here: relaxing the NC's NOT NULL is a PostgreSQL-only
    # step (see test_migration_329_relaxes_the_network_canvas_not_null), and this
    # case is about the columns being added.
    "simulation_results": (
        "CREATE TABLE simulation_results (id TEXT PRIMARY KEY, topology_id TEXT, "
        "sim_type TEXT, input_json TEXT, result_json TEXT, ran_at TEXT)",
        ("id", "scenario_id", "dimension", "metric_name", "delta_pct"),
        ("sim-1", "scenario-a", "cost", "annual_spend", -12.5),
    ),
}


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_329", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pre_migration_db(tmp_path):
    """A SQLite database carrying the exact pre-329 shape of each table."""
    conn = sqlite3.connect(tmp_path / "parity.db")
    for create, _, _ in ROUND_TRIP.values():
        conn.execute(create)
    conn.commit()
    yield conn
    conn.close()


def test_the_insert_fails_before_the_migration(pre_migration_db):
    """Guards the fixture: these columns really are absent to begin with."""
    for table, (_, columns, values) in ROUND_TRIP.items():
        placeholders = ", ".join("?" for _ in columns)
        with pytest.raises(sqlite3.OperationalError):
            pre_migration_db.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )


def test_migration_329_columns_persist_a_row(pre_migration_db):
    """After migration 329 the previously-rejected row round-trips."""
    _load_migration().up(pre_migration_db)

    for table, (_, columns, values) in ROUND_TRIP.items():
        placeholders = ", ".join("?" for _ in columns)
        pre_migration_db.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
    pre_migration_db.commit()

    for table, (_, columns, values) in ROUND_TRIP.items():
        row = pre_migration_db.execute(
            f"SELECT {', '.join(columns)} FROM {table} WHERE id = ?", (values[0],)
        ).fetchone()
        assert row is not None, f"{table}: the row did not persist"
        assert tuple(row) == values, f"{table}: persisted values differ"


def test_migration_329_declares_every_column_it_adds(pre_migration_db):
    """Every column in COLUMNS lands on a table that exists."""
    module = _load_migration()
    module.up(pre_migration_db)

    for table in ROUND_TRIP:
        live = {
            str(r[1]).lower()
            for r in pre_migration_db.execute(f'PRAGMA table_info("{table}")')
        }
        for column, _type in module.COLUMNS.get(table, []):
            assert column.lower() in live, f"{table}.{column} was not added"


def test_migration_329_is_idempotent(pre_migration_db):
    """Re-running adds nothing and does not raise — required by the runner."""
    module = _load_migration()
    module.up(pre_migration_db)
    before = {
        table: {str(r[1]) for r in pre_migration_db.execute(f'PRAGMA table_info("{table}")')}
        for table in ROUND_TRIP
    }

    module.up(pre_migration_db)

    after = {
        table: {str(r[1]) for r in pre_migration_db.execute(f'PRAGMA table_info("{table}")')}
        for table in ROUND_TRIP
    }
    assert before == after


def test_migration_329_relaxes_the_network_canvas_not_null():
    """Adding the twin's columns is not enough while sim_type stays NOT NULL.

    Where the Network Canvas won the CREATE race for ``simulation_results`` it
    left ``sim_type NOT NULL``.  The Digital Program Twin names
    scenario_id / dimension / metric_name and never sim_type, so once 329 adds
    those columns the INSERT stops raising ``UndefinedColumn`` and starts
    raising ``NotNullViolation`` — still rejected, just further along.
    """
    assert ("simulation_results", "sim_type") in _load_migration().DROP_NOT_NULL


@pytest.mark.skipif(
    not _live_backend_is_postgres(),
    reason="ALTER COLUMN DROP NOT NULL is PostgreSQL-only; SQLite cannot express it.",
)
def test_live_simulation_results_accepts_a_twin_row():
    """On the live PG schema the twin's column set is writable."""
    from tools.db.storage import get_connection

    conn = get_connection()
    # rls-bypass: information_schema is catalog metadata, not tenant rows, and the RLS
    # predicate references classification/tenant_id columns system catalogs do not have.
    conn.set_security_context(None)
    blocking = [
        dict(r)["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'simulation_results' "
            "AND is_nullable = 'NO' AND column_default IS NULL"
        ).fetchall()
    ]
    twin_columns = {"scenario_id", "dimension", "metric_name"}
    unwritable = sorted(set(blocking) - twin_columns)
    assert not unwritable, (
        "simulation_results has NOT NULL columns with no default that the "
        "Digital Program Twin never writes, so its INSERT is still rejected: "
        + ", ".join(unwritable)
    )


def test_network_canvas_simulation_rows_are_carried_across(pre_migration_db):
    """The NC rows move to nc_simulation_results rather than being dropped."""
    pre_migration_db.execute(
        "INSERT INTO simulation_results (id, topology_id, sim_type, input_json, "
        "result_json, ran_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("nc-1", "topo-a", "failover", "{}", '{"ok": true}', "2026-08-02T00:00:00Z"),
    )
    pre_migration_db.commit()

    _load_migration().up(pre_migration_db)

    row = pre_migration_db.execute(
        "SELECT topology_id, sim_type, result_json FROM nc_simulation_results "
        "WHERE id = ?",
        ("nc-1",),
    ).fetchone()
    assert row is not None, "the Network Canvas row was not carried across"
    assert tuple(row) == ("topo-a", "failover", '{"ok": true}')
