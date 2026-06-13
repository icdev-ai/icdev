"""Tests: qa_manager role has exactly 5 structured steps with correct fields."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from icdev.tools.ace.role_loader import RoleLoader, RoleStep


ROLES_DIR = Path(__file__).parent.parent / "args" / "ace" / "roles"


@pytest.fixture(scope="module")
def qa_role():
    loader = RoleLoader(roles_dir=ROLES_DIR, hot_reload=False)
    return loader.get_role("qa_manager")


def test_qa_manager_has_five_steps(qa_role):
    assert len(qa_role.steps) == 5


def test_step_names_in_order(qa_role):
    names = [s.name for s in qa_role.steps]
    assert names == [
        "codelens_scan",
        "coherence_check",
        "e2e_run",
        "autofix",
        "verify_response",
    ]


def test_codelens_scan_tool_and_params(qa_role):
    step = qa_role.steps[0]
    assert step.tool == "CodeLens.analyze_file"
    assert step.params.get("path") == "$build_result.primary_file"
    assert step.condition is None


def test_coherence_check_gate_param(qa_role):
    step = qa_role.steps[1]
    assert step.tool == "CoherenceChecker.run_all"
    assert step.params.get("gate") is True
    assert step.condition is None


def test_e2e_run_step(qa_role):
    step = qa_role.steps[2]
    assert step.tool == "e2e_runner.run_all"
    assert step.condition is None


def test_autofix_step_condition_and_params(qa_role):
    step = qa_role.steps[3]
    assert step.tool == "MessageBus.negotiate"
    assert step.params.get("message_type") == "cw_negotiate_propose"
    assert step.params.get("to_role") == "ai_developer"
    assert step.params.get("max_rounds") == 3
    assert step.condition == "$e2e_result.failed_count > 0"


def test_verify_response_step(qa_role):
    step = qa_role.steps[4]
    assert step.tool == "MessageBus.send"
    assert step.params.get("message_type") == "cw_verify_response"
    assert step.params.get("to") == "ai_developer"
    payload = step.params.get("payload", {})
    assert "passed" in payload
    assert "details" in payload
    assert step.condition is None


def test_all_steps_are_role_step_instances(qa_role):
    for step in qa_role.steps:
        assert isinstance(step, RoleStep)
