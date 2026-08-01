# CUI // SP-CTI
"""Repo-aware kanban dispatch — resolve which git repo a task builds in.

The kanban scheduler historically dispatched EVERY task into an ICDev worktree
off ICDev's origin/main and validated it against the ICDev tree. External-repo
tasks (e.g. ``prem-*`` compass / idea_lab work) can never pass that pipeline —
their deliverables land in another repo, so ICDev's phantom-completion +
merge-to-origin/main gates fail them and they churn.

This resolver maps a task id to its target repo. A task whose id matches no
prefix — or when the registry file is absent — resolves to ICDev (the default),
so an empty/missing registry is a complete no-op and every existing task is
byte-unchanged.

Config: ``args/kanban_external_repos.yaml`` maps id PREFIXES to a logical repo
NAME; the repo's on-disk root is read from an env var so the committed config
stays environment-agnostic::

    repos:
      compass:  { base_branch: main, root_env: ICDEV_KANBAN_REPO_COMPASS }
      idea_lab: { base_branch: main, root_env: ICDEV_KANBAN_REPO_IDEA_LAB }
    prefixes:
      prem-cpmp: compass
      prem-ideal: idea_lab

An external match whose root env var is unset resolves to ``is_external=True``
with ``root=None`` — the dispatcher must then SKIP the task (never build an
external task inside ICDev), not silently fall back.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ICDEV_ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
_DEFAULT_ICDEV_BRANCH = "main"


@dataclass(frozen=True)
class RepoTarget:
    """Where a task builds. ``is_external`` False == ICDev (default)."""

    name: str              # "icdev" or the logical external repo name
    root: Optional[Path]   # on-disk repo root; None == external-but-unconfigured
    base_branch: str       # branch to build off (e.g. "main")
    is_external: bool

    @property
    def dispatchable(self) -> bool:
        """False only for an external task whose repo root isn't configured."""
        return self.root is not None


def _icdev_target() -> RepoTarget:
    return RepoTarget(name="icdev", root=_ICDEV_ROOT,
                      base_branch=_DEFAULT_ICDEV_BRANCH, is_external=False)


def _registry_path() -> Path:
    override = os.environ.get("ICDEV_KANBAN_REPOS_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return _ICDEV_ROOT / "args" / "kanban_external_repos.yaml"


def _load_registry(config_path: Optional[Path] = None) -> dict:
    path = config_path or _registry_path()
    try:
        if not path.is_file():
            return {}
        import yaml
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 — a broken registry must never break dispatch
        return {}


def resolve_task_repo(task_id: str, config_path: Optional[Path] = None) -> RepoTarget:
    """Resolve the target repo for ``task_id`` (defaults to ICDev).

    Longest matching id-prefix wins. Any resolution error degrades to the ICDev
    default — dispatch behaviour is never broken by a bad registry.
    """
    try:
        reg = _load_registry(config_path)
        prefixes = reg.get("prefixes") or {}
        repos = reg.get("repos") or {}
        # Longest prefix match so "prem-cpmp" beats a broader "prem".
        match = None
        for prefix in sorted(prefixes, key=len, reverse=True):
            if task_id.startswith(prefix):
                match = prefixes[prefix]
                break
        if not match or match not in repos:
            return _icdev_target()
        repo_cfg = repos[match] or {}
        base_branch = str(repo_cfg.get("base_branch") or "main")
        root_env = repo_cfg.get("root_env")
        root_str = os.environ.get(root_env, "").strip() if root_env else ""
        # An explicit root in the config is allowed but discouraged (env wins).
        root_str = root_str or str(repo_cfg.get("root") or "").strip()
        root = Path(root_str).expanduser() if root_str else None
        return RepoTarget(name=str(match), root=root,
                          base_branch=base_branch, is_external=True)
    except Exception:  # noqa: BLE001
        return _icdev_target()


def is_external_task(task_id: str, config_path: Optional[Path] = None) -> bool:
    return resolve_task_repo(task_id, config_path).is_external
