# CUI // SP-CTI
"""A verified task the RUNNER lands by direct merge must end ``done`` (omx-adapt-05).

MEASURED 2026-10-11 on the Omarchy laptop, omx-smoke-04 (pi_cli, external scratch
repo with a bare remote, ICDEV_KANBAN_PR_FLOW unset). Its transition log holds:

    19:02:06  scheduled   -> in_progress
    19:04:32  in_progress -> REFUSED_done_unmerged

and nothing else -- while the bare remote's ``main`` moved to the task commit in
that same second. The completion path asked for ``done`` BEFORE
``_cleanup_worktree`` merged the branch, the merge-verify gate rightly refused,
the runner then merged and pushed, and nothing asked again.

These tests run the real pieces over real git: a bare "origin", a clone standing
in for the external repo, a ``kanban/<id>`` branch carrying the agent's commit,
and the real ``_move_task`` against a SQLite board.
"""
from __future__ import annotations

import importlib
import sqlite3
import subprocess

import pytest

from tools.db.storage import get_connection as _real_get_connection

TID = "omx-dm-01"

_SCHEMA = """
CREATE TABLE kanban_tasks (
    id                  TEXT PRIMARY KEY,
    title               TEXT,
    status              TEXT,
    updated_at          TEXT,
    completed_at        TEXT,
    completed_via_bypass INTEGER DEFAULT 0,
    execution_seconds   REAL,
    scheduled_at        TEXT,
    depends_on_task_id  TEXT,
    failure_count       INTEGER DEFAULT 0,
    last_failure_reason TEXT,
    last_failure_at     TEXT,
    files_changed       INTEGER,
    lines_added         INTEGER,
    lines_removed       INTEGER,
    branch_name         TEXT,
    commit_summary      TEXT,
    executor_url        TEXT
);
CREATE TABLE kanban_status_transitions (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    actor       TEXT,
    reason      TEXT,
    recorded_at TEXT
);
"""


def _git(cwd, *args):
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, f"git {args} failed: {out.stderr}"
    return out.stdout.strip()


class _Log:
    """Records the module logger's calls (caplog cannot see get_logger())."""

    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, msg, *a, **_k):
        self.warnings.append(msg % a if a else msg)

    def error(self, msg, *a, **_k):
        self.warnings.append(msg % a if a else msg)

    def info(self, *_a, **_k):
        pass

    debug = info


@pytest.fixture
def board(tmp_path, monkeypatch):
    km = importlib.import_module("tools.genesis.reflexes.kanban")

    db_path = tmp_path / "k.db"
    c = sqlite3.connect(str(db_path))
    c.executescript(_SCHEMA)
    c.execute("INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
              (TID, "direct-merge smoke", "in_progress"))
    c.commit()
    c.close()

    def _conn(*_a, **_k):
        return _real_get_connection(db_path=str(db_path))

    monkeypatch.setattr(km, "get_connection", _conn)

    # The external repo: a bare origin and a clone with one commit on main.
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(repo))
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "checkout", "-b", "main")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(repo, "add", "calc.py")
    _git(repo, "commit", "-m", "init")
    _git(repo, "push", "-u", "origin", "main")
    # The agent's work: a commit on kanban/<id>, pushed as a BRANCH only.
    _git(repo, "checkout", "-b", f"kanban/{TID}")
    (repo / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef negate(a):\n    return -a\n",
        encoding="utf-8")
    _git(repo, "commit", "-am", f"{TID}: negate")
    _git(repo, "push", "origin", f"kanban/{TID}")
    _git(repo, "checkout", "main")

    monkeypatch.setattr(km, "_task_repo_root", lambda _t: repo)
    monkeypatch.setattr(km, "_task_base_branch", lambda _t: "main")
    monkeypatch.setattr(km, "_default_branch", lambda: "main")
    monkeypatch.setattr(km, "_task_worktree_path", lambda t: tmp_path / "wt" / t)
    monkeypatch.setattr(km, "_post_merge_route_smoke", lambda *a, **k: None)
    # A dispatched task carries a dispatch record in production; this board has
    # no dispatch tables, so answer the delivery-evidence gate the way it would.
    monkeypatch.setattr(km, "done_delivery_refusal", lambda *a, **k: "")
    monkeypatch.delenv("ICDEV_KANBAN_PR_FLOW", raising=False)
    monkeypatch.delenv("ICDEV_HITL_KANBAN_GATE", raising=False)
    log = _Log()
    monkeypatch.setattr(km, "logger", log)
    return {"km": km, "db": db_path, "repo": repo, "origin": origin, "log": log}


def _task(db):
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    row = dict(c.execute("SELECT * FROM kanban_tasks WHERE id = ?", (TID,)).fetchone())
    c.close()
    return row


def _transitions(db):
    c = sqlite3.connect(str(db))
    rows = [r[0] for r in c.execute(
        "SELECT to_status FROM kanban_status_transitions WHERE task_id = ? "
        "ORDER BY recorded_at", (TID,))]
    c.close()
    return rows


def _origin_main_has_negate(origin):
    return "negate" in _git(origin, "show", "main:calc.py")


def test_runner_landed_direct_merge_ends_done_with_a_transition_row(board):
    km, db = board["km"], board["db"]

    # The incident: at verification time the work is NOT on origin/main, so an
    # early done is refused -- the completion path must defer it instead.
    assert km._branch_has_unmerged_commits(TID) is True
    km._move_task(TID, "done", actor="scheduler", reason="verified: early")
    assert _task(db)["status"] == "in_progress"
    assert _transitions(db) == ["REFUSED_done_unmerged"]

    landed = km._cleanup_worktree(TID)
    assert landed is True
    assert _origin_main_has_negate(board["origin"])

    assert km._complete_direct_merge(TID, landed, "Verified (git-first)") is True
    row = _task(db)
    assert row["status"] == "done"
    assert row["completed_at"]
    assert _transitions(db)[-1] == "done"


def test_a_failed_push_stays_not_done_with_a_reason_and_a_warning(board):
    km, db, repo = board["km"], board["db"], board["repo"]
    # Origin becomes unreachable after the agent pushed its branch.
    _git(repo, "remote", "set-url", "origin", str(repo.parent / "gone.git"))

    landed = km._cleanup_worktree(TID)
    assert landed is False

    assert km._complete_direct_merge(TID, landed, "Verified (git-first)") is False
    row = _task(db)
    assert row["status"] == "in_progress"
    assert row["completed_at"] is None
    assert row["last_failure_reason"] and "did not land" in row["last_failure_reason"]
    assert "done" not in _transitions(db)
    assert any(TID in w and "did not land" in w for w in board["log"].warnings)
    assert not _origin_main_has_negate(board["origin"])


def test_work_the_agent_already_put_on_main_completes_without_deferral(board):
    """The omx-smoke-03 control: the agent pushed to main itself."""
    km, db, repo = board["km"], board["db"], board["repo"]
    _git(repo, "push", "origin", f"kanban/{TID}:main")

    assert km._branch_has_unmerged_commits(TID) is False  # no deferral
    km._move_task(TID, "done", actor="scheduler", reason="verified: on main")
    row = _task(db)
    assert row["status"] == "done"
    assert row["completed_at"]
    assert _transitions(db) == ["done"]


def test_a_refused_post_merge_done_is_logged_at_warning(board, monkeypatch):
    km = board["km"]
    monkeypatch.setattr(km, "done_delivery_refusal", lambda *a, **k: "no evidence")

    assert km._complete_direct_merge(TID, True, "Verified") is False
    assert _task(board["db"])["status"] == "in_progress"
    assert any(TID in w and "still" in w for w in board["log"].warnings)
