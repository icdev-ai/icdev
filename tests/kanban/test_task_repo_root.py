# CUI // SP-CTI
"""Tests for ``kanban._task_repo_root`` — the repo root a task's git gates run in.

An unregistered/internal task resolves to ICDev's BASE_DIR (today's behaviour,
byte-unchanged); a registered external task resolves to that repo's on-disk root.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as kb  # noqa: E402
from tools.kanban.repo_registry import RepoTarget  # noqa: E402


def _patch_resolver(monkeypatch, target: RepoTarget) -> None:
    """Patch the resolver the helper imports at call time (module attr, not name)."""
    import importlib

    rr = importlib.import_module("tools.kanban.repo_registry")
    monkeypatch.setattr(rr, "resolve_task_repo", lambda task_id, **kw: target)


def test_unregistered_task_is_base_dir():
    assert kb._task_repo_root("nonexistent-task") == kb.BASE_DIR


def test_internal_task_is_base_dir(monkeypatch):
    _patch_resolver(monkeypatch, RepoTarget(
        name="icdev", root=Path("/some/icdev"), base_branch="main", is_external=False,
    ))
    assert kb._task_repo_root("ked-core-03-d1") == kb.BASE_DIR


def test_external_task_resolves_to_its_repo_root(monkeypatch, tmp_path):
    external = tmp_path / "compass"
    external.mkdir()
    _patch_resolver(monkeypatch, RepoTarget(
        name="compass", root=external, base_branch="main", is_external=True,
    ))
    assert kb._task_repo_root("prem-cpmp-01") == external


def test_external_task_without_configured_root_is_base_dir(monkeypatch):
    # root_env unset -> root None. Fall back to BASE_DIR so the git gates behave
    # exactly as they do today (they simply find no kanban/<id> branch).
    _patch_resolver(monkeypatch, RepoTarget(
        name="compass", root=None, base_branch="main", is_external=True,
    ))
    assert kb._task_repo_root("prem-cpmp-01") == kb.BASE_DIR


def test_resolution_error_degrades_to_base_dir(monkeypatch):
    import importlib

    rr = importlib.import_module("tools.kanban.repo_registry")

    def _boom(task_id, **kw):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(rr, "resolve_task_repo", _boom)
    assert kb._task_repo_root("prem-cpmp-01") == kb.BASE_DIR


def test_returns_a_path():
    assert isinstance(kb._task_repo_root("nonexistent-task"), Path)


@pytest.mark.parametrize("tid", ["", "x", "prem", "ked-core-03-d1"])
def test_never_raises(tid):
    assert isinstance(kb._task_repo_root(tid), Path)
