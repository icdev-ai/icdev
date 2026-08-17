#!/usr/bin/env python3
"""Rehearse `merge=union` on docs/reference/commands.md before trusting it.

Third in the series after ``manifest_merge_rehearsal.py`` (kax-conflict-03) and
``ci_test_list_merge_rehearsal.py`` (kax-conflict-07), and it exists for the
same reason: union is only safe on files whose lines are INDEPENDENT, and
"looks append-only" is an assumption until something measures it.

Why this file. ``docs/reference/commands.md`` is the largest unprotected
collision surface in the repo — it is touched by 18 of the last 40 branches,
against 27 for ``args/ci_test_files/core.txt`` (already union) and 6 for
``tools/manifest/kanban.md`` (already union, and it did NOT conflict). Two of
six conflicts resolved by hand on 2026-08-16 were this file, and both
resolutions were "keep both blocks".

Why it is not obviously safe. Unlike a manifest shard or an allowlist, this
file is BLOCK-structured: a fenced ```bash region holding a comment paragraph
and several command lines. Union merges line by line and knows nothing about
fences, so the question worth measuring is whether the real edit pattern
survives it and whether the fences stay balanced.

What was measured (2026-08-16, 60 most recent merges on origin/main):

    branches whose own change touched the file .... 14
    pure additions (union merges correctly) ....... 14
    also removed or edited lines .................. 0

Every branch appends. Nothing edits an existing line. That is the same shape
the two earlier rehearsals found, arrived at independently.

Scenarios below, each run against the REAL file at 2/3/5 concurrent branches in
BOTH the local ``git merge`` path and the bare ``git merge-tree --write-tree``
plumbing a forge runs server-side:

    append_tail    every branch appends its own block at end of file — the
                   observed pattern
    append_section every branch inserts a block at the same interior offset
    edit_same_line the case union is NOT safe for, included so that a clean
                   pass everywhere would be a reason to distrust the harness
                   rather than to ship

``edit_same_line`` is EXPECTED to duplicate the line rather than conflict. That
is union's documented cost, it is visible in the diff, and it is not observed
in 14 of 14 real branches — but it is reported rather than hidden, because a
rehearsal that only prints the reassuring cases is not evidence.

Usage:
    python tools/git/commands_doc_merge_rehearsal.py
    python tools/git/commands_doc_merge_rehearsal.py --branches 5 --json
    python tools/git/commands_doc_merge_rehearsal.py --scenario append_tail
    python tools/git/commands_doc_merge_rehearsal.py --gate   # exit 1 if the
                                                              # observed pattern
                                                              # is not clean
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404 - git plumbing, fixed argv, no shell
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: The file under rehearsal, and its packaged mirror.
TARGET = "docs/reference/commands.md"

SCENARIOS = ("append_tail", "append_section", "edit_same_line")
MODES = ("worktree", "merge-tree")

#: Scenarios whose result the --gate flag actually enforces. `edit_same_line`
#: is diagnostic: union duplicating a line there is the KNOWN cost, not a
#: regression, so gating on it would make the gate permanently red.
GATED_SCENARIOS = ("append_tail", "append_section")


class RehearsalError(RuntimeError):
    """Raised when the throwaway repo cannot be prepared."""


@dataclass
class ScenarioResult:
    scenario: str
    mode: str
    branches: int
    conflicted: bool = False
    lost_blocks: List[str] = field(default_factory=list)
    duplicated: List[str] = field(default_factory=list)
    fences_balanced: bool = True
    markers_left: bool = False
    detail: str = ""

    @property
    def clean(self) -> bool:
        return not (
            self.conflicted
            or self.lost_blocks
            or self.duplicated
            or self.markers_left
            or not self.fences_balanced
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "branches": self.branches,
            "clean": self.clean,
            "conflicted": self.conflicted,
            "lost_blocks": self.lost_blocks,
            "duplicated": self.duplicated,
            "fences_balanced": self.fences_balanced,
            "markers_left": self.markers_left,
            "detail": self.detail,
        }


def _git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and proc.returncode != 0:
        raise RehearsalError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()}")
    return proc


def _init_repo(repo: Path, source: Path, union: bool = True) -> None:
    """Seed a throwaway repo holding the REAL target file plus the union rule.

    ``union=False`` is the CONTROL: it seeds the same repo without the rule, so
    the harness can show it would report a conflict if union were absent. A
    rehearsal that cannot fail is not evidence that anything passed.
    """
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "rehearsal@icdev.local")
    _git(repo, "config", "user.name", "Merge Rehearsal")
    _git(repo, "config", "commit.gpgsign", "false")

    dest = repo / TARGET
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        raise RehearsalError(f"target not found: {source}")
    shutil.copy(source, dest)

    # The rule under test. Rehearsing without it would measure nothing.
    if union:
        (repo / ".gitattributes").write_text(f"{TARGET} merge=union\n", encoding="utf-8")
    _git(repo, "add", "-A", check=True)
    _git(repo, "commit", "-qm", "base", check=True)


def _block(tag: str) -> str:
    """A realistic addition: a comment paragraph plus two command lines."""
    return (
        f"\n# {tag} — commands added by branch {tag}\n"
        f"# second line of the {tag} rationale\n"
        f"python tools/{tag}/thing.py --run\n"
        f"python tools/{tag}/thing.py --json\n"
    )


def _make_branches(repo: Path, scenario: str, branches: int) -> List[str]:
    base = _git(repo, "rev-parse", "HEAD", check=True).stdout.strip()
    original = (repo / TARGET).read_text(encoding="utf-8")
    lines = original.split("\n")
    mid = len(lines) // 2
    names: List[str] = []

    for i in range(branches):
        tag = f"b{i}"
        names.append(tag)
        _git(repo, "checkout", "-q", "-b", tag, base, check=True)
        target = repo / TARGET
        if scenario == "append_tail":
            target.write_text(original + _block(tag), encoding="utf-8")
        elif scenario == "append_section":
            target.write_text(
                "\n".join(lines[:mid]) + _block(tag) + "\n".join(lines[mid:]),
                encoding="utf-8",
            )
        else:  # edit_same_line — the deliberately unsafe control
            edited = list(lines)
            edited[mid] = f"{edited[mid]}  # touched by {tag}"
            target.write_text("\n".join(edited), encoding="utf-8")
        _git(repo, "add", "-A", check=True)
        _git(repo, "commit", "-qm", f"{tag} change", check=True)

    _git(repo, "checkout", "-q", base, check=True)
    return names


def _merge_worktree(repo: Path, result: ScenarioResult, names: List[str]) -> str:
    """The path a developer takes locally: checkout b0, merge the rest."""
    _git(repo, "checkout", "-q", names[0], check=True)
    for name in names[1:]:
        proc = _git(repo, "merge", "--no-edit", name)
        if "CONFLICT" in (proc.stdout + proc.stderr):
            result.conflicted = True
            result.detail = f"conflict merging {name}"
            return (repo / TARGET).read_text(encoding="utf-8")
    return (repo / TARGET).read_text(encoding="utf-8")


def _merge_tree(repo: Path, result: ScenarioResult, names: List[str]) -> str:
    """The path a forge takes server-side: merge-tree, no working tree at all.

    This is the half that matters for "will the PR merge button work", and it
    is the half a purely local rehearsal silently skips.
    """
    head = names[0]
    for name in names[1:]:
        proc = _git(repo, "merge-tree", "--write-tree", "--messages", head, name)
        out = proc.stdout.strip().split("\n")
        if proc.returncode != 0 or not out or not out[0].strip():
            result.conflicted = True
            result.detail = f"merge-tree conflict merging {name}: {proc.stdout.strip()[:160]}"
            return ""
        tree = out[0].strip()
        commit = _git(repo, "commit-tree", tree, "-p", head, "-p", name, "-m", "merge")
        if commit.returncode != 0:
            result.conflicted = True
            result.detail = f"commit-tree failed: {commit.stderr.strip()[:160]}"
            return ""
        head = commit.stdout.strip()
    show = _git(repo, "show", f"{head}:{TARGET}")
    if show.returncode != 0:
        result.conflicted = True
        result.detail = "could not read merged blob"
        return ""
    return show.stdout


def _inspect(result: ScenarioResult, body: str, names: List[str]) -> None:
    if result.conflicted:
        return
    if "<<<<<<<" in body or ">>>>>>>" in body:
        result.markers_left = True
    if body.count("```") % 2:
        result.fences_balanced = False

    # `edit_same_line` adds no blocks, so there is nothing to lose there.
    if result.scenario != "edit_same_line":
        for tag in names:
            marker = f"python tools/{tag}/thing.py --run"
            count = body.count(marker)
            if count == 0:
                result.lost_blocks.append(tag)
            elif count > 1:
                result.duplicated.append(f"{tag} x{count}")
    else:
        touched = [ln for ln in body.split("\n") if "# touched by b" in ln]
        if len(touched) > 1:
            result.duplicated.append(f"same line kept {len(touched)}x")
            result.detail = (
                "union kept every edit of the shared line instead of "
                "conflicting — the known cost, visible in the diff"
            )


def run_scenario(scenario: str, mode: str, branches: int, source: Path,
                 union: bool = True) -> ScenarioResult:
    result = ScenarioResult(scenario=scenario, mode=mode, branches=branches)
    with tempfile.TemporaryDirectory(prefix="icdev-cmddoc-") as td:
        repo = Path(td) / "repo"
        try:
            _init_repo(repo, source, union=union)
            names = _make_branches(repo, scenario, branches)
            body = (
                _merge_worktree(repo, result, names)
                if mode == "worktree"
                else _merge_tree(repo, result, names)
            )
            _inspect(result, body, names)
        except RehearsalError as exc:
            result.conflicted = True
            result.detail = f"setup failed: {exc}"
    return result


def run_all(branches: int, scenario: Optional[str], mode: Optional[str],
            source: Path, union: bool = True) -> List[ScenarioResult]:
    scenarios = (scenario,) if scenario else SCENARIOS
    modes = (mode,) if mode else MODES
    return [
        run_scenario(s, m, branches, source, union=union)
        for s in scenarios
        for m in modes
    ]


def _render(results: List[ScenarioResult]) -> str:
    out = [f"{'scenario':<16}{'mode':<12}{'br':>3}  {'clean':<6} detail"]
    for r in results:
        bits = []
        if r.conflicted:
            bits.append(r.detail or "CONFLICT")
        if r.lost_blocks:
            bits.append(f"lost {','.join(r.lost_blocks)}")
        if r.duplicated:
            bits.append(f"duplicated {','.join(r.duplicated)}")
        if not r.fences_balanced:
            bits.append("code fences unbalanced")
        if r.markers_left:
            bits.append("conflict markers in file")
        out.append(
            f"{r.scenario:<16}{r.mode:<12}{r.branches:>3}  "
            f"{('yes' if r.clean else 'NO'):<6} {'; '.join(bits) or 'clean'}"
        )
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scenario", choices=SCENARIOS, help="run a single scenario")
    parser.add_argument("--mode", choices=MODES, help="run a single merge mode")
    parser.add_argument("--branches", type=int, default=2,
                        help="concurrent branches (default 2)")
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT / TARGET,
                        help="file to rehearse (default: the real one)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--gate", action="store_true",
                        help=f"exit 1 unless {'/'.join(GATED_SCENARIOS)} are clean")
    parser.add_argument("--without-union", action="store_true",
                        help="CONTROL: rehearse with the rule absent. Every "
                             "scenario is expected to CONFLICT; if it does not, "
                             "the harness is measuring nothing and the clean "
                             "run above proves nothing either.")
    args = parser.parse_args(argv)

    union = not args.without_union
    results = run_all(args.branches, args.scenario, args.mode, args.source, union=union)

    gated = [r for r in results if r.scenario in GATED_SCENARIOS]
    if union:
        failures = [r for r in gated if not r.clean]
    else:
        # Inverted: with no union rule, a scenario that merges CLEANLY means the
        # harness never exercised the collision it claims to exercise.
        failures = [r for r in gated if not r.conflicted]

    if args.json:
        print(json.dumps({
            "target": TARGET,
            "branches": args.branches,
            "union": union,
            "results": [r.to_dict() for r in results],
            "gated_clean": not failures,
        }, indent=2))
    else:
        print(_render(results))
        if not union:
            print(
                f"\n{'FAIL' if failures else 'OK'}: control run — "
                + (f"{len(failures)} scenario(s) merged cleanly WITHOUT the union "
                   "rule, so the harness is not exercising a real collision"
                   if failures else
                   "every observed-pattern scenario conflicts without the rule, "
                   "so the clean run with it is meaningful")
            )
        elif failures:
            print(f"\nFAIL: {len(failures)} observed-pattern scenario(s) not clean")
        else:
            print("\nOK: the observed append-only pattern merges cleanly under union")

    return 1 if (args.gate and failures) else 0


if __name__ == "__main__":
    sys.exit(main())
