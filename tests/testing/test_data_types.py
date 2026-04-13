# CUI // SP-CTI
"""Spec-conformance + enhancement tests for tools/testing/data_types.py."""
from __future__ import annotations

import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.testing import data_types as dt  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Module surface
# ────────────────────────────────────────────────────────────────────────────


_REQUIRED_NAMES = (
    "TestResult", "E2ETestResult", "CheckResult", "HealthCheckResult",
    "GateResult", "GateEvaluation", "TestRunState",
    "AgentPromptRequest", "AgentPromptResponse", "AgentTemplateRequest",
    "AcceptanceCriterionResult", "UIPageCheckResult", "AcceptanceReport",
    "BaseModel", "Field",
)


def test_module_exports_every_documented_name():
    for name in _REQUIRED_NAMES:
        assert hasattr(dt, name), f"missing export: {name}"


# ────────────────────────────────────────────────────────────────────────────
# Field defaults
# ────────────────────────────────────────────────────────────────────────────


def test_test_result_defaults():
    r = dt.TestResult(test_name="t1", passed=True,
                      execution_command="pytest", test_purpose="x")
    assert r.error is None
    assert r.test_type == "unit"
    assert r.duration_ms is None
    assert r.nist_controls == []


def test_e2e_test_result_passed_property():
    pass_one = dt.E2ETestResult(
        test_name="t", status="passed", test_path="t.md",
    )
    fail_one = dt.E2ETestResult(
        test_name="t", status="failed", test_path="t.md",
    )
    assert pass_one.passed is True
    assert fail_one.passed is False


def test_health_check_defaults():
    h = dt.HealthCheckResult(success=True, timestamp="2026-04-11")
    assert h.checks == {}
    assert h.warnings == []
    assert h.errors == []


def test_gate_evaluation_defaults():
    g = dt.GateEvaluation(gate_type="merge", overall_pass=True)
    assert g.gates == []
    assert g.evaluated_by == "icdev-testing"
    assert g.timestamp == ""


def test_test_run_state_counts_default_to_zero():
    s = dt.TestRunState(run_id="run-1")
    for field in (
        "unit_passed", "unit_failed", "bdd_passed", "bdd_failed",
        "e2e_passed", "e2e_failed", "unit_attempts", "e2e_attempts",
    ):
        assert getattr(s, field) == 0, f"{field} default wrong"


def test_agent_prompt_request_defaults():
    r = dt.AgentPromptRequest(prompt="hi")
    assert r.agent_name == "ops"
    assert r.model == "sonnet"
    assert r.output_file == ""
    assert r.project_dir == "."


def test_agent_template_request_args_default_empty():
    r = dt.AgentTemplateRequest(agent_name="ops", slash_command="/x")
    assert r.args == []


def test_acceptance_report_defaults():
    a = dt.AcceptanceReport(plan_file="plan.md")
    assert a.criteria == []
    assert a.page_checks == []
    assert a.overall_pass is False


# ────────────────────────────────────────────────────────────────────────────
# Independent mutable defaults — the bug class the spec calls out
# ────────────────────────────────────────────────────────────────────────────


def test_list_defaults_are_not_shared_between_instances():
    a = dt.TestResult(
        test_name="a", passed=True,
        execution_command="x", test_purpose="x",
    )
    b = dt.TestResult(
        test_name="b", passed=True,
        execution_command="x", test_purpose="x",
    )
    a.nist_controls.append("SA-11")
    assert b.nist_controls == [], (
        "mutating one TestResult.nist_controls leaked into the next instance"
    )


def test_dict_defaults_are_not_shared_between_instances():
    a = dt.HealthCheckResult(success=True, timestamp="t")
    b = dt.HealthCheckResult(success=True, timestamp="t")
    a.checks["x"] = dt.CheckResult(success=True)
    assert b.checks == {}


# ────────────────────────────────────────────────────────────────────────────
# Serialisation
# ────────────────────────────────────────────────────────────────────────────


def test_model_dump_round_trip():
    r = dt.TestResult(
        test_name="t", passed=True,
        execution_command="pytest", test_purpose="smoke",
    )
    d = r.model_dump()
    assert d["test_name"] == "t"
    assert d["passed"] is True


def test_model_dump_json_parses():
    r = dt.GateResult(gate_name="merge", passed=True)
    parsed = json.loads(r.model_dump_json())
    assert parsed["gate_name"] == "merge"
    assert parsed["passed"] is True


# ────────────────────────────────────────────────────────────────────────────
# Forbidden imports
# ────────────────────────────────────────────────────────────────────────────


def test_no_db_or_llm_imports():
    src = pathlib.Path(dt.__file__).read_text(encoding="utf-8")
    assert "tools.db" not in src
    assert "tools.llm" not in src
    assert "psycopg2" not in src
    assert "import requests" not in src
