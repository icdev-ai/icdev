"""Tests for the Studio automation condition DSL (dwo-evt-01-d2).

``evaluate_condition`` / ``evaluate_conditions`` are the ONE implementation of
``CONDITION_OPERATORS``.  Workflow triggers and any other event filter import
them; a second DSL would drift from the operator list the UI renders, so these
tests pin the public surface and the operator coverage.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.studio.automation_builder import (
    CONDITION_OPERATORS,
    evaluate_condition,
    evaluate_conditions,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── The helper is importable and module-level ────────────────────────


def test_helper_is_importable_and_public():
    """Other modules must be able to import it without touching a private name."""
    import tools.studio.automation_builder as ab

    assert callable(ab.evaluate_condition)
    assert callable(ab.evaluate_conditions)
    assert not hasattr(ab, "_evaluate_condition"), "the private name should be gone"


def test_icdev_mirror_exposes_the_same_surface():
    """The icdev/ package copy is what installed users get."""
    mirror = REPO_ROOT / "icdev" / "tools" / "studio" / "automation_builder.py"
    tree = ast.parse(mirror.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {"evaluate_condition", "evaluate_conditions"} <= names
    assert "_evaluate_condition" not in names


# ── Every declared operator is implemented ───────────────────────────


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "want"),
    [
        ("equals", "FedRAMP", "fedramp", True),
        ("equals", "FedRAMP", "cmmc", False),
        ("not_equals", "FedRAMP", "cmmc", True),
        ("not_equals", "FedRAMP", "fedramp", False),
        ("contains", "critical stig finding", "stig", True),
        ("contains", "critical stig finding", "cve", False),
        ("greater_than", 10, "5", True),
        ("greater_than", 5, "10", False),
        ("greater_than", "not-a-number", "5", False),
        ("less_than", 5, "10", True),
        ("less_than", 10, "5", False),
        ("less_than", "not-a-number", "5", False),
        ("in_list", "high", "low, medium, high", True),
        ("in_list", "critical", "low, medium, high", False),
        ("is_empty", "", "", True),
        ("is_empty", None, "", True),
        ("is_empty", "value", "", False),
        ("is_not_empty", "value", "", True),
        ("is_not_empty", "", "", False),
        ("is_not_empty", None, "", False),
    ],
)
def test_each_operator(operator, actual, expected, want):
    assert evaluate_condition(actual, operator, expected) is want


def test_every_declared_operator_is_implemented():
    """An operator the UI offers but the evaluator ignores is a silent no-match."""
    probes = [("value", "value"), ("value", "other"), ("10", "5"), ("5", "10"), ("", "value")]
    for op in CONDITION_OPERATORS:
        results = {evaluate_condition(actual, op["id"], expected) for actual, expected in probes}
        assert True in results, f"operator {op['id']} never matches anything"


def test_unknown_operator_never_matches():
    assert evaluate_condition("value", "regex_match", "value") is False


# ── The trace form ───────────────────────────────────────────────────


def test_evaluate_conditions_returns_a_per_condition_trace():
    event = {"severity": "critical", "framework": "FedRAMP"}
    results = evaluate_conditions(
        [
            {"field": "severity", "operator": "equals", "value": "critical"},
            {"field": "framework", "operator": "equals", "value": "cmmc"},
        ],
        event,
    )
    assert [r["met"] for r in results] == [True, False]
    assert results[1] == {
        "field": "framework",
        "operator": "equals",
        "expected": "cmmc",
        "actual": "FedRAMP",
        "met": False,
    }


def test_evaluate_conditions_handles_no_conditions():
    assert evaluate_conditions(None, {}) == []
    assert evaluate_conditions([], {"a": 1}) == []


def test_missing_field_is_evaluated_not_skipped():
    """A condition on a field the event lacks must be a non-match, not a crash."""
    results = evaluate_conditions(
        [{"field": "absent", "operator": "equals", "value": "x"}], {}
    )
    assert results[0]["met"] is False
    assert results[0]["actual"] == ""


def test_condition_defaults_to_equals():
    results = evaluate_conditions([{"field": "a", "value": "1"}], {"a": "1"})
    assert results[0]["operator"] == "equals"
    assert results[0]["met"] is True


# ── No second condition DSL ──────────────────────────────────────────


def test_no_second_condition_dsl_in_studio():
    """Any Studio module filtering events must import the shared helper."""
    offenders = []
    for path in (REPO_ROOT / "tools" / "studio").rglob("*.py"):
        if path.name == "automation_builder.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "not_equals" in source and "is_not_empty" in source:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"second condition DSL found in: {offenders}"
