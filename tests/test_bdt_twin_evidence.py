#!/usr/bin/env python3
# CUI // SP-CTI
"""bdt-sch-1: prove the cATO Twin reflex produces NON-EMPTY snapshots with REAL
per-control evidence pulled from the multi-regime assessor tables.

Before bdt-sch-1 the reflex ran HEALTHY but wrote 0 snapshots: its
``_pull_framework_controls`` expected per-control ``score`` / ``evidence_ref`` /
``assessed_at`` columns that ``fedramp_assessments`` / ``cssp_assessments`` /
``cmmc_assessments`` never exposed, and it probed table existence via
``sqlite_master`` (which does not exist on PostgreSQL). This suite seeds the
real assessor schemas, runs a full reflex cycle, and asserts:

  * ``snapshots_written`` / ``metric_value`` > 0
  * ``compliance_twin_snapshots`` gains rows (control_count > 0)
  * at least one snapshot row carries the seeded evidence reference
  * scores are derived honestly from status (no fabricated numbers; unassessed
    controls keep score NULL)

All DB work uses the shared translating ``StorageConnection`` over in-memory
SQLite, mirroring tests/test_cato_twin.py::mem_db. The LLM router is forced into
no-LLM mode so the run is hermetic (no Telegram / network).
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Schema: the twin tables the writer needs + the three real assessor tables.
# ---------------------------------------------------------------------------
_DDL = """
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

CREATE TABLE IF NOT EXISTS compliance_twin_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    framework TEXT NOT NULL,
    control_id TEXT NOT NULL,
    implementation_status TEXT NOT NULL,
    evidence_ref TEXT,
    score REAL,
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

-- Real assessor tables (mirrors tools/db/init_icdev_db.py columns).
CREATE TABLE IF NOT EXISTS fedramp_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    baseline TEXT NOT NULL,
    control_id TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed',
    implementation_status TEXT,
    customer_responsible TEXT,
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cssp_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    functional_area TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed',
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cmmc_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    level INTEGER NOT NULL,
    practice_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT DEFAULT 'not_assessed',
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    nist_171_id TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def _seed(raw: sqlite3.Connection) -> None:
    raw.execute("INSERT INTO projects (id, name) VALUES ('proj-001', 'Test Project')")

    # FedRAMP Moderate — AC-2 satisfied WITH evidence, AC-3 other_than_satisfied
    # no evidence. AC-2 also has an OLDER, superseded row to prove latest-wins
    # de-duplication (must not violate the snapshot UNIQUE constraint).
    raw.executemany(
        "INSERT INTO fedramp_assessments "
        "(project_id, baseline, control_id, status, evidence_path, evidence_description, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("proj-001", "moderate", "AC-2", "not_assessed", None, None, "2026-01-01T00:00:00Z"),
            ("proj-001", "moderate", "AC-2", "satisfied", "evidence/ac-2.pdf", "AC-2 policy", "2026-06-01T00:00:00Z"),
            ("proj-001", "moderate", "AC-3", "other_than_satisfied", None, None, "2026-06-01T00:00:00Z"),
        ],
    )
    # FedRAMP High — SC-7 satisfied with evidence
    raw.execute(
        "INSERT INTO fedramp_assessments "
        "(project_id, baseline, control_id, status, evidence_path, updated_at) "
        "VALUES ('proj-001', 'high', 'SC-7', 'satisfied', 'evidence/sc-7.pdf', '2026-06-02T00:00:00Z')"
    )
    # CSSP (mapped to 'NIST 800-53') — IR-4 satisfied w/ evidence, IR-5 not_satisfied
    raw.executemany(
        "INSERT INTO cssp_assessments "
        "(project_id, functional_area, requirement_id, status, evidence_path, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("proj-001", "Respond", "IR-4", "satisfied", "evidence/ir-4.md", "2026-06-03T00:00:00Z"),
            ("proj-001", "Detect", "IR-5", "not_satisfied", None, "2026-06-03T00:00:00Z"),
        ],
    )
    # CMMC — AC.L2-3.1.1 met w/ evidence, AC.L2-3.1.2 not_met
    raw.executemany(
        "INSERT INTO cmmc_assessments "
        "(project_id, level, practice_id, domain, status, evidence_path, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("proj-001", 2, "AC.L2-3.1.1", "AC", "met", "evidence/cmmc-3.1.1.pdf", "2026-06-04T00:00:00Z"),
            ("proj-001", 2, "AC.L2-3.1.2", "AC", "not_met", None, "2026-06-04T00:00:00Z"),
        ],
    )
    raw.commit()


@pytest.fixture
def seeded_db():
    """Translating StorageConnection over in-memory SQLite, seeded with assessor data."""
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(_DDL)
    _seed(raw)
    conn = StorageConnection(raw, "sqlite")
    yield conn
    raw.close()


# ---------------------------------------------------------------------------
# _pull_framework_controls — schema-aware reads
# ---------------------------------------------------------------------------
class TestPullFrameworkControls:
    def test_fedramp_moderate_dedupes_and_maps(self, seeded_db):
        from tools.genesis.reflexes.cato_twin import _pull_framework_controls
        controls = _pull_framework_controls(seeded_db, "proj-001", "FedRAMP Moderate")
        by_id = {c["control_id"]: c for c in controls}
        # AC-2 appears once (latest row wins), mapped satisfied w/ evidence + score 1.0
        assert len([c for c in controls if c["control_id"] == "AC-2"]) == 1
        assert by_id["AC-2"]["implementation_status"] == "satisfied"
        assert by_id["AC-2"]["evidence_ref"] == "evidence/ac-2.pdf"
        assert by_id["AC-2"]["score"] == 1.0
        # other_than_satisfied maps to not_satisfied (score 0.0), no evidence
        assert by_id["AC-3"]["implementation_status"] == "not_satisfied"
        assert by_id["AC-3"]["evidence_ref"] is None
        assert by_id["AC-3"]["score"] == 0.0

    def test_fedramp_high_baseline_filter(self, seeded_db):
        from tools.genesis.reflexes.cato_twin import _pull_framework_controls
        mod = _pull_framework_controls(seeded_db, "proj-001", "FedRAMP Moderate")
        high = _pull_framework_controls(seeded_db, "proj-001", "FedRAMP High")
        assert "SC-7" in {c["control_id"] for c in high}
        # SC-7 (high) must NOT bleed into the moderate baseline result
        assert "SC-7" not in {c["control_id"] for c in mod}

    def test_cssp_uses_requirement_id(self, seeded_db):
        from tools.genesis.reflexes.cato_twin import _pull_framework_controls
        controls = _pull_framework_controls(seeded_db, "proj-001", "NIST 800-53")
        by_id = {c["control_id"]: c for c in controls}
        assert "IR-4" in by_id and "IR-5" in by_id
        assert by_id["IR-4"]["evidence_ref"] == "evidence/ir-4.md"
        assert by_id["IR-5"]["implementation_status"] == "not_satisfied"

    def test_cmmc_uses_practice_id_and_maps_status(self, seeded_db):
        from tools.genesis.reflexes.cato_twin import _pull_framework_controls
        controls = _pull_framework_controls(seeded_db, "proj-001", "CMMC")
        by_id = {c["control_id"]: c for c in controls}
        assert by_id["AC.L2-3.1.1"]["implementation_status"] == "satisfied"
        assert by_id["AC.L2-3.1.2"]["implementation_status"] == "not_satisfied"

    def test_missing_table_degrades_to_empty(self):
        from tools.db.storage import StorageConnection
        from tools.genesis.reflexes.cato_twin import _pull_framework_controls
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        raw.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)")
        conn = StorageConnection(raw, "sqlite")
        try:
            assert _pull_framework_controls(conn, "proj-001", "CMMC") == []
        finally:
            raw.close()


# ---------------------------------------------------------------------------
# End-to-end reflex run — the acceptance criterion
# ---------------------------------------------------------------------------
class TestReflexProducesEvidence:
    def _run(self, seeded_db):
        from tools.genesis.reflexes.cato_twin import run
        with patch("tools.db.storage.get_connection", return_value=seeded_db):
            with patch("tools.llm.router.LLMRouter.is_no_llm_mode", return_value=True):
                return run({}, seeded_db)

    def test_snapshots_written_metric_positive(self, seeded_db):
        result = self._run(seeded_db)
        assert result["status"] in ("ok",), result
        assert result["snapshots_written"] > 0, result
        assert result["metric_value"] > 0, result
        assert result["success"] is True

    def test_snapshot_rows_have_evidence(self, seeded_db):
        self._run(seeded_db)
        rows = seeded_db.execute(
            "SELECT control_id, framework, evidence_ref, score, implementation_status "
            "FROM compliance_twin_snapshots"
        ).fetchall()
        rows = [dict(r) for r in rows]
        assert len(rows) > 0, "reflex must persist snapshot rows"
        # Real evidence linkage: at least one control carries the seeded evidence ref
        evidenced = [r for r in rows if r["evidence_ref"]]
        assert evidenced, "expected at least one snapshot row with a linked evidence_ref"
        refs = {r["evidence_ref"] for r in evidenced}
        assert "evidence/ac-2.pdf" in refs

    def test_scores_derived_honestly(self, seeded_db):
        self._run(seeded_db)
        rows = {
            (dict(r)["framework"], dict(r)["control_id"]): dict(r)
            for r in seeded_db.execute(
                "SELECT framework, control_id, score, implementation_status "
                "FROM compliance_twin_snapshots"
            ).fetchall()
        }
        # satisfied -> 1.0, not_satisfied -> 0.0 (derived, not fabricated)
        assert rows[("FedRAMP Moderate", "AC-2")]["score"] == 1.0
        assert rows[("FedRAMP Moderate", "AC-3")]["score"] == 0.0

    def test_control_count_and_runs(self, seeded_db):
        self._run(seeded_db)
        control_count = seeded_db.execute(
            "SELECT COUNT(*) AS c FROM compliance_twin_snapshots"
        ).fetchone()
        assert dict(control_count)["c"] > 0
        # A run summary row per snapshot
        runs = seeded_db.execute(
            "SELECT COUNT(*) AS c FROM compliance_twin_runs"
        ).fetchone()
        assert dict(runs)["c"] > 0

    def test_violations_generated_for_unsatisfied(self, seeded_db):
        result = self._run(seeded_db)
        # AC-3 / IR-5 / AC.L2-3.1.2 are unsatisfied -> violations must be recorded
        assert result["violations_found"] > 0, result
        viols = seeded_db.execute(
            "SELECT COUNT(*) AS c FROM compliance_twin_violations"
        ).fetchone()
        assert dict(viols)["c"] > 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
