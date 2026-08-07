# [TEMPLATE: CUI // SP-CTI]
"""The four agent_execution_* types are emitted by the runner, and admitted.

The reachability gate in tests/test_agent_event_type_reachability.py proves an
emit site *exists*. That is not enough on its own: an emit site whose event type
the CHECK constraint rejects writes nothing, and log_event swallows the failure
and returns -1, so the caller cannot tell. That is precisely how the govcon
audit writes stayed invisible (see tests/test_audit_event_type_parity.py).

So these tests close the other half:

* the runner's helper actually calls log_event, with the type we expect
* both retry counters emit, because a retry is the one lifecycle moment the
  dispatch site cannot infer
* a real INSERT against the generated CHECK constraint lands a row for each of
  the four --- not a mock; the constraint either admits them or it does not
* a failing audit path never breaks dispatch

Structural assertions cover the dispatch and completion sites, matching the
approach in tests/test_agent_surface_dispatch.py: driving a real subprocess
dispatch would need a claude binary, a worktree and a board.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tools.audit import audit_logger
from tools.audit.audit_logger import event_type_check_sql
from tools.genesis.reflexes import kanban as K

_KANBAN_SRC = (Path(__file__).resolve().parent.parent
               / "tools" / "genesis" / "reflexes" / "kanban.py")

EXECUTION_TYPES = (
    "agent_execution_started",
    "agent_execution_completed",
    "agent_execution_failed",
    "agent_execution_retried",
)


@pytest.fixture()
def emitted(monkeypatch):
    """Capture log_event calls made through the runner's helper.

    Patched on the module rather than on the imported object: the helper does a
    lazy `from tools.audit.audit_logger import log_event` inside the function
    body, so the attribute is resolved at call time and a module-level patch is
    what it sees.
    """
    calls = []

    def _recorder(**kwargs):
        assert kwargs["event_type"] in audit_logger.VALID_EVENT_TYPES, (
            f"{kwargs['event_type']} would be rejected by the CHECK constraint"
        )
        calls.append(kwargs)
        return len(calls)

    monkeypatch.setattr(audit_logger, "log_event", _recorder)
    return calls


@pytest.fixture()
def prompt_dir(tmp_path, monkeypatch):
    """Redirect the runner's retry/timeout counter files away from the repo."""
    monkeypatch.setattr(K, "PROMPT_DIR", tmp_path / "kanban", raising=False)
    return tmp_path / "kanban"


# ---------------------------------------------------------------- the helper

def test_helper_emits_the_requested_type(emitted):
    K._audit_agent_execution("agent_execution_started", "task-1", pid=4242)

    assert len(emitted) == 1
    call = emitted[0]
    assert call["event_type"] == "agent_execution_started"
    assert call["details"]["task_id"] == "task-1"
    assert call["details"]["pid"] == 4242
    assert call["classification"] == "CUI"


def test_a_broken_audit_path_never_breaks_dispatch(monkeypatch):
    """An agent that cannot build because audit_trail is locked is far worse
    than a missing row, so the helper swallows everything."""
    def explode(**_kwargs):
        raise RuntimeError("audit_trail is locked")

    monkeypatch.setattr(audit_logger, "log_event", explode)
    K._audit_agent_execution("agent_execution_failed", "task-2")  # must not raise


# ------------------------------------------------------------ the retry sites

def test_token_retry_emits_retried_with_its_attempt_number(emitted, prompt_dir):
    assert K._increment_retry_count("task-3") == 1
    assert K._increment_retry_count("task-3") == 2

    retried = [c for c in emitted if c["event_type"] == "agent_execution_retried"]
    assert [c["details"]["attempt"] for c in retried] == [1, 2]
    assert {c["details"]["reason"] for c in retried} == {"token_exhaustion"}


def test_timeout_retry_emits_retried_and_says_so(emitted, prompt_dir):
    K._increment_timeout_count("task-4")

    retried = [c for c in emitted if c["event_type"] == "agent_execution_retried"]
    assert len(retried) == 1
    assert retried[0]["details"]["reason"] == "timeout"
    assert retried[0]["details"]["task_id"] == "task-4"


def test_the_counters_still_count(emitted, prompt_dir):
    """Guard the instrumentation: the emit must not have changed the behaviour
    the runner's backoff ladder depends on."""
    assert [K._increment_timeout_count("task-5") for _ in range(3)] == [1, 2, 3]
    assert K._get_timeout_count("task-5") == 3


# ------------------------------------------------- the constraint admits them

@pytest.mark.parametrize("event_type", EXECUTION_TYPES)
def test_the_generated_constraint_admits_the_type(tmp_path, event_type):
    """A real INSERT, against the real generated CHECK. No mock.

    log_event returns -1 on a rejected write and does not raise, so a type the
    constraint refuses is indistinguishable from a successful write at the call
    site. Asserting on the returned id alone would therefore pass for a type
    that was silently dropped --- the row count is what settles it.
    """
    db = tmp_path / "audit.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE audit_trail ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  project_id TEXT, event_type TEXT NOT NULL, actor TEXT NOT NULL,"
        "  action TEXT NOT NULL, details TEXT, affected_files TEXT,"
        "  classification TEXT, ip_address TEXT, session_id TEXT,"
        f"  {event_type_check_sql()})"
    )
    conn.commit()
    conn.close()

    entry_id = audit_logger.log_event(
        event_type=event_type,
        actor="kanban-runner",
        action=f"{event_type} for task-6",
        db_path=db,
    )
    assert entry_id != -1, f"{event_type} was rejected and the failure swallowed"

    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT event_type FROM audit_trail WHERE event_type = ?", (event_type,)
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


# ------------------------------------------------- dispatch + completion sites

def _kanban_src() -> str:
    return _KANBAN_SRC.read_text(encoding="utf-8", errors="replace")


def test_dispatch_emits_started_where_the_process_is_spawned():
    """Beside the runtime_invocations open, on the Popen path — not on
    execute_agent, which this runner never calls (#1196 / #1304)."""
    dispatch = _kanban_src().split("def _dispatch_via_claude_cli", 1)[1][:9000]
    assert '"agent_execution_started"' in dispatch, (
        "the claude-cli dispatch does not emit agent_execution_started — this is "
        "the primary build path, so the type would stay dead"
    )
    assert dispatch.find('"agent_execution_started"') > dispatch.find("_open_agent_invocation("), (
        "started must be emitted after the invocation opens, so a failed open "
        "cannot leave an audit row claiming an execution that never began"
    )


def test_completion_emits_completed_or_failed_on_the_exit_code():
    completion = _kanban_src().split("ret = proc.poll()", 1)[1][:1500]
    assert '"agent_execution_completed" if ret == 0 else "agent_execution_failed"' in completion, (
        "the completion path does not split on the exit code — agent_execution_failed "
        "is the type an auditor actually needs and it would never be written"
    )
