#!/usr/bin/env python3
"""Scripted multi-branch git rehearsal for the CI test allowlist (kax-conflict-07).

MEASURES, rather than asserts, whether two (or three, or five) branches that each
add a test file can merge without a human. Each scenario builds a throwaway git
repository, applies a layout, cuts N branches off the same base, performs the
registration each task actually performs, merges them back, and records whether
git needed a hand-resolve and whether every entry survived.

Layouts
-------
inline          The shape before this task: the allowlist is a shell
                line-continuation chain inside .github/workflows/icdev-ci.yml.
                A registration appends `  tests/<x>.py \\` at the same offset.
                This is the CONTROL — it is expected to CONFLICT, and a run
                where it does not means the rehearsal is not reproducing the
                problem it was written for.
external        The list moved to args/ci_test_files/core.txt, plain text, no
                merge driver. Better than inline (the workflow is untouched) but
                the appends still land at the same end-of-file offset.
external-union  `external` plus `.gitattributes` marking the list `merge=union`.
                The shipped shape.

Modes
-----
worktree    `git merge` with a checked-out index — what a developer or agent runs.
merge-tree  `git merge-tree --write-tree` — the bare, index-free plumbing a forge
            runs server-side for its mergeability probe and merge button. A
            layout clean only in worktree mode still needs a local rebase to
            unblock a PR, so both are reported.

Usage
-----
    python tools/git/ci_test_list_merge_rehearsal.py
    python tools/git/ci_test_list_merge_rehearsal.py --branches 5 --json
    python tools/git/ci_test_list_merge_rehearsal.py --repo . --gate
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

LAYOUTS = ("inline", "external", "external-union")
MODES = ("worktree", "merge-tree")

LIST_FILE = "args/ci_test_files/core.txt"
WORKFLOW = ".github/workflows/icdev-ci.yml"

BACKSLASH = chr(92)

#: Enough seed entries that the appends land in one hunk — which is what makes
#: end-of-list appends collide in the first place.
SEED_TESTS = [
    "tests/test_circuit_breaker.py",
    "tests/test_retry.py",
    "tests/test_correlation.py",
    "tests/test_errors.py",
    "tests/test_schemas.py",
]

_WORKFLOW_HEAD = """name: ICDEV CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    name: Test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run core unit tests
        run: |
"""


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
            f"git {' '.join(args)} failed ({proc.returncode}):{chr(10)}{proc.stdout}{chr(10)}{proc.stderr}"
        )
    return proc


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "main")
    # CI runners have no ambient git identity; set it locally so the rehearsal is
    # self-contained rather than inheriting the developer's config.
    _git(repo, "config", "user.email", "rehearsal@icdev.local")
    _git(repo, "config", "user.name", "CI List Rehearsal")
    _git(repo, "config", "commit.gpgsign", "false")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" keeps LF endings on Windows; the repo is `* text eol=lf`.
    path.write_text(text, encoding="utf-8", newline="")


def _workflow_text(tests: List[str]) -> str:
    body = "          pytest " + (" " + BACKSLASH + "\n            ").join(tests)
    return _WORKFLOW_HEAD + body + " " + BACKSLASH + "\n            -v --tb=short -x\n"


def _seed(repo: Path, layout: str) -> None:
    if layout == "inline":
        _write(repo / WORKFLOW, _workflow_text(SEED_TESTS))
    else:
        _write(repo / WORKFLOW, _workflow_text(["--from-file"]))
        _write(
            repo / LIST_FILE,
            "# CI test allowlist — core\n\n" + "".join(f"{t}\n" for t in SEED_TESTS),
        )
    if layout == "external-union":
        _write(
            repo / ".gitattributes",
            "* text=auto eol=lf\n"
            "*.txt text eol=lf\n"
            "args/ci_test_files/*.txt merge=union\n",
        )
    for t in SEED_TESTS:
        _write(repo / t, "def test_seed():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")


def _register(repo: Path, layout: str, test: str) -> None:
    """The edit a task makes when it adds a gated test file."""
    _write(repo / test, "def test_new():\n    assert True\n")
    if layout == "inline":
        path = repo / WORKFLOW
        text = path.read_text(encoding="utf-8")
        marker = "            -v --tb=short -x\n"
        assert marker in text, "workflow shape changed"
        text = text.replace(marker, f"            {test} {BACKSLASH}\n" + marker)
        _write(path, text)
        return
    path = repo / LIST_FILE
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    _write(path, text + f"{test}\n")


def _resolved(repo: Path, layout: str) -> str:
    """The text a reader greps for an entry — layout-agnostic on purpose."""
    target = repo / (WORKFLOW if layout == "inline" else LIST_FILE)
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8", errors="replace")


def _merge_worktree(repo: Path, result: ScenarioResult, branches: List[str], base: str) -> None:
    _git(repo, "checkout", "-q", base)
    for name in branches:
        proc = _git(repo, "merge", "--no-edit", f"feat/{name}", check=False)
        if proc.returncode == 0:
            continue
        result.conflicted = True
        status = _git(repo, "diff", "--name-only", "--diff-filter=U")
        paths = [p for p in status.stdout.splitlines() if p.strip()]
        result.conflicted_paths.extend(paths)
        result.notes.append(
            f"merge of feat/{name} required a human: {', '.join(paths) or 'unknown path'}"
        )
        _git(repo, "merge", "--abort", check=False)
        return


def _merge_tree(repo: Path, result: ScenarioResult, branches: List[str], base: str) -> None:
    _git(repo, "checkout", "-q", base)
    head = base
    for name in branches:
        proc = _git(
            repo, "merge-tree", "--write-tree", "--messages", head, f"feat/{name}",
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
            first = next((ln for ln in body.splitlines() if ln.strip()), "")
            result.notes.append(
                f"server-side merge of feat/{name} conflicted: "
                f"{', '.join(paths) or first or 'no detail from git'}"
            )
            return
        tree = proc.stdout.splitlines()[0].strip()
        commit = _git(
            repo, "commit-tree", tree, "-p", head, "-p", f"feat/{name}",
            "-m", f"merge {name}",
        ).stdout.strip()
        head = commit
    _git(repo, "reset", "-q", "--hard", head)


def run_scenario(
    layout: str, branches: int = 2, mode: str = "worktree", keep: bool = False
) -> ScenarioResult:
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; expected one of {LAYOUTS}")
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")

    workdir = Path(tempfile.mkdtemp(prefix=f"ci-list-rehearsal-{layout}-"))
    repo = workdir / "repo"
    repo.mkdir()
    result = ScenarioResult(layout=layout, branches=branches, mode=mode, conflicted=False)
    try:
        _init_repo(repo)
        _seed(repo, layout)

        new = [f"tests/test_added_{chr(ord('a') + i)}.py" for i in range(branches)]
        names = [f"add_{chr(ord('a') + i)}" for i in range(branches)]
        for name, test in zip(names, new):
            _git(repo, "checkout", "-q", "-b", f"feat/{name}", "main")
            _register(repo, layout, test)
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", f"add {test}")

        if mode == "merge-tree":
            _merge_tree(repo, result, names, "main")
        else:
            _merge_worktree(repo, result, names, "main")

        if not result.conflicted:
            text = _resolved(repo, layout)
            missing = [t for t in new if t not in text]
            result.all_entries_present = not missing
            if missing:
                result.notes.append(f"entries lost in merge: {', '.join(missing)}")
            if "<<<<<<<" in text:
                result.conflicted = True
                result.notes.append("conflict markers left in the merged file")
            for t in new:
                if text.count(t) > 1:
                    result.duplicate_entries.append(t)
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
    source: Path, branches: int = 2, mode: str = "worktree", keep: bool = False
) -> ScenarioResult:
    """Rehearse against a CLONE of a real repository and the SHIPPED list file.

    The synthetic scenarios prove the property for a hand-built file. This proves
    it for the shipped `.gitattributes`, the real `args/ci_test_files/core.txt`
    and its real length. The clone is disposable; `source` is never written to.
    """
    workdir = Path(tempfile.mkdtemp(prefix="ci-list-rehearsal-real-"))
    repo = workdir / "repo"
    result = ScenarioResult(
        layout=f"repo:{LIST_FILE}", branches=branches, mode=mode, conflicted=False
    )
    try:
        # Shallow + single-branch: the rehearsal only branches off HEAD, and a
        # full clone of this repo takes minutes — a proof nobody waits for is a
        # proof nobody runs.
        proc = subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", "--depth", "1",
             "--single-branch", "--no-tags", str(source), str(repo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            raise RehearsalError(f"clone failed: {proc.stderr}")
        _git(repo, "config", "user.email", "rehearsal@icdev.local")
        _git(repo, "config", "user.name", "CI List Rehearsal")
        _git(repo, "config", "commit.gpgsign", "false")

        base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        attr = _git(repo, "check-attr", "merge", "--", LIST_FILE).stdout.strip()
        result.notes.append(f"git check-attr -> {attr}")

        target = repo / LIST_FILE
        if not target.exists():
            raise RehearsalError(f"{LIST_FILE} not present in the clone")

        new = [f"tests/test_rehearsal_{chr(ord('a') + i)}.py" for i in range(branches)]
        names = [f"add_{chr(ord('a') + i)}" for i in range(branches)]
        for name, test in zip(names, new):
            _git(repo, "checkout", "-q", "-b", f"feat/{name}", base)
            text = target.read_text(encoding="utf-8")
            if not text.endswith("\n"):
                text += "\n"
            _write(target, text + f"{test}\n")
            _write(repo / test, "def test_x():\n    assert True\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", f"add {test}")

        if mode == "merge-tree":
            _merge_tree(repo, result, names, base)
        else:
            _merge_worktree(repo, result, names, base)

        if not result.conflicted:
            merged = target.read_text(encoding="utf-8", errors="replace")
            missing = [t for t in new if t not in merged]
            result.all_entries_present = not missing
            if missing:
                result.notes.append(f"entries lost in merge: {', '.join(missing)}")
            if "<<<<<<<" in merged:
                result.conflicted = True
                result.notes.append("conflict markers left in the merged file")
            for t in new:
                if merged.count(t) > 1:
                    result.duplicate_entries.append(t)
        return result
    finally:
        if keep:
            result.notes.append(f"clone kept at {repo}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _render(report: Dict[str, object]) -> str:
    rows = report["scenarios"]  # type: ignore[index]
    width = max([16, *(len(r["layout"]) for r in rows)])
    lines = [
        f"CI test-list merge rehearsal — {report['branches']} branches each adding a test file",
        "",
        f"{'layout':<{width}} {'mode':<12} {'conflict':<10} {'entries kept':<14} notes",
        f"{'-' * width} {'-' * 12} {'-' * 10} {'-' * 14} {'-' * 40}",
    ]
    for row in rows:
        lines.append(
            f"{row['layout']:<{width}} {row['mode']:<12} "
            f"{('CONFLICT' if row['conflicted'] else 'clean'):<10} "
            f"{('yes' if row['all_entries_present'] else '-'):<14} "
            f"{row['notes'][0] if row['notes'] else ''}"
        )
    lines.append("")
    lines.append("conflict-free: " + (", ".join(report["conflict_free_layouts"]) or "none"))  # type: ignore[index]
    return "\n".join(lines)


def _summarize(scenarios: List[dict], branches: int, mode: str) -> Dict[str, object]:
    clean = sorted(
        {r["layout"] for r in scenarios if not r["conflicted"] and r["all_entries_present"]}
        - {r["layout"] for r in scenarios if r["conflicted"] or not r["all_entries_present"]}
    )
    return {"branches": branches, "mode": mode, "scenarios": scenarios,
            "conflict_free_layouts": clean}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--layout", choices=LAYOUTS, help="run a single layout")
    parser.add_argument("--mode", choices=(*MODES, "both"), default="both")
    parser.add_argument("--branches", type=int, default=2, help="concurrent branches (default 2)")
    parser.add_argument("--repo", type=Path,
                        help="clone this real repository and rehearse the SHIPPED list")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway repo")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--gate", action="store_true",
        help="exit 1 unless external-union (or --repo) is clean AND, for the "
             "synthetic run, the inline control still reproduces the conflict",
    )
    args = parser.parse_args(argv)

    modes = MODES if args.mode == "both" else (args.mode,)
    if args.repo:
        scenarios = [
            asdict(run_real_repo(args.repo, args.branches, mode=m, keep=args.keep))
            for m in modes
        ]
    else:
        layouts = (args.layout,) if args.layout else LAYOUTS
        scenarios = [
            asdict(run_scenario(layout, args.branches, mode=m, keep=args.keep))
            for m in modes
            for layout in layouts
        ]
    report = _summarize(scenarios, args.branches, args.mode)

    print(json.dumps(report, indent=2) if args.json else _render(report))

    if not args.gate:
        return 0

    shipped = [
        r for r in scenarios
        if r["layout"] == "external-union" or r["layout"].startswith("repo:")
    ]
    if not shipped:
        print("::error::--gate ran no shipped-layout scenario", file=sys.stderr)
        return 1
    bad = [r for r in shipped if r["conflicted"] or not r["all_entries_present"]]
    for r in bad:
        print(f"::error::{r['layout']} ({r['mode']}): {'; '.join(r['notes']) or 'failed'}",
              file=sys.stderr)
    # The control must still fail. A rehearsal where the OLD layout merges
    # cleanly is not measuring the problem, and would report success for the
    # wrong reason.
    control = [r for r in scenarios if r["layout"] == "inline"]
    for r in control:
        if not r["conflicted"]:
            print(
                "::error::the `inline` control merged cleanly — the rehearsal is no "
                "longer reproducing the conflict it exists to measure",
                file=sys.stderr,
            )
            bad.append(r)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
