#!/usr/bin/env python3
# CUI // SP-CTI
"""Security-hardening tests for the BDC cATO Twin IQE query engine (bdr-sec-1).

Covers the three defects closed by task bdr-sec-1:

  1. SQL injection — projection/predicate FIELD tokens must resolve through a
     strict whitelist; any unmapped token raises ValueError (no raw
     interpolation).
  2. Fail-open predicates — an unknown WHERE predicate must fail CLOSED
     (ValueError), not be silently dropped.
  3. Cross-project reads — snapshot/violation reads must honour a project_id
     scope so a per-project context never bleeds another project's data.

Plus a compatibility check that every IQE query string the live Genesis reflex
(`tools/genesis/reflexes/cato_twin.py`) issues still parses and executes.

Tests route through ``tools.db.storage.get_connection`` against a dedicated
temp SQLite file so the production placeholder-translation path is exercised
(the same path the reflex uses at runtime).
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.boundary_canvas.cato_twin.query_engine import run_query  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402

_FRAMEWORK = "FedRAMP Moderate"

_DDL = """
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


def _seed_two_projects(conn):
    """Seed a two-project fixture (proj-A, proj-B) for one framework.

    proj-A latest snapshot: AC-2 not_satisfied (no evidence), AC-3 satisfied.
    proj-B latest snapshot: SC-7 not_satisfied (no evidence).
    proj-A also carries an open violation on AC-2, and proj-A's run is the most
    recent for the framework (later started_at) so the violations query resolves
    to proj-A's snapshot.
    """
    conn.executescript(_DDL)

    # Runs — proj-A is the most recent for the framework.
    conn.execute(
        "INSERT INTO compliance_twin_runs "
        "(snapshot_id, framework, project_id, started_at) VALUES (%s, %s, %s, %s)",
        ("snap-A", _FRAMEWORK, "proj-A", "2026-01-02T00:00:00"),
    )
    conn.execute(
        "INSERT INTO compliance_twin_runs "
        "(snapshot_id, framework, project_id, started_at) VALUES (%s, %s, %s, %s)",
        ("snap-B", _FRAMEWORK, "proj-B", "2026-01-01T00:00:00"),
    )

    # Snapshots.
    snap_rows = [
        ("snap-A", "proj-A", "AC-2", "not_satisfied", None, 0.0),
        ("snap-A", "proj-A", "AC-3", "satisfied", "ev-1", 1.0),
        ("snap-B", "proj-B", "SC-7", "not_satisfied", None, 0.0),
    ]
    for snap_id, pid, cid, status, ev, score in snap_rows:
        conn.execute(
            "INSERT INTO compliance_twin_snapshots "
            "(snapshot_id, project_id, framework, control_id, "
            " implementation_status, evidence_ref, score) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (snap_id, pid, _FRAMEWORK, cid, status, ev, score),
        )

    # Violation on proj-A's (latest) snapshot.
    conn.execute(
        "INSERT INTO compliance_twin_violations "
        "(snapshot_id, project_id, framework, control_id, violation_type, severity) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ("snap-A", "proj-A", _FRAMEWORK, "AC-2", "not_satisfied", "high"),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Translating StorageConnection backed by a dedicated temp SQLite file."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "bdc_query_engine_hardening.db"
    conn = get_connection(str(db_path))
    _seed_two_projects(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. SQL injection — strict projection whitelist
# ---------------------------------------------------------------------------

class TestProjectionWhitelist:
    def test_injection_shaped_projection_token_raises(self, db):
        # A classic injection payload smuggled into the projection list.
        malicious = (
            "foreach ctrl in framework('FedRAMP Moderate').controls "
            "select ctrl.control_id, (SELECT evidence_ref FROM compliance_twin_snapshots)"
        )
        with pytest.raises(ValueError):
            run_query(malicious, conn=db)

    def test_unmapped_projection_token_raises(self, db):
        with pytest.raises(ValueError):
            run_query(
                "foreach ctrl in framework('FedRAMP Moderate').controls "
                "select ctrl.control_id, ctrl.not_a_real_field",
                conn=db,
            )

    def test_star_projection_still_allowed(self, db):
        rows = run_query(
            "foreach ctrl in framework('FedRAMP Moderate').controls select *",
            conn=db,
        )
        assert isinstance(rows, list)
        assert len(rows) >= 1

    def test_valid_projection_returns_only_whitelisted_columns(self, db):
        rows = run_query(
            "foreach ctrl in framework('FedRAMP Moderate').controls "
            "where ctrl.status != 'satisfied' "
            "select ctrl.control_id, ctrl.implementation_status",
            conn=db,
        )
        assert rows
        for r in rows:
            assert set(r.keys()) == {"control_id", "implementation_status"}


# ---------------------------------------------------------------------------
# 2. Fail-closed predicates
# ---------------------------------------------------------------------------

class TestFailClosedPredicates:
    def test_unknown_predicate_raises(self, db):
        # `matches` is not a supported operator — must fail closed, not be dropped.
        with pytest.raises(ValueError):
            run_query(
                "foreach ctrl in framework('FedRAMP Moderate').controls "
                "where ctrl.control_id matches 'AC.*' "
                "select ctrl.control_id",
                conn=db,
            )

    def test_unknown_predicate_field_raises(self, db):
        with pytest.raises(ValueError):
            run_query(
                "foreach ctrl in framework('FedRAMP Moderate').controls "
                "where ctrl.bogus_field == 'x' "
                "select ctrl.control_id",
                conn=db,
            )

    def test_injection_in_predicate_literal_is_parameterized(self, db):
        # A value containing SQL is bound as a parameter, so it matches nothing
        # and cannot execute — the tables remain intact.
        rows = run_query(
            "foreach ctrl in framework('FedRAMP Moderate').controls "
            "where ctrl.control_id == 'AC-2; DROP TABLE compliance_twin_runs' "
            "select ctrl.control_id",
            conn=db,
        )
        assert rows == []
        # Tables still present / unharmed.
        remaining = db.execute(
            "SELECT COUNT(*) AS c FROM compliance_twin_snapshots"
        ).fetchone()
        assert dict(remaining)["c"] == 3


# ---------------------------------------------------------------------------
# 3. Cross-project scoping
# ---------------------------------------------------------------------------

class TestProjectScoping:
    def test_controls_unscoped_spans_all_projects(self, db):
        rows = run_query(
            "foreach ctrl in framework('FedRAMP Moderate').controls "
            "where ctrl.status != 'satisfied' "
            "select ctrl.control_id, ctrl.project_id",
            conn=db,
        )
        ids = {r["control_id"] for r in rows}
        # Without a project scope both projects' unsatisfied controls appear.
        assert "AC-2" in ids  # proj-A
        assert "SC-7" in ids  # proj-B

    def test_controls_scoped_to_single_project(self, db):
        rows = run_query(
            "foreach ctrl in framework('FedRAMP Moderate').controls "
            "where ctrl.status != 'satisfied' "
            "select ctrl.control_id, ctrl.project_id",
            conn=db,
            project_id="proj-A",
        )
        ids = {r["control_id"] for r in rows}
        assert ids == {"AC-2"}
        assert all(r["project_id"] == "proj-A" for r in rows)

    def test_controls_scoped_excludes_other_project(self, db):
        rows = run_query(
            "foreach ctrl in framework('FedRAMP Moderate').controls "
            "where ctrl.status != 'satisfied' "
            "select ctrl.control_id",
            conn=db,
            project_id="proj-B",
        )
        ids = {r["control_id"] for r in rows}
        assert ids == {"SC-7"}

    def test_violations_scoped_by_project(self, db):
        got = run_query(
            "foreach v in framework('FedRAMP Moderate').violations "
            "select viol.control_id, viol.project_id",
            conn=db,
            project_id="proj-A",
        )
        assert [r["control_id"] for r in got] == ["AC-2"]

        none = run_query(
            "foreach v in framework('FedRAMP Moderate').violations "
            "select viol.control_id",
            conn=db,
            project_id="proj-B",
        )
        assert none == []


# ---------------------------------------------------------------------------
# 4. Reflex compatibility — every live seed query still parses & executes
# ---------------------------------------------------------------------------

class TestReflexSeedQueryCompatibility:
    def test_all_reflex_seed_queries_execute(self, db):
        from tools.genesis.reflexes.cato_twin import _build_seed_queries

        # Exercise a couple of thresholds the reflex may compute at runtime.
        for threshold in (0.5, 0.3, 0.8):
            for query in _build_seed_queries(threshold):
                # Must not raise: every token/predicate is in the whitelist.
                results = run_query(query, conn=db, project_id="proj-A")
                assert isinstance(results, list)

    def test_reflex_moderate_seed_query_matches_expected_control(self, db):
        from tools.genesis.reflexes.cato_twin import _build_seed_queries

        queries = _build_seed_queries()
        # First Moderate seed query: unsatisfied controls.
        unsatisfied = queries[0]
        rows = run_query(unsatisfied, conn=db, project_id="proj-A")
        assert any(r["control_id"] == "AC-2" for r in rows)
