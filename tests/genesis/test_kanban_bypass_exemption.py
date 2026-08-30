# CUI // SP-CTI
"""Tests: bypass completion exemption from the no_commits check in kanban.py.

Acceptance criterion (task diag-9060bae699-d4):
  A task with completed_via_bypass=1 (primary signal) or a
  kanban_verifications row with result='bypassed' (secondary signal)
  must pass _run_verify_checks without triggering the
  "No git commits" failure.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban


# ─── Helpers ──────────────────────────────────────────────────────────────────


class _Row(dict):
    """sqlite3.Row-alike: supports item and attribute access."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def _fake_conn(rows_by_fragment: dict):
    """Minimal DB connection mock.

    rows_by_fragment maps a SQL substring to the _Row returned by fetchone()
    for any query containing that substring.  Unmatched queries return None.
    """
    last = {"sql": ""}

    class _Cursor:
        def fetchone(self_inner):
            for fragment, row in rows_by_fragment.items():
                if fragment in last["sql"]:
                    return row
            return None

    cursor = _Cursor()

    class _Conn:
        def execute(self_inner, sql, params=None):
            last["sql"] = sql
            return cursor

        def close(self_inner):
            pass

    return _Conn()


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_bypass_flag_primary_signal_passes_verify(monkeypatch):
    """completed_via_bypass=1 on the task row → verified, git never called."""
    monkeypatch.setattr(
        kanban, "_git_worktree_has_real_changes", lambda tid, committed_only=False: (False, "")
    )
    conn = _fake_conn(
        {"completed_via_bypass FROM": _Row({"completed_via_bypass": 1})}
    )
    monkeypatch.setattr(kanban, "get_connection", lambda: conn)

    with patch("subprocess.run") as mock_sp:
        verified, reason = kanban._run_verify_checks("bypass-task-001", "")

    assert verified is True
    assert "bypass" in reason.lower()
    mock_sp.assert_not_called()


def test_bypass_secondary_signal_passes_verify(monkeypatch):
    """kanban_verifications row with result='bypassed' → verified without task flag."""
    monkeypatch.setattr(
        kanban, "_git_worktree_has_real_changes", lambda tid, committed_only=False: (False, "")
    )
    conn = _fake_conn({
        "completed_via_bypass FROM": _Row({"completed_via_bypass": 0}),
        "kanban_verifications": _Row({"reason": "pre-existing correct state"}),
    })
    monkeypatch.setattr(kanban, "get_connection", lambda: conn)

    with patch("subprocess.run") as mock_sp:
        verified, reason = kanban._run_verify_checks("bypass-task-002", "")

    assert verified is True
    assert "bypass" in reason.lower()
    mock_sp.assert_not_called()


def test_without_bypass_falls_through_to_normal_checks(monkeypatch):
    """Without bypass signals, normal validation gates apply (Check 0c not triggered).

    With no git changes and no bypass flag, the function falls through to the
    output-length or no_commits gate — confirming Check 0c is not a blanket
    exemption.
    """
    monkeypatch.setattr(
        kanban, "_git_worktree_has_real_changes", lambda tid, committed_only=False: (False, "")
    )
    conn = _fake_conn(
        {"completed_via_bypass FROM": _Row({"completed_via_bypass": 0})}
    )
    monkeypatch.setattr(kanban, "get_connection", lambda: conn)

    verified, reason = kanban._run_verify_checks("no-bypass-task-003", "")

    assert verified is False
    assert "bypass" not in reason.lower()
    assert "too short" in reason.lower() or "no git commits" in reason.lower()
