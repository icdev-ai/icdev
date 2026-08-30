# CUI // SP-CTI
"""The git-first fast path may accept only DELIVERED work (kpr-rvfy-04).

``_run_verify_checks``'s own docstring has said "uncommitted changes alone are
NOT evidence of completion" since the dirty fallback was removed from check 5.
Check 0, which runs FIRST, reinstated it — and that is how four of the five
falsely-completed ``ftp-*`` tasks reached ``done`` on 2026-08-29. The fifth came
through the other arm, whose range counted commits MAIN gained rather than
commits the task contributed.

Both arms are exercised against a REAL git repository. The defect is entirely
about what git reports, so a mocked git would prove only that the mock returns
what the test put in it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.genesis.reflexes import kanban as K  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True, timeout=60)
    subprocess.run(["git", "clone", str(origin), str(work)],
                   capture_output=True, check=True, timeout=60)
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "seed")
    _git(work, "push", "-u", "origin", "main")
    return work


@pytest.fixture()
def wired(monkeypatch, repo):
    """Point the verifier's repo/worktree resolvers at the temp repo."""
    task_id = "ftp-ezb-06"
    monkeypatch.setattr(K, "_task_repo_root", lambda tid: repo)
    monkeypatch.setattr(K, "_task_base_branch", lambda tid: "main")
    monkeypatch.setattr(K, "_work_dir_for", lambda tid: str(repo))
    monkeypatch.setattr(K, "_dir_owns_its_repo_root", lambda d: True)
    monkeypatch.setitem(K._worktrees, task_id, str(repo))
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setitem(K._dispatch_main_heads, task_id, head)
    return task_id


class TestDirtyWorktreeIsNotDelivery:
    """ftp-ezb-06: 24 uncommitted changes, marked done, work then lost."""

    def test_dirty_worktree_still_answers_did_anything_happen(self, wired, repo):
        (repo / "glossary.ts").write_text("export const GLOSSARY = []\n",
                                          encoding="utf-8")
        ok, reason = K._git_worktree_has_real_changes(wired)
        assert ok is True
        assert "uncommitted change(s) in worktree" in reason

    def test_dirty_worktree_is_not_delivery(self, wired, repo):
        (repo / "glossary.ts").write_text("export const GLOSSARY = []\n",
                                          encoding="utf-8")
        ok, reason = K._git_worktree_has_real_changes(wired, committed_only=True)
        assert ok is False, "an uncommitted change is not delivered work"
        assert reason == ""

    def test_the_fast_path_does_not_complete_on_a_dirty_worktree(self, wired, repo):
        """The transition that was actually written on 2026-08-29, refused."""
        for name in ("glossary.ts", "glossary.tsx", "config.py"):
            (repo / name).write_text("x\n", encoding="utf-8")
        monkey_reason = K._run_verify_checks(wired, "")[1]
        assert "uncommitted change(s) in worktree" not in monkey_reason

    def test_a_commit_still_verifies(self, wired, repo):
        (repo / "glossary.ts").write_text("export const GLOSSARY = []\n",
                                          encoding="utf-8")
        _git(repo, "checkout", "-b", f"kanban/{wired}")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat: glossary")
        ok, reason = K._git_worktree_has_real_changes(wired, committed_only=True)
        assert ok is True
        assert f"file(s) changed on kanban/{wired}" in reason


class TestBranchRangeExcludesMainsOwnAdvance:
    """ftp-prd-07: "18 file(s) changed" on a branch 0 commits ahead of main."""

    def test_mains_own_commits_are_not_this_tasks_work(self, wired, repo):
        task_id = wired
        # The branch exists and points at main; main then advances with OTHER
        # tasks' merged work. `<baseline>..<branch>` would count all of it.
        _git(repo, "branch", f"kanban/{task_id}")
        for name in ("other_a.py", "other_b.py"):
            (repo / name).write_text("x\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", f"feat: {name} from another task")
        _git(repo, "push", "origin", "main")
        # Move the task branch onto the new main, exactly as a re-created or
        # reset worktree branch sits — 0 commits ahead.
        _git(repo, "branch", "-f", f"kanban/{task_id}", "main")
        ahead = _git(repo, "rev-list", "--count",
                     f"origin/main..kanban/{task_id}").stdout.strip()
        assert ahead == "0"
        # Arm 3 ("main advanced AND the worktree is clean") is a SEPARATE arm
        # with its own precondition, is not what completed ftp-prd-07, and is
        # deliberately untouched by this card — see
        # `test_arm_three_still_reads_a_clean_worktree_as_merged`. Dirtying the
        # worktree takes it out of the picture so this assertion is about arm 1
        # and nothing else.
        (repo / "scratch.txt").write_text("in progress\n", encoding="utf-8")

        ok, reason = K._git_worktree_has_real_changes(task_id, committed_only=True)
        assert ok is False, (
            "commits that are already on the default branch are other tasks' "
            f"work, not this one's — got {reason!r}"
        )

    def test_arm_three_still_reads_a_clean_worktree_as_merged(self, wired, repo):
        """CHARACTERISATION, not an endorsement — the residual after kpr-rvfy-04.

        Arm 3 infers "the work merged" from "main advanced since dispatch AND
        the worktree is clean". That is post hoc: main advances constantly on a
        board running many tasks at once. It is recorded here rather than left
        silent, and deliberately NOT narrowed by this card: arm 3 carries 18.88%
        of scheduler completions (measured 2026-08-29, n=498) and is the auto-
        merge path, so changing it needs its own survey. What kpr-rvfy-04
        establishes is that a completion reached this way must still satisfy the
        merge-verify and delivery-evidence gates in ``_move_task``.
        """
        task_id = wired
        _git(repo, "branch", f"kanban/{task_id}")
        (repo / "other.py").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat: another task's work")
        _git(repo, "push", "origin", "main")
        _git(repo, "branch", "-f", f"kanban/{task_id}", "main")

        ok, reason = K._git_worktree_has_real_changes(task_id, committed_only=True)
        assert ok is True
        assert "main advanced" in reason

    def test_the_tasks_own_commit_is_still_counted(self, wired, repo):
        task_id = wired
        _git(repo, "checkout", "-b", f"kanban/{task_id}")
        (repo / "alert_delivery.py").write_text("x\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat: alerts")
        ok, reason = K._git_worktree_has_real_changes(task_id, committed_only=True)
        assert ok is True
        assert "alert_delivery.py" in reason
