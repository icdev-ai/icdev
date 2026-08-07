# CUI // SP-CTI
"""The agent surface must be instrumented on the path the runner ACTUALLY uses.

#1196 instrumented `tools/agent/agent_executor.py::execute_agent` and claimed
the agent surface was covered. It was not: the kanban runner dispatches with
`subprocess.Popen(claude ...)` and never calls that function. Measured on the
live board 2026-08-07 — `mcp` recorded fine while `agent`, `persona` and `role`
had zero rows for their entire existence.

These tests pin the correction. The load-bearing one is
``test_dispatch_path_does_not_route_through_execute_agent``: it fails if someone
"fixes" coverage by instrumenting a function this runner does not call.
"""
from __future__ import annotations


import sqlite3
from pathlib import Path

import pytest

from tools.observability import invocation_recorder as R

_KANBAN_SRC = (Path(__file__).resolve().parent.parent
               / "tools" / "genesis" / "reflexes" / "kanban.py")


@pytest.fixture()
def inv_db(tmp_path, monkeypatch):
    db = tmp_path / "inv.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE runtime_invocations ("
        " id TEXT PRIMARY KEY, surface TEXT NOT NULL, name TEXT NOT NULL,"
        " session_id TEXT, project_id TEXT, parent_id TEXT,"
        " started_at TEXT NOT NULL, completed_at TEXT, duration_ms INTEGER,"
        " status TEXT NOT NULL DEFAULT 'running', error_class TEXT,"
        " error_message TEXT, arg_keys TEXT, classification TEXT,"
        " created_at TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.delenv("ICDEV_OBS_INVOCATIONS", raising=False)
    monkeypatch.setattr(R, "_table_missing", False, raising=False)
    import tools.db.storage as storage
    monkeypatch.setattr(storage, "DB_PATH", str(db), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)
    return db


def _rows(db):
    conn = sqlite3.connect(str(db))
    try:
        return [dict(zip([c[0] for c in cur.description], row))
                for cur in [conn.execute(
                    "SELECT surface, name, status, duration_ms, error_class "
                    "FROM runtime_invocations ORDER BY started_at")]
                for row in cur.fetchall()]
    finally:
        conn.close()


# ------------------------------------------------------- long-lived handles

def test_open_then_close_records_a_completed_invocation(inv_db):
    """The shape the runner needs: opened in one call, closed in another."""
    handle = R.open_invocation(R.SURFACE_AGENT, "task-a")
    assert handle is not None
    assert _rows(inv_db)[-1]["status"] == "running", "should be open in between"

    R.close_invocation(handle, status="ok")
    row = _rows(inv_db)[-1]
    assert row["surface"] == "agent" and row["name"] == "task-a"
    assert row["status"] == "ok"
    assert row["duration_ms"] is not None and row["duration_ms"] >= 0


def test_close_records_a_failure_with_its_exit_code(inv_db):
    handle = R.open_invocation(R.SURFACE_AGENT, "task-b")
    R.close_invocation(handle, status="error", error_class="exit_1")
    row = _rows(inv_db)[-1]
    assert row["status"] == "error" and row["error_class"] == "exit_1"


def test_close_is_safe_with_none(inv_db):
    """Disabled telemetry yields None; closing it must not raise."""
    R.close_invocation(None, status="ok")


def test_disabled_returns_none_rather_than_a_dead_handle(inv_db, monkeypatch):
    monkeypatch.setenv("ICDEV_OBS_INVOCATIONS", "0")
    assert R.open_invocation(R.SURFACE_AGENT, "task-c") is None
    assert not _rows(inv_db)


def test_a_failing_open_still_yields_a_usable_flow(inv_db, monkeypatch):
    """Telemetry must never break dispatch, even if the DB write explodes."""
    def explode(*_a, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(R, "_open", explode)
    monkeypatch.setattr(R, "_close", explode)
    handle = R.open_invocation(R.SURFACE_AGENT, "task-d")
    R.close_invocation(handle, status="ok")   # must not raise


# ------------------------------------------------ the correction being pinned

def _kanban_src() -> str:
    return _KANBAN_SRC.read_text(encoding="utf-8", errors="replace")


def test_dispatch_path_does_not_route_through_execute_agent():
    """The reason #1196's agent coverage was fictional.

    If this ever becomes False the runner has changed shape, and instrumenting
    `execute_agent` might become correct — but until then, instrumenting it is
    instrumenting a function this file never calls.
    """
    src = _kanban_src()
    assert "subprocess.Popen(" in src, "runner no longer spawns its own process"
    assert "execute_agent(" not in src, (
        "kanban.py now calls execute_agent — re-check whether the agent surface "
        "should be instrumented there instead of at the Popen site"
    )


def test_claude_cli_dispatch_opens_an_agent_invocation():
    """The Popen site must open a handle, or the agent surface stays empty."""
    src = _kanban_src()
    dispatch = src.split("def _dispatch_via_claude_cli", 1)[1][:9000]
    assert "_open_agent_invocation(" in dispatch, (
        "the claude-cli dispatch does not open an agent invocation — this is the "
        "primary build path and it would record nothing"
    )
    assert "_SURFACE_AGENT" in dispatch


def test_completion_path_closes_the_agent_invocation():
    src = _kanban_src()
    assert "_close_agent_invocation(" in src
    # Closed on the poll branch, so duration is the agent's own wall-clock.
    completion = src.split("ret = proc.poll()", 1)[1][:1500]
    assert "_close_agent_invocation(" in completion, (
        "invocation is not closed where the subprocess is reaped"
    )


def test_redispatch_supersedes_rather_than_stranding():
    """The #1188 lesson, applied before it can recur here."""
    src = _kanban_src()
    dispatch = src.split("def _dispatch_via_claude_cli", 1)[1][:9000]
    # The close call and its status are on separate lines and the argument
    # itself contains parentheses, so match the span between the call and the
    # open rather than trying to bracket-match in a regex.
    idx = dispatch.find("_close_agent_invocation(")
    assert idx != -1, "re-dispatch does not close the previous handle at all"
    span = dispatch[idx:idx + 400]
    assert 'status="superseded"' in span, (
        "re-dispatch closes the previous handle but not as superseded — "
        "stranded rows will sit in 'running' forever"
    )
    assert span.find("_open_agent_invocation(") != -1, (
        "the supersede must sit immediately before the new open"
    )


def test_every_running_removal_path_is_reconciled():
    """Eight paths remove from _running; one sweep must cover them all."""
    src = _kanban_src()
    check = src.split("def _check_completed", 1)[1][:2500]
    assert "_agent_invocations" in check and "not in _running" in check, (
        "no reconcile sweep — a task removed from _running by a path other than "
        "the poll loop would strand its invocation in 'running'"
    )
