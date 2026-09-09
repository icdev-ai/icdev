#!/usr/bin/env python3
# CUI // SP-CTI
"""A branch whose head is a MERGE COMMIT is integrated by merging the base IN,
never by a plain rebase that replays the commit the merge discarded (kpr-watch-15).

THE DEFECT. `rebase_and_push` runs a default `git rebase`, which does not
preserve merges. A branch whose head is a `merge -s ours` supersede -- the
documented recipe for "keep the rebased tree, discard the pre-rebase commit" --
is therefore FLATTENED, and the commit the supersede deliberately discarded is
REPLAYED alongside its own replacement. Proven on `dwr-ev-03` (PR #2179), whose
head from 2026-09-08T07:57:53Z was a two-parent `-s ours` merge:

    $ git rev-list --no-merges --reverse 39d6655e6..234cea39c
    acad8b4d1  feat(dic): redraft with my comments ... (dwr-ev-03)
    26c59984d  feat(dic): redraft with my comments ... (dwr-ev-03)

Two commits where the branch intends one; ten `rebase_failed` rows followed.

Every fixture here is a REAL git repository with a REAL `merge -s ours`, driven
through the shipped `rebase_and_push`. A mocked runner would prove only that the
argv we chose is the argv we assert.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import rebase_recovery  # noqa: E402

TASK = "kpr-watch-15"
BRANCH = "kanban/" + TASK


def _git(args, cwd, check=True):
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, p.stderr))
    return p


def _identity(cwd):
    _git(["config", "user.email", "t@icdev.local"], cwd)
    _git(["config", "user.name", "icdev-test"], cwd)


@pytest.fixture()
def repo(tmp_path):
    """origin (bare) + a clone, with `main` and room for a task branch off it."""
    origin = tmp_path / "origin.git"
    _git(["init", "--bare", "-b", "main", str(origin)], tmp_path)
    work = tmp_path / "work"
    _git(["clone", str(origin), str(work)], tmp_path)
    _identity(work)
    (work / "shared.txt").write_text("base\n", encoding="utf-8")
    (work / "other.txt").write_text("other\n", encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-m", "base"], work)
    _git(["push", "-u", "origin", "main"], work)
    return {"origin": origin, "work": work}


def _advance_main(work, text="main moved\n", path="other.txt"):
    _git(["checkout", "main"], work)
    (work / path).write_text(text, encoding="utf-8")
    _git(["commit", "-am", "main moves"], work)
    _git(["push", "origin", "main"], work)


def _superseded_branch(work):
    """A branch whose head is a `merge -s ours` supersede.

    `bad` is the pre-rebase commit; `good` is its rebased replacement; the head
    keeps `good`'s tree and records `bad` only as a discarded second parent --
    the exact shape memory `supersede-a-rejected-branch-with-merge-s-ours`
    prescribes and `dwr-ev-03` carried.
    """
    _git(["checkout", "-b", "bad-line", "main"], work)
    (work / "shared.txt").write_text("SUPERSEDED\n", encoding="utf-8")
    _git(["commit", "-am", "the pre-rebase commit"], work)
    bad = _git(["rev-parse", "HEAD"], work).stdout.strip()

    _git(["checkout", "-b", BRANCH, "main"], work)
    (work / "shared.txt").write_text("KEPT\n", encoding="utf-8")
    _git(["commit", "-am", "the rebased replacement"], work)
    good = _git(["rev-parse", "HEAD"], work).stdout.strip()

    _git(["merge", "-s", "ours", "--no-edit", "-m",
          "chore: supersede the pre-rebase commit", "bad-line"], work)
    _git(["push", "-u", "origin", BRANCH], work)
    return {"bad": bad, "good": good,
            "head": _git(["rev-parse", "HEAD"], work).stdout.strip()}


def _linear_branch(work):
    _git(["checkout", "-b", BRANCH, "main"], work)
    (work / "shared.txt").write_text("LINEAR\n", encoding="utf-8")
    _git(["commit", "-am", "a linear task commit"], work)
    _git(["push", "-u", "origin", BRANCH], work)
    return _git(["rev-parse", "HEAD"], work).stdout.strip()


# ---------------------------------------------------------------------------
# 1. The predicate
# ---------------------------------------------------------------------------

def test_branch_carries_merge_sees_the_supersede(repo):
    work = repo["work"]
    _superseded_branch(work)
    _advance_main(work)
    _git(["fetch", "origin"], work)
    merges = rebase_recovery.branch_carries_merge(
        str(work), "origin/main", "origin/" + BRANCH)
    assert merges, "the -s ours supersede is a merge commit on the branch"


def test_branch_carries_merge_is_empty_for_a_linear_branch(repo):
    work = repo["work"]
    _linear_branch(work)
    _advance_main(work)
    _git(["fetch", "origin"], work)
    assert rebase_recovery.branch_carries_merge(
        str(work), "origin/main", "origin/" + BRANCH) == []


def test_branch_carries_merge_is_none_when_git_cannot_answer(repo):
    """UNMEASURABLE is None and NEVER an empty list: an unreadable history must
    fall through to the unchanged rebase, never be read as `linear`."""
    assert rebase_recovery.branch_carries_merge(
        str(repo["work"]), "origin/main", "refs/heads/no-such-branch") is None


# ---------------------------------------------------------------------------
# 2. The integration
# ---------------------------------------------------------------------------

def test_supersede_branch_is_integrated_by_merge_and_keeps_its_tree(repo):
    """The whole card: the discarded commit is NOT resurrected and the push
    succeeds where a plain rebase replayed two commits and collided."""
    work = repo["work"]
    _superseded_branch(work)
    _advance_main(work)

    verdict = rebase_recovery.rebase_and_push(
        TASK, BRANCH, base="main", repo_root=str(work), union_rules=False)

    assert verdict["strategy"] == "merge", verdict
    assert verdict["pushed"] is True, verdict
    _git(["fetch", "origin"], work)
    head = _git(["rev-parse", "origin/" + BRANCH], work).stdout.strip()
    kept = _git(["show", head + ":shared.txt"], work).stdout
    assert kept.strip() == "KEPT", "the superseded content came back"
    # main is now contained, so the PR is no longer DIRTY.
    assert _git(["merge-base", "--is-ancestor", "origin/main", head], work,
                check=False).returncode == 0


def test_a_plain_rebase_of_that_branch_replays_the_discarded_commit(repo):
    """The control. The same branch offers TWO commits to a default rebase --
    the superseded one and its replacement -- which is the mechanism, measured.
    """
    work = repo["work"]
    shas = _superseded_branch(work)
    _advance_main(work)
    _git(["fetch", "origin"], work)
    replayed = _git(
        ["rev-list", "--no-merges", "origin/main..origin/" + BRANCH], work
    ).stdout.split()
    assert sorted(replayed) == sorted([shas["bad"], shas["good"]]), (
        "a default rebase replays both the discarded commit and its replacement")


def test_a_linear_branch_still_takes_the_unchanged_rebase(repo):
    """SCOPE. 475 of the 649 recorded rebase failures were on linear branches
    and this card must not touch one of them."""
    work = repo["work"]
    _linear_branch(work)
    _advance_main(work)

    verdict = rebase_recovery.rebase_and_push(
        TASK, BRANCH, base="main", repo_root=str(work), union_rules=False)

    assert verdict["strategy"] == "rebase", verdict
    assert verdict["pushed"] is True, verdict
    _git(["fetch", "origin"], work)
    head = _git(["rev-parse", "origin/" + BRANCH], work).stdout.strip()
    assert _git(["rev-list", "--merges", "origin/main.." + head], work).stdout.strip() == "", (
        "the merge path must never make a linear branch non-linear")


def test_already_current_is_not_reported_as_an_integration(repo):
    """5 of the 62 measured rows are a branch that ALREADY contains its base --
    the forge's phantom. Reporting that as `pushed` claims an act that did not
    happen."""
    work = repo["work"]
    _advance_main(work)
    _superseded_branch(work)          # branched from the ADVANCED main

    verdict = rebase_recovery.rebase_and_push(
        TASK, BRANCH, base="main", repo_root=str(work), union_rules=False)

    assert verdict["strategy"] == "merge", verdict
    assert verdict.get("already_current") is True, verdict
    assert verdict["pushed"] is False, verdict


def test_a_real_conflict_on_the_merge_path_pushes_nothing(repo):
    """Clean-only, unchanged: a merge that hits a real conflict is aborted and
    reported, and the remote branch is left exactly as it was."""
    work = repo["work"]
    _superseded_branch(work)
    before = _git(["rev-parse", "origin/" + BRANCH], work).stdout.strip()
    _advance_main(work, text="MAIN WINS\n", path="shared.txt")

    verdict = rebase_recovery.rebase_and_push(
        TASK, BRANCH, base="main", repo_root=str(work), union_rules=False)

    assert verdict["strategy"] == "merge", verdict
    assert verdict["pushed"] is False and verdict["conflict"] is True, verdict
    _git(["fetch", "origin"], work)
    assert _git(["rev-parse", "origin/" + BRANCH], work).stdout.strip() == before


def test_dry_run_pushes_nothing_on_the_merge_path(repo):
    work = repo["work"]
    _superseded_branch(work)
    before = _git(["rev-parse", "origin/" + BRANCH], work).stdout.strip()
    _advance_main(work)

    verdict = rebase_recovery.rebase_and_push(
        TASK, BRANCH, base="main", repo_root=str(work), union_rules=False,
        dry_run=True)

    assert verdict["strategy"] == "merge" and verdict["pushed"] is False, verdict
    _git(["fetch", "origin"], work)
    assert _git(["rev-parse", "origin/" + BRANCH], work).stdout.strip() == before


# ---------------------------------------------------------------------------
# 3. Structure -- the properties a behavioural test cannot pin
# ---------------------------------------------------------------------------

def test_the_merge_path_is_reachable_only_through_the_predicate():
    """A future edit threading a caller-supplied strategy through would keep
    every behavioural test above green. `rebase_and_push` takes no such
    parameter, and the merge verb is chosen from `branch_carries_merge` alone.
    """
    src = pathlib.Path(rebase_recovery.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "rebase_and_push")
    names = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
    assert "strategy" not in names and "integrate_by" not in names, (
        "the integration strategy must be MEASURED, never passed in")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "branch_carries_merge" in called
