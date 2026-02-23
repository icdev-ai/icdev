# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.simulation.simulation_engine -- 6-dimension what-if simulation."""

import json
import sqlite3
from unittest.mock import patch

import pytest

from tools.simulation.simulation_engine import (
    ALL_DIMENSIONS,
    DEFAULT_HOURLY_RATE,
    INFRA_COST_PER_COMPONENT,
    STORIES_PER_SPRINT,
    SPRINTS_PER_PI,
    TSHIRT_HOURS,
    _impact_score,
    _pct,
    _generate_recommendations,
    create_scenario,
    get_scenario,
    list_scenarios,
    run_simulation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMULATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS simulation_scenarios (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT,
    scenario_name TEXT NOT NULL,
    scenario_type TEXT NOT NULL
        CHECK(scenario_type IN ('what_if', 'coa_comparison', 'risk_monte_carlo',
            'schedule_impact', 'cost_impact', 'compliance_impact',
            'supply_chain_disruption', 'architecture_change', 'compound')),
    base_state TEXT NOT NULL,
    modifications TEXT NOT NULL,
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending', 'running', 'completed', 'failed', 'archived')),
    classification TEXT DEFAULT 'CUI',
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS simulation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL,
    dimension TEXT NOT NULL
        CHECK(dimension IN ('architecture', 'compliance', 'supply_chain',
            'schedule', 'cost', 'risk')),
    metric_name TEXT NOT NULL,
    baseline_value REAL,
    simulated_value REAL,
    delta REAL,
    delta_pct REAL,
    confidence REAL DEFAULT 0.0,
    impact_tier TEXT CHECK(impact_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    details TEXT,
    visualizations TEXT,
    calculated_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def sim_db(icdev_db):
    """Extend icdev_db with simulation_scenarios and simulation_results tables."""
    conn = sqlite3.connect(str(icdev_db))
    conn.executescript(SIMULATION_SCHEMA)
    conn.close()
    return icdev_db


# ---------------------------------------------------------------------------
# TestCreateScenario
# ---------------------------------------------------------------------------

class TestCreateScenario:
    """create_scenario: persist a new simulation scenario to the DB."""

    def test_create_basic_scenario(self, sim_db):
        result = create_scenario(
            project_id="proj-test-001",
            scenario_name="Add auth module",
            scenario_type="what_if",
            modifications={"add_requirements": 3},
            db_path=sim_db,
        )
        assert result["scenario_name"] == "Add auth module"
        assert result["scenario_type"] == "what_if"
        assert result["status"] == "pending"
        assert "scenario_id" in result

    def test_create_returns_unique_id(self, sim_db):
        r1 = create_scenario("proj-test-001", "S1", "what_if", {}, db_path=sim_db)
        r2 = create_scenario("proj-test-001", "S2", "what_if", {}, db_path=sim_db)
        assert r1["scenario_id"] != r2["scenario_id"]

    def test_create_with_risk_analysis_type(self, sim_db):
        result = create_scenario(
            project_id="proj-test-001",
            scenario_name="Risk scenario",
            scenario_type="risk_analysis",
            modifications={},
            db_path=sim_db,
        )
        assert result["scenario_type"] == "risk_analysis"

    def test_create_with_session_id(self, sim_db):
        result = create_scenario(
            project_id="proj-test-001",
            scenario_name="Session-linked",
            scenario_type="what_if",
            modifications={"add_requirements": 1},
            base_session_id="sess-abc",
            db_path=sim_db,
        )
        assert result["status"] == "pending"


# ---------------------------------------------------------------------------
# TestGetScenario
# ---------------------------------------------------------------------------

class TestGetScenario:
    """get_scenario: retrieve scenario metadata and results."""

    def test_get_existing_scenario(self, sim_db):
        created = create_scenario(
            "proj-test-001", "Get test", "what_if", {"add_requirements": 1},
            db_path=sim_db,
        )
        fetched = get_scenario(created["scenario_id"], db_path=sim_db)
        assert fetched["scenario_name"] == "Get test"
        assert fetched["status"] == "pending"
        assert isinstance(fetched["modifications"], dict)

    def test_get_nonexistent_raises(self, sim_db):
        with pytest.raises(ValueError, match="Scenario not found"):
            get_scenario("nonexistent-id", db_path=sim_db)

    def test_get_includes_results_after_run(self, sim_db):
        created = create_scenario(
            "proj-test-001", "Run then get", "what_if",
            {"add_requirements": 2}, db_path=sim_db,
        )
        run_simulation(created["scenario_id"], dimensions=["cost"], db_path=sim_db)
        fetched = get_scenario(created["scenario_id"], db_path=sim_db)
        assert "results" in fetched
        assert "cost" in fetched["results"]


# ---------------------------------------------------------------------------
# TestListScenarios
# ---------------------------------------------------------------------------

class TestListScenarios:
    """list_scenarios: list all scenarios for a project."""

    def test_list_empty_project(self, sim_db):
        result = list_scenarios("proj-no-scenarios", db_path=sim_db)
        assert result["count"] == 0
        assert result["scenarios"] == []

    def test_list_returns_created_scenarios(self, sim_db):
        create_scenario("proj-test-001", "A", "what_if", {}, db_path=sim_db)
        create_scenario("proj-test-001", "B", "what_if", {}, db_path=sim_db)
        result = list_scenarios("proj-test-001", db_path=sim_db)
        assert result["count"] == 2
        names = [s["scenario_name"] for s in result["scenarios"]]
        assert "A" in names
        assert "B" in names

    def test_list_only_returns_own_project(self, sim_db):
        create_scenario("proj-test-001", "Mine", "what_if", {}, db_path=sim_db)
        create_scenario("proj-other", "Other", "what_if", {}, db_path=sim_db)
        result = list_scenarios("proj-test-001", db_path=sim_db)
        assert result["count"] == 1
        assert result["scenarios"][0]["scenario_name"] == "Mine"


# ---------------------------------------------------------------------------
# TestRunSimulation
# ---------------------------------------------------------------------------

class TestRunSimulation:
    """run_simulation: execute dimension simulators and persist results."""

    def test_run_all_dimensions(self, sim_db):
        created = create_scenario(
            "proj-test-001", "All dims", "what_if",
            {"add_requirements": 5}, db_path=sim_db,
        )
        result = run_simulation(created["scenario_id"], db_path=sim_db)
        assert set(result["dimensions"].keys()) == set(ALL_DIMENSIONS)
        assert "overall_impact_score" in result
        assert "recommendations" in result

    def test_run_single_dimension(self, sim_db):
        created = create_scenario(
            "proj-test-001", "Cost only", "what_if",
            {"add_requirements": 2}, db_path=sim_db,
        )
        result = run_simulation(
            created["scenario_id"], dimensions=["cost"], db_path=sim_db,
        )
        assert list(result["dimensions"].keys()) == ["cost"]
        cost = result["dimensions"]["cost"]
        assert "baseline" in cost
        assert "simulated" in cost
        assert "delta" in cost
        assert "delta_pct" in cost
        assert "chart_data" in cost

    def test_run_marks_completed(self, sim_db):
        created = create_scenario(
            "proj-test-001", "Complete me", "what_if", {}, db_path=sim_db,
        )
        run_simulation(created["scenario_id"], db_path=sim_db)
        fetched = get_scenario(created["scenario_id"], db_path=sim_db)
        assert fetched["status"] == "completed"

    def test_run_nonexistent_scenario_raises(self, sim_db):
        with pytest.raises(ValueError, match="Scenario not found"):
            run_simulation("does-not-exist", db_path=sim_db)

    def test_run_cost_adds_hours_for_new_requirements(self, sim_db):
        created = create_scenario(
            "proj-test-001", "Cost check", "what_if",
            {"add_requirements": 3}, db_path=sim_db,
        )
        result = run_simulation(
            created["scenario_id"], dimensions=["cost"], db_path=sim_db,
        )
        cost = result["dimensions"]["cost"]
        # 3 new reqs * 80 hours each = 240 hours added
        assert cost["simulated"]["total_hours"] == cost["baseline"]["total_hours"] + 240

    def test_run_schedule_adds_stories(self, sim_db):
        created = create_scenario(
            "proj-test-001", "Schedule check", "what_if",
            {"add_requirements": 10}, db_path=sim_db,
        )
        result = run_simulation(
            created["scenario_id"], dimensions=["schedule"], db_path=sim_db,
        )
        sched = result["dimensions"]["schedule"]
        assert sched["simulated"]["story_count"] >= 10


# ---------------------------------------------------------------------------
# TestImpactScoring
# ---------------------------------------------------------------------------

class TestImpactScoring:
    """_impact_score and _generate_recommendations: overall analysis."""

    def test_impact_score_empty_returns_zero(self):
        assert _impact_score({}) == 0.0

    def test_impact_score_with_dimensions(self):
        dims = {
            "cost": {"delta_pct": 50.0},
            "schedule": {"delta_pct": 30.0},
        }
        score = _impact_score(dims)
        # cost weight=0.20, schedule weight=0.15 -> 50*0.20 + 30*0.15 = 10+4.5 = 14.5
        assert score == 14.5

    def test_impact_score_capped_at_100(self):
        dims = {dim: {"delta_pct": 200.0} for dim in ALL_DIMENSIONS}
        score = _impact_score(dims)
        assert score <= 100.0

    def test_recommendations_no_concerns(self):
        dims = {
            "cost": {"delta_pct": 5.0},
            "schedule": {"delta_pct": 3.0},
            "risk": {"delta_pct": 2.0},
            "compliance": {"delta_pct": 1.0},
        }
        recs = _generate_recommendations(dims)
        assert any("No significant concerns" in r for r in recs)

    def test_recommendations_cost_increase(self):
        dims = {"cost": {"delta_pct": 25.0}}
        recs = _generate_recommendations(dims)
        assert any("Cost increased" in r for r in recs)


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------

class TestHelpers:
    """_pct and constants: utility functions and module-level values."""

    def test_pct_zero_baseline_nonzero_sim(self):
        assert _pct(0, 10) == 100.0

    def test_pct_zero_baseline_zero_sim(self):
        assert _pct(0, 0) == 0.0

    def test_pct_positive_change(self):
        assert _pct(100, 150) == 50.0

    def test_pct_negative_change(self):
        assert _pct(100, 80) == -20.0

    def test_pct_string_inputs_return_zero(self):
        assert _pct("GREEN", "RED") == 0.0

    def test_all_dimensions_constant(self):
        assert len(ALL_DIMENSIONS) == 6
        assert "architecture" in ALL_DIMENSIONS
        assert "compliance" in ALL_DIMENSIONS
        assert "risk" in ALL_DIMENSIONS

    def test_tshirt_hours_keys(self):
        assert set(TSHIRT_HOURS.keys()) == {"XS", "S", "M", "L", "XL", "XXL"}


# [TEMPLATE: CUI // SP-CTI]
