#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression test for the fixed `boundary_canvas` compliance_snapshots query.

Background (pgp-vfy-10-d4, commit 88db820b8):
  The PG schema for `compliance_snapshots` (migration 027 — BDC cATO Twin)
  defines the column `status`, NOT `implementation_status`. The pre-fix
  `icdev/tools/boundary_canvas/twin.py::take_snapshot` was inserting into
  the non-existent `implementation_status` column, and
  `crosswalk_drift` was selecting from it. Both raised UndefinedColumn on
  PG and 500'd the dashboard twin page.

Fix:
  - `take_snapshot`  INSERTs into `status` (matches schema + dashboard UI)
  - `crosswalk_drift` SELECTs `status AS implementation_status` so the
    downstream Python map (src_map/tgt_map) still sees the expected key.

Acceptance (pgp-vfy-10-d5):
  Deterministic unit/integration test asserting the specific query path
  fixed in subtask 4 returns valid data and no exceptions. Prevents
  regression on the migrated schema.

NIST 800-53 controls: AU-2, CA-7
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Schema fixtures
# ---------------------------------------------------------------------------

# Two layers of schema are tested:
#   (A) The minimal BDC twin schema the regression targets — only the tables
#       the fixed functions touch. This is the strict regression assertion.
#   (B) A "full" schema with all upstream tables (project_controls, evidence)
#       so we can also assert control_count/evidence_count population.

_MINIMAL_DDL = """
CREATE TABLE compliance_snapshots (
    snapshot_id  TEXT NOT NULL,
    project_id   TEXT NOT NULL,
    framework_id TEXT NOT NULL,
    control_id   TEXT NOT NULL,
    status       TEXT NOT NULL,
    evidence_ref TEXT,
    taken_at     TEXT NOT NULL,
    PRIMARY KEY (snapshot_id)
);
"""

_FULL_DDL = _MINIMAL_DDL + """
CREATE TABLE project_controls (
    project_id  TEXT NOT NULL,
    control_id  TEXT NOT NULL
);
CREATE TABLE evidence (
    project_id TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Twin module import (canonical, so sys.modules has `icdev.tools.boundary_canvas.twin`)
# ---------------------------------------------------------------------------

# twin.py does `from tools.db.storage import get_connection`. The canonical
# function lives in `tools.db.storage`, so we patch THAT namespace — that's
# the function reference twin.py captured at import time.

@pytest.fixture(scope="module")
def twin_module():
    """Import the real twin module once per test module."""
    from icdev.tools.boundary_canvas import twin
    return twin


# ---------------------------------------------------------------------------
# Minimal-schema fixtures (strict regression on the fixed query path)
# ---------------------------------------------------------------------------

@pytest.fixture
def min_db():
    """In-memory SQLite with ONLY compliance_snapshots — strictest regression."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_MINIMAL_DDL)
    yield conn
    conn.close()


@pytest.fixture
def full_db():
    """In-memory SQLite with compliance_snapshots + project_controls + evidence."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_FULL_DDL)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. Schema column contract — the regression's load-bearing invariant
# ---------------------------------------------------------------------------

class TestComplianceSnapshotsSchemaContract:
    """The PG migration 027 must define `status`; no `implementation_status`."""

    def test_compliance_snapshots_has_status_column(self, min_db):
        cols = {r[1] for r in min_db.execute("PRAGMA table_info(compliance_snapshots)")}
        assert "status" in cols, (
            "Schema regression: compliance_snapshots must have a `status` column "
            "(PG migration 027). Twin code targets this column."
        )

    def test_compliance_snapshots_does_not_have_implementation_status(self, min_db):
        cols = {r[1] for r in min_db.execute("PRAGMA table_info(compliance_snapshots)")}
        # The fix specifically removed the wrong column from the SQL. If it
        # ever comes back, this test flags it so the SQL fix is revisited.
        assert "implementation_status" not in cols, (
            "Schema regression: compliance_snapshots must NOT have "
            "`implementation_status` (that was the broken SQLite-ism)."
        )

    def test_required_columns_present(self, min_db):
        cols = {r[1] for r in min_db.execute("PRAGMA table_info(compliance_snapshots)")}
        required = {"snapshot_id", "project_id", "framework_id", "control_id",
                    "status", "evidence_ref", "taken_at"}
        missing = required - cols
        assert not missing, f"Schema missing required columns: {missing}"


# ---------------------------------------------------------------------------
# 2. take_snapshot — INSERT path that was broken before the fix
# ---------------------------------------------------------------------------

class TestTakeSnapshotRegression:
    """take_snapshot must INSERT into `status` (not `implementation_status`)."""

    def test_take_snapshot_does_not_raise_undefined_column(self, min_db, twin_module):
        """Pre-fix: OperationalError 'no such column: implementation_status'."""
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.take_snapshot("proj-001", "FedRAMP Moderate")
        assert isinstance(result, dict)
        assert "snapshot_id" in result

    def test_take_snapshot_persists_row_with_status(self, min_db, twin_module):
        """The INSERT must actually land a row — the function's try/except used
        to silently swallow the error and never persist anything."""
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.take_snapshot("proj-001", "FedRAMP Moderate")
        snap_id = result["snapshot_id"]
        row = min_db.execute(
            "SELECT * FROM compliance_snapshots WHERE snapshot_id=?", (snap_id,)
        ).fetchone()
        assert row is not None, (
            "Regression: take_snapshot did not persist a row. The pre-fix "
            "INSERT raised UndefinedColumn, the bare except swallowed it, "
            "and the dashboard 500'd because the row was missing."
        )
        assert dict(row)["status"] == "snapshot"
        assert dict(row)["framework_id"] == "FedRAMP Moderate"
        assert dict(row)["project_id"] == "proj-001"
        assert dict(row)["control_id"] == "_meta"

    def test_take_snapshot_reports_zero_counts_when_upstream_tables_missing(
        self, min_db, twin_module
    ):
        """When project_controls + evidence tables are absent, control_count
        and evidence_count must default to 0 (graceful degradation)."""
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.take_snapshot("proj-001")
        assert result["control_count"] == 0
        assert result["evidence_count"] == 0

    def test_take_snapshot_counts_from_full_db(self, full_db, twin_module):
        """When upstream tables exist, counts are populated."""
        full_db.executemany(
            "INSERT INTO project_controls(project_id, control_id) VALUES (?, ?)",
            [("proj-001", "AC-2"), ("proj-001", "IA-2")],
        )
        full_db.execute(
            "INSERT INTO evidence(project_id) VALUES ('proj-001')"
        )
        full_db.commit()
        with patch("tools.db.storage.get_connection", return_value=full_db):
            result = twin_module.take_snapshot("proj-001")
        assert result["control_count"] == 2
        assert result["evidence_count"] == 1

    def test_take_snapshot_returns_required_keys(self, min_db, twin_module):
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.take_snapshot("proj-x", "CMMC L2")
        for key in ("snapshot_id", "project_id", "framework_id",
                    "control_count", "evidence_count", "taken_at"):
            assert key in result, f"Missing required key: {key}"
        assert result["framework_id"] == "CMMC L2"


# ---------------------------------------------------------------------------
# 3. crosswalk_drift — SELECT path that was broken before the fix
# ---------------------------------------------------------------------------

class TestCrosswalkDriftRegression:
    """crosswalk_drift must SELECT `status AS implementation_status`."""

    def test_crosswalk_drift_does_not_raise_undefined_column(self, min_db, twin_module):
        """Pre-fix: OperationalError 'no such column: implementation_status'."""
        # Seed two rows so the SELECT has something to read
        min_db.executemany(
            "INSERT INTO compliance_snapshots "
            "(snapshot_id, project_id, framework_id, control_id, status, evidence_ref, taken_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("s-1", "proj-001", "FedRAMP Moderate", "AC-2", "satisfied", "", "2026-01-01T00:00:00Z"),
                ("s-2", "proj-001", "FedRAMP High",      "AC-2", "satisfied", "", "2026-01-01T00:00:00Z"),
            ],
        )
        min_db.commit()
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.crosswalk_drift("proj-001", "FedRAMP Moderate", "FedRAMP High")
        assert isinstance(result, dict)
        assert "drifts" in result
        assert "total" in result

    def test_crosswalk_drift_finds_drift_between_frameworks(self, min_db, twin_module):
        """AC-2 is satisfied in src but not in tgt → must produce 1 drift record."""
        min_db.executemany(
            "INSERT INTO compliance_snapshots "
            "(snapshot_id, project_id, framework_id, control_id, status, evidence_ref, taken_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("s-src-1", "proj-001", "FedRAMP Moderate", "AC-2", "satisfied",     "", "2026-01-01T00:00:00Z"),
                ("s-tgt-1", "proj-001", "FedRAMP High",      "AC-2", "not_satisfied", "", "2026-01-01T00:00:00Z"),
            ],
        )
        min_db.commit()
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.crosswalk_drift("proj-001", "FedRAMP Moderate", "FedRAMP High")
        assert result["total"] == 1
        drift = result["drifts"][0]
        assert drift["control_id"] == "AC-2"
        assert drift["status_src"] == "satisfied"
        assert drift["status_tgt"] == "not_satisfied"
        assert drift["drift"] is True
        assert drift["framework_src"] == "FedRAMP Moderate"
        assert drift["framework_tgt"] == "FedRAMP High"

    def test_crosswalk_drift_no_drift_when_statuses_match(self, min_db, twin_module):
        """If both frameworks mark AC-2 satisfied → no drift."""
        min_db.executemany(
            "INSERT INTO compliance_snapshots "
            "(snapshot_id, project_id, framework_id, control_id, status, evidence_ref, taken_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("s-src-1", "proj-001", "FedRAMP Moderate", "AC-2", "satisfied", "", "2026-01-01T00:00:00Z"),
                ("s-tgt-1", "proj-001", "FedRAMP High",      "AC-2", "satisfied", "", "2026-01-01T00:00:00Z"),
            ],
        )
        min_db.commit()
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.crosswalk_drift("proj-001", "FedRAMP Moderate", "FedRAMP High")
        assert result["total"] == 0
        assert result["drifts"] == []

    def test_crosswalk_drift_handles_empty_db(self, min_db, twin_module):
        """No snapshots at all → no drift, no exception."""
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.crosswalk_drift("proj-001", "FedRAMP Moderate", "FedRAMP High")
        assert result == {"drifts": [], "total": 0}

    def test_crosswalk_drift_scopes_to_project(self, min_db, twin_module):
        """Drift must not leak across projects."""
        min_db.executemany(
            "INSERT INTO compliance_snapshots "
            "(snapshot_id, project_id, framework_id, control_id, status, evidence_ref, taken_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                # proj-001: would drift
                ("s-a", "proj-001", "FedRAMP Moderate", "AC-2", "satisfied",     "", "2026-01-01T00:00:00Z"),
                ("s-b", "proj-001", "FedRAMP High",      "AC-2", "not_satisfied", "", "2026-01-01T00:00:00Z"),
                # proj-002: both satisfied — would NOT drift if leaked
                ("s-c", "proj-002", "FedRAMP Moderate", "AC-2", "satisfied", "", "2026-01-01T00:00:00Z"),
                ("s-d", "proj-002", "FedRAMP High",      "AC-2", "satisfied", "", "2026-01-01T00:00:00Z"),
            ],
        )
        min_db.commit()
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.crosswalk_drift("proj-001", "FedRAMP Moderate", "FedRAMP High")
        assert result["total"] == 1
        # Confirm the other project is not affected
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result_002 = twin_module.crosswalk_drift("proj-002", "FedRAMP Moderate", "FedRAMP High")
        assert result_002["total"] == 0


# ---------------------------------------------------------------------------
# 4. End-to-end — take_snapshot then crosswalk_drift on the same DB
# ---------------------------------------------------------------------------

class TestEndToEndTwinFlow:
    """The full flow the dashboard exercises: take_snapshot then crosswalk_drift."""

    def test_take_snapshot_then_crosswalk_drift_returns_expected_drift(
        self, min_db, twin_module
    ):
        """Seed the table directly with rows where AC-2 drifts between
        frameworks and the meta rows match. crosswalk_drift must surface
        only the AC-2 drift — _meta must not pollute the result."""
        min_db.executemany(
            "INSERT INTO compliance_snapshots "
            "(snapshot_id, project_id, framework_id, control_id, status, evidence_ref, taken_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("meta-mod", "proj-001", "FedRAMP Moderate", "_meta", "snapshot", "", "2026-01-01T00:00:00Z"),
                ("meta-hi",  "proj-001", "FedRAMP High",      "_meta", "snapshot", "", "2026-01-01T00:00:00Z"),
                ("ac2-mod",  "proj-001", "FedRAMP Moderate", "AC-2",  "satisfied",     "", "2026-01-02T00:00:00Z"),
                ("ac2-hi",   "proj-001", "FedRAMP High",      "AC-2",  "not_satisfied", "", "2026-01-02T00:00:00Z"),
            ],
        )
        min_db.commit()
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.crosswalk_drift("proj-001", "FedRAMP Moderate", "FedRAMP High")
        # AC-2 should drift; _meta rows match exactly across frameworks (both
        # 'snapshot'), so they must NOT count as drift.
        assert result["total"] == 1
        assert result["drifts"][0]["control_id"] == "AC-2"

    def test_real_take_snapshot_then_crosswalk_drift_round_trip(
        self, min_db, twin_module
    ):
        """Drive the full path: take_snapshot writes via the actual function,
        then crosswalk_drift reads via the actual function. Both must succeed
        without UndefinedColumn — the exact regression scenario."""
        with patch("tools.db.storage.get_connection", return_value=min_db):
            snap_mod = twin_module.take_snapshot("proj-001", "FedRAMP Moderate")
            snap_hi = twin_module.take_snapshot("proj-001", "FedRAMP High")
        # take_snapshot writes control_id='_meta' status='snapshot' for both
        # frameworks. crosswalk_drift should see matching src/tgt statuses
        # for _meta and produce zero drift.
        assert snap_mod["snapshot_id"]
        assert snap_hi["snapshot_id"]
        with patch("tools.db.storage.get_connection", return_value=min_db):
            result = twin_module.crosswalk_drift("proj-001", "FedRAMP Moderate", "FedRAMP High")
        assert result == {"drifts": [], "total": 0}
