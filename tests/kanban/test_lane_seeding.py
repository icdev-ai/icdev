# CUI // SP-CTI
"""Seeding a card as lanes, and the number that catches a one-lane mistake.

THE DEFECT, and it was a planning session's own. On 2026-09-11 a 22-task card
was seeded as ONE linear `depends_on_task_id` chain -- what the seeding
convention literally says -- so exactly one task could ever be dispatched
against `MAX_IN_PROGRESS = 3`. The repair was a hand-written
`UPDATE kanban_tasks SET depends_on_task_id` over 21 rows: a raw board write, by
the session that should know better.

`startable` is the number that would have caught it in the second it was made,
so it is what these tests assert on.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.kanban.lanes import LaneError, describe, plan_lanes

_LANES = Path(__file__).resolve().parents[2] / "tools" / "kanban" / "lanes.py"


def _spec(task_id: str) -> dict:
    return {"id": task_id, "title": task_id, "description": "d",
            "acceptance_criteria": "a"}


def test_three_lanes_give_three_startable_tasks():
    plan = plan_lanes({"cost": [_spec("a1"), _spec("a2")],
                       "mem": [_spec("b1"), _spec("b2")],
                       "bin": [_spec("c1")]})
    assert plan["startable"] == 3
    assert sorted(plan["startable_ids"]) == ["a1", "b1", "c1"]
    assert plan["critical_path"] == 2
    assert plan["task_count"] == 5


def test_within_a_lane_the_order_is_preserved():
    """Ordering is the POINT of a lane -- tasks that share files must not run
    together. Only the ordering ACROSS lanes is removed."""
    plan = plan_lanes({"cost": [_spec("a1"), _spec("a2"), _spec("a3")]})
    by_id = {s["id"]: s for s in plan["specs"]}
    assert by_id["a1"]["depends_on_task_id"] is None
    assert by_id["a2"]["depends_on_task_id"] == "a1"
    assert by_id["a3"]["depends_on_task_id"] == "a2"


def test_one_lane_reports_one_startable_and_says_so():
    """THE INCIDENT, reproduced. A single chain is legal -- it is one lane -- but
    the report must make the consequence impossible to miss."""
    plan = plan_lanes({"all": [_spec(f"x{i}") for i in range(6)]})
    assert plan["startable"] == 1
    assert plan["critical_path"] == 6 == plan["task_count"]
    assert "one lane means one task at a time" in describe(plan)


def test_a_gate_holds_every_lane_and_nothing_is_startable():
    """`depends_on_task_id=<gate>` is the ONLY thing that holds a task; a claim
    is a second lock, never the first."""
    plan = plan_lanes({"lab": [_spec("g1"), _spec("g2")]},
                      gate_task_id="xrv-gate-00")
    by_id = {s["id"]: s for s in plan["specs"]}
    assert by_id["g1"]["depends_on_task_id"] == "xrv-gate-00"
    assert by_id["g2"]["depends_on_task_id"] == "g1"
    assert plan["startable"] == 0


def test_a_caller_declared_dependency_is_never_overwritten():
    """Silently replacing a declared gate dependency with a lane predecessor
    would RELEASE work a human deliberately held."""
    held = _spec("h1")
    held["depends_on_task_id"] = "other-gate-00"
    plan = plan_lanes({"x": [_spec("a"), held]})
    by_id = {s["id"]: s for s in plan["specs"]}
    assert by_id["h1"]["depends_on_task_id"] == "other-gate-00"


def test_the_same_task_in_two_lanes_is_refused():
    with pytest.raises(LaneError, match="exactly one lane"):
        plan_lanes({"a": [_spec("dup")], "b": [_spec("dup")]})


def test_an_empty_lane_is_refused_not_ignored():
    """Ignoring it would seed fewer tasks than the caller thinks they asked
    for -- silently."""
    with pytest.raises(LaneError, match="empty"):
        plan_lanes({"a": [_spec("x")], "b": []})


def test_no_lanes_at_all_is_refused():
    with pytest.raises(LaneError):
        plan_lanes({})


def test_a_spec_without_an_id_is_refused():
    with pytest.raises(LaneError, match="no id"):
        plan_lanes({"a": [{"title": "t"}]})


def test_planning_touches_no_database(monkeypatch):
    """`plan_lanes` must be safe to call anywhere -- it is the half a caller
    inspects before deciding to seed."""
    import tools.kanban.task_factory as tf

    def boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("plan_lanes reached the seeder")

    monkeypatch.setattr(tf, "create_tasks", boom)
    assert plan_lanes({"a": [_spec("x")]})["task_count"] == 1


def test_create_lanes_seeds_only_through_the_canonical_seeder(monkeypatch):
    """Every guarantee create_tasks provides -- task-type validation, the
    real-board refusal, the gate-shaped-id refusal, dedupe -- must still apply."""
    calls = {}

    def fake_create_tasks(specs, *, claim=False):
        calls["specs"] = specs
        calls["claim"] = claim
        return [s["id"] for s in specs]

    import tools.kanban.lanes as lanes_mod
    monkeypatch.setattr("tools.kanban.task_factory.create_tasks", fake_create_tasks)
    out = lanes_mod.create_lanes({"a": [_spec("x"), _spec("y")]}, claim=True)
    assert out["created"] == ["x", "y"]
    assert calls["claim"] is True
    assert calls["specs"][1]["depends_on_task_id"] == "x"


def test_the_module_contains_no_board_write():
    """A helper that could INSERT or UPDATE directly would reintroduce exactly
    the raw board write this module exists to remove. Read from source, because
    a behavioural test over today's callers would still pass the day someone
    adds one."""
    src = _LANES.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # DOCSTRINGS ARE EXCLUDED, and this is not a loophole -- it is the trap this
    # repo has already been bitten by twice. lanes.py EXPLAINS itself by quoting
    # the very statement it exists to remove ("a hand-written `UPDATE
    # kanban_tasks SET depends_on_task_id` over 21 rows"), so a naive substring
    # scan flags the fix's own explanation of itself. Same shape as the
    # perfect_score census, whose first candidate entry was the previous fix's
    # comment describing the defect. What matters is EXECUTABLE SQL, so only
    # non-docstring string literals are scanned.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            upper = node.value.upper()
            for sql in ("INSERT INTO", "UPDATE KANBAN_TASKS", "DELETE FROM"):
                assert sql not in upper, f"lanes.py builds SQL: {sql}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None)
            assert name not in {"execute", "executemany", "commit"}, (
                f"lanes.py calls {name}; it must seed only through create_tasks")
