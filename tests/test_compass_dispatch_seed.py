# CUI // SP-CTI
"""Tests for the compass dispatch-proof seed (ked-vfy-01 d1).

Covers the acceptance criterion (a valid task definition targeting compass) and,
more importantly, the fail-closed guard: an unregistered prefix resolves to ICDev
by default, so seeding must REFUSE rather than build a compass task in ICDev.
"""
import pytest

from tools.kanban.repo_registry import resolve_task_repo
from tools.kanban.seed_compass_dispatch_probe import check_routing, load_seed


def test_seed_definition_is_valid_and_targets_compass():
    task = load_seed()

    assert task["id"] == "prem-vfy-01"
    assert task["task_type"] == "build"
    assert task["target_repo"] == "icdev-ai/compass"
    assert task["complexity"] == "low"
    assert task["description"].strip()


def test_seed_id_routes_to_the_external_compass_repo():
    """The prefix must be registered, else dispatch silently defaults to ICDev."""
    target = resolve_task_repo(load_seed()["id"])

    assert target.is_external is True
    assert target.name == "compass"
    assert target.base_branch == "main"


def test_check_routing_agrees_with_the_resolver():
    routing = check_routing(load_seed())

    assert routing["repo"] == "compass"
    assert routing["task_id"] == "prem-vfy-01"


def test_unregistered_prefix_is_refused_not_silently_built_in_icdev():
    task = {
        "id": "zzz-unregistered-01",
        "title": "t",
        "task_type": "build",
        "target_repo": "icdev-ai/compass",
        "complexity": "low",
    }
    # Guard against the default-to-ICDev footgun.
    assert resolve_task_repo(task["id"]).is_external is False

    with pytest.raises(ValueError, match="resolves to ICDev"):
        check_routing(task)


def test_routing_to_a_different_repo_than_claimed_is_refused():
    task = {
        "id": "prem-ideal-99",  # registered -> idea_lab, not compass
        "title": "t",
        "task_type": "build",
        "target_repo": "icdev-ai/compass",
        "complexity": "low",
    }
    with pytest.raises(ValueError, match="but the seed claims"):
        check_routing(task)


def test_seed_routes_to_compass_under_the_canonical_import_too():
    """The registry is mirrored, so BOTH import paths must route to compass.

    repo_registry derives its config path from __file__, so the canonical
    `icdev.tools.*` module reads icdev/args/kanban_external_repos.yaml while the
    `tools.*` shim can read args/. Registering prem-vfy in only one of them
    leaves the other defaulting to ICDev — i.e. the compass seed task would be
    built inside ICDev's own tree. Pin both.
    """
    from icdev.tools.kanban.repo_registry import (
        resolve_task_repo as canonical_resolve,
    )

    target = canonical_resolve(load_seed()["id"])

    assert target.is_external is True
    assert target.name == "compass"
