#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for BDC cATO Twin Phase 1.

TDD suite covering:
  - DB migration 027 (compliance_twin_snapshots, compliance_twin_violations, compliance_twin_runs)
  - snapshot_writer.py — freeze cross-framework state
  - poam_auto_generator.py — POA&M from twin violations
  - cato_twin Genesis reflex (6h cadence)

The IQE query surface (formerly ``query_engine.py``) was migrated onto the
maintained IQE executor/adapters in bdt-iqe-1; its behaviour is covered by
tests/test_bdt_iqe_migration.py.

All tests use in-memory SQLite (wrapped in the shared translating
StorageConnection) so they do not touch data/icdev.db. The wrapper is
mandatory: the modules under test issue PG-native ``%s`` placeholders, and a
RAW ``sqlite3.Connection`` bypasses the ``%s`` → ``?`` translator (raising
sqlite3.ProgrammingError). This mirrors the connection pattern used by
tests/test_bdt_iqe_migration.py and tests/test_bdc_poam_generator_fk.py.
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Minimal schema fixture — only the tables the cATO twin touches
# ---------------------------------------------------------------------------

MINIMAL_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    event_type TEXT,
    actor TEXT,
    action TEXT,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS poam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    weakness_id TEXT NOT NULL,
    weakness_description TEXT NOT NULL,
    severity TEXT NOT NULL,
    source TEXT NOT NULL,
    control_id TEXT,
    status TEXT DEFAULT 'open',
    corrective_action TEXT,
    milestone_date DATE,
    completion_date DATE,
    responsible_party TEXT,
    resources_required TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- cATO Twin canonical schema
CREATE TABLE IF NOT EXISTS compliance_twin_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    framework TEXT NOT NULL,
    control_id TEXT NOT NULL,
    implementation_status TEXT NOT NULL,
    evidence_ref TEXT,
    score REAL DEFAULT 0.0,
    assessor TEXT,
    notes TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(snapshot_id, project_id, framework, control_id)
);

CREATE TABLE IF NOT EXISTS compliance_twin_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    framework TEXT NOT NULL,
    control_id TEXT NOT NULL,
    violation_type TEXT NOT NULL,
    severity TEXT DEFAULT 'moderate',
    details TEXT,
    poam_id INTEGER,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS compliance_twin_runs (
    snapshot_id TEXT PRIMARY KEY,
    framework TEXT NOT NULL,
    project_id TEXT,
    triggered_by TEXT DEFAULT 'genesis_reflex',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    total_controls INTEGER DEFAULT 0,
    satisfied INTEGER DEFAULT 0,
    partially_satisfied INTEGER DEFAULT 0,
    not_satisfied INTEGER DEFAULT 0,
    not_applicable INTEGER DEFAULT 0,
    not_assessed INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def mem_db():
    """Return a translating StorageConnection over in-memory SQLite.

    The raw sqlite3 connection is set up (schema + seed project) directly, then
    wrapped in ``StorageConnection(raw, "sqlite")`` so that the PG-native ``%s``
    placeholders issued by snapshot_writer / poam_auto_generator
    are translated to SQLite ``?`` exactly as they are in production.
    """
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(MINIMAL_DDL)
    raw.execute("INSERT INTO projects (id, name) VALUES ('proj-001', 'Test Project')")
    raw.commit()
    conn = StorageConnection(raw, "sqlite")
    yield conn
    raw.close()


# ---------------------------------------------------------------------------
# 1. Migration 027 — schema DDL
# ---------------------------------------------------------------------------

class TestMigration027:
    """Migration creates the three new tables idempotently."""

    def test_tables_created(self, mem_db):
        tables = {
            r[0]
            for r in mem_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "compliance_twin_snapshots" in tables
        assert "compliance_twin_violations" in tables
        assert "compliance_twin_runs" in tables

    def test_snapshots_unique_constraint(self, mem_db):
        mem_db.execute(
            "INSERT INTO compliance_twin_snapshots "
            "(snapshot_id, project_id, framework, control_id, implementation_status) "
            "VALUES ('snap-1', 'proj-001', 'FedRAMP Moderate', 'AC-2', 'satisfied')"
        )
        mem_db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            mem_db.execute(
                "INSERT INTO compliance_twin_snapshots "
                "(snapshot_id, project_id, framework, control_id, implementation_status) "
                "VALUES ('snap-1', 'proj-001', 'FedRAMP Moderate', 'AC-2', 'not_satisfied')"
            )
            mem_db.commit()

    def test_runs_primary_key(self, mem_db):
        mem_db.execute(
            "INSERT INTO compliance_twin_runs "
            "(snapshot_id, framework, started_at) VALUES ('run-1', 'FedRAMP High', datetime('now'))"
        )
        mem_db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            mem_db.execute(
                "INSERT INTO compliance_twin_runs "
                "(snapshot_id, framework, started_at) VALUES ('run-1', 'FedRAMP High', datetime('now'))"
            )
            mem_db.commit()


# ---------------------------------------------------------------------------
# 2. Snapshot Writer
# ---------------------------------------------------------------------------

class TestSnapshotWriter:
    """snapshot_writer.write_snapshot() freezes cross-framework state."""

    def _import_writer(self, mem_db):
        """Import snapshot_writer with get_connection patched to use mem_db."""
        import importlib
        spec = importlib.util.spec_from_file_location(
            "snapshot_writer",
            ROOT / "tools" / "boundary_canvas" / "cato_twin" / "snapshot_writer.py",
        )
        mod = importlib.util.module_from_spec(spec)
        # Patch get_connection to return mem_db
        with patch("tools.db.storage.get_connection", return_value=mem_db):
            spec.loader.exec_module(mod)
        return mod

    def test_write_snapshot_returns_snapshot_id(self, mem_db):
        from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
        controls = [
            {"control_id": "AC-2", "implementation_status": "satisfied", "evidence_ref": "ev-001", "score": 1.0},
            {"control_id": "AC-3", "implementation_status": "not_satisfied", "evidence_ref": None, "score": 0.0},
        ]
        with patch("tools.db.storage.get_connection", return_value=mem_db):
            snap_id = write_snapshot(
                project_id="proj-001",
                framework="FedRAMP Moderate",
                controls=controls,
                conn=mem_db,
            )
        assert snap_id.startswith("snap-")

    def test_snapshot_persists_all_controls(self, mem_db):
        from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
        controls = [
            {"control_id": "AC-2", "implementation_status": "satisfied", "evidence_ref": "ev-001", "score": 1.0},
            {"control_id": "IA-2", "implementation_status": "partially_satisfied", "evidence_ref": "ev-002", "score": 0.5},
            {"control_id": "SC-7", "implementation_status": "not_satisfied", "evidence_ref": None, "score": 0.0},
        ]
        snap_id = write_snapshot(
            project_id="proj-001",
            framework="FedRAMP Moderate",
            controls=controls,
            conn=mem_db,
        )
        rows = mem_db.execute(
            "SELECT * FROM compliance_twin_snapshots WHERE snapshot_id = ?",
            (snap_id,),
        ).fetchall()
        assert len(rows) == 3

    def test_snapshot_run_row_created(self, mem_db):
        from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
        controls = [
            {"control_id": "AC-2", "implementation_status": "satisfied", "evidence_ref": "ev-1", "score": 1.0},
        ]
        snap_id = write_snapshot(
            project_id="proj-001",
            framework="FedRAMP High",
            controls=controls,
            conn=mem_db,
        )
        run = mem_db.execute(
            "SELECT * FROM compliance_twin_runs WHERE snapshot_id = ?", (snap_id,)
        ).fetchone()
        assert run is not None
        assert dict(run)["total_controls"] == 1
        assert dict(run)["satisfied"] == 1

    def test_violations_written_for_not_satisfied(self, mem_db):
        from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
        controls = [
            {"control_id": "AC-2", "implementation_status": "not_satisfied", "evidence_ref": None, "score": 0.0},
        ]
        snap_id = write_snapshot(
            project_id="proj-001",
            framework="FedRAMP Moderate",
            controls=controls,
            conn=mem_db,
        )
        viols = mem_db.execute(
            "SELECT * FROM compliance_twin_violations WHERE snapshot_id = ?", (snap_id,)
        ).fetchall()
        assert len(viols) >= 1
        assert dict(viols[0])["control_id"] == "AC-2"

    def test_idempotent_duplicate_snapshot_raises(self, mem_db):
        """Writing two snapshots with the same ID is blocked by UNIQUE constraint."""
        from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
        controls = [{"control_id": "AC-2", "implementation_status": "satisfied", "evidence_ref": None, "score": 1.0}]
        snap_id = write_snapshot("proj-001", "FedRAMP Moderate", controls, conn=mem_db)
        with pytest.raises(Exception):
            # Manually insert with same snapshot_id to trigger UNIQUE violation
            mem_db.execute(
                "INSERT INTO compliance_twin_snapshots "
                "(snapshot_id, project_id, framework, control_id, implementation_status) "
                "VALUES (?, 'proj-001', 'FedRAMP Moderate', 'AC-2', 'satisfied')",
                (snap_id,),
            )
            mem_db.commit()


# ---------------------------------------------------------------------------
# 3. IQE Query Engine — migrated onto the IQE executor/adapters (bdt-iqe-1).
#    Behaviour now lives in tests/test_bdt_iqe_migration.py.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. POA&M Auto-Generator from Twin Violations
# ---------------------------------------------------------------------------

class TestTwinPoamGenerator:
    """poam_auto_generator.generate_from_violations() creates POA&M items."""

    def _seed_violations(self, mem_db):
        from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
        controls = [
            {"control_id": "AC-2", "implementation_status": "not_satisfied", "evidence_ref": None, "score": 0.0},
            {"control_id": "IA-5", "implementation_status": "not_satisfied", "evidence_ref": None, "score": 0.0},
        ]
        return write_snapshot("proj-001", "FedRAMP Moderate", controls, conn=mem_db)

    def test_generate_creates_poam_items(self, mem_db):
        snap_id = self._seed_violations(mem_db)
        from tools.boundary_canvas.cato_twin.poam_auto_generator import generate_from_violations
        result = generate_from_violations(
            snapshot_id=snap_id,
            project_id="proj-001",
            conn=mem_db,
        )
        assert result["new_items"] >= 2

    def test_poam_items_in_db(self, mem_db):
        snap_id = self._seed_violations(mem_db)
        from tools.boundary_canvas.cato_twin.poam_auto_generator import generate_from_violations
        generate_from_violations(snap_id, "proj-001", conn=mem_db)
        items = mem_db.execute(
            "SELECT * FROM poam_items WHERE project_id = 'proj-001'"
        ).fetchall()
        assert len(items) >= 2

    def test_idempotent_no_duplicates(self, mem_db):
        snap_id = self._seed_violations(mem_db)
        from tools.boundary_canvas.cato_twin.poam_auto_generator import generate_from_violations
        generate_from_violations(snap_id, "proj-001", conn=mem_db)
        generate_from_violations(snap_id, "proj-001", conn=mem_db)
        items = mem_db.execute(
            "SELECT weakness_id FROM poam_items WHERE project_id = 'proj-001'"
        ).fetchall()
        ids = [dict(r)["weakness_id"] for r in items]
        # No duplicate weakness_ids
        assert len(ids) == len(set(ids))

    def test_poam_violation_link(self, mem_db):
        snap_id = self._seed_violations(mem_db)
        from tools.boundary_canvas.cato_twin.poam_auto_generator import generate_from_violations
        generate_from_violations(snap_id, "proj-001", conn=mem_db)
        viols = mem_db.execute(
            "SELECT poam_id FROM compliance_twin_violations WHERE snapshot_id = ?",
            (snap_id,),
        ).fetchall()
        # At least some violations should have a linked poam_id
        linked = [dict(v)["poam_id"] for v in viols if dict(v)["poam_id"]]
        assert len(linked) >= 1


# ---------------------------------------------------------------------------
# 5. Genesis Reflex — cATO Twin (6h cadence)
# ---------------------------------------------------------------------------

class TestCatoTwinReflex:
    """cato_twin reflex can be imported, has correct cadence, returns a result dict."""

    def test_reflex_importable(self):
        from tools.genesis.reflexes import cato_twin
        assert hasattr(cato_twin, "run")

    def test_reflex_cadence_hours(self):
        from tools.genesis.reflexes.cato_twin import CADENCE_HOURS
        assert CADENCE_HOURS == 6

    def test_reflex_run_returns_dict(self, mem_db):
        from tools.genesis.reflexes.cato_twin import run
        with patch("tools.db.storage.get_connection", return_value=mem_db):
            result = run({}, mem_db)
        assert isinstance(result, dict)
        assert "snapshots_written" in result or "status" in result

    def test_reflex_handles_empty_projects(self, mem_db):
        """Reflex should not crash when there are no projects."""
        mem_db.execute("DELETE FROM projects")
        mem_db.commit()
        from tools.genesis.reflexes.cato_twin import run
        with patch("tools.db.storage.get_connection", return_value=mem_db):
            result = run({}, mem_db)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 6. AI Anomaly Detection
# ---------------------------------------------------------------------------

class TestCatoTwinAnomalyDetection:
    """AI-driven anomaly detection replaces hardcoded 0.5 threshold."""

    def test_ai_score_threshold_default_exists(self):
        """Module must expose _AI_SCORE_THRESHOLD_DEFAULT as the fallback."""
        from tools.genesis.reflexes.cato_twin import _AI_SCORE_THRESHOLD_DEFAULT
        assert isinstance(_AI_SCORE_THRESHOLD_DEFAULT, float)
        assert 0.0 <= _AI_SCORE_THRESHOLD_DEFAULT <= 1.0

    def test_determine_anomaly_threshold_returns_float(self):
        """_determine_anomaly_threshold() must return a float in [0, 1]."""
        from tools.genesis.reflexes.cato_twin import _determine_anomaly_threshold
        controls = [
            {"control_id": "AC-2", "score": 0.3, "implementation_status": "not_satisfied"},
            {"control_id": "IA-2", "score": 0.8, "implementation_status": "satisfied"},
        ]
        with patch("tools.llm.router.LLMRouter.is_no_llm_mode", return_value=True):
            threshold = _determine_anomaly_threshold(controls, "FedRAMP Moderate")
        assert isinstance(threshold, float)
        assert 0.0 <= threshold <= 1.0

    def test_determine_anomaly_threshold_falls_back_on_llm_error(self):
        """LLM errors must return the default threshold, not raise."""
        from tools.genesis.reflexes.cato_twin import (
            _determine_anomaly_threshold,
            _AI_SCORE_THRESHOLD_DEFAULT,
        )
        controls = [{"control_id": "AC-2", "score": 0.4, "implementation_status": "not_satisfied"}]
        with patch("tools.llm.router.LLMRouter.invoke", side_effect=RuntimeError("LLM unavailable")):
            threshold = _determine_anomaly_threshold(controls, "FedRAMP High")
        assert threshold == _AI_SCORE_THRESHOLD_DEFAULT

    def test_detect_anomalies_llm_returns_list(self):
        """_detect_anomalies_llm() returns a list (possibly empty) of anomaly dicts."""
        from tools.genesis.reflexes.cato_twin import _detect_anomalies_llm
        controls = [
            {"control_id": "AC-2", "score": 0.1, "implementation_status": "not_satisfied"},
            {"control_id": "IA-5", "score": 0.0, "implementation_status": "not_satisfied"},
        ]
        with patch("tools.llm.router.LLMRouter.is_no_llm_mode", return_value=True):
            result = _detect_anomalies_llm(controls, "FedRAMP Moderate", "proj-001")
        assert isinstance(result, list)

    def test_detect_anomalies_llm_falls_back_gracefully(self):
        """LLM failure must return rule-based fallback list, not raise."""
        from tools.genesis.reflexes.cato_twin import _detect_anomalies_llm
        controls = [
            {"control_id": "AC-2", "score": 0.2, "implementation_status": "not_satisfied"},
        ]
        with patch("tools.llm.router.LLMRouter.invoke", side_effect=RuntimeError("timeout")):
            result = _detect_anomalies_llm(controls, "FedRAMP Moderate", "proj-001")
        assert isinstance(result, list)

    def test_reflex_result_includes_ai_anomalies_key(self, mem_db):
        """run() result dict must include 'ai_anomalies_found' key."""
        from tools.genesis.reflexes.cato_twin import run
        with patch("tools.db.storage.get_connection", return_value=mem_db):
            with patch("tools.llm.router.LLMRouter.is_no_llm_mode", return_value=True):
                result = run({}, mem_db)
        assert "ai_anomalies_found" in result

    def test_seed_queries_built_with_threshold(self):
        """_build_seed_queries() must accept a threshold and use it in query strings."""
        from tools.genesis.reflexes.cato_twin import _build_seed_queries
        queries = _build_seed_queries(threshold=0.3)
        assert isinstance(queries, list)
        assert len(queries) > 0
        # At least one query must embed the threshold value
        threshold_present = any("0.3" in q for q in queries)
        assert threshold_present, "Expected threshold 0.3 in at least one seed query"


# ---------------------------------------------------------------------------
# 7. IQE Seed Queries — file existence checks
# ---------------------------------------------------------------------------

IQE_QUERY_DIR = ROOT / "context" / "iqe" / "queries" / "boundary"


class TestIqeSeedQueries:
    """20 seed query files must exist and be non-empty."""

    def test_query_directory_exists(self):
        assert IQE_QUERY_DIR.is_dir(), f"Missing: {IQE_QUERY_DIR}"

    def test_twenty_queries_exist(self):
        queries = list(IQE_QUERY_DIR.glob("*.iqe"))
        assert len(queries) >= 20, f"Found {len(queries)} queries, expected >= 20"

    def test_all_queries_non_empty(self):
        queries = list(IQE_QUERY_DIR.glob("*.iqe"))
        for q in queries:
            assert q.stat().st_size > 0, f"Empty query file: {q.name}"

    def test_all_queries_contain_foreach(self):
        """Every IQE file must use the foreach ... select DSL."""
        queries = list(IQE_QUERY_DIR.glob("*.iqe"))
        for q in queries:
            content = q.read_text(encoding="utf-8")
            assert "foreach" in content.lower(), f"No 'foreach' in {q.name}"
            assert "select" in content.lower(), f"No 'select' in {q.name}"

    def test_all_queries_have_cui_header(self):
        queries = list(IQE_QUERY_DIR.glob("*.iqe"))
        for q in queries:
            content = q.read_text(encoding="utf-8")
            assert "CUI" in content, f"Missing CUI marking in {q.name}"
