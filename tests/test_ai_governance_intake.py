# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
"""Tests for AI governance detection in intake pipeline and readiness scoring (Phase 50).

Covers:
  - _detect_ai_governance_signals() from intake_engine.py
  - score_ai_governance_readiness() from ai_governance_scorer.py
  - _load_weights() from readiness_scorer.py (7th dimension)
  - ai_governance_config.yaml loading
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools" / "requirements"))
from intake_engine import _detect_ai_governance_signals  # noqa: E402
from ai_governance_scorer import (  # noqa: E402
    DEFAULT_WEIGHTS,
    _load_gov_config,
    _table_exists,
    score_ai_governance_readiness,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory SQLite with all tables needed by ai_governance_scorer."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE ai_use_case_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, system_name TEXT, created_at TEXT
        );
        CREATE TABLE ai_model_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, model_name TEXT, created_at TEXT
        );
        CREATE TABLE ai_oversight_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, plan_name TEXT, approval_status TEXT,
            created_by TEXT, created_at TEXT
        );
        CREATE TABLE ai_ethics_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, review_type TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE ai_caio_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, name TEXT, role TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE framework_applicability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, framework_id TEXT, status TEXT, created_at TEXT
        );
    """)
    c.commit()
    yield c
    c.close()


@pytest.fixture
def empty_conn():
    """In-memory SQLite with NO governance tables at all."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ===========================================================================
# Keyword detection — _detect_ai_governance_signals
# ===========================================================================

class TestDetectAIGovernanceSignalsPillars:
    """Test each governance pillar keyword detection."""

    def test_ai_inventory_pillar_detected(self):
        result = _detect_ai_governance_signals("We need a machine learning model")
        assert result["ai_governance_detected"] is True
        assert "ai_inventory" in result["detected_pillars"]

    def test_model_documentation_pillar_detected(self):
        result = _detect_ai_governance_signals("The model card needs to be updated")
        assert result["ai_governance_detected"] is True
        assert "model_documentation" in result["detected_pillars"]

    def test_human_oversight_pillar_detected(self):
        result = _detect_ai_governance_signals("We require human oversight for AI decisions")
        assert result["ai_governance_detected"] is True
        assert "human_oversight" in result["detected_pillars"]

    def test_impact_assessment_pillar_detected(self):
        result = _detect_ai_governance_signals("Need an impact assessment for our AI")
        assert result["ai_governance_detected"] is True
        assert "impact_assessment" in result["detected_pillars"]

    def test_transparency_pillar_detected(self):
        result = _detect_ai_governance_signals("Transparency is a core requirement for us")
        assert result["ai_governance_detected"] is True
        assert "transparency" in result["detected_pillars"]

    def test_accountability_pillar_detected(self):
        result = _detect_ai_governance_signals("We need a responsible ai framework")
        assert result["ai_governance_detected"] is True
        assert "accountability" in result["detected_pillars"]


class TestDetectAIGovernanceSignalsFederal:
    """Test federal agency auto-trigger logic."""

    def test_federal_agency_keyword_triggers(self):
        result = _detect_ai_governance_signals("We are a federal agency building a portal")
        assert result["ai_governance_detected"] is True
        assert result["federal_agency_detected"] is True

    def test_omb_keyword_triggers(self):
        result = _detect_ai_governance_signals("Per OMB guidance we need compliance")
        assert result["ai_governance_detected"] is True
        assert result["federal_agency_detected"] is True

    def test_specific_agency_gsa_triggers(self):
        result = _detect_ai_governance_signals("GSA needs a new procurement tool")
        assert result["ai_governance_detected"] is True
        assert result["federal_agency_detected"] is True


class TestDetectAIGovernanceSignalsSession:
    """Test session data customer_org triggers."""

    def test_session_customer_org_dod(self):
        session = {"customer_org": "Department of Defense"}
        result = _detect_ai_governance_signals("build a web app", session)
        assert result["ai_governance_detected"] is True
        assert result["federal_agency_detected"] is True

    def test_session_customer_org_federal(self):
        session = {"customer_org": "Federal Aviation Agency"}
        result = _detect_ai_governance_signals("build a tool", session)
        assert result["ai_governance_detected"] is True
        assert result["federal_agency_detected"] is True

    def test_session_customer_org_commercial(self):
        """Commercial org without AI keywords => no detection."""
        session = {"customer_org": "Acme Corp"}
        result = _detect_ai_governance_signals("build a basic website", session)
        assert result["ai_governance_detected"] is False
        assert result["federal_agency_detected"] is False

    def test_session_none_no_crash(self):
        result = _detect_ai_governance_signals("build a web app", None)
        assert result["ai_governance_detected"] is False


class TestDetectAIGovernanceSignalsMisc:
    """Misc edge cases for signal detection."""

    def test_no_detection_for_unrelated_text(self):
        result = _detect_ai_governance_signals("We need a basic CRUD web application")
        assert result["ai_governance_detected"] is False
        assert result["detected_pillars"] == []
        assert result["pillar_count"] == 0

    def test_multiple_pillars_detected(self):
        text = ("We use a machine learning model and need model card documentation, "
                "plus human oversight for decisions and transparency to users")
        result = _detect_ai_governance_signals(text)
        assert result["ai_governance_detected"] is True
        # Should detect at least ai_inventory, model_documentation, human_oversight, transparency
        assert result["pillar_count"] >= 4
        assert "ai_inventory" in result["detected_pillars"]
        assert "model_documentation" in result["detected_pillars"]
        assert "human_oversight" in result["detected_pillars"]
        assert "transparency" in result["detected_pillars"]

    def test_all_six_pillars_detected(self):
        text = (
            "We have a machine learning chatbot with model card documentation. "
            "We need human oversight, an impact assessment, "
            "transparency and responsible ai accountability."
        )
        result = _detect_ai_governance_signals(text)
        assert result["pillar_count"] == 6
        expected = {"ai_inventory", "model_documentation", "human_oversight",
                    "impact_assessment", "transparency", "accountability"}
        assert set(result["detected_pillars"]) == expected

    def test_absence_signals_not_confused_with_positive(self):
        """Phrases like 'no ai governance' should still trigger detection
        since they contain the AI keywords."""
        result = _detect_ai_governance_signals("We have no ai governance process")
        # 'ai governance' is an accountability keyword, so detection fires
        assert result["ai_governance_detected"] is True
        assert "accountability" in result["detected_pillars"]

    def test_case_insensitivity(self):
        result = _detect_ai_governance_signals("MACHINE LEARNING and NLP systems")
        assert result["ai_governance_detected"] is True
        assert "ai_inventory" in result["detected_pillars"]

    def test_return_structure(self):
        result = _detect_ai_governance_signals("some text")
        assert "ai_governance_detected" in result
        assert "federal_agency_detected" in result
        assert "detected_pillars" in result
        assert "pillar_count" in result
        assert isinstance(result["detected_pillars"], list)

    def test_detected_pillars_sorted(self):
        text = "transparency and accountability with machine learning"
        result = _detect_ai_governance_signals(text)
        assert result["detected_pillars"] == sorted(result["detected_pillars"])


# ===========================================================================
# Config file loading
# ===========================================================================

class TestConfigLoading:
    """Test ai_governance_config.yaml loading."""

    def test_config_file_exists(self):
        config_path = ROOT / "args" / "ai_governance_config.yaml"
        assert config_path.exists(), "ai_governance_config.yaml should exist"

    def test_config_loads_valid_yaml(self):
        config_path = ROOT / "args" / "ai_governance_config.yaml"
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert "ai_governance" in cfg
        assert "intake_detection" in cfg["ai_governance"]
        assert "readiness" in cfg["ai_governance"]
        assert "chat_governance" in cfg["ai_governance"]

    def test_config_has_six_pillars(self):
        config_path = ROOT / "args" / "ai_governance_config.yaml"
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        pillars = cfg["ai_governance"]["intake_detection"]["keywords_by_pillar"]
        expected = {"ai_inventory", "model_documentation", "human_oversight",
                    "impact_assessment", "transparency", "accountability"}
        assert set(pillars.keys()) == expected

    def test_load_gov_config_returns_weights(self):
        weights = _load_gov_config()
        assert isinstance(weights, dict)
        assert "inventory_registered" in weights
        assert "model_cards_present" in weights
        # All weights should be float-like
        for k, v in weights.items():
            assert isinstance(v, (int, float)), f"Weight {k} should be numeric"


# ===========================================================================
# AI Governance Scorer — score_ai_governance_readiness
# ===========================================================================

class TestScoreAIGovernanceReadiness:
    """Test the AI governance readiness scorer."""

    def test_empty_project_score_zero(self, conn):
        result = score_ai_governance_readiness("proj-empty", conn=conn)
        assert result["score"] == 0.0
        assert result["gap_count"] == 6
        assert result["project_id"] == "proj-empty"

    def test_empty_project_all_six_gaps(self, conn):
        result = score_ai_governance_readiness("proj-empty", conn=conn)
        gap_components = [g["component"] for g in result["gaps"]]
        expected = [
            "inventory_registered", "model_cards_present",
            "oversight_plan_exists", "impact_assessment_done",
            "caio_designated", "transparency_frameworks_selected",
        ]
        for comp in expected:
            assert comp in gap_components, f"Missing gap: {comp}"

    def test_with_inventory_partial_score(self, conn):
        conn.execute(
            "INSERT INTO ai_use_case_inventory (project_id, system_name) VALUES (?, ?)",
            ("proj-1", "ChatBot v1"),
        )
        conn.commit()
        result = score_ai_governance_readiness("proj-1", conn=conn)
        # inventory weight = 0.20, so score should be 0.20
        assert result["score"] == pytest.approx(0.20, abs=0.01)
        assert result["components"]["inventory_registered"] == 1.0
        assert result["gap_count"] == 5

    def test_with_all_six_components_full_score(self, conn):
        pid = "proj-full"
        conn.execute(
            "INSERT INTO ai_use_case_inventory (project_id, system_name) VALUES (?, ?)",
            (pid, "System A"),
        )
        conn.execute(
            "INSERT INTO ai_model_cards (project_id, model_name) VALUES (?, ?)",
            (pid, "Model X"),
        )
        conn.execute(
            "INSERT INTO ai_oversight_plans (project_id, plan_name) VALUES (?, ?)",
            (pid, "Plan 1"),
        )
        conn.execute(
            "INSERT INTO ai_ethics_reviews (project_id, review_type) VALUES (?, ?)",
            (pid, "impact_assessment"),
        )
        conn.execute(
            "INSERT INTO ai_caio_registry (project_id, name, role) VALUES (?, ?, ?)",
            (pid, "Jane Doe", "CAIO"),
        )
        conn.execute(
            "INSERT INTO framework_applicability (project_id, framework_id) VALUES (?, ?)",
            (pid, "nist_ai_rmf"),
        )
        conn.commit()
        result = score_ai_governance_readiness(pid, conn=conn)
        assert result["score"] == pytest.approx(1.0, abs=0.01)
        assert result["gap_count"] == 0
        assert all(v == 1.0 for v in result["components"].values())

    def test_missing_tables_handled_gracefully(self, empty_conn):
        """When governance tables don't exist, scorer returns 0 without error."""
        result = score_ai_governance_readiness("proj-x", conn=empty_conn)
        assert result["score"] == 0.0
        assert result["gap_count"] == 6

    def test_gap_list_has_remediation(self, conn):
        result = score_ai_governance_readiness("proj-empty", conn=conn)
        for gap in result["gaps"]:
            assert "component" in gap
            assert "message" in gap
            assert "remediation" in gap

    def test_components_dict_has_all_keys(self, conn):
        result = score_ai_governance_readiness("proj-empty", conn=conn)
        expected_keys = set(DEFAULT_WEIGHTS.keys())
        assert set(result["components"].keys()) == expected_keys

    def test_score_clamped_between_zero_and_one(self, conn):
        result = score_ai_governance_readiness("proj-x", conn=conn)
        assert 0.0 <= result["score"] <= 1.0

    def test_table_exists_helper(self, conn):
        assert _table_exists(conn, "ai_use_case_inventory") is True
        assert _table_exists(conn, "nonexistent_table_xyz") is False

    def test_weights_loaded_from_config(self):
        """_load_gov_config should return weights matching DEFAULT_WEIGHTS keys."""
        weights = _load_gov_config()
        for key in DEFAULT_WEIGHTS:
            assert key in weights
        # Weights should sum to ~1.0
        total = sum(weights.values())
        assert total == pytest.approx(1.0, abs=0.01)


# ===========================================================================
# Readiness scorer integration — 7th dimension
# ===========================================================================

class TestReadinessScorer7thDimension:
    """Test that _load_weights includes ai_governance_readiness."""

    def test_load_weights_returns_seven_dimensions(self):
        sys.path.insert(0, str(ROOT / "tools" / "requirements"))
        from readiness_scorer import _load_weights
        weights = _load_weights()
        assert "ai_governance_readiness" in weights, \
            "_load_weights should include ai_governance_readiness"
        assert isinstance(weights["ai_governance_readiness"], (int, float))

    def test_load_weights_ai_governance_weight_value(self):
        from readiness_scorer import _load_weights
        weights = _load_weights()
        # Per ricoas_config.yaml, should be 0.10
        assert weights["ai_governance_readiness"] == pytest.approx(0.10, abs=0.02)

    def test_load_weights_all_dimensions_present(self):
        from readiness_scorer import _load_weights
        weights = _load_weights()
        expected = {"completeness", "clarity", "feasibility", "compliance",
                    "testability", "devsecops_readiness", "ai_governance_readiness"}
        assert expected.issubset(set(weights.keys()))
