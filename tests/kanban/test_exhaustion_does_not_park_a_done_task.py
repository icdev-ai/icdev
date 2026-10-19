# CUI // SP-CTI
"""An abnormal exit AFTER the worker reached `done` must not park the task.

kph-orphan-task-e2e-8f6cb4d0, measured 2026-10-19: the card went
done -> token_exhausted twice, each ~40s after the worker's own `done`, and the
retry re-dispatched a finished card. _detect_token_exhaustion is True for any
exit code < 0 or >= 128, and the park branch never asked what the board said.
"""
from __future__ import annotations

import importlib
import subprocess
import sys

import pytest


@pytest.fixture
def board(icdev_db, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    return icdev_db


def _seed(task_id: str, status: str) -> None:
    from tools.db.storage import get_connection

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, status, task_type, priority) "
            "VALUES (%s, %s, %s, 'chore', 'medium')",
            (task_id, "exhaustion guard fixture", status),
        )
        conn.commit()


def _drive(monkeypatch, task_id: str, returncode: int):
    kmod = importlib.import_module("tools.genesis.reflexes.kanban")
    proc = subprocess.Popen([sys.executable, "-c", f"import sys; sys.exit({returncode})"])
    proc.wait(timeout=30)
    monkeypatch.setattr(kmod, "_running", {task_id: proc})
    monkeypatch.setattr(kmod, "_dispatch_times", {})
    monkeypatch.setattr(kmod, "_worktrees", {})
    moves: list = []
    monkeypatch.setattr(kmod, "_move_task", lambda tid, status, **kw: moves.append((tid, status)))
    monkeypatch.setattr(kmod, "_write_handoff_record", lambda *a, **k: None)
    monkeypatch.setattr(kmod, "_send_notification", lambda *a, **k: None)
    monkeypatch.setattr(kmod, "_hand_parked_task_to_pr_watcher", lambda *a, **k: None)
    kmod._check_completed()
    return kmod, moves


def test_abnormal_exit_after_done_is_not_parked(board, monkeypatch):
    _seed("exh-done-01", "done")
    kmod, moves = _drive(monkeypatch, "exh-done-01", 137)
    assert ("exh-done-01", "token_exhausted") not in moves
    assert moves == []
    assert "exh-done-01" not in kmod._running


def test_abnormal_exit_while_in_progress_still_parks(board, monkeypatch):
    _seed("exh-live-01", "in_progress")
    _kmod, moves = _drive(monkeypatch, "exh-live-01", 137)
    assert ("exh-live-01", "token_exhausted") in moves
