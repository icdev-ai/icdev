# CUI // SP-CTI
"""The AICUR seed is board-valid: ids land on a registered epic, types are legal,
and every dependency names a task in the same batch."""

from pathlib import Path

import yaml

from tools.kanban.seed_aicur_kanban import TASKS
from tools.kanban.task_factory import VALID_TASK_TYPES

REPO = Path(__file__).resolve().parents[2]


def _aicur_card() -> dict:
    data = yaml.safe_load((REPO / "args" / "projects.yaml").read_text(encoding="utf-8"))
    projects = data if isinstance(data, list) else data["projects"]
    return next(p for p in projects if p["key"] == "aicur")


def test_every_task_id_is_claimed_by_a_registered_epic():
    card = _aicur_card()
    prefixes = [f"{card['task_prefix']}{e['key']}-" for e in card["epics"]]
    unclaimed = [t["id"] for t in TASKS if not any(t["id"].startswith(p) for p in prefixes)]
    assert unclaimed == []


def test_task_types_are_legal_and_ids_unique():
    assert all(t["task_type"] in VALID_TASK_TYPES for t in TASKS)
    ids = [t["id"] for t in TASKS]
    assert len(ids) == len(set(ids)) == 15


def test_dependencies_resolve_within_the_batch():
    ids = {t["id"] for t in TASKS}
    assert all(t["depends_on_task_id"] in ids for t in TASKS if "depends_on_task_id" in t)
