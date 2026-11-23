# CUI // SP-CTI
"""The join seeds run over the join-view collections and keep their inner-join meaning.

aadc/08, aadc/09 and aimc/10 were written with a ``join <v> in <c> on a == b`` clause
IQE does not have; IQE's ``join()`` built-in needs one key name on both sides, and
these pair ``design_id`` with the design's ``id`` (aadc/09 three-way). They now read
``aadc.lifecycle_states``, ``aadc.ato_readiness`` and ``aimc.design_artifacts``, which
are those inner joins. This runs each seed FILE end to end on a scratch in-memory DB.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# Importing the adapters registers the aadc.* and aimc.* collections.
import tools.iqe.adapters.aadc
import tools.iqe.adapters.aimc  # noqa: F401  (import-for-side-effect)
from tools.iqe.executor import execute_query
from tools.iqe.parser import parse

SEEDS = Path(__file__).resolve().parents[1] / "context" / "iqe" / "queries"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    try:
        yield c
    finally:
        c.close()


def _run(rel: str, conn) -> list[dict]:
    return execute_query(parse((SEEDS / rel).read_text(encoding="utf-8")), conn)


def _aadc(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE aadc_designs (id TEXT, name TEXT, description TEXT, domain TEXT,
            classification TEXT, autonomy_max INTEGER, safety_impacting INTEGER,
            rights_impacting INTEGER, hitl_required INTEGER, created_at TEXT, updated_at TEXT);
        CREATE TABLE aadc_lifecycle_states (id TEXT, design_id TEXT, from_state TEXT,
            to_state TEXT, actor TEXT, reason TEXT, created_at TEXT);
        CREATE TABLE aadc_ato_reports (id TEXT, design_id TEXT, score_pct REAL, ato_ready INTEGER,
            passed INTEGER, failed INTEGER, critical_failed INTEGER, report_json TEXT, created_at TEXT);
        CREATE TABLE aadc_scorecard_snapshots (id TEXT, design_id TEXT, overall_score REAL,
            health TEXT, snapshot_json TEXT, created_at TEXT);
        INSERT INTO aadc_designs (id, name, domain, created_at) VALUES
            ('d1', 'Triage agent', 'health', '2026-01-01'),
            ('d2', 'Router agent', 'network', '2026-01-02');
        INSERT INTO aadc_lifecycle_states VALUES
            ('l1', 'd1', 'REVIEW', 'APPROVED', 'ao-test', '', '2026-02-01'),
            ('l2', 'd2', 'DRAFT', 'REVIEW', 'isso-test', '', '2026-02-02'),
            ('l3', 'ghost', 'REVIEW', 'APPROVED', 'ao-test', '', '2026-02-03');
        INSERT INTO aadc_ato_reports VALUES
            ('a1', 'd1', 91.5, 1, 40, 2, 0, '{}', '2026-03-01'),
            ('a2', 'd2', 50.0, 0, 20, 9, 3, '{}', '2026-03-02');
        INSERT INTO aadc_scorecard_snapshots VALUES ('s1', 'd1', 0.92, 'GREEN', '{}', '2026-03-03');
        """
    )


def test_approved_lifecycle_seed_joins_lifecycle_to_design(conn):
    _aadc(conn)
    rows = _run("aadc/08_approved_lifecycle.iqe", conn)
    # l2 is not APPROVED; l3's design does not exist, so the inner join drops it.
    assert rows == [{
        "design_name": "Triage agent", "design_domain": "health",
        "actor": "ao-test", "created_at": "2026-02-01",
    }]


def test_full_ato_readiness_seed_is_the_three_way_inner_join(conn):
    _aadc(conn)
    rows = _run("aadc/09_full_ato_readiness.iqe", conn)
    # d2 has an ATO report but no scorecard snapshot, so the inner join drops it.
    assert rows == [{
        "design_name": "Triage agent", "design_domain": "health", "ato_ready": 1,
        "score_pct": 91.5, "passed": 40, "failed": 2, "critical_failed": 0,
        "overall_score": 0.92, "health": "GREEN",
    }]


def test_model_card_seed_joins_artifacts_to_design(conn):
    conn.executescript(
        """
        CREATE TABLE aiml_designs (id TEXT, name TEXT, description TEXT, classification TEXT,
            il_level TEXT, primary_use_case TEXT, adaptation_strategy TEXT,
            created_at TEXT, updated_at TEXT);
        CREATE TABLE aiml_artifacts (id TEXT, design_id TEXT, artifact_type TEXT, title TEXT,
            format TEXT, created_at TEXT);
        INSERT INTO aiml_designs (id, name, il_level, adaptation_strategy) VALUES
            ('m1', 'Summariser', 'IL4', 'rag');
        INSERT INTO aiml_artifacts VALUES
            ('ar1', 'm1', 'model_card', 'Summariser card', 'md', '2026-04-01'),
            ('ar2', 'm1', 'ml_bom', 'Summariser BOM', 'json', '2026-04-02'),
            ('ar3', 'gone', 'model_card', 'Orphan card', 'md', '2026-04-03');
        """
    )
    rows = _run("aimc/10_cost_by_design.iqe", conn)
    assert rows == [{
        "design_name": "Summariser", "il_level": "IL4",
        "adaptation_strategy": "rag", "title": "Summariser card",
    }]


def test_join_views_raise_on_a_missing_table_rather_than_answer_no_rows(conn):
    """An unreadable view is an error the route reports, never an empty success."""
    for seed in ("aadc/08_approved_lifecycle.iqe", "aadc/09_full_ato_readiness.iqe",
                 "aimc/10_cost_by_design.iqe"):
        with pytest.raises(sqlite3.OperationalError):
            _run(seed, conn)


def test_ato_readiness_is_the_latest_report_and_snapshot_per_design_not_every_pair(conn):
    """Several reports and snapshots per design give ONE row: the latest of each.

    Latest is by created_at, ties broken by id, so the answer is deterministic.
    A cross join would give reports x snapshots rows per design (here 3 x 2 = 6).
    """
    _aadc(conn)
    conn.executescript(
        """
        INSERT INTO aadc_ato_reports VALUES
            ('a0', 'd1', 10.0, 0, 1, 30, 9, '{}', '2026-01-15'),
            ('a9', 'd1', 95.0, 1, 41, 1, 0, '{}', '2026-03-01');
        INSERT INTO aadc_scorecard_snapshots VALUES
            ('s0', 'd1', 0.10, 'RED', '{}', '2026-01-10'),
            ('s9', 'd1', 0.99, 'GREEN', '{}', '2026-02-20');
        INSERT INTO aadc_scorecard_snapshots VALUES ('s2', 'd2', 0.40, 'AMBER', '{}', '2026-03-05');
        """
    )
    rows = _run("aadc/09_full_ato_readiness.iqe", conn)
    by_design = {r["design_name"]: r for r in rows}
    assert len(rows) == 2 and set(by_design) == {"Triage agent", "Router agent"}
    # d1 reports: a0 (01-15) and a1, a9 (both 03-01) -> the tie goes to the higher id, a9.
    assert by_design["Triage agent"]["score_pct"] == 95.0
    # d1 snapshots: s0 (01-10), s9 (02-20), s1 (03-03) -> s1 is the latest.
    assert by_design["Triage agent"]["overall_score"] == 0.92
    assert by_design["Router agent"]["score_pct"] == 50.0
    assert by_design["Router agent"]["health"] == "AMBER"


def test_lifecycle_and_design_artifact_views_have_one_row_per_child(conn):
    """No N x M: each lifecycle transition and each artifact joins exactly one design."""
    _aadc(conn)
    conn.execute("INSERT INTO aadc_lifecycle_states VALUES "
                 "('l4', 'd1', 'APPROVED', 'APPROVED', 'ao-test', 're-approved', '2026-02-09')")
    rows = _run("aadc/08_approved_lifecycle.iqe", conn)
    assert sorted(r["created_at"] for r in rows) == ["2026-02-01", "2026-02-09"]
