# CUI // SP-CTI
"""The CTX seed must not be able to produce an undispatchable or unheld card.

Every assertion here is a failure mode this repo has actually shipped:

- A task with no ``depends_on_task_id`` is NOT held by a held gate sentinel.
  ``ctx-gate-00`` sitting ``in_progress`` looks like a hold and is not one —
  ``promote_backlog_to_scheduled`` reads the scalar dependency, not gate status.
  A single ungated task means the runner starts building a card whose whole
  point is that a human drives it.
- An id ending ``-gate-<n>`` makes ``tools/kanban/gates.py::is_manual_gate``
  return True, so a WORK task wearing that shape is filtered out forever and
  nothing reports it as stuck (kax-exec-04). ``task_factory`` refuses this at
  seed time; this pins that the CTX set never offers it one.
- A ``task_type`` outside the live CHECK constraint seeds cleanly against a
  fallback SQLite DB and only fails part-way through the insert loop on
  PostgreSQL.
- An epic key in a task id that does not exist in ``args/projects.yaml`` renders
  no progress on the Home card, which is the card's only visible surface.

These are all cheap to assert and expensive to discover on the board.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from tools.kanban import seed_ctx_kanban as seed

PROJECT_KEY = "ctx"
PREFIX = "ctx-"


@pytest.fixture(scope="module")
def project() -> dict:
    root = pathlib.Path(__file__).resolve().parents[2]
    data = yaml.safe_load((root / "args" / "projects.yaml").read_text(encoding="utf-8"))
    matches = [p for p in data["projects"] if p.get("key") == PROJECT_KEY]
    assert len(matches) == 1, f"expected exactly one {PROJECT_KEY!r} project, got {len(matches)}"
    return matches[0]


def test_every_work_task_declares_the_gate_as_its_dependency():
    """A held gate alone does not hold — depends_on_task_id is the real gate."""
    ungated = [
        t["id"] for t in seed.TASKS
        if t["id"] != seed.GATE and t.get("depends_on_task_id") != seed.GATE
    ]
    assert not ungated, (
        f"{len(ungated)} task(s) would dispatch while the gate is held: {ungated}"
    )


def test_the_gate_sentinel_is_held_and_depends_on_nothing():
    gate = [t for t in seed.TASKS if t["id"] == seed.GATE]
    assert len(gate) == 1
    assert gate[0]["status"] == "in_progress", "a backlog gate is not holding anything"
    assert gate[0].get("depends_on_task_id") is None


def test_task_ids_are_unique():
    ids = [t["id"] for t in seed.TASKS]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate task ids: {dupes}"


def test_no_work_task_wears_a_gate_shaped_id():
    """`<card>-gate-<n>` on a work task is undispatchable, silently (kax-exec-04)."""
    offenders = [
        t["id"] for t in seed.TASKS
        if t["id"] != seed.GATE and "-gate-" in t["id"]
    ]
    assert not offenders, f"gate-shaped work ids: {offenders}"


def test_task_types_match_the_live_check_constraint():
    bad = sorted({
        t["task_type"] for t in seed.TASKS
        if t["task_type"] not in seed.VALID_TASK_TYPES
    })
    assert not bad, (
        f"task_type {bad} violates kanban_tasks_task_type_check; "
        f"allowed: {sorted(seed.VALID_TASK_TYPES)} (there is no 'bug' — use 'fix')"
    )


def test_every_task_id_uses_a_declared_epic(project):
    """`<task_prefix><epic_key>-<N>` — an unknown epic renders no progress."""
    epics = {e["key"] for e in project["epics"]}
    bad = []
    for t in seed.TASKS:
        assert t["id"].startswith(PREFIX), f"{t['id']} does not start with {PREFIX!r}"
        epic = t["id"][len(PREFIX):].rsplit("-", 1)[0]
        if epic not in epics:
            bad.append((t["id"], epic))
    assert not bad, f"tasks referencing undeclared epics {sorted(epics)}: {bad}"


def test_project_prefix_does_not_collide_with_another_project():
    """No two projects may have prefixes where one is a prefix of the other."""
    root = pathlib.Path(__file__).resolve().parents[2]
    data = yaml.safe_load((root / "args" / "projects.yaml").read_text(encoding="utf-8"))
    others = [
        str(p["task_prefix"]) for p in data["projects"]
        if p.get("task_prefix") and p.get("key") != PROJECT_KEY
    ]
    clashes = [o for o in others if o.startswith(PREFIX) or PREFIX.startswith(o)]
    assert not clashes, f"{PREFIX!r} collides with {clashes}"


def test_no_epic_key_is_a_prefix_of_another(project):
    keys = [e["key"] for e in project["epics"]]
    bad = [(a, b) for a in keys for b in keys if a != b and b.startswith(a + "-")]
    assert not bad, f"epic key prefix collisions: {bad}"


def test_every_work_task_carries_acceptance_criteria():
    """Persisted for the dispatcher; without it review_conformance cannot judge."""
    missing = [
        t["id"] for t in seed.TASKS
        if t["id"] != seed.GATE and not t.get("acceptance_criteria")
    ]
    assert not missing, f"work tasks with no acceptance_criteria: {missing}"


def test_dry_run_inserts_nothing_and_reports_every_task(capsys):
    """--dry-run must never reach task_factory."""
    import sys

    argv = sys.argv
    sys.argv = ["seed_ctx_kanban", "--dry-run", "--json"]
    try:
        assert seed.main() == 0
    finally:
        sys.argv = argv

    import json

    report = json.loads(capsys.readouterr().out)
    assert report["count"] == len(seed.TASKS)
    assert report["gate"] == seed.GATE
    assert set(report["would_create"]) == {t["id"] for t in seed.TASKS}
