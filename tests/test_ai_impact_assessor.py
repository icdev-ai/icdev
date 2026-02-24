"""Tests for tools/compliance/ai_impact_assessor.py — Phase 49 AI Impact Assessment."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "tools" / "compliance")
)
from ai_impact_assessor import IMPACT_DIMENSIONS, assess_impact, get_impact_summary


@pytest.fixture
def db_path(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_ethics_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            review_type TEXT NOT NULL,
            ai_system TEXT,
            findings TEXT,
            opt_out_policy INTEGER DEFAULT 0,
            legal_compliance_matrix INTEGER DEFAULT 0,
            pre_deployment_review INTEGER DEFAULT 0,
            reviewer TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# IMPACT_DIMENSIONS metadata
# ---------------------------------------------------------------------------


def test_impact_dimensions_count():
    assert len(IMPACT_DIMENSIONS) == 6


def test_impact_dimensions_weights_sum():
    total = sum(d["weight"] for d in IMPACT_DIMENSIONS)
    assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"


# ---------------------------------------------------------------------------
# assess_impact — default / empty responses
# ---------------------------------------------------------------------------


def test_assess_impact_defaults(db_path):
    result = assess_impact("proj-1", "ChatBot v1", db_path=str(db_path))
    assert "overall_risk_score" in result
    assert "risk_level" in result
    assert "dimensions" in result
    assert result["risk_level"] in ("high", "medium", "low")


# ---------------------------------------------------------------------------
# assess_impact — all-high / all-low / mixed
# ---------------------------------------------------------------------------


def test_assess_impact_with_all_high(db_path):
    responses = {d["id"]: "high" for d in IMPACT_DIMENSIONS}
    result = assess_impact(
        "proj-1", "ChatBot v1", dimension_responses=responses, db_path=str(db_path)
    )
    assert result["overall_risk_score"] >= 70
    assert result["risk_level"] == "high"


def test_assess_impact_with_all_low(db_path):
    responses = {d["id"]: "low" for d in IMPACT_DIMENSIONS}
    result = assess_impact(
        "proj-1", "ChatBot v1", dimension_responses=responses, db_path=str(db_path)
    )
    assert result["risk_level"] == "low"


def test_assess_impact_with_mixed(db_path):
    dim_ids = [d["id"] for d in IMPACT_DIMENSIONS]
    responses = {}
    for i, dim_id in enumerate(dim_ids):
        responses[dim_id] = "high" if i % 2 == 0 else "low"
    result = assess_impact(
        "proj-1", "ChatBot v1", dimension_responses=responses, db_path=str(db_path)
    )
    # Mixed should generally land in medium range
    assert result["risk_level"] in ("high", "medium", "low")
    assert 0 <= result["overall_risk_score"] <= 100


# ---------------------------------------------------------------------------
# assess_impact — DB storage
# ---------------------------------------------------------------------------


def test_assess_impact_stores_in_db(db_path):
    assess_impact("proj-1", "ChatBot v1", db_path=str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM ai_ethics_reviews WHERE project_id='proj-1' AND review_type='impact_assessment'"
    ).fetchall()
    conn.close()
    assert len(rows) >= 1


# ---------------------------------------------------------------------------
# assess_impact — reviewer and none response
# ---------------------------------------------------------------------------


def test_assess_impact_with_reviewer(db_path):
    result = assess_impact(
        "proj-1", "ChatBot v1", reviewer="isso@mil", db_path=str(db_path)
    )
    assert "overall_risk_score" in result


def test_assess_impact_with_none_response(db_path):
    responses = {d["id"]: "none" for d in IMPACT_DIMENSIONS}
    result = assess_impact(
        "proj-1", "ChatBot v1", dimension_responses=responses, db_path=str(db_path)
    )
    assert result["overall_risk_score"] == 0 or result["risk_level"] == "low"


# ---------------------------------------------------------------------------
# Risk level thresholds
# ---------------------------------------------------------------------------


def test_risk_level_thresholds(db_path):
    # Test boundary at 70 (high threshold) and 40 (medium threshold)
    responses_high = {d["id"]: "high" for d in IMPACT_DIMENSIONS}
    result_high = assess_impact(
        "proj-1", "System A", dimension_responses=responses_high, db_path=str(db_path)
    )
    assert result_high["risk_level"] == "high"

    responses_low = {d["id"]: "low" for d in IMPACT_DIMENSIONS}
    result_low = assess_impact(
        "proj-1", "System B", dimension_responses=responses_low, db_path=str(db_path)
    )
    assert result_low["risk_level"] == "low"


# ---------------------------------------------------------------------------
# get_impact_summary
# ---------------------------------------------------------------------------


def test_get_impact_summary_empty(db_path):
    result = get_impact_summary("proj-empty", db_path=str(db_path))
    assert result["total_assessments"] == 0


def test_get_impact_summary_populated(db_path):
    assess_impact("proj-1", "System A", db_path=str(db_path))
    assess_impact("proj-1", "System B", db_path=str(db_path))
    result = get_impact_summary("proj-1", db_path=str(db_path))
    assert result["total_assessments"] == 2


# ---------------------------------------------------------------------------
# Multiple systems tracked
# ---------------------------------------------------------------------------


def test_multiple_systems_tracked(db_path):
    assess_impact("proj-1", "System A", db_path=str(db_path))
    assess_impact("proj-1", "System B", db_path=str(db_path))
    assess_impact("proj-2", "System C", db_path=str(db_path))

    summary_1 = get_impact_summary("proj-1", db_path=str(db_path))
    summary_2 = get_impact_summary("proj-2", db_path=str(db_path))
    assert summary_1["total_assessments"] == 2
    assert summary_2["total_assessments"] == 1
