# CUI // SP-CTI
"""Structural tests for tools/kanban/seed_oss_adaptation.py.

These guard the thing that actually went wrong when this card was first seeded:
the scalar ``depends_on_task_id = oss-gate-00`` alone makes all 22 build tasks
eligible the moment the gate opens, and because ``create_tasks`` stamps one
identical ``created_at`` for the whole batch, the promoter's ``created_at ASC``
tiebreak is arbitrary among same-priority tasks. The junction edges in
:data:`EDGES` are what impose real ordering — so they are asserted here, not
merely assumed.

No DB required: every assertion is over the declared graph.
"""
from __future__ import annotations

import importlib

import pytest

seed = importlib.import_module("tools.kanban.seed_oss_adaptation")
gates = importlib.import_module("tools.kanban.gates")

TASK_IDS = {t["id"] for t in seed.TASKS}


# ---------------------------------------------------------------------------
# Declared graph is internally consistent
# ---------------------------------------------------------------------------

def test_validate_reports_no_problems():
    assert seed.validate() == []


def test_expected_shape():
    assert len(seed.TASKS) == 23           # 1 gate + 22 build tasks
    assert seed.GATE in TASK_IDS
    assert len(TASK_IDS) == len(seed.TASKS)  # no duplicate ids


def test_every_task_id_uses_the_project_prefix():
    for task_id in TASK_IDS:
        assert task_id.startswith("oss-"), task_id


def test_every_edge_endpoint_is_a_declared_task():
    for task_id, dep_id in seed.EDGES:
        assert task_id in TASK_IDS, f"unknown task in edge: {task_id}"
        assert dep_id in TASK_IDS, f"unknown dependency in edge: {dep_id}"


def test_no_self_dependencies():
    assert [e for e in seed.EDGES if e[0] == e[1]] == []


def test_no_duplicate_edges():
    assert len(seed.EDGES) == len(set(seed.EDGES))


def test_graph_is_acyclic():
    """validate() does the cycle check; prove it actually fires on a cycle."""
    original = list(seed.EDGES)
    try:
        seed.EDGES.append(("oss-meas-01", "oss-filter-01"))   # meas <- filter <- meas
        problems = seed.validate()
        assert any("cycle" in p for p in problems), problems
    finally:
        seed.EDGES[:] = original
    assert seed.validate() == []


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_gate_is_recognised_by_both_signals():
    """gates.py matches on id suffix OR title marker; a gate should satisfy both,
    so renaming either one cannot silently un-gate the card."""
    gate = next(t for t in seed.TASKS if t["id"] == seed.GATE)

    assert gate["id"].endswith(gates.GATE_ID_SUFFIX)
    assert gates.GATE_TITLE_MARKER in gate["title"]
    assert gates.is_manual_gate(gate["id"], gate["title"]) is True


def test_gate_is_held_in_progress_and_depends_on_nothing():
    gate = next(t for t in seed.TASKS if t["id"] == seed.GATE)

    assert gate["status"] == "in_progress"
    assert "depends_on_task_id" not in gate
    assert not [e for e in seed.EDGES if e[0] == seed.GATE]


def test_every_build_task_depends_on_the_gate():
    specs = {s["id"]: s for s in seed.build_specs()}

    for task_id, spec in specs.items():
        if task_id == seed.GATE:
            assert spec.get("depends_on_task_id") is None
        else:
            assert spec["depends_on_task_id"] == seed.GATE, task_id


def test_build_specs_are_idempotent_and_start_in_backlog():
    for spec in seed.build_specs():
        assert spec["idempotency_key"] == f"seed:{spec['id']}"
        if spec["id"] != seed.GATE:
            assert spec["status"] == "backlog"


# ---------------------------------------------------------------------------
# The orderings that were actually wrong the first time
# ---------------------------------------------------------------------------

def _blocks(dep_id: str, task_id: str) -> bool:
    """True when task_id cannot start until dep_id is done (transitively)."""
    seen, stack = set(), [task_id]
    while stack:
        node = stack.pop()
        for parent in (d for t, d in seed.EDGES if t == node):
            if parent == dep_id:
                return True
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return False


@pytest.mark.parametrize("dep_id,task_id", [
    # a primitive precedes the scope controls that constrain it, and the
    # registration that exposes it — browse-02 is `high` while browse-01 is
    # `medium`, so priority ordering alone inverts this pair
    ("oss-browse-01", "oss-browse-02"),
    ("oss-browse-01", "oss-browse-03"),
    ("oss-browse-01", "oss-browse-04"),
    # a module precedes the retrofit of its call sites
    ("oss-filter-01", "oss-filter-02"),
    ("oss-filter-01", "oss-filter-03"),
    ("oss-filter-01", "oss-cite-01"),
    # template chunking precedes the schema/benchmark work built on it
    ("oss-chunk-01", "oss-chunk-02"),
    ("oss-chunk-01", "oss-chunk-03"),
    ("oss-chunk-02", "oss-chunk-03"),
    # table-aware chunking precedes the dependency/loudness cleanup
    ("oss-chunk-01", "oss-table-01"),
    ("oss-table-01", "oss-table-02"),
    # measurement precedes the retrieval changes it baselines
    ("oss-meas-01", "oss-filter-01"),
    ("oss-meas-01", "oss-chunk-01"),
    ("oss-meas-01", "oss-chunk-02"),
    # the red team needs the browser and the validation discipline first
    ("oss-browse-01", "oss-redteam-01"),
    ("oss-poc-01", "oss-redteam-01"),
    ("oss-redteam-01", "oss-redteam-02"),
])
def test_ordering_is_enforced(dep_id, task_id):
    assert _blocks(dep_id, task_id), f"{task_id} is not blocked by {dep_id}"


def test_measurement_task_is_unblocked():
    """oss-meas-01 must be able to start the moment the gate opens."""
    assert [e for e in seed.EDGES if e[0] == "oss-meas-01"] == []


def test_defect_cleanup_runs_in_parallel():
    """The card brief promises fix-* can run at any time — keep them edge-free."""
    for task_id in ("oss-fix-01", "oss-fix-02", "oss-fix-03"):
        assert [e for e in seed.EDGES if e[0] == task_id] == [], task_id


def test_no_task_is_stranded_behind_a_nonexistent_parent():
    """A dependency on a task that is never seeded can never be satisfied."""
    for _task_id, dep_id in seed.EDGES:
        assert dep_id in TASK_IDS
