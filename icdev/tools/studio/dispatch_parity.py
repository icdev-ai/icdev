#!/usr/bin/env python3
"""Prove wave-parallel dispatch did not reorder any pre-existing template (hgx-vv-01).

`hgx-par-01` replaced the workflow runner's eager `_resolve_dag` walk with a
prepared `TopologicalSorter` driven by `get_ready()`/`done()`. The claim that
made that change safe to ship is written in the runner as a comment:

    At max_parallel == 1 this walks the graph in exactly `_resolve_dag` order.

A comment is not evidence. This module turns it into a check anybody can re-run:
for every template on disk it computes the order the PRE-parallel runner would
have executed, replays the CURRENT dispatch loop at one slot, and diffs the two
sequences. A template that does not declare `max_parallel` must come out
byte-for-byte identical or the change was not backward compatible.

Both template roots are covered — `args/workflow_templates` (the FORGE composer
set) and `context/workflow_templates` (the Studio gallery) — matching
`template_linter.TEMPLATE_DIRS`.

    python -m icdev.tools.studio.dispatch_parity --json
    python -m icdev.tools.studio.dispatch_parity --gate

Exit code is 1 under `--gate` when any template's order diverges.
"""

from __future__ import annotations

import argparse
import json
import sys
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

import yaml

# Root from __file__, never os.getcwd() — this runs from worktrees and CI
# checkouts whose working directory is not the repo root.
#
# A FIXED parents[N] cannot work here: this module ships as two full copies at
# DIFFERENT depths — `tools/studio/` (3 up) and `icdev/tools/studio/` (4 up) —
# so the same literal resolves one level above the repo for one of them, and
# `glob` on a nonexistent directory returns empty rather than raising. That
# failure mode is a check that silently compares zero templates and reports
# success. Walk up and match a MARKER instead, so both copies land on the same
# root and a packaged install still resolves.
def _find_root(start: Path) -> Path:
    """Nearest ancestor holding the template gallery.

    `args/workflow_templates` is the repo layout; `data/args/workflow_templates`
    is the packaged one (`icdev/data/...`). Checking a directory that only the
    real root has keeps `icdev/` itself from matching.
    """
    chain = [start, *start.parents]
    # Repo layout FIRST, across the whole chain. Checking both layouts per
    # candidate would let the packaged gallery under `icdev/data/` win for the
    # `icdev/tools/studio/` copy, pointing the two copies at DIFFERENT galleries
    # while both are sitting in the same checkout.
    for candidate in chain:
        if (candidate / "args" / "workflow_templates").is_dir():
            return candidate
    for candidate in chain:
        if (candidate / "data" / "args" / "workflow_templates").is_dir():
            return candidate / "data"
    # Nothing matched: fall back to the repo-layout guess so the caller gets an
    # empty, clearly-attributable report rather than an exception at import.
    return start.parents[2]


_REPO_ROOT = _find_root(Path(__file__).resolve().parent)

TEMPLATE_DIRS: tuple[Path, ...] = (
    _REPO_ROOT / "args" / "workflow_templates",
    _REPO_ROOT / "context" / "workflow_templates",
)


def _dag_graph(steps: list) -> dict:
    """{step_id: {dependency_id, ...}} — identical in both runner generations."""
    graph: dict[str, set] = {}
    for step in steps:
        graph[step["id"]] = set(step.get("depends_on", []) or [])
    return graph


def baseline_order(steps: list) -> list:
    """The order the PRE-hgx-par-01 runner executed.

    Reproduces `_resolve_dag` as it stood at 6aaa6b014^: one flattened
    `static_order()`, walked one step at a time.
    """
    return list(TopologicalSorter(_dag_graph(steps)).static_order())


def dispatch_order(steps: list, max_parallel: int = 1) -> list:
    """The order the CURRENT runner dispatches, replayed without subprocesses.

    Mirrors the `Wave-parallel dispatch` block of `workflow_runner._execute_run`:
    submit every id `get_ready()` hands out, let a FIFO pool of `max_parallel`
    workers run them, then retire in SUBMISSION order rather than completion
    order. At one slot the pool serializes submissions, so submission order is
    execution order and no thread scheduling can enter the result — which is why
    this replay is deterministic and needs no pool of its own.
    """
    sorter = TopologicalSorter(_dag_graph(steps))
    sorter.prepare()
    step_ids = {step["id"] for step in steps}
    executed: list[str] = []
    in_flight: list[str] = []       # submission-ordered, mirrors `futures`

    while sorter.is_active():
        ready = list(sorter.get_ready())
        for sid in ready:
            if sid not in step_ids:
                # Dangling depends_on target: retired without executing, exactly
                # as the runner does, so the walk can still finish.
                sorter.done(sid)
                continue
            in_flight.append(sid)
        if not in_flight:
            if not ready:
                break
            continue
        # `wait(FIRST_COMPLETED)` on a pool of `max_parallel` workers can only
        # complete something already started, and the pool starts in FIFO order.
        for sid in in_flight[:max_parallel]:
            executed.append(sid)
            sorter.done(sid)
        del in_flight[:max_parallel]

    return executed


def check_template(path: Path) -> dict:
    """Compare baseline and current dispatch order for one template file."""
    record: dict = {
        "template": str(path.relative_to(_REPO_ROOT)).replace("\\", "/"),
        "declares_max_parallel": False,
        "conditional_steps": 0,
        "steps": 0,
        "identical": None,
        "error": None,
    }
    try:
        # newline="" so a CRLF checkout does not change what YAML sees, and the
        # comparison is the same on Windows and Linux.
        with path.open("r", encoding="utf-8", newline="") as handle:
            data = yaml.safe_load(handle) or {}
    except (yaml.YAMLError, OSError) as exc:
        record["error"] = f"unreadable: {exc}"
        return record

    if not isinstance(data, dict):
        record["error"] = "template is not a mapping"
        return record

    steps = data.get("steps") or []
    if not isinstance(steps, list) or not steps:
        record["error"] = "no steps"
        return record

    record["steps"] = len(steps)
    record["declares_max_parallel"] = "max_parallel" in data
    record["conditional_steps"] = sum(
        1 for s in steps if isinstance(s, dict) and s.get("when")
    )

    try:
        before = baseline_order(steps)
        after = dispatch_order(steps, max_parallel=1)
    except CycleError as exc:
        # A cyclic template failed in BOTH generations — same failure, still
        # backward compatible. Recorded rather than counted as a divergence.
        record["error"] = f"cycle: {exc.args[0]}"
        return record
    except KeyError as exc:
        record["error"] = f"malformed step, missing {exc}"
        return record

    record["identical"] = before == after
    if not record["identical"]:
        record["baseline_order"] = before
        record["dispatch_order"] = after
    return record


def run_check() -> dict:
    """Check every template under both roots."""
    results = []
    for directory in TEMPLATE_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            results.append(check_template(path))

    compared = [r for r in results if r["identical"] is not None]
    diverged = [r for r in compared if not r["identical"]]
    return {
        "template_roots": [
            str(d.relative_to(_REPO_ROOT)).replace("\\", "/") for d in TEMPLATE_DIRS
        ],
        "templates_found": len(results),
        "templates_compared": len(compared),
        "templates_skipped": [
            {"template": r["template"], "reason": r["error"]}
            for r in results
            if r["identical"] is None
        ],
        "declaring_max_parallel": [
            r["template"] for r in results if r["declares_max_parallel"]
        ],
        "with_conditional_steps": [
            r["template"] for r in results if r["conditional_steps"]
        ],
        "diverged": diverged,
        "identical": len(diverged) == 0,
        "results": results,
    }


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument(
        "--gate", action="store_true", help="exit 1 if any template's order diverged"
    )
    args = parser.parse_args(argv)

    report = run_check()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"templates found:    {report['templates_found']}")
        print(f"templates compared: {report['templates_compared']}")
        for skipped in report["templates_skipped"]:
            print(f"  skipped {skipped['template']}: {skipped['reason']}")
        print(f"declaring max_parallel: {len(report['declaring_max_parallel'])}")
        for name in report["declaring_max_parallel"]:
            print(f"  {name}")
        print(f"with conditional steps: {len(report['with_conditional_steps'])}")
        for name in report["with_conditional_steps"]:
            print(f"  {name}")
        if report["diverged"]:
            print(f"DIVERGED: {len(report['diverged'])}")
            for rec in report["diverged"]:
                print(f"  {rec['template']}")
                print(f"    before: {rec['baseline_order']}")
                print(f"    after:  {rec['dispatch_order']}")
        else:
            print("PASS — every compared template dispatches in baseline order")

    if args.gate and report["diverged"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
