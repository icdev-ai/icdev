# CUI // SP-CTI
"""Seed kanban tasks from a YAML task-registry file under ``tasks/``.

The historical way to seed a stream was a bespoke ``seed_<stream>.py`` script with
the task list hard-coded in Python. That is fine for a one-shot batch but useless
for an EXTERNAL task, where the thing a reviewer needs to see — which repo does
this build in? — is buried in code. A registry file states it as data:

    # tasks/compass/verify_dispatch.yml
    target_repo: icdev-ai/compass
    repo_key: compass
    tasks:
      - {id: prem-vdis-01, title: ..., task_type: build, complexity: low}

Crucially, ``target_repo`` does NOT choose the build repo — the dispatcher always
resolves that from the task-id prefix via ``args/kanban_external_repos.yaml``
(tools/kanban/repo_registry.py). Declaring it here lets us CROSS-CHECK the two:
``validate_seed`` fails if an id's prefix does not resolve to the declared repo,
so a seed file can never quietly ship tasks that build somewhere other than where
it claims. An external task that resolves to ICDev is the exact bug ked-reg-01
fixed; this keeps it fixed.

Writes go through ``tools.kanban.task_factory.create_tasks`` (idempotent, skips
existing ids) — never a raw INSERT. Dry-run is the default; pass ``--seed`` to
write.

    python -m tools.kanban.seed_from_yaml --file tasks/compass/verify_dispatch.yml --json
    python -m tools.kanban.seed_from_yaml --file tasks/compass/verify_dispatch.yml --seed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_DIR = REPO_ROOT / "tasks"

# task_type must be a value the board actually dispatches on; complexity is
# registry metadata (the board has no such column) used to keep seeds small.
VALID_TASK_TYPES = {"build", "bug", "chore", "test", "research", "review"}
VALID_COMPLEXITY = {"low", "medium", "high"}


class SeedError(ValueError):
    """A seed file is malformed or contradicts the dispatch registry."""


def load_seed(path: Path) -> dict[str, Any]:
    """Parse a seed file. Raises SeedError if it is not a YAML mapping."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SeedError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SeedError(f"{path.name}: top level must be a mapping, got {type(data).__name__}")
    return data


def validate_seed(seed: dict[str, Any], name: str = "seed",
                  registry_path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Validate a parsed seed and return its task dicts.

    Beyond shape, this asserts every task id's prefix resolves — through the REAL
    dispatch registry — to the repo the seed declares. A seed that says compass
    but whose ids fall through to the ICDev default is rejected, loudly.
    """
    from tools.kanban.repo_registry import resolve_task_repo

    tasks = seed.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise SeedError(f"{name}: `tasks:` must be a non-empty list")

    target_repo = seed.get("target_repo")
    repo_key = seed.get("repo_key")
    if target_repo and not repo_key:
        raise SeedError(f"{name}: `target_repo: {target_repo}` requires `repo_key:` "
                        "naming the repo in args/kanban_external_repos.yaml")

    seen: set[str] = set()
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise SeedError(f"{name}: tasks[{i}] must be a mapping")
        where = f"{name}: tasks[{i}]"

        task_id = str(task.get("id") or "").strip()
        if not task_id:
            raise SeedError(f"{where} has no `id`")
        if task_id in seen:
            raise SeedError(f"{where}: duplicate id {task_id!r}")
        seen.add(task_id)

        if not str(task.get("title") or "").strip():
            raise SeedError(f"{where} ({task_id}) has no `title`")

        task_type = task.get("task_type")
        if task_type not in VALID_TASK_TYPES:
            raise SeedError(f"{where} ({task_id}): task_type={task_type!r} not in "
                            f"{sorted(VALID_TASK_TYPES)}")

        complexity = task.get("complexity")
        if complexity not in VALID_COMPLEXITY:
            raise SeedError(f"{where} ({task_id}): complexity={complexity!r} not in "
                            f"{sorted(VALID_COMPLEXITY)}")

        # The load-bearing check: does this id ACTUALLY build where we claim?
        if repo_key:
            target = (resolve_task_repo(task_id, config_path=registry_path)
                      if registry_path else resolve_task_repo(task_id))
            if target.name != repo_key:
                raise SeedError(
                    f"{where} ({task_id}): seed declares repo_key={repo_key!r} but the id "
                    f"prefix resolves to {target.name!r}. Register the prefix in "
                    "args/kanban_external_repos.yaml — otherwise this task builds in the "
                    "wrong repo."
                )
    return tasks


def seed_file(path: Path, write: bool = False) -> dict[str, Any]:
    """Validate one seed file and (if write) create its tasks on the board."""
    seed = load_seed(path)
    tasks = validate_seed(seed, name=path.name)
    task_ids = [str(t["id"]) for t in tasks]

    result: dict[str, Any] = {
        "file": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
        "target_repo": seed.get("target_repo"),
        "repo_key": seed.get("repo_key"),
        "tasks": task_ids,
        "created": [],
        "dry_run": not write,
    }
    if not write:
        return result

    from tools.kanban.task_factory import create_tasks

    # complexity is registry-only metadata — strip it before it reaches the DB.
    specs = [{k: v for k, v in t.items() if k != "complexity"} for t in tasks]
    result["created"] = create_tasks(specs)
    return result


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Seed kanban tasks from a YAML task-registry file")
    ap.add_argument("--file", help="seed file (default: every tasks/**/*.yml)")
    ap.add_argument("--seed", action="store_true", help="write to the board (default: dry-run)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.file:
        paths = [Path(args.file) if Path(args.file).is_absolute() else REPO_ROOT / args.file]
    else:
        paths = sorted(TASKS_DIR.rglob("*.yml")) + sorted(TASKS_DIR.rglob("*.yaml"))

    results, errors = [], []
    for path in paths:
        if not path.exists():
            errors.append(f"{path}: not found")
            continue
        try:
            results.append(seed_file(path, write=args.seed))
        except SeedError as exc:
            errors.append(str(exc))

    if args.json:
        print(json.dumps({"results": results, "errors": errors}, indent=2))
    else:
        for r in results:
            verb = f"created {len(r['created'])}" if not r["dry_run"] else "dry-run"
            print(f"{r['file']} -> {r['repo_key'] or 'icdev'} ({verb}): {', '.join(r['tasks'])}")
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
