# CUI // SP-CTI
"""Tests for tools.workforce.team_composition.

Validates the functional requirement:
  Team composition: 2 squads of 8 engineers each (16 total),
  1 DevSecOps engineer, 1 ISSO. Total team size 18 members.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.workforce.team_composition import (
    Role,
    Squad,
    TeamComposition,
    data_mesh_conflict_monitoring_team,
    default_engineering_team,
    validate_composition,
)


# ---------------------------------------------------------------------------
# Functional Requirement Validation
# ---------------------------------------------------------------------------


def test_default_team_has_two_squads_of_eight():
    """Scenario: Team composition includes 2 squads of 8 engineers each."""
    team = default_engineering_team()
    assert len(team.squads) == 2
    assert all(squad.role == "engineer" and squad.count == 8 for squad in team.squads)


def test_default_team_has_one_devsecops():
    """Scenario: Team composition includes 1 DevSecOps engineer."""
    team = default_engineering_team()
    devsecops = [r for r in team.specialists if r.name == "DevSecOps"]
    assert len(devsecops) == 1
    assert devsecops[0].count == 1


def test_default_team_has_one_isso():
    """Scenario: Team composition includes 1 ISSO."""
    team = default_engineering_team()
    isso = [r for r in team.specialists if r.name == "ISSO"]
    assert len(isso) == 1
    assert isso[0].count == 1


def test_default_team_total_is_eighteen():
    """Scenario: Total team size is 18 members."""
    team = default_engineering_team()
    assert team.total_members == 18


def test_default_team_passes_validation():
    """Scenario: Validating the default engineering team returns valid."""
    team = default_engineering_team()
    result = validate_composition(team, expected_total=18)
    assert result.valid is True
    assert result.actual_total == 18
    assert result.expected_total == 18
    assert result.errors == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_team_fails_validation():
    team = TeamComposition()
    result = validate_composition(team)
    assert result.valid is False
    assert "at least one squad or specialist" in result.errors[0]


def test_negative_count_fails_validation():
    team = TeamComposition(squads=[Squad(role="engineer", count=-1)])
    result = validate_composition(team)
    assert result.valid is False
    assert "must be positive" in result.errors[0]


def test_mismatch_total_fails_validation():
    team = TeamComposition(
        squads=[Squad(role="engineer", count=5)],
        specialists=[Role(name="DevSecOps", count=1)],
    )
    result = validate_composition(team, expected_total=10)
    assert result.valid is False
    assert "mismatch" in result.errors[0]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_team_serializes_to_dict():
    team = default_engineering_team()
    d = team.to_dict()
    assert d["total_members"] == 18
    assert len(d["squads"]) == 2
    assert len(d["specialists"]) == 2


def test_team_serializes_to_json():
    team = default_engineering_team()
    json_str = team.to_json()
    assert '"total_members": 18' in json_str
    assert "Squad Alpha" in json_str
    assert "Squad Bravo" in json_str


# ---------------------------------------------------------------------------
# Data-Mesh Conflict-Monitoring Team (5 experts)
# ---------------------------------------------------------------------------


def test_data_mesh_team_has_two_data_engineers():
    """Scenario: Team includes 2 data engineers for continuous data-mesh operation."""
    team = data_mesh_conflict_monitoring_team()
    de = [s for s in team.squads if s.role == "data_engineer"]
    assert len(de) == 1
    assert de[0].count == 2


def test_data_mesh_team_has_two_ml_specialists():
    """Scenario: Team includes 2 ML specialists for conflict-monitoring accuracy."""
    team = data_mesh_conflict_monitoring_team()
    ml = [s for s in team.squads if s.role == "ml_specialist"]
    assert len(ml) == 1
    assert ml[0].count == 2


def test_data_mesh_team_has_one_security_analyst():
    """Scenario: Team includes 1 security analyst."""
    team = data_mesh_conflict_monitoring_team()
    sa = [r for r in team.specialists if r.name == "security_analyst"]
    assert len(sa) == 1
    assert sa[0].count == 1


def test_data_mesh_team_total_is_five():
    """Scenario: Total dedicated team size is 5 experts."""
    team = data_mesh_conflict_monitoring_team()
    assert team.total_members == 5


def test_data_mesh_team_passes_validation():
    """Scenario: Validating the data-mesh team returns valid with expected total 5."""
    team = data_mesh_conflict_monitoring_team()
    result = validate_composition(team, expected_total=5)
    assert result.valid is True
    assert result.actual_total == 5
    assert result.expected_total == 5
    assert result.errors == []


def test_data_mesh_team_serializes_to_dict():
    team = data_mesh_conflict_monitoring_team()
    d = team.to_dict()
    assert d["total_members"] == 5
    assert len(d["squads"]) == 2
    assert len(d["specialists"]) == 1
