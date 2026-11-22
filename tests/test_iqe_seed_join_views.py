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


def test_join_views_fail_soft_to_no_rows_on_a_missing_table(conn):
    """Like every other adapter here, a canvas DB without the tables answers no rows."""
    assert _run("aadc/08_approved_lifecycle.iqe", conn) == []
    assert _run("aadc/09_full_ato_readiness.iqe", conn) == []
    assert _run("aimc/10_cost_by_design.iqe", conn) == []
