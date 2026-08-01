# CUI // SP-CTI
"""Init/seed + round-trip tests for the Agentic AI Canvas DB (penta-aadc-03).

Regression: the SQLite fallback in ``tools/agentic_ai_canvas/db/init_db.py``
returned a RAW ``sqlite3.connect`` while every statement in the module (and in
``blueprint.py``) uses PostgreSQL ``%s`` placeholders. That raised
``sqlite3.OperationalError`` on the first statement whenever the backend
resolved to sqlite or the PG import failed.

The fix returns the storage shim's translating ``StorageConnection`` for the
sqlite path so ``%s`` → ``?`` translation and ``executescript()`` behave the
same as on PostgreSQL. These tests exercise init + seed + a design save/load
round-trip through the ``%s`` (PG-style) statement path under a forced sqlite
backend.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def init_db_mod(tmp_path, monkeypatch):
    """Load the AADC init_db module pinned to a temp sqlite file."""
    mod = importlib.import_module("tools.agentic_ai_canvas.db.init_db")
    db_path = tmp_path / "agentic_ai_canvas_test.db"
    # Pin backend + dedicated db file for this test (module-level globals).
    monkeypatch.setattr(mod, "_BACKEND", "sqlite", raising=True)
    monkeypatch.setattr(mod, "DB_PATH", db_path, raising=True)
    return mod


def test_get_connection_returns_translating_shim(init_db_mod):
    """The sqlite path must return a StorageConnection, never a raw sqlite3."""
    from tools.db.storage import StorageConnection

    conn = init_db_mod.get_connection()
    try:
        assert isinstance(conn, StorageConnection)
        assert not isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_init_and_seed_completes(init_db_mod):
    """init_db() runs schema + all three seed passes without OperationalError."""
    init_db_mod.init_db()

    conn = init_db_mod.get_connection()
    try:
        # Schema loaded via executescript — core tables exist.
        for tbl in ("aadc_designs", "aadc_templates", "aadc_snippets",
                    "aadc_cost_estimates", "aadc_aimc_model_refs"):
            conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()  # noqa: S608

        # Seeds populated (12 built-in templates + solution packs, 12 snippets).
        tpl = conn.execute("SELECT COUNT(*) FROM aadc_templates").fetchone()[0]
        snp = conn.execute("SELECT COUNT(*) FROM aadc_snippets").fetchone()[0]
        assert tpl >= 12
        assert snp >= 12
    finally:
        conn.close()


def test_design_save_load_round_trip_pg_style_placeholders(init_db_mod):
    """A design INSERT/SELECT using PG-style %s placeholders round-trips.

    This is the exact statement shape that raised OperationalError before the
    fix (raw sqlite3 cannot bind %s). Through the translating shim it succeeds.
    """
    init_db_mod.init_db()

    conn = init_db_mod.get_connection()
    try:
        conn.execute(
            "INSERT INTO aadc_designs (id, name, description, domain, graph_json) "
            "VALUES (%s,%s,%s,%s,%s)",
            ("d-test01", "Round Trip", "desc", "gov",
             '{"nodes":[],"edges":[]}'),
        )
        conn.commit()

        row = conn.execute(
            "SELECT id, name, domain FROM aadc_designs WHERE id=%s", ("d-test01",)
        ).fetchone()
        assert row is not None
        # Index access works cross-backend (matches blueprint.py usage).
        assert row[0] == "d-test01"
        assert row[1] == "Round Trip"

        # Update via %s placeholders (the save path shape) also translates.
        conn.execute(
            "UPDATE aadc_designs SET name=%s WHERE id=%s",
            ("Renamed", "d-test01"),
        )
        conn.commit()
        again = conn.execute(
            "SELECT name FROM aadc_designs WHERE id=%s", ("d-test01",)
        ).fetchone()
        assert again[0] == "Renamed"
    finally:
        conn.close()


def test_seed_is_idempotent(init_db_mod):
    """Re-running init_db() does not duplicate seeded templates/snippets."""
    init_db_mod.init_db()
    conn = init_db_mod.get_connection()
    try:
        tpl1 = conn.execute("SELECT COUNT(*) FROM aadc_templates").fetchone()[0]
        snp1 = conn.execute("SELECT COUNT(*) FROM aadc_snippets").fetchone()[0]
    finally:
        conn.close()

    init_db_mod.init_db()  # second run — seed guards should short-circuit
    conn = init_db_mod.get_connection()
    try:
        tpl2 = conn.execute("SELECT COUNT(*) FROM aadc_templates").fetchone()[0]
        snp2 = conn.execute("SELECT COUNT(*) FROM aadc_snippets").fetchone()[0]
    finally:
        conn.close()

    assert tpl1 == tpl2
    assert snp1 == snp2
