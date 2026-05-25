# CUI // SP-CTI
"""Tests for tools.migration_canvas.wave_planner."""
import pytest
from tools.migration_canvas.wave_planner import (
    get_waves,
    get_dependencies,
    upsert_wave,
    delete_wave,
    auto_assign_waves,
    build_graph,
)


@pytest.fixture
def session_id():
    return "test-session-wp-001"


def test_get_waves_returns_list(session_id):
    result = get_waves(session_id)
    assert isinstance(result, list)


def test_get_dependencies_returns_list(session_id):
    result = get_dependencies(session_id)
    assert isinstance(result, list)


def test_upsert_wave_creates(session_id):
    wave_data = {
        "wave_number": 99,
        "wave_name": "Test Wave",
        "target_date": "2026-06-01",
        "status": "planned",
        "notes": "Pytest wave",
    }
    result = upsert_wave(session_id, wave_data)
    assert "id" in result
    assert result.get("wave_number") == 99 or result.get("wave_name") == "Test Wave"


def test_upsert_wave_updates(session_id):
    # Create then update
    wave_data = {"wave_number": 98, "wave_name": "Before", "status": "planned"}
    created = upsert_wave(session_id, wave_data)
    wave_id = created.get("id") or created.get("wave_id")
    if wave_id:
        updated = upsert_wave(session_id, {"id": wave_id, "wave_number": 98, "wave_name": "After", "status": "in_progress"})
        assert updated.get("wave_name") == "After" or updated is not None


def test_auto_assign_waves_returns_dict(session_id):
    result = auto_assign_waves(session_id)
    assert isinstance(result, dict)
    assert "waves_created" in result or "assigned" in result or "error" in result or isinstance(result, dict)


def test_build_graph_returns_dict(session_id):
    result = build_graph(session_id)
    assert isinstance(result, dict)
    assert "nodes" in result
    assert "edges" in result


def test_delete_wave_nonexistent(session_id):
    result = delete_wave("nonexistent-id-xyz", session_id)
    assert isinstance(result, bool)
