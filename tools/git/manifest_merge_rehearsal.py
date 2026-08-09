#!/usr/bin/env python3
"""Scripted two-branch git rehearsal for `tools/manifest/` layouts.

Measures, rather than asserts, which manifest layout survives the edit pattern
actually observed on this repo: two unrelated tasks each registering a NEW tool
under the SAME topic, on branches cut from the same base, merged one after the
other into the default branch.

Each scenario builds a throwaway git repository, applies the layout, cuts two
(or three) branches, performs the registrations, merges them back, and records
whether git needed a human.

Layouts
-------
table-row      Current shape. One markdown table per topic; a registration
               appends a row at end-of-table.
heading-block  One file per topic; a registration appends a `###` block at
               end-of-file. Same offset as table-row, so same hazard.
union          table-row plus `.gitattributes` marking the shards
               `merge=union`, so conflicting hunks resolve to the superset.
per-tool-file  One file per tool (`tools/manifest/<topic>/<tool>.md`);
               a registration creates a new file.

Usage
-----
    python tools/git/manifest_merge_rehearsal.py --json
    python tools/git/manifest_merge_rehearsal.py --layout union
    python tools/git/manifest_merge_rehearsal.py --layout per-tool-file --branches 3
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

LAYOUTS = ("table-row", "heading-block", "union", "per-tool-file")
MODES = ("worktree", "merge-tree")

TOPIC = "kanban"

# A shard body large enough that the appended entries land in the same hunk,
# which is what makes end-of-file appends collide.
_SHARD_HEADER = """# Kanban System

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Kanban Tools

"""

_TABLE_HEAD = (
    "| Tool | File | Description | Input | Output |\n"
    "|------|------|-------------|-------|--------|\n"
)


class RehearsalError(RuntimeError):
    """A git command failed in a way the rehearsal cannot interpret."""


@dataclass
class ScenarioResult:
    layout: str
    branches: int
    mode: str
    conflicted: bool
    conflicted_paths: List[str] = field(default_factory=list)
    all_entries_present: bool = False
    duplicate_entries: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise RehearsalError(
            f"git {' '.join(args)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "main")
    # CI runners have no ambient git identity; set it locally so the rehearsal
    # is self-contained rather than inheriting the developer's config.
    _git(repo, "config", "user.email", "rehearsal@icdev.local")
    _git(repo, "config", "user.name", "Manifest Rehearsal")
    _git(repo, "config", "commit.gpgsign", "false")


def _shard_path(repo: Path) -> Path:
    return repo / "tools" / "manifest" / f"{TOPIC}.md"


def _fragment_path(repo: Path, tool: str) -> Path:
    return repo / "tools" / "manifest" / TOPIC / f"{tool}.md"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" keeps LF endings on Windows; the repo is `* text eol=lf`.
    path.write_text(text, encoding="utf-8", newline="")


def _seed(repo: Path, layout: str, seed_tools: List[str]) -> None:
    """Lay down the baseline manifest for `layout` and commit it."""
    if layout == "per-tool-file":
        for name in seed_tools:
            _write(
                _fragment_path(repo, name),
                f"## {name}\n\n"
                f"- **File:** `tools/kanban/{name}.py`\n"
                f"- **Description:** Existing {name} tool.\n"
                f"- **Input:** `--json`\n"
                f"- **Output:** JSON\n",
            )
        _write(
            repo / "tools" / "manifest" / TOPIC / "README.md",
            "# Kanban System\n\n"
            "> Shard of `tools/manifest.md`. One file per tool in this directory.\n",
        )
    elif layout == "heading-block":
        body = _SHARD_HEADER + "".join(
            f"### {name}\n\n"
            f"- **File:** `tools/kanban/{name}.py`\n"
            f"- **Description:** Existing {name} tool.\n\n"
            for name in seed_tools
        )
        _write(_shard_path(repo), body)
    else:  # table-row, union
        body = _SHARD_HEADER + _TABLE_HEAD + "".join(
            f"| {name} | tools/kanban/{name}.py | Existing {name} tool. | --json | JSON |\n"
            for name in seed_tools
        )
        _write(_shard_path(repo), body)

    if layout == "union":
        _write(
            repo / ".gitattributes",
            "* text=auto eol=lf\n"
            "*.md text eol=lf\n"
            "tools/manifest.md merge=union\n"
            "tools/manifest/*.md merge=union\n",
        )

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed manifest")


def _register(repo: Path, layout: str, tool: str) -> None:
    """Apply the edit a task makes when it registers a new tool."""
    if layout == "per-tool-file":
        _write(
            _fragment_path(repo, tool),
            f"## {tool}\n\n"
            f"- **File:** `tools/kanban/{tool}.py`\n"
            f"- **Description:** New {tool} tool.\n"
            f"- **Input:** `--json`\n"
            f"- **Output:** JSON\n",
        )
        return

    path = _shard_path(repo)
    text = path.read_text(encoding="utf-8")
    if layout == "heading-block":
        text += (
            f"### {tool}\n\n"
            f"- **File:** `tools/kanban/{tool}.py`\n"
            f"- **Description:** New {tool} tool.\n\n"
        )
    else:
        text += (
            f"| {tool} | tools/kanban/{tool}.py | New {tool} tool. | --json | JSON |\n"
        )
    _write(path, text)


def _assembled_text(repo: Path, layout: str) -> str:
    root = repo / "tools" / "manifest"
    if not root.exists():
        return ""
    parts = []
    for md in sorted(root.rglob("*.md")):
        parts.append(md.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _merge_worktree(
    repo: Path, result: ScenarioResult, new_tools: List[str], base: str = "main"
) -> None:
    """Merge each branch with a checked-out worktree — what a developer/agent runs."""
    _git(repo, "checkout", "-q", base)
    for tool in new_tools:
        proc = _git(repo, "merge", "--no-edit", f"feat/{tool}", check=False)
        if proc.returncode == 0:
            continue
        result.conflicted = True
        status = _git(repo, "diff", "--name-only", "--diff-filter=U")
        paths = [p for p in status.stdout.splitlines() if p.strip()]
        result.conflicted_paths.extend(paths)
        result.notes.append(
            f"merge of feat/{tool} required a human: {', '.join(paths) or 'unknown path'}"
        )
        _git(repo, "merge", "--abort", check=False)
        return


def _merge_tree(
    repo: Path, result: ScenarioResult, new_tools: List[str], base: str = "main"
) -> None:
    """Merge via `git merge-tree --write-tree` — the bare, index-free plumbing a
    forge (GitHub's mergeability probe, its merge button) runs server-side.

    A layout that is clean here needs no local `git merge main` to unblock a PR;
    a layout that is clean only in the worktree mode does.
    """
    _git(repo, "checkout", "-q", base)
    head = base
    for tool in new_tools:
        proc = _git(
            repo, "merge-tree", "--write-tree", "--messages", head, f"feat/{tool}",
            check=False,
        )
        if proc.returncode != 0:
            result.conflicted = True
            body = (proc.stdout or "") + (proc.stderr or "")
            paths = sorted({
                line.split()[-1]
                for line in body.splitlines()
                if "CONFLICT" in line and line.split()
            })
            result.conflicted_paths.extend(paths)
            result.notes.append(
                f"server-side merge of feat/{tool} conflicted: "
                f"{', '.join(paths) or body.strip().splitlines()[:1]}"
            )
            return
        tree = proc.stdout.splitlines()[0].strip()
        commit = _git(
            repo, "commit-tree", tree, "-p", head, "-p", f"feat/{tool}",
            "-m", f"merge {tool}",
        ).stdout.strip()
        head = commit
    # Land the synthesised merge so the assembled-content assertions can read it.
    _git(repo, "reset", "-q", "--hard", head)


def run_scenario(
    layout: str, branches: int = 2, keep: bool = False, mode: str = "worktree"
) -> ScenarioResult:
    """Cut `branches` branches off main, register one new tool on each, merge back."""
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; expected one of {LAYOUTS}")
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")

    workdir = Path(tempfile.mkdtemp(prefix=f"manifest-rehearsal-{layout}-"))
    repo = workdir / "repo"
    repo.mkdir()
    result = ScenarioResult(layout=layout, branches=branches, mode=mode, conflicted=False)

    try:
        _init_repo(repo)
        _seed(repo, layout, ["task_factory", "cli", "scheduler"])

        new_tools = [f"new_tool_{chr(ord('a') + i)}" for i in range(branches)]

        for tool in new_tools:
            _git(repo, "checkout", "-q", "-b", f"feat/{tool}", "main")
            _register(repo, layout, tool)
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", f"register {tool}")

        if mode == "merge-tree":
            _merge_tree(repo, result, new_tools)
        else:
            _merge_worktree(repo, result, new_tools)

        if not result.conflicted:
            text = _assembled_text(repo, layout)
            missing = [t for t in new_tools if t not in text]
            result.all_entries_present = not missing
            if missing:
                result.notes.append(f"entries lost in merge: {', '.join(missing)}")
            for tool in new_tools:
                if text.count(f"New {tool} tool.") > 1:
                    result.duplicate_entries.append(tool)
            if result.duplicate_entries:
                result.notes.append(
                    "duplicate entries produced: " + ", ".join(result.duplicate_entries)
                )
        return result
    finally:
        if keep:
            result.notes.append(f"repo kept at {repo}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def run_real_repo(
    source: Path, shard: str = "tools/manifest/kanban.md", branches: int = 2,
    mode: str = "worktree", keep: bool = False,
) -> ScenarioResult:
    """Run the rehearsal against a CLONE of a real repository and a real shard.

    The synthetic scenarios prove the property for a hand-built file. This
    proves it for the shipped `.gitattributes` and an actual manifest shard —
    long rows, escaped pipes, several tables and all. The clone is disposable;
    the source repository is never written to.
    """
    workdir = Path(tempfile.mkdtemp(prefix="manifest-rehearsal-real-"))
    repo = workdir / "repo"
    result = ScenarioResult(
        layout="repo:" + shard, branches=branches, mode=mode, conflicted=False
    )
    try:
        # Shallow + single-branch: the rehearsal only ever branches off HEAD, and
        # a full clone of this repo takes minutes — a proof nobody waits for is a
        # proof nobody runs.
        proc = subprocess.run(
            [
                "git", "clone", "--quiet", "--no-hardlinks", "--depth", "1",
                "--single-branch", "--no-tags", str(source), str(repo),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            raise RehearsalError(f"clone failed: {proc.stderr}")
        _git(repo, "config", "user.email", "rehearsal@icdev.local")
        _git(repo, "config", "user.name", "Manifest Rehearsal")
        _git(repo, "config", "commit.gpgsign", "false")

        base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        attr = _git(repo, "check-attr", "merge", "--", shard).stdout.strip()
        result.notes.append(f"git check-attr -> {attr}")

        target = repo / shard
        if not target.exists():
            raise RehearsalError(f"{shard} not present in the clone")

        new_tools = [f"rehearsal_tool_{chr(ord('a') + i)}" for i in range(branches)]
        for tool in new_tools:
            _git(repo, "checkout", "-q", "-b", f"feat/{tool}", base)
            text = target.read_text(encoding="utf-8")
            if not text.endswith("\n"):
                text += "\n"
            text += (
                f"| {tool} | tools/kanban/{tool}.py | Registered by an unrelated "
                f"task ({tool}). | --json | JSON |\n"
            )
            _write(target, text)
            _git(repo, "commit", "-qam", f"register {tool}")

        _git(repo, "checkout", "-q", base)
        if mode == "merge-tree":
            _merge_tree(repo, result, new_tools, base=base)
        else:
            _merge_worktree(repo, result, new_tools, base=base)

        if not result.conflicted:
            merged = target.read_text(encoding="utf-8", errors="replace")
            missing = [t for t in new_tools if t not in merged]
            result.all_entries_present = not missing
            if missing:
                result.notes.append(f"entries lost in merge: {', '.join(missing)}")
            if "<<<<<<<" in merged:
                result.conflicted = True
                result.notes.append("conflict markers left in the merged file")
            for tool in new_tools:
                if merged.count(f"({tool}).") > 1:
                    result.duplicate_entries.append(tool)
        return result
    finally:
        if keep:
            result.notes.append(f"clone kept at {repo}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def run_all(branches: int = 2, mode: str = "worktree") -> Dict[str, object]:
    modes = MODES if mode == "both" else (mode,)
    results = [
        asdict(run_scenario(layout, branches=branches, mode=m))
        for m in modes
        for layout in LAYOUTS
    ]
    clean = sorted({
        r["layout"] for r in results if not r["conflicted"] and r["all_entries_present"]
    } - {
        r["layout"] for r in results if r["conflicted"] or not r["all_entries_present"]
    })
    return {
        "branches": branches,
        "mode": mode,
        "scenarios": results,
        "conflict_free_layouts": clean,
    }


def _render(report: Dict[str, object]) -> str:
    width = max([16, *(len(r["layout"]) for r in report["scenarios"])])  # type: ignore[index]
    lines = [
        f"Manifest merge rehearsal — {report['branches']} branches each registering a new tool",
        "",
        f"{'layout':<{width}} {'mode':<12} {'conflict':<10} {'entries kept':<14} notes",
        f"{'-' * width} {'-' * 12} {'-' * 10} {'-' * 14} {'-' * 40}",
    ]
    for row in report["scenarios"]:  # type: ignore[index]
        conflict = "CONFLICT" if row["conflicted"] else "clean"
        kept = "yes" if row["all_entries_present"] else "-"
        note = row["notes"][0] if row["notes"] else ""
        lines.append(
            f"{row['layout']:<{width}} {row['mode']:<12} {conflict:<10} {kept:<14} {note}"
        )
    lines.append("")
    lines.append("conflict-free: " + (", ".join(report["conflict_free_layouts"]) or "none"))  # type: ignore[index]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--layout", choices=LAYOUTS, help="run a single layout")
    parser.add_argument(
        "--mode", choices=(*MODES, "both"), default="both",
        help="worktree (local git merge) / merge-tree (bare, forge-style) / both",
    )
    parser.add_argument("--branches", type=int, default=2, help="concurrent branches (default 2)")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway repo")
    parser.add_argument(
        "--repo", type=Path,
        help="clone this real repository and rehearse against a real shard",
    )
    parser.add_argument(
        "--shard", default="tools/manifest/kanban.md",
        help="shard to append to under --repo (default: the hottest one)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.repo:
        modes = MODES if args.mode == "both" else (args.mode,)
        report = {
            "branches": args.branches,
            "mode": args.mode,
            "scenarios": [
                asdict(run_real_repo(
                    args.repo, args.shard, args.branches, mode=m, keep=args.keep,
                ))
                for m in modes
            ],
        }
        report["conflict_free_layouts"] = sorted(
            {
                r["layout"] for r in report["scenarios"]  # type: ignore[index]
                if not r["conflicted"] and r["all_entries_present"]
            }
            - {
                r["layout"] for r in report["scenarios"]  # type: ignore[index]
                if r["conflicted"] or not r["all_entries_present"]
            }
        )
    elif args.layout:
        modes = MODES if args.mode == "both" else (args.mode,)
        report = {
            "branches": args.branches,
            "mode": args.mode,
            "scenarios": [
                asdict(run_scenario(args.layout, args.branches, args.keep, mode=m))
                for m in modes
            ],
        }
        report["conflict_free_layouts"] = sorted(
            {
                r["layout"] for r in report["scenarios"]  # type: ignore[index]
                if not r["conflicted"] and r["all_entries_present"]
            }
            - {
                r["layout"] for r in report["scenarios"]  # type: ignore[index]
                if r["conflicted"] or not r["all_entries_present"]
            }
        )
    else:
        report = run_all(branches=args.branches, mode=args.mode)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render(report))

    failed = [
        r for r in report["scenarios"]  # type: ignore[index]
        if r["conflicted"] or not r["all_entries_present"]
    ]
    return 1 if args.layout and failed else 0


if __name__ == "__main__":
    sys.exit(main())
