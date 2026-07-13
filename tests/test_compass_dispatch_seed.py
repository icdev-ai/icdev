# CUI // SP-CTI
"""Tests for the compass dispatch-proof seed (ked-vfy-01 d1).

Covers the acceptance criterion — a valid task definition targeting compass —
and the fail-closed guard behind it: ``resolve_task_repo`` defaults an
unregistered id prefix to ICDev, so a seed task whose prefix is missing from
args/kanban_external_repos.yaml would be BUILT INSIDE ICDev's own tree. The
seed file and the routing registry must therefore agree, and these tests fail
if they ever drift apart.
"""
from pathlib import Path

import yaml

from icdev.tools.kanban.repo_registry import resolve_task_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / "args" / "kanban_seed_compass_dispatch.yaml"


def load_seed() -> dict:
    with open(SEED_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["task"]


def test_seed_file_exists_and_is_valid_yaml():
    assert SEED_PATH.is_file(), f"seed task definition missing: {SEED_PATH}"
    assert isinstance(load_seed(), dict)


def test_seed_definition_targets_compass():
    task = load_seed()

    assert task["id"] == "prem-vfy-01"
    assert task["task_type"] == "build"
    assert task["target_repo"] == "icdev-ai/compass"
    assert task["complexity"] == "low"
    assert task["description"].strip()


def test_seed_id_routes_to_the_external_compass_repo():
    """An unregistered prefix would silently default to ICDev — assert it doesn't."""
    target = resolve_task_repo(load_seed()["id"])

    assert target.is_external is True
    assert target.name == "compass"
    assert target.base_branch == "main"


def test_seed_target_repo_and_routing_registry_agree():
    """target_repo in the seed must name the same repo the resolver picks."""
    task = load_seed()
    resolved = resolve_task_repo(task["id"])

    # "icdev-ai/compass" -> "compass"
    claimed = task["target_repo"].split("/")[-1]
    assert claimed == resolved.name


def test_unregistered_prefix_still_defaults_to_icdev():
    """Documents the footgun the prefix registration exists to avoid."""
    assert resolve_task_repo("zzz-unregistered-01").is_external is False
