# CUI // SP-CTI
"""Seed the single compass task that proves repo-aware dispatch (ked-vfy-01).

Reads the task definition from ``args/kanban_seed_compass_dispatch.yaml`` and
inserts it via ``task_factory.create_tasks`` (never a raw INSERT).

Why this validates before it seeds
----------------------------------
``repo_registry.resolve_task_repo`` resolves an UNREGISTERED prefix to ICDev —
that is the intended default, but it means a task we *believe* targets compass
will silently build inside ICDev's own tree if its prefix was never registered
in ``args/kanban_external_repos.yaml``. A dispatch proof that quietly proves the
wrong thing is worse than no proof, so this seeder refuses to insert unless the
resolver independently agrees the task routes to the repo the YAML claims.

An external repo whose root env var is unset resolves to ``dispatchable=False``;
the dispatcher then SKIPS the task rather than falling back to ICDev. That is a
safe state to seed into, so it is a warning here, not an error.

Usage:
    python -m tools.kanban.seed_compass_dispatch_probe --dry-run --json
    python -m tools.kanban.seed_compass_dispatch_probe --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from tools.kanban.repo_registry import resolve_task_repo

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SEED_FILE = _REPO_ROOT / "args" / "kanban_seed_compass_dispatch.yaml"

_REQUIRED = ("id", "title", "task_type", "target_repo", "complexity")


def load_seed(path: Path | None = None) -> dict[str, Any]:
    """Load and structurally validate the task definition."""
    seed_path = path or _SEED_FILE
    with open(seed_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}

    task = doc.get("task")
    if not isinstance(task, dict):
        raise ValueError(f"{seed_path.name}: missing top-level 'task' mapping")

    missing = [k for k in _REQUIRED if not str(task.get(k) or "").strip()]
    if missing:
        raise ValueError(f"{seed_path.name}: task missing required field(s): {', '.join(missing)}")
    return task


def check_routing(task: dict[str, Any]) -> dict[str, Any]:
    """Confirm the resolver routes this task id to the repo the YAML claims.

    Returns the routing facts; raises ValueError on any disagreement.
    """
    task_id = str(task["id"])
    # "icdev-ai/compass" -> "compass", the logical repo name used by the registry.
    claimed = str(task["target_repo"]).rsplit("/", 1)[-1]
    target = resolve_task_repo(task_id)

    if not target.is_external:
        raise ValueError(
            f"{task_id!r} resolves to ICDev, not {claimed!r}. Its prefix is not "
            f"registered in args/kanban_external_repos.yaml — seeding now would "
            f"build a compass task inside ICDev's tree."
        )
    if target.name != claimed:
        raise ValueError(
            f"{task_id!r} resolves to repo {target.name!r} but the seed claims "
            f"{claimed!r}. Fix the prefix mapping in args/kanban_external_repos.yaml."
        )

    return {
        "task_id": task_id,
        "repo": target.name,
        "base_branch": target.base_branch,
        "root": str(target.root) if target.root else None,
        "dispatchable": target.dispatchable,
    }


def _to_spec(task: dict[str, Any]) -> dict[str, Any]:
    """Map the YAML definition onto a task_factory spec.

    ``target_repo``/``complexity`` are routing/sizing metadata, not kanban_tasks
    columns — the repo is carried by the id prefix, so they are not passed on.
    """
    return {
        "id": task["id"],
        "title": task["title"],
        "description": task.get("description") or "",
        "task_type": task.get("task_type", "build"),
        "priority": task.get("priority", "medium"),
        "status": task.get("status", "backlog"),
        "idempotency_key": f"seed:{task['id']}",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed the compass dispatch-proof task")
    ap.add_argument("--dry-run", action="store_true",
                    help="Validate the seed and its routing; write nothing")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args(argv)

    try:
        task = load_seed()
        routing = check_routing(task)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        result = {"ok": False, "error": str(exc)}
        print(json.dumps(result) if args.json else f"FAIL: {exc}", file=sys.stderr)
        return 1

    warnings: list[str] = []
    if not routing["dispatchable"]:
        warnings.append(
            f"repo {routing['repo']!r} has no configured root (env var unset) — the "
            f"dispatcher will SKIP this task, not build it in ICDev. Set the root_env "
            f"from args/kanban_external_repos.yaml where the scheduler runs."
        )

    created: list[str] = []
    if not args.dry_run:
        from tools.kanban.task_factory import create_tasks
        created = create_tasks([_to_spec(task)])

    result = {
        "ok": True,
        "dry_run": args.dry_run,
        "routing": routing,
        "created": created,
        "warnings": warnings,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{task['id']} -> {routing['repo']} (base {routing['base_branch']})")
        for w in warnings:
            print(f"WARN: {w}")
        if args.dry_run:
            print("dry-run: nothing written")
        else:
            print(f"created: {created or '(already present)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
