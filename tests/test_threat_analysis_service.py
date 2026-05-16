# CUI // SP-CTI
"""Tests for ICDEV™ Threat Analysis Service — baseline score validation.

Covers:
- validate_indicator_score (hierarchical lookup, threshold comparison)
- create_baseline (input validation, persistence)
- list_baselines (filtering)
"""
import pytest

from tools.threat_analysis.service import (
    create_baseline,
    list_baselines,
    validate_indicator_score,
)


@pytest.fixture
def db_conn():
    """Yield a fresh SQLite connection and clean up indicator_baselines."""
    from tools.db.storage import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM indicator_baselines")
    conn.commit()
    yield conn
    conn.execute("DELETE FROM indicator_baselines")
    conn.commit()
    conn.close()


class TestValidateIndicatorScore:
    def test_unbounded_when_no_baselines(self, db_conn):
        result = validate_indicator_score("cpu_usage", 85.0)
        assert result["indicator_name"] == "cpu_usage"
        assert result["score"] == 85.0
        assert result["valid"] is True
        assert result["exceeded"] is False
        assert result["unbounded"] is True
        assert result["threshold"] is None

    def test_global_baseline_valid(self, db_conn):
        create_baseline("cpu_usage", threshold_score=90.0, scope="global", operator_id="op-1")
        result = validate_indicator_score("cpu_usage", 85.0)
        assert result["valid"] is True
        assert result["exceeded"] is False
        assert result["unbounded"] is False
        assert result["threshold"] == 90.0
        assert result["severity_band"] == "medium"
        assert result["delta"] == 0.0

    def test_global_baseline_exceeded(self, db_conn):
        create_baseline("cpu_usage", threshold_score=80.0, scope="global", operator_id="op-1")
        result = validate_indicator_score("cpu_usage", 85.0)
        assert result["valid"] is False
        assert result["exceeded"] is True
        assert result["threshold"] == 80.0
        assert result["delta"] == 5.0

    def test_project_baseline_takes_precedence_over_global(self, db_conn):
        create_baseline(
            "cpu_usage", threshold_score=70.0, scope="global", operator_id="op-1"
        )
        create_baseline(
            "cpu_usage", threshold_score=60.0, scope="project", scope_id="proj-a",
            operator_id="op-1"
        )
        # Score 65 exceeds project baseline (60) but not global (70)
        result = validate_indicator_score("cpu_usage", 65.0, scope="project", scope_id="proj-a")
        assert result["valid"] is False
        assert result["exceeded"] is True
        assert result["threshold"] == 60.0
        assert result["scope_used"] == "project"

    def test_fallback_to_global_when_no_project_match(self, db_conn):
        create_baseline(
            "cpu_usage", threshold_score=70.0, scope="global", operator_id="op-1"
        )
        create_baseline(
            "cpu_usage", threshold_score=60.0, scope="project", scope_id="proj-a",
            operator_id="op-1"
        )
        # Different project ID — should fall back to global
        result = validate_indicator_score("cpu_usage", 65.0, scope="project", scope_id="proj-b")
        assert result["valid"] is True
        assert result["exceeded"] is False
        assert result["threshold"] == 70.0
        assert result["scope_used"] == "global"

    def test_negative_score_raises(self, db_conn):
        with pytest.raises(ValueError, match="non-negative"):
            validate_indicator_score("cpu_usage", -1.0)


class TestCreateBaseline:
    def test_persists_and_returns_id(self, db_conn):
        result = create_baseline(
            "memory_usage", threshold_score=75.0, scope="project", scope_id="proj-x",
            indicator_category="infrastructure", severity_band="high",
            operator_id="op-42", rationale="Production stress limit"
        )
        assert "id" in result
        assert result["indicator_name"] == "memory_usage"
        assert result["threshold_score"] == 75.0
        assert result["scope"] == "project"
        assert result["severity_band"] == "high"

    def test_invalid_scope_raises(self, db_conn):
        with pytest.raises(ValueError, match="Invalid scope"):
            create_baseline("cpu_usage", threshold_score=50.0, scope="invalid")

    def test_negative_threshold_raises(self, db_conn):
        with pytest.raises(ValueError, match="non-negative"):
            create_baseline("cpu_usage", threshold_score=-5.0)


class TestListBaselines:
    def test_filter_by_indicator_name(self, db_conn):
        create_baseline("cpu_usage", 80.0, scope="global", operator_id="op-1")
        create_baseline("memory_usage", 70.0, scope="global", operator_id="op-1")
        rows = list_baselines(indicator_name="cpu_usage")
        assert len(rows) == 1
        assert rows[0]["indicator_name"] == "cpu_usage"

    def test_filter_by_scope(self, db_conn):
        create_baseline("cpu_usage", 80.0, scope="global", operator_id="op-1")
        create_baseline("cpu_usage", 70.0, scope="project", scope_id="p1", operator_id="op-1")
        rows = list_baselines(scope="project")
        assert len(rows) == 1
        assert rows[0]["scope"] == "project"

    def test_filter_by_is_active(self, db_conn):
        create_baseline("cpu_usage", 80.0, scope="global", operator_id="op-1")
        rows = list_baselines(is_active=True)
        assert len(rows) == 1
        rows = list_baselines(is_active=False)
        assert len(rows) == 0
