#!/usr/bin/env python3
# CUI // SP-CTI
"""bdt-iqe-1: the cATO Twin IQE surface migrated onto the maintained IQE
executor/adapters (``tools/iqe/adapters/compliance.py``), retiring the Phase-1
regex engine (``tools/boundary_canvas/cato_twin/query_engine.py``).

These tests re-assert — on the NEW layer — the three safety properties the
retired regex engine's hardening suite (tests/test_bdc_query_engine_hardening.py,
bdr-sec-1) guaranteed, plus reflex seed-query compatibility:

  1. Fail-closed fields — an unknown/injection-shaped projection or predicate
     FIELD token raises (never a silently widened result set). The IQE executor
     is lenient (unmapped attr → None), so ``run_query`` enforces a per-collection
     whitelist that raises ValueError instead.
  2. Fail-closed syntax — an unknown operator / smuggled sub-SELECT fails at
     parse time (IQESyntaxError), not silently dropped.
  3. Cross-project scoping — snapshot/violation reads honour a ``project_id``
     scope (parameterised at the adapter SQL layer) so a per-project context
     never bleeds another project's data. Proven with a two-project fixture.

Tests route through ``tools.db.storage.get_connection`` against a dedicated temp
SQLite file so the production ``%s`` → ``?`` translation path (the same one the
reflex uses) is exercised.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.iqe.adapters.compliance import run_query  # noqa: E402
from tools.iqe.parser import IQESyntaxError  # noqa: E402

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
    """Two-project fixture (proj-A, proj-B) for one framework.

    proj-A latest snapshot: AC-2 not_satisfied (no evidence), AC-3 satisfied.
    proj-B latest snapshot: SC-7 not_satisfied (no evidence).
    proj-A also carries an open violation on AC-2, and proj-A's run is the most
    recent for the framework so the violations query resolves to proj-A's
    snapshot.
    """
    conn.executescript(_DDL)

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
    db_path = tmp_path / "bdt_iqe_migration.db"
    conn = get_connection(str(db_path))
    _seed_two_projects(conn)
    yield conn
    conn.close()


def _q(where: str, select: str, framework: str = _FRAMEWORK) -> str:
    return (
        f'foreach ctrl in compliance.twin_snapshots("{framework}") '
        f"{where} select {select}"
    )


# ---------------------------------------------------------------------------
# 1. Fail-closed FIELD whitelist (no silent widening)
# ---------------------------------------------------------------------------

class TestFailClosedFields:
    def test_unmapped_projection_token_raises(self, db):
        with pytest.raises(ValueError):
            run_query(_q("", "ctrl.control_id, ctrl.not_a_real_field"), conn=db)

    def test_unmapped_predicate_field_raises(self, db):
        with pytest.raises(ValueError):
            run_query(
                _q('where ctrl.bogus_field == "x"', "ctrl.control_id"),
                conn=db,
            )

    def test_unknown_collection_raises(self, db):
        with pytest.raises(ValueError):
            run_query(
                'foreach x in compliance.not_a_collection("FedRAMP Moderate") '
                "select x.control_id",
                conn=db,
            )

    def test_star_projection_still_allowed(self, db):
        rows = run_query(_q("", "*"), conn=db)
        assert isinstance(rows, list)
        assert len(rows) >= 1

    def test_valid_projection_returns_only_whitelisted_columns(self, db):
        rows = run_query(
            _q('where ctrl.status != "satisfied"',
               "ctrl.control_id, ctrl.implementation_status"),
            conn=db,
        )
        assert rows
        for r in rows:
            assert set(r.keys()) == {"control_id", "implementation_status"}


# ---------------------------------------------------------------------------
# 2. Fail-closed SYNTAX (unknown operator / smuggled sub-SELECT)
# ---------------------------------------------------------------------------

class TestFailClosedSyntax:
    def test_unknown_operator_raises(self, db):
        # `matches` is not an IQE operator — parse fails closed, never dropped.
        with pytest.raises(IQESyntaxError):
            run_query(
                _q('where ctrl.control_id matches "AC.*"', "ctrl.control_id"),
                conn=db,
            )

    def test_injection_shaped_projection_raises(self, db):
        # A smuggled sub-SELECT is not a valid projection token — parse fails.
        malicious = _q(
            "",
            'ctrl.control_id, (SELECT evidence_ref FROM compliance_twin_snapshots)',
        )
        with pytest.raises((ValueError, IQESyntaxError)):
            run_query(malicious, conn=db)

    def test_injection_in_predicate_literal_is_parameterized(self, db):
        # A value containing SQL is bound as a parameter (adapter SQL) and then
        # matched in Python — it matches nothing and cannot execute.
        rows = run_query(
            _q('where ctrl.control_id == "AC-2; DROP TABLE compliance_twin_runs"',
               "ctrl.control_id"),
            conn=db,
            project_id="proj-A",
        )
        assert rows == []
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
            _q('where ctrl.status != "satisfied"',
               "ctrl.control_id, ctrl.project_id"),
            conn=db,
        )
        ids = {r["control_id"] for r in rows}
        assert "AC-2" in ids  # proj-A
        assert "SC-7" in ids  # proj-B

    def test_controls_scoped_to_single_project(self, db):
        rows = run_query(
            _q('where ctrl.status != "satisfied"',
               "ctrl.control_id, ctrl.project_id"),
            conn=db,
            project_id="proj-A",
        )
        ids = {r["control_id"] for r in rows}
        assert ids == {"AC-2"}
        assert all(r["project_id"] == "proj-A" for r in rows)

    def test_controls_scoped_excludes_other_project(self, db):
        rows = run_query(
            _q('where ctrl.status != "satisfied"', "ctrl.control_id"),
            conn=db,
            project_id="proj-B",
        )
        ids = {r["control_id"] for r in rows}
        assert ids == {"SC-7"}

    def test_violations_scoped_by_project(self, db):
        got = run_query(
            'foreach v in compliance.twin_violations("FedRAMP Moderate") '
            "select v.control_id, v.project_id",
            conn=db,
            project_id="proj-A",
        )
        assert [r["control_id"] for r in got] == ["AC-2"]

        none = run_query(
            'foreach v in compliance.twin_violations("FedRAMP Moderate") '
            "select v.control_id",
            conn=db,
            project_id="proj-B",
        )
        assert none == []


# ---------------------------------------------------------------------------
# 4. Data-driven behaviour (null check, score threshold, unknown framework)
# ---------------------------------------------------------------------------

class TestQuerySemantics:
    def test_null_evidence_check(self, db):
        rows = run_query(
            _q("where ctrl.evidence_ref == null", "ctrl.control_id"),
            conn=db,
        )
        ids = {r["control_id"] for r in rows}
        assert "AC-2" in ids  # no evidence
        assert "AC-3" not in ids  # has ev-1

    def test_score_threshold(self, db):
        rows = run_query(
            _q("where ctrl.score < 0.50", "ctrl.control_id, ctrl.score"),
            conn=db,
        )
        ids = {r["control_id"] for r in rows}
        assert "AC-2" in ids  # score 0.0
        assert "AC-3" not in ids  # score 1.0

    def test_unknown_framework_returns_empty(self, db):
        rows = run_query(
            _q('where ctrl.status != "satisfied"', "ctrl.control_id",
               framework="UnknownFramework"),
            conn=db,
        )
        assert rows == []

    def test_runs_collection(self, db):
        rows = run_query(
            'foreach r in compliance.twin_runs("FedRAMP Moderate") '
            "select r.snapshot_id, r.project_id",
            conn=db,
        )
        assert {r["snapshot_id"] for r in rows} == {"snap-A", "snap-B"}


# ---------------------------------------------------------------------------
# 5. Reflex seed-query compatibility — every live seed query parses & executes
# ---------------------------------------------------------------------------

class TestReflexSeedQueryCompatibility:
    def test_all_reflex_seed_queries_execute(self, db):
        from tools.genesis.reflexes.cato_twin import _build_seed_queries

        for threshold in (0.5, 0.3, 0.8):
            for query in _build_seed_queries(threshold):
                results = run_query(query, conn=db, project_id="proj-A")
                assert isinstance(results, list)

    def test_reflex_moderate_seed_query_matches_expected_control(self, db):
        from tools.genesis.reflexes.cato_twin import _build_seed_queries

        queries = _build_seed_queries()
        # First Moderate seed query: unsatisfied controls.
        rows = run_query(queries[0], conn=db, project_id="proj-A")
        assert any(r["control_id"] == "AC-2" for r in rows)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
