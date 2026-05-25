# CUI // SP-CTI
"""Integration tests for nc_simulation_* schema.

Covers:
  1. All three tables created via migration 037
  2. Foreign key chain: session → runs → artifacts
  3. Append-only policy registered in pre_tool_use hook
  4. canvas_type NOT NULL constraint
  5. JSON columns round-trip correctly (metadata, steps)

Uses icdev_db fixture from conftest.py for full-schema tests and local
fixtures for migration and FK-isolation tests.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

_MIG_037_PATH = (
    BASE_DIR / "tools" / "db" / "migrations" / "037_nc_simulation_tables" / "up.py"
)

_HOOK_PATH = BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py"

# SQLite DDL mirroring migration 037 (used by local fixtures)
_DDL = """
CREATE TABLE IF NOT EXISTS nc_simulation_sessions (
    id           TEXT NOT NULL PRIMARY KEY,
    canvas_type  TEXT NOT NULL,
    topology_id  TEXT,
    mode         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    metadata     TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS nc_simulation_runs (
    id          TEXT NOT NULL PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES nc_simulation_sessions(id),
    run_at      TEXT NOT NULL DEFAULT (datetime('now')),
    steps       TEXT NOT NULL DEFAULT '[]',
    summary     TEXT
);
CREATE TABLE IF NOT EXISTS nc_simulation_artifacts (
    id            TEXT NOT NULL PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES nc_simulation_runs(id),
    artifact_type TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def bare_db(tmp_path):
    """File-based SQLite DB with no simulation tables — for migration tests."""
    db_path = tmp_path / "bare.db"
    sqlite3.connect(str(db_path)).close()
    return db_path


@pytest.fixture()
def sim_conn():
    """In-memory SQLite connection with simulation tables and FK enforcement."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. Migration 037 creates all three tables from scratch
# ---------------------------------------------------------------------------

class TestMigration037TableCreation:
    """Verify migration 037 up() builds the schema on a fresh empty database."""

    def _load_mod(self):
        spec = importlib.util.spec_from_file_location("mig_037_up", _MIG_037_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_creates_all_three_tables(self, bare_db):
        mod = self._load_mod()
        mod.get_connection = lambda: sqlite3.connect(str(bare_db))
        mod.up()

        conn = sqlite3.connect(str(bare_db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()

        assert "nc_simulation_sessions" in tables
        assert "nc_simulation_runs" in tables
        assert "nc_simulation_artifacts" in tables

    def test_creates_all_indexes(self, bare_db):
        mod = self._load_mod()
        mod.get_connection = lambda: sqlite3.connect(str(bare_db))
        mod.up()

        conn = sqlite3.connect(str(bare_db))
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        conn.close()

        assert "idx_nc_sim_sessions_canvas_type" in indexes
        assert "idx_nc_sim_sessions_topology_id" in indexes
        assert "idx_nc_sim_runs_session_id" in indexes
        assert "idx_nc_sim_artifacts_run_id" in indexes
        assert "idx_nc_sim_artifacts_type" in indexes

    def test_idempotent_second_run(self, bare_db):
        mod = self._load_mod()
        mod.get_connection = lambda: sqlite3.connect(str(bare_db))
        mod.up()
        mod.up()  # CREATE TABLE IF NOT EXISTS — must not raise

        conn = sqlite3.connect(str(bare_db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "nc_simulation_sessions" in tables

    def test_sessions_table_columns(self, bare_db):
        mod = self._load_mod()
        mod.get_connection = lambda: sqlite3.connect(str(bare_db))
        mod.up()

        conn = sqlite3.connect(str(bare_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nc_simulation_sessions)")}
        conn.close()
        assert {"id", "canvas_type", "topology_id", "mode", "created_at", "metadata"} <= cols

    def test_runs_table_columns(self, bare_db):
        mod = self._load_mod()
        mod.get_connection = lambda: sqlite3.connect(str(bare_db))
        mod.up()

        conn = sqlite3.connect(str(bare_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nc_simulation_runs)")}
        conn.close()
        assert {"id", "session_id", "run_at", "steps", "summary"} <= cols

    def test_artifacts_table_columns(self, bare_db):
        mod = self._load_mod()
        mod.get_connection = lambda: sqlite3.connect(str(bare_db))
        mod.up()

        conn = sqlite3.connect(str(bare_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(nc_simulation_artifacts)")}
        conn.close()
        assert {"id", "run_id", "artifact_type", "content", "created_at"} <= cols

    def test_migration_tables_present_in_conftest_schema(self, icdev_db):
        """icdev_db fixture (conftest.py MINIMAL_ICDEV_SCHEMA) also contains the tables."""
        conn = sqlite3.connect(str(icdev_db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "nc_simulation_sessions" in tables
        assert "nc_simulation_runs" in tables
        assert "nc_simulation_artifacts" in tables


# ---------------------------------------------------------------------------
# 2. Foreign key chain: session → runs → artifacts
# ---------------------------------------------------------------------------

class TestForeignKeyChain:
    """Verify referential integrity across the three-table hierarchy."""

    def test_run_references_valid_session(self, sim_conn):
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('s1', 'ndc')"
        )
        sim_conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id) VALUES ('r1', 's1')"
        )
        sim_conn.commit()
        row = sim_conn.execute(
            "SELECT session_id FROM nc_simulation_runs WHERE id='r1'"
        ).fetchone()
        assert row["session_id"] == "s1"

    def test_artifact_references_valid_run(self, sim_conn):
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('s2', 'sdc')"
        )
        sim_conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id) VALUES ('r2', 's2')"
        )
        sim_conn.execute(
            "INSERT INTO nc_simulation_artifacts (id, run_id, artifact_type) "
            "VALUES ('a1', 'r2', 'mermaid')"
        )
        sim_conn.commit()
        row = sim_conn.execute(
            "SELECT run_id FROM nc_simulation_artifacts WHERE id='a1'"
        ).fetchone()
        assert row["run_id"] == "r2"

    def test_full_chain_join_query(self, sim_conn):
        """Three-way JOIN across the full session → run → artifact chain."""
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type, topology_id, mode) "
            "VALUES ('s3', 'ndc', 'topo-1', 'explain')"
        )
        sim_conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id, steps) "
            "VALUES ('r3', 's3', '[\"init\",\"route\",\"teardown\"]')"
        )
        sim_conn.execute(
            "INSERT INTO nc_simulation_artifacts (id, run_id, artifact_type, content) "
            "VALUES ('a2', 'r3', 'stig_delta', 'V-12345 applied')"
        )
        sim_conn.commit()

        row = sim_conn.execute(
            "SELECT s.canvas_type, r.id AS run_id, a.artifact_type "
            "FROM nc_simulation_sessions s "
            "JOIN nc_simulation_runs r ON r.session_id = s.id "
            "JOIN nc_simulation_artifacts a ON a.run_id = r.id "
            "WHERE s.id = 's3'"
        ).fetchone()

        assert row["canvas_type"] == "ndc"
        assert row["run_id"] == "r3"
        assert row["artifact_type"] == "stig_delta"

    def test_run_orphan_session_raises(self, sim_conn):
        with pytest.raises(sqlite3.IntegrityError):
            sim_conn.execute(
                "INSERT INTO nc_simulation_runs (id, session_id) "
                "VALUES ('r-bad', 'nonexistent-session')"
            )
            sim_conn.commit()

    def test_artifact_orphan_run_raises(self, sim_conn):
        with pytest.raises(sqlite3.IntegrityError):
            sim_conn.execute(
                "INSERT INTO nc_simulation_artifacts (id, run_id, artifact_type) "
                "VALUES ('a-bad', 'nonexistent-run', 'mermaid')"
            )
            sim_conn.commit()

    def test_one_session_many_runs(self, sim_conn):
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('s4', 'eda')"
        )
        for i in range(3):
            sim_conn.execute(
                "INSERT INTO nc_simulation_runs (id, session_id) VALUES (?, 's4')",
                (f"r4-{i}",),
            )
        sim_conn.commit()
        count = sim_conn.execute(
            "SELECT COUNT(*) FROM nc_simulation_runs WHERE session_id='s4'"
        ).fetchone()[0]
        assert count == 3

    def test_one_run_many_artifacts(self, sim_conn):
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('s5', 'bdc')"
        )
        sim_conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id) VALUES ('r5', 's5')"
        )
        for atype in ["mermaid", "summary", "stig_delta", "code_snippet"]:
            sim_conn.execute(
                "INSERT INTO nc_simulation_artifacts (id, run_id, artifact_type) "
                "VALUES (?, 'r5', ?)",
                (f"a5-{atype}", atype),
            )
        sim_conn.commit()
        count = sim_conn.execute(
            "SELECT COUNT(*) FROM nc_simulation_artifacts WHERE run_id='r5'"
        ).fetchone()[0]
        assert count == 4

    def test_fk_chain_using_icdev_db(self, icdev_db):
        """Same FK chain test exercised against the full MINIMAL_ICDEV_SCHEMA DB."""
        conn = sqlite3.connect(str(icdev_db))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('fk-s1', 'ndc')"
        )
        conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id) VALUES ('fk-r1', 'fk-s1')"
        )
        conn.execute(
            "INSERT INTO nc_simulation_artifacts (id, run_id, artifact_type) "
            "VALUES ('fk-a1', 'fk-r1', 'mermaid')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT COUNT(*) FROM nc_simulation_artifacts WHERE run_id='fk-r1'"
        ).fetchone()
        assert row[0] == 1
        conn.close()


# ---------------------------------------------------------------------------
# 3. Append-only policy — all three tables in APPEND_ONLY_TABLES hook list
# ---------------------------------------------------------------------------

class TestAppendOnlyPolicy:
    """Confirm all three simulation tables are registered as append-only.

    The enforcement is in .claude/hooks/pre_tool_use.py (not DB triggers).
    These tests guard against accidental removal from the list.
    """

    @staticmethod
    def _read_append_only_tables() -> set[str]:
        src = _HOOK_PATH.read_text(encoding="utf-8")
        in_list, tables = False, set()
        for line in src.splitlines():
            if "APPEND_ONLY_TABLES = [" in line:
                in_list = True
                continue
            if in_list:
                if line.strip().startswith("]"):
                    break
                stripped = line.strip().strip(",").strip('"').strip("'")
                if stripped and not stripped.startswith("#"):
                    tables.add(stripped)
        return tables

    def test_sessions_registered(self):
        assert "nc_simulation_sessions" in self._read_append_only_tables()

    def test_runs_registered(self):
        assert "nc_simulation_runs" in self._read_append_only_tables()

    def test_artifacts_registered(self):
        assert "nc_simulation_artifacts" in self._read_append_only_tables()

    def test_inserts_are_permitted(self, sim_conn):
        """Append-only means INSERT works; no DB-level trigger blocks it."""
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('ao-1', 'ndc')"
        )
        sim_conn.commit()
        count = sim_conn.execute(
            "SELECT COUNT(*) FROM nc_simulation_sessions WHERE id='ao-1'"
        ).fetchone()[0]
        assert count == 1

    def test_all_three_tables_protected(self):
        tables = self._read_append_only_tables()
        sim_tables = {"nc_simulation_sessions", "nc_simulation_runs", "nc_simulation_artifacts"}
        assert sim_tables.issubset(tables), (
            f"Missing from APPEND_ONLY_TABLES: {sim_tables - tables}"
        )


# ---------------------------------------------------------------------------
# 4. canvas_type NOT NULL constraint
# ---------------------------------------------------------------------------

class TestCanvasTypeNotNull:
    """canvas_type is mandatory — every simulation session requires a canvas."""

    def test_null_canvas_type_raises(self, sim_conn):
        with pytest.raises(sqlite3.IntegrityError):
            sim_conn.execute(
                "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('bad1', NULL)"
            )

    def test_missing_canvas_type_raises(self, sim_conn):
        with pytest.raises(sqlite3.IntegrityError):
            sim_conn.execute(
                "INSERT INTO nc_simulation_sessions (id) VALUES ('bad2')"
            )

    def test_empty_string_is_allowed(self, sim_conn):
        """Empty string is not NULL — DB-level constraint does not restrict values."""
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('empty-ct', '')"
        )
        sim_conn.commit()
        row = sim_conn.execute(
            "SELECT canvas_type FROM nc_simulation_sessions WHERE id='empty-ct'"
        ).fetchone()
        assert row["canvas_type"] == ""

    def test_all_seven_canvas_types_accepted(self, sim_conn):
        for i, ctype in enumerate(["ndc", "sdc", "pdc", "bdc", "ddc", "odc", "idc"]):
            sim_conn.execute(
                "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES (?, ?)",
                (f"ct-{i}", ctype),
            )
        sim_conn.commit()
        count = sim_conn.execute(
            "SELECT COUNT(*) FROM nc_simulation_sessions "
            "WHERE canvas_type IN ('ndc','sdc','pdc','bdc','ddc','odc','idc')"
        ).fetchone()[0]
        assert count == 7

    def test_canvas_type_null_via_icdev_db(self, icdev_db):
        conn = sqlite3.connect(str(icdev_db))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('cn-bad', NULL)"
            )
        conn.close()


# ---------------------------------------------------------------------------
# 5. JSON columns accept valid data (metadata, steps)
# ---------------------------------------------------------------------------

class TestJsonColumns:
    """metadata (sessions) and steps (runs) store JSON text; must round-trip."""

    def test_metadata_object_round_trip(self, sim_conn):
        payload = {"region": "us-east-1", "impact_level": "IL4", "ttl_hours": 24}
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type, metadata) "
            "VALUES ('j-s1', 'ndc', ?)",
            (json.dumps(payload),),
        )
        sim_conn.commit()
        row = sim_conn.execute(
            "SELECT metadata FROM nc_simulation_sessions WHERE id='j-s1'"
        ).fetchone()
        assert json.loads(row["metadata"]) == payload

    def test_metadata_default_parses_as_empty_object(self, sim_conn):
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('j-s2', 'sdc')"
        )
        sim_conn.commit()
        row = sim_conn.execute(
            "SELECT metadata FROM nc_simulation_sessions WHERE id='j-s2'"
        ).fetchone()
        assert json.loads(row["metadata"]) == {}

    def test_steps_array_round_trip(self, sim_conn):
        steps = [
            {"step": "init", "status": "ok"},
            {"step": "route", "status": "ok", "hops": 3},
            {"step": "teardown", "status": "ok"},
        ]
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('j-s3', 'ndc')"
        )
        sim_conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id, steps) VALUES ('j-r1', 'j-s3', ?)",
            (json.dumps(steps),),
        )
        sim_conn.commit()
        row = sim_conn.execute(
            "SELECT steps FROM nc_simulation_runs WHERE id='j-r1'"
        ).fetchone()
        recovered = json.loads(row["steps"])
        assert recovered == steps
        assert recovered[1]["hops"] == 3

    def test_steps_default_parses_as_empty_array(self, sim_conn):
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type) VALUES ('j-s4', 'eda')"
        )
        sim_conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id) VALUES ('j-r2', 'j-s4')"
        )
        sim_conn.commit()
        row = sim_conn.execute(
            "SELECT steps FROM nc_simulation_runs WHERE id='j-r2'"
        ).fetchone()
        assert json.loads(row["steps"]) == []

    def test_metadata_nested_object(self, sim_conn):
        payload = {
            "config": {"mode": "explain", "depth": 3},
            "tags": ["nist-800-53", "il4"],
            "dimensions": {"architecture": True, "compliance": True},
        }
        sim_conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type, metadata) "
            "VALUES ('j-s5', 'ndc', ?)",
            (json.dumps(payload),),
        )
        sim_conn.commit()
        row = sim_conn.execute(
            "SELECT metadata FROM nc_simulation_sessions WHERE id='j-s5'"
        ).fetchone()
        recovered = json.loads(row["metadata"])
        assert recovered["config"]["depth"] == 3
        assert "il4" in recovered["tags"]
        assert recovered["dimensions"]["compliance"] is True

    def test_json_columns_via_icdev_db(self, icdev_db):
        """Round-trip JSON through the full conftest schema DB."""
        conn = sqlite3.connect(str(icdev_db))
        payload = {"source": "integration_test", "version": 2}
        conn.execute(
            "INSERT INTO nc_simulation_sessions (id, canvas_type, metadata) "
            "VALUES ('jdb-s1', 'ndc', ?)",
            (json.dumps(payload),),
        )
        conn.execute(
            "INSERT INTO nc_simulation_runs (id, session_id, steps) "
            "VALUES ('jdb-r1', 'jdb-s1', ?)",
            (json.dumps([{"step": "verify", "ok": True}]),),
        )
        conn.commit()

        sess_row = conn.execute(
            "SELECT metadata FROM nc_simulation_sessions WHERE id='jdb-s1'"
        ).fetchone()
        run_row = conn.execute(
            "SELECT steps FROM nc_simulation_runs WHERE id='jdb-r1'"
        ).fetchone()
        conn.close()

        assert json.loads(sess_row[0]) == payload
        assert json.loads(run_row[0])[0]["ok"] is True
