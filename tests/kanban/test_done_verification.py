# CUI // SP-CTI
"""Regression tests for the kanban done-verification + coordination hardening
(2026-07-11).

Guarantees under test:
  1. Fail-closed push: _push_main returns False when the git push fails.
  2. Fail-closed merge: _merge_worktree_to_main returns False when the push
     step fails (so the task is NOT treated as merged/done).
  3. _branch_has_unmerged_commits: True only when the branch exists AND has
     commits not on origin/<default>; fail-open (False) on missing branch/error.
  4. Merge-verify done-gate: _move_task(id, "done") is REFUSED (status unchanged)
     when the branch still has unmerged commits, and SUCCEEDS when it doesn't.
  5. Provider-agnostic exhaustion: signal-kill exit codes and non-Claude
     rate/quota/OOM phrasing are detected; normal exit 1 is NOT mislabeled.
  6. Per-task lease mutual exclusion across sessions (CLI <-> runner handoff).
"""
from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as kb  # noqa: E402

try:
    from tools.db.storage import get_connection
except ImportError:  # pragma: no cover
    get_connection = None  # type: ignore[assignment]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Fake:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── 1. Fail-closed push ──────────────────────────────────────────────────────

def test_push_main_returns_false_on_failed_push(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: _Fake(returncode=1, stderr="rejected"))
    assert kb._push_main(cwd=".") is False


def test_push_main_returns_true_on_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Fake(returncode=0))
    assert kb._push_main(cwd=".") is True


# ── 2. Fail-closed merge (push failure => not merged) ────────────────────────

def test_merge_returns_false_when_push_fails(monkeypatch):
    """Merge steps succeed but the push fails => _merge_worktree_to_main False."""
    def fake_run(cmd, *a, **k):
        argv = cmd if isinstance(cmd, list) else [cmd]
        if "log" in argv and any(".." in x for x in argv):
            return _Fake(returncode=0, stdout="abc123 did work\n")  # has commits
        if "rev-parse" in argv:
            return _Fake(returncode=0, stdout="deadbeef")
        if "worktree" in argv:
            return _Fake(returncode=0)
        if "checkout" in argv:
            return _Fake(returncode=0)
        if "merge" in argv:
            return _Fake(returncode=0)  # ff merge succeeds
        if "push" in argv:
            return _Fake(returncode=1, stderr="remote rejected")  # push FAILS
        if "branch" in argv:
            return _Fake(returncode=0)
        return _Fake(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    assert kb._merge_worktree_to_main("test-task-xyz") is False


# ── 3. _branch_has_unmerged_commits ──────────────────────────────────────────

@pytest.mark.parametrize("verify_rc,log_rc,log_out,expected", [
    (0, 0, "c1 work\n", True),    # branch exists, commits not on origin -> True
    (0, 0, "", False),            # branch exists, nothing unmerged -> False
    (1, 0, "c1 work\n", False),   # branch missing -> fail-open False
    (0, 1, "", False),            # log errored -> fail-open False
])
def test_branch_has_unmerged_commits(monkeypatch, verify_rc, log_rc, log_out, expected):
    def fake_run(cmd, *a, **k):
        argv = cmd if isinstance(cmd, list) else [cmd]
        if "rev-parse" in argv:
            return _Fake(returncode=verify_rc)
        if "fetch" in argv:
            return _Fake(returncode=0)
        if "log" in argv:
            return _Fake(returncode=log_rc, stdout=log_out)
        return _Fake(returncode=0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    assert kb._branch_has_unmerged_commits("t-1") is expected


def test_branch_has_unmerged_commits_fail_open_on_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("git unavailable")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    assert kb._branch_has_unmerged_commits("t-1") is False


# ── 3b. The done-gate verifies a task in ITS OWN repo (ked-core-03) ──────────
#
# This check used to run every git command with cwd=BASE_DIR. For an ICDev task
# that is right. For a compass task it means we ask ICDEV whether COMPASS's work
# landed — the answer is always no, so the done-gate refuses forever and the task
# churns. These pin the cwd (and the base branch) the git calls actually use.

@pytest.fixture
def external_repo(tmp_path, monkeypatch):
    """Register prefix 'ext-' against a temp repo whose base branch is NOT 'main'."""
    root = tmp_path / "extrepo"
    root.mkdir()
    cfg = tmp_path / "repos.yaml"
    cfg.write_text(yaml.safe_dump({
        "repos": {"extrepo": {"base_branch": "trunk", "root_env": "TEST_EXT_ROOT"}},
        "prefixes": {"ext-": "extrepo"},
    }), encoding="utf-8")
    monkeypatch.setenv("ICDEV_KANBAN_REPOS_CONFIG", str(cfg))
    monkeypatch.setenv("TEST_EXT_ROOT", str(root))
    return root


def _record_git_calls(monkeypatch, log_out=""):
    """Patch subprocess.run to record the (argv, cwd) of every git call."""
    calls = []

    def fake_run(cmd, *a, **k):
        argv = cmd if isinstance(cmd, list) else [cmd]
        calls.append((argv, k.get("cwd")))
        if "log" in argv:
            return _Fake(returncode=0, stdout=log_out)
        return _Fake(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_unmerged_check_runs_git_inside_the_external_repo(external_repo, monkeypatch):
    calls = _record_git_calls(monkeypatch, log_out="c1 work landed in compass\n")

    assert kb._branch_has_unmerged_commits("ext-thing-01") is True

    cwds = {cwd for _, cwd in calls}
    assert cwds == {str(external_repo)}, f"git ran outside the external repo: {cwds}"
    assert str(kb.BASE_DIR) not in cwds, "the ICDev checkout must never be consulted"


def test_unmerged_check_compares_against_the_external_base_branch(
        external_repo, monkeypatch):
    """compass's main is not ICDev's main — the compare must use the registered base."""
    calls = _record_git_calls(monkeypatch)

    kb._branch_has_unmerged_commits("ext-thing-01")

    compared = [arg for argv, _ in calls if "log" in argv for arg in argv if ".." in arg]
    assert compared == ["origin/trunk..kanban/ext-thing-01"]


def test_unmerged_check_still_runs_in_BASE_DIR_for_an_icdev_task(
        external_repo, monkeypatch):
    """No regression: an id matching no prefix is an ICDev task, verified in ICDev."""
    monkeypatch.setattr(kb, "_default_branch", lambda: "main")
    calls = _record_git_calls(monkeypatch)

    kb._branch_has_unmerged_commits("dm-portal-01")

    assert {cwd for _, cwd in calls} == {str(kb.BASE_DIR)}


# ── 4. Merge-verify done-gate in _move_task ──────────────────────────────────

@pytest.fixture
def _isolated_db(tmp_path, monkeypatch):
    """Redirect the main SQLite DB to a temp file so the board-mutating tests
    below never touch the real data/icdev.db (opx-kan-02).

    ``get_connection()`` resolves ``ICDEV_DB_PATH`` at CALL time (see
    ``tools/db/storage.py::_get_sqlite_connection``), so this single env
    redirect isolates the fixture INSERT/DELETE, the ``_status()`` reads, AND
    every internal ``get_connection()`` inside ``kb._move_task`` (status
    transitions, parent-done guard) onto the temp DB. Without it these tests
    write ``task-test-doneverify-*`` rows straight into the live board and
    strand them when teardown is skipped (``-x`` / interrupt)."""
    db = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    return db


@pytest.fixture
def ephemeral_task(_isolated_db):
    if get_connection is None:
        pytest.skip("get_connection unavailable")
    try:
        from tools.kanban.init_db import init_kanban_tables
        init_kanban_tables()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"cannot init kanban tables: {exc}")
    tid = f"task-test-doneverify-{uuid.uuid4().hex[:10]}"
    now = _now()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
        "status, executor_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, "done-verify fixture", "test", "chore", "low", "in_progress",
         "claude_cli", now, now),
    )
    conn.commit()
    conn.close()
    yield tid
    conn = get_connection()
    conn.cursor().execute("DELETE FROM kanban_tasks WHERE id = ?", (tid,))
    conn.commit()
    conn.close()


def _status(tid):
    conn = get_connection()
    row = conn.execute("SELECT status FROM kanban_tasks WHERE id = %s", (tid,)).fetchone()
    conn.close()
    return dict(row)["status"] if row else None


def test_move_task_refuses_done_when_branch_unmerged(monkeypatch, ephemeral_task):
    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda tid: True)
    kb._move_task(ephemeral_task, "done", actor="test")
    assert _status(ephemeral_task) == "in_progress"  # REFUSED, unchanged


def test_move_task_allows_done_when_branch_merged(monkeypatch, ephemeral_task):
    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda tid: False)
    kb._move_task(ephemeral_task, "done", actor="test")
    assert _status(ephemeral_task) == "done"


def test_move_task_gate_disabled_by_env(monkeypatch, ephemeral_task):
    monkeypatch.setenv("KANBAN_REQUIRE_MERGE_FOR_DONE", "0")
    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda tid: True)
    kb._move_task(ephemeral_task, "done", actor="test")
    assert _status(ephemeral_task) == "done"  # gate off -> allowed


# ── 5. Provider-agnostic exhaustion / interruption ───────────────────────────

@pytest.mark.parametrize("exit_code,output,expected", [
    (137, "", True),                              # OOM-killer signal
    (143, "", True),                              # SIGTERM
    (-9, "", True),                               # killed by signal (POSIX)
    (0, "Error: 429 rate_limit exceeded", True),  # OpenAI/Kimi style
    (0, "insufficient_quota for this key", True), # quota
    (0, "failed to connect to ollama", True),     # local model down
    (0, "cuda out of memory", True),              # local OOM
    (0, "context window exceeded", True),         # context exhaustion
    (1, "AssertionError: expected 3 got 4", False),  # normal failure, NOT exhaustion
    (0, "all good, task complete", False),        # clean
])
def test_detect_exhaustion_provider_agnostic(exit_code, output, expected):
    is_ex, _ = kb._detect_token_exhaustion(exit_code, output)
    assert is_ex is expected


# ── 6. Per-task lease mutual exclusion (CLI <-> runner) ──────────────────────

def test_task_lease_mutual_exclusion(monkeypatch):
    from tools.coordination import leases
    res = f"kanban:task:test-{uuid.uuid4().hex[:8]}"

    # Session A claims it.
    monkeypatch.setattr(leases, "get_session_id", lambda: "sessionA")
    a = leases.acquire(res, intent="cli-manual", block=False)
    assert a is not None

    # Session B cannot (hard namespace).
    monkeypatch.setattr(leases, "get_session_id", lambda: "sessionB")
    b = leases.acquire(res, intent="kanban-runner", block=False)
    assert b is None

    # B's release is a no-op (doesn't own it); A's release frees it.
    assert leases.release(res) is False  # as sessionB
    monkeypatch.setattr(leases, "get_session_id", lambda: "sessionA")
    assert leases.release(res) is True

    # Now B can acquire.
    monkeypatch.setattr(leases, "get_session_id", lambda: "sessionB")
    b2 = leases.acquire(res, intent="kanban-runner", block=False)
    assert b2 is not None
    leases.release(res)
