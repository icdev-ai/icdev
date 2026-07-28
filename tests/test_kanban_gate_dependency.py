# CUI // SP-CTI
"""A manual-mode gate must actually hold its tasks back.

Holding a ``*-gate-00`` sentinel ``in_progress`` does nothing on its own. The
dispatcher's eligibility predicate in ``tools/genesis/reflexes/kanban.py`` is::

    depends_on_task_id IS NULL OR the dependency is done

so a task only waits behind a gate when something points at it. A seeder that
creates the sentinel but wires no dependency produces a gate that looks held on
the board and dispatches anyway — which is exactly what happened: nine ARR/CLX
tasks were picked up and built by the runner while a human was implementing the
same work by hand.

These tests are seeder-level and DB-independent. They assert the wiring exists,
not that a particular board is in a particular state.
"""
from __future__ import annotations

import pytest

from tools.kanban.gates import GATE_ID_SUFFIX, is_manual_gate
from tools.kanban.seed_ahx_arr_clx import _gate_for, _specs


def test_every_seeded_work_task_depends_on_a_gate():
    """The regression guard for the incident. Without this, gates are decorative."""
    _gates, work = _specs()
    unwired = [t["id"] for t in work if not t.get("depends_on_task_id")]
    assert not unwired, (
        "these tasks would be dispatched despite a held gate — a sentinel with "
        f"nothing pointing at it does not hold anything: {unwired}"
    )


def test_every_dependency_names_a_gate_that_is_actually_seeded():
    """A dependency on a task that does not exist would never resolve."""
    gates, work = _specs()
    gate_ids = {g["id"] for g in gates}
    dangling = [
        (t["id"], t["depends_on_task_id"])
        for t in work
        if t["depends_on_task_id"] not in gate_ids
    ]
    assert not dangling, f"dependency points at a non-existent gate: {dangling}"


def test_tasks_wait_behind_their_own_cards_gate():
    """arr- work must not be held by the clx- gate, or the cards uncouple."""
    _gates, work = _specs()
    for task in work:
        prefix = task["id"].split("-", 1)[0]
        assert task["depends_on_task_id"] == f"{prefix}-gate-00", (
            f"{task['id']} waits behind {task['depends_on_task_id']}, "
            f"not its own card's gate"
        )


@pytest.mark.parametrize(
    "task_id,expected",
    [
        ("arr-res-01", "arr-gate-00"),
        ("clx-fb-01", "clx-gate-00"),
        ("ahx-eval-02", "ahx-gate-00"),
    ],
)
def test_gate_derivation(task_id, expected):
    assert _gate_for(task_id) == expected


def test_gates_are_recognised_as_manual_sentinels():
    """The sentinels must match the shared predicate the runner uses."""
    gates, _work = _specs()
    for gate in gates:
        assert gate["id"].endswith(GATE_ID_SUFFIX)
        assert is_manual_gate(gate["id"], gate.get("title"))


def test_gates_are_seeded_held_and_work_is_seeded_parked():
    gates, work = _specs()
    assert all(g["status"] == "in_progress" for g in gates), (
        "a gate that is not in_progress is a gate that is open"
    )
    assert all(t["status"] == "backlog" for t in work)


def test_a_gate_does_not_depend_on_itself():
    """Self-dependency would deadlock the gate against its own completion."""
    gates, _work = _specs()
    for gate in gates:
        assert not gate.get("depends_on_task_id")
