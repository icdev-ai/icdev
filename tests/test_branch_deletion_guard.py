# CUI // SP-CTI
"""A remote branch holding unmerged work must not be deletable.

2026-08-07, from the repo's own DeleteEvent log: an agent "cleaning up
duplicates" deleted four remote branches matching one task id in six seconds,
keeping only its own.

    23:37:00  fix/xbm-wake-01-rebased
    23:37:02  fix/xbm-wake-01-backoff-and-reason   <- PR #1332, ALL CHECKS GREEN
    23:37:03  feat/xbm-wake-01-scout-breaker
    23:37:06  kanban/xbm-wake-01

Deleting a head branch makes GitHub close its PR as CLOSED-not-merged. #1332
died with no review, no failing check and no comment, and `gh pr reopen` then
failed because the head ref was gone. The commit survived only because it still
existed locally.

A task legitimately has several branches — a retry, a rebase, a rival
implementation, a human's fix — so sharing a task id is never grounds for
deletion.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"


def _hook():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ptu_bd", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _targets(cmd):
    return _hook()._remote_branch_delete_targets(cmd)


# ---------------------------------------------------------------------------
# What counts as a remote deletion

@pytest.mark.parametrize("cmd,expected", [
    ("git push origin --delete feat/x", ["feat/x"]),
    ("git push origin -d feat/x", ["feat/x"]),
    ("git push origin --delete feat/x feat/y", ["feat/x", "feat/y"]),
    ("git push origin :feat/x", ["feat/x"]),
    ("gh api -X DELETE repos/o/r/git/refs/heads/feat/x", ["feat/x"]),
])
def test_remote_deletions_are_detected(cmd, expected):
    assert _targets(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "git push origin feat/x",              # ordinary push
    "git push -u origin feat/x",
    "git push --force-with-lease origin feat/x",
    "git branch -D feat/x",                # LOCAL only — cannot close a PR
    "git branch -d feat/x",
    "git status",
])
def test_non_deletions_are_ignored(cmd):
    assert _targets(cmd) == [], cmd


def test_local_branch_delete_is_deliberately_out_of_scope():
    """`git branch -D` removes a local ref; the remote and its PR are untouched.

    Blocking it would break every legitimate cleanup in the runner, which uses
    it in five places.
    """
    assert _targets("git branch -D kanban/xbm-wake-01") == []


# ---------------------------------------------------------------------------
# End-to-end through the real hook

def _run_hook(command: str, env_extra=None):
    payload = {"session_id": "t", "tool_name": "Bash", "tool_input": {"command": command}}
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=180, env=env,
                       cwd=str(REPO_ROOT))
    return p.returncode, (p.stderr or "")


def _make_branch_with_unique_commit(name: str) -> bool:
    """Create a local ref that `git cherry origin/main origin/<name>` sees."""
    r = subprocess.run(["git", "rev-parse", "origin/main"], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return False
    base = r.stdout.strip()
    # An empty commit still has a distinct patch-id from nothing, but `git cherry`
    # ignores empties — use a real tree change via commit-tree on a new blob.
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], input="guard-test\n",
                          cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60)
    if blob.returncode != 0:
        return False
    mk = subprocess.run(
        ["git", "mktree"], input=f"100644 blob {blob.stdout.strip()}\tguard_test.txt\n",
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60)
    if mk.returncode != 0:
        return False
    ct = subprocess.run(["git", "commit-tree", mk.stdout.strip(), "-p", base, "-m", "guard test"],
                        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60)
    if ct.returncode != 0:
        return False
    return subprocess.run(["git", "update-ref", f"refs/remotes/origin/{name}", ct.stdout.strip()],
                          cwd=str(REPO_ROOT), capture_output=True, text=True,
                          timeout=60).returncode == 0


def test_deleting_a_branch_with_unmerged_commits_is_blocked():
    """The #1332 case: green PR, unmerged commit, deleted anyway."""
    name = "zz-guard-unmerged"
    if not _make_branch_with_unique_commit(name):
        pytest.skip("could not synthesise a test ref")
    try:
        rc, err = _run_hook(f"git push origin --delete {name}")
        assert rc == 2, f"unmerged branch was deletable (rc={rc}) {err[:300]}"
        assert "unmerged work" in err
        assert "closed-not-merged" in err, "must say WHY this is destructive"
    finally:
        subprocess.run(["git", "update-ref", "-d", f"refs/remotes/origin/{name}"],
                       cwd=str(REPO_ROOT), capture_output=True, timeout=60)


def test_deleting_a_fully_merged_branch_is_allowed():
    """Post-merge cleanup is the common case and must stay frictionless."""
    r = subprocess.run(["git", "update-ref", "refs/remotes/origin/zz-guard-merged",
                        "origin/main"], cwd=str(REPO_ROOT), capture_output=True, timeout=60)
    if r.returncode != 0:
        pytest.skip("could not synthesise a test ref")
    try:
        rc, err = _run_hook("git push origin --delete zz-guard-merged")
        assert rc == 0, f"merged branch was blocked: {err[:300]}"
    finally:
        subprocess.run(["git", "update-ref", "-d", "refs/remotes/origin/zz-guard-merged"],
                       cwd=str(REPO_ROOT), capture_output=True, timeout=60)


def test_unknown_branch_fails_open():
    """git cherry cannot compare a ref that does not exist — allow, never block."""
    rc, _ = _run_hook("git push origin --delete zz-guard-does-not-exist")
    assert rc == 0


def test_ordinary_push_is_untouched():
    rc, _ = _run_hook("git push -u origin feat/something")
    assert rc == 0


def test_guard_can_be_disabled():
    name = "zz-guard-override"
    if not _make_branch_with_unique_commit(name):
        pytest.skip("could not synthesise a test ref")
    try:
        rc, _ = _run_hook(f"git push origin --delete {name}",
                          env_extra={"ICDEV_BRANCH_DELETE_GUARD": "0"})
        assert rc == 0, "ICDEV_BRANCH_DELETE_GUARD=0 must allow a deliberate discard"
    finally:
        subprocess.run(["git", "update-ref", "-d", f"refs/remotes/origin/{name}"],
                       cwd=str(REPO_ROOT), capture_output=True, timeout=60)
