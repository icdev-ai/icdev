"""A linked PR lands as a MERGE COMMIT, and the git semantics prove why (mfx-mrg-01).

`_auto_merge` merged every linked PR with `gh pr merge --squash`. Two shapes
followed from that, both measured on the live board:

  * a squash puts a NEW commit on main with the branch's content while the
    branch keeps its own commits, so `origin/main..kanban/<id>` still lists them
    and a worker finishing a minute later opens a DUPLICATE PR that can only land
    as a revert (#2015/#2014, #1985/#1983 on 2026-09-03; #2056/#2053 and
    #2049/#2053 on 2026-09-04). Surveyed 2026-09-04: 51 of the 60 linked branches
    still on origin read AHEAD of main after their squash;
  * a sibling resolution STACKED on the first sibling's branch collapses the
    moment that sibling squash-merges -- its merge base against main is still the
    old main, so the first sibling's hunks come back beside its own and the PR
    goes CONFLICTING (#2046/#2047/#2048 within a minute of #2045).

This file does not stub gh. It reads the method flag the watcher actually
passes, REPLAYS what GitHub does for that flag in a throwaway repository with
two fake branches, and asserts the card's two acceptance criteria: a stacked
sibling stays MERGEABLE after the first lands, and `main..branch` is EMPTY
afterwards. A control replays `--squash` on the same fixture and reproduces both
defects, so the test is a statement about the flag and not about the fixture.
"""
from __future__ import annotations

import subprocess

import pytest

from tools.ci.pr_watcher import MERGE_METHOD_FLAG, PRWatcher

PR = "https://github.com/icdev-ai/icdev/pull/2045"
_METHODS = ("--merge", "--squash", "--rebase")


def _git(cwd, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/nonexistent",
         "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=check,
    )


@pytest.fixture
def repo(tmp_path):
    """main holds ten lines; kanban/a changes line 5; kanban/b is STACKED on
    kanban/a and changes the adjacent line 6 -- the sibling-resolution shape."""
    _git(tmp_path, "init", "-q", "-b", "main")
    f = tmp_path / "f.txt"
    lines = [f"line {i}" for i in range(1, 11)]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "f.txt")
    _git(tmp_path, "commit", "-q", "-m", "base")

    _git(tmp_path, "checkout", "-q", "-b", "kanban/a")
    lines[4] = "line 5 changed by a"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "feat: a (kanban/a)")

    _git(tmp_path, "checkout", "-q", "-b", "kanban/b")   # stacked on a
    lines[5] = "line 6 changed by b"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "feat: b (kanban/b)")

    _git(tmp_path, "checkout", "-q", "main")
    return tmp_path


def _land(repo, branch: str, flag: str, number: int) -> None:
    """Replay what GitHub does for `gh pr merge <flag>` on `branch` into main."""
    _git(repo, "checkout", "-q", "main")
    if flag == "--merge":
        _git(repo, "merge", "--no-ff", "-q", branch,
             "-m", f"Merge pull request #{number} from icdev-ai/{branch}")
    elif flag == "--squash":
        _git(repo, "merge", "--squash", "-q", branch)
        _git(repo, "commit", "-q", "-m", f"squashed {branch} (#{number})")
    else:  # pragma: no cover - the watcher passes one of the two above
        pytest.fail(f"no replay for {flag!r}")


def _mergeable(repo, branch: str) -> bool:
    """Would the forge report `branch` MERGEABLE into main right now?
    `git merge-tree --write-tree` exits 0 on a clean merge, 1 on conflicts."""
    r = _git(repo, "merge-tree", "--write-tree", "main", branch, check=False)
    assert r.returncode in (0, 1), r.stderr
    return r.returncode == 0


def _ahead(repo, branch: str) -> int:
    return int(_git(repo, "rev-list", "--count", f"main..{branch}").stdout.strip())


def _flag_the_watcher_passes() -> str:
    """The method flag from the argv `_auto_merge` really builds -- not the
    constant, so a call site that respelled it would be caught here."""
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    w = PRWatcher.__new__(PRWatcher)
    w._auto_merge_runner = runner
    w.config = {"auto_merge_enabled": True}
    w.dry_run = False
    assert w._auto_merge(PR) is True
    flags = {m for cmd in calls for m in cmd if m in _METHODS}
    assert len(flags) == 1, f"one method, spelled once: {calls}"
    return flags.pop()


def test_the_watcher_passes_the_declared_method():
    assert _flag_the_watcher_passes() == MERGE_METHOD_FLAG == "--merge"


def test_a_stacked_sibling_stays_mergeable_after_the_first_lands(repo):
    """Acceptance 1. Before anything lands both are mergeable; after kanban/a
    lands the way the watcher lands it, kanban/b (stacked on a) still is."""
    assert _mergeable(repo, "kanban/a") and _mergeable(repo, "kanban/b")
    _land(repo, "kanban/a", _flag_the_watcher_passes(), 2045)
    assert _mergeable(repo, "kanban/b"), (
        "the stacked sibling went CONFLICTING the moment the first landed")


def test_the_branch_is_not_ahead_after_the_merge(repo):
    """Acceptance 2. `main..kanban/a` is EMPTY once it has landed, so nothing
    ancestry-based -- the duplicate-PR opener, reclaim_worktree, orphan_requeue
    -- can read the landed branch as unmerged work."""
    _land(repo, "kanban/a", _flag_the_watcher_passes(), 2045)
    assert _ahead(repo, "kanban/a") == 0
    assert _git(repo, "merge-base", "--is-ancestor", "kanban/a", "main",
                check=False).returncode == 0


def test_control_a_squash_reproduces_both_defects(repo):
    """The fixture is not rigged: replaying `--squash` on the same two branches
    leaves kanban/a AHEAD and turns the stacked kanban/b CONFLICTING -- the two
    board incidents the card names. If this ever passes clean, git's merge
    semantics changed and the fixture no longer says anything."""
    _land(repo, "kanban/a", "--squash", 2045)
    assert _ahead(repo, "kanban/a") > 0
    assert not _mergeable(repo, "kanban/b")
