# CUI // SP-CTI
"""Tests for the YAML task-seed registry (tools/kanban/seed_from_yaml.py).

Two things are under test:

1. The compass dispatch seed (tasks/compass/verify_dispatch.yml) meets the
   ked-vfy-01-d1 acceptance criteria: valid YAML, task_type=build,
   target_repo=icdev-ai/compass, complexity=low.
2. The property that actually matters — the seeded id resolves, through the REAL
   args/kanban_external_repos.yaml, to the compass repo and is EXTERNAL. A seed
   that claims compass but whose ids fall through to the ICDev default would
   build compass work inside the ICDev tree; that is the ked-reg-01 bug, and
   validate_seed must reject it.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from tools.kanban import seed_from_yaml as sfy
from tools.kanban.repo_registry import resolve_task_repo

SEED_PATH = sfy.REPO_ROOT / "tasks" / "compass" / "verify_dispatch.yml"


# --- the compass dispatch seed -------------------------------------------------

def test_compass_seed_file_exists():
    assert SEED_PATH.exists(), f"missing seed registry file: {SEED_PATH}"


def test_compass_seed_is_valid_yaml_and_declares_compass():
    seed = sfy.load_seed(SEED_PATH)
    assert seed["target_repo"] == "icdev-ai/compass"
    assert seed["repo_key"] == "compass"


def test_compass_seed_task_is_a_low_complexity_build():
    seed = sfy.load_seed(SEED_PATH)
    tasks = sfy.validate_seed(seed, name="verify_dispatch.yml")
    assert len(tasks) == 1, "the dispatch proof seeds exactly one small task"
    task = tasks[0]
    assert task["task_type"] == "build"
    assert task["complexity"] == "low"
    assert task["id"] == "prem-vdis-01"
    assert task["description"].strip()


def test_compass_seed_id_resolves_to_external_compass_repo():
    """The load-bearing one: this must NOT build in the ICDev tree."""
    seed = sfy.load_seed(SEED_PATH)
    for task in seed["tasks"]:
        target = resolve_task_repo(task["id"])
        assert target.is_external is True, f"{task['id']} would build inside ICDev"
        assert target.name == "compass"


def test_prem_vdis_prefix_is_registered_in_the_real_registry():
    registry = yaml.safe_load(
        (sfy.REPO_ROOT / "args" / "kanban_external_repos.yaml").read_text(encoding="utf-8")
    )
    assert registry["prefixes"]["prem-vdis"] == "compass"


# --- validate_seed guardrails ---------------------------------------------------

def _seed(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "seed.yml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_validate_rejects_seed_whose_ids_resolve_to_the_wrong_repo(tmp_path):
    """A seed claiming compass whose ids fall through to ICDev is the ked-reg-01 bug."""
    path = _seed(tmp_path, """
        target_repo: icdev-ai/compass
        repo_key: compass
        tasks:
          - {id: ctx-core-01, title: T, task_type: build, complexity: low}
    """)
    seed = sfy.load_seed(path)
    with pytest.raises(sfy.SeedError, match="resolves to 'icdev'"):
        sfy.validate_seed(seed, name="seed.yml")


def test_validate_rejects_bad_task_type_and_complexity(tmp_path):
    seed = sfy.load_seed(_seed(tmp_path, """
        tasks:
          - {id: x-01, title: T, task_type: sculpt, complexity: low}
    """))
    with pytest.raises(sfy.SeedError, match="task_type"):
        sfy.validate_seed(seed, name="seed.yml")

    seed = sfy.load_seed(_seed(tmp_path, """
        tasks:
          - {id: x-01, title: T, task_type: build, complexity: trivial}
    """))
    with pytest.raises(sfy.SeedError, match="complexity"):
        sfy.validate_seed(seed, name="seed.yml")


def test_validate_rejects_empty_and_duplicate_and_titleless_tasks(tmp_path):
    seed = sfy.load_seed(_seed(tmp_path, "tasks: []\n"))
    with pytest.raises(sfy.SeedError, match="non-empty list"):
        sfy.validate_seed(seed, name="seed.yml")

    seed = sfy.load_seed(_seed(tmp_path, """
        tasks:
          - {id: x-01, title: T, task_type: build, complexity: low}
          - {id: x-01, title: T2, task_type: build, complexity: low}
    """))
    with pytest.raises(sfy.SeedError, match="duplicate id"):
        sfy.validate_seed(seed, name="seed.yml")

    seed = sfy.load_seed(_seed(tmp_path, """
        tasks:
          - {id: x-01, title: '', task_type: build, complexity: low}
    """))
    with pytest.raises(sfy.SeedError, match="no `title`"):
        sfy.validate_seed(seed, name="seed.yml")


def test_load_seed_rejects_invalid_yaml(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("tasks: [unclosed\n", encoding="utf-8")
    with pytest.raises(sfy.SeedError, match="invalid YAML"):
        sfy.load_seed(path)


def test_seed_file_dry_run_does_not_write(tmp_path, monkeypatch):
    """Default is dry-run: no task_factory call, no board write."""
    import tools.kanban.task_factory as tf
    monkeypatch.setattr(tf, "create_tasks",
                        lambda specs: pytest.fail("dry-run must not write to the board"))
    result = sfy.seed_file(SEED_PATH, write=False)
    assert result["dry_run"] is True
    assert result["created"] == []
    assert result["tasks"] == ["prem-vdis-01"]


def test_seed_file_write_strips_complexity_before_the_db(monkeypatch):
    """`complexity` is registry metadata — kanban_tasks has no such column."""
    import tools.kanban.task_factory as tf
    captured: list[dict] = []

    def fake_create(specs):
        captured.extend(specs)
        return [s["id"] for s in specs]

    monkeypatch.setattr(tf, "create_tasks", fake_create)
    result = sfy.seed_file(SEED_PATH, write=True)

    assert result["created"] == ["prem-vdis-01"]
    assert captured and all("complexity" not in s for s in captured)
    assert captured[0]["task_type"] == "build"
