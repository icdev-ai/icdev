"""dwo-evt-01-d2 — the condition DSL is one importable, module-level helper.

`evaluate_condition` used to be private to `automation_builder`, which pushed
any other module that needed the same operators toward a second copy of the
DSL. These tests pin the public names and pin that there is only one
implementation behind them.
"""

import pytest

from tools.studio import automation_builder
from tools.studio.automation_builder import evaluate_condition, evaluate_conditions


def test_public_helper_is_importable_and_module_level():
    assert callable(evaluate_condition)
    assert evaluate_condition.__module__.endswith("studio.automation_builder")


def test_private_spellings_alias_the_public_ones():
    """No fork: the old underscore names are the same function object."""
    assert automation_builder._evaluate_condition is evaluate_condition
    assert automation_builder._evaluate_conditions is evaluate_conditions


@pytest.mark.parametrize(
    ("actual", "operator", "expected", "want"),
    [
        ("OPEN", "equals", "open", True),
        ("OPEN", "not_equals", "open", False),
        ("build failed", "contains", "FAILED", True),
        (7, "greater_than", "3", True),
        ("x", "greater_than", "3", False),
        (2, "less_than", "3", True),
        ("beta", "in_list", "alpha, beta", True),
        ("", "is_empty", "", True),
        (None, "is_empty", "", True),
        ("v", "is_not_empty", "", True),
        ("v", "no_such_operator", "", False),
    ],
)
def test_every_operator_in_the_dsl(actual, operator, expected, want):
    assert evaluate_condition(actual, operator, expected) is want


def test_conditions_wrapper_returns_a_per_condition_trace():
    trace = evaluate_conditions(
        [{"field": "status", "operator": "equals", "value": "open"}],
        {"status": "OPEN"},
    )
    assert trace == [
        {
            "field": "status",
            "operator": "equals",
            "expected": "open",
            "actual": "OPEN",
            "met": True,
        }
    ]


def test_no_second_condition_dsl_in_studio():
    """Any studio module needing the operators must import, not re-define."""
    import pathlib

    studio = pathlib.Path(automation_builder.__file__).parent
    definers = [
        path.name
        for path in studio.glob("*.py")
        if "def evaluate_condition(" in path.read_text(encoding="utf-8")
        or "def _evaluate_condition(" in path.read_text(encoding="utf-8")
    ]
    assert definers == ["automation_builder.py"]
