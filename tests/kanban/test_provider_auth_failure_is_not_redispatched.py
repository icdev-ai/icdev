# CUI // SP-CTI
"""omx-host-05 -- a non-retryable provider AUTH failure must not loop.

MEASURED on the Omarchy laptop board 2026-10-12: task lap-e2e-01 on
``opencode_cli`` / ``ollama-cloud/kimi-k3`` died in ~2s on every dispatch with

    [LLMTaskHandle EXCEPTION] RuntimeError: opencode_cli did not complete
    lap-e2e-01: APIError: Unauthorized

and the adapter stream said ``"statusCode":401,"isRetryable":false``. The
non-zero-exit branch of ``_check_completed`` moved it straight back to
``backlog`` -- no failure_count, no last_failure_reason, nothing surfaced -- and
the backlog query's 2-minute ``updated_at`` window was the only thing between
it and the next dispatch: ~40 cycles in 60 minutes.

These tests drive the REAL completion path: a fake adapter is dispatched
through ``_dispatch_via_agent_adapter`` (so the RuntimeError text is the one the
runner itself writes), and ``_check_completed`` reaps it against a throwaway
SQLite board. No network, no LLM, no live database.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tools.genesis.reflexes import kanban as k

#: A credential-shaped string that must NEVER reach the board or a notification.
SECRET = "sk-ollama-THIS-IS-A-FAKE-KEY-0123456789"

OPENCODE_401_STREAM = json.dumps({
    "type": "error",
    "error": {"name": "APIError", "data": {
        "message": "Unauthorized", "statusCode": 401, "isRetryable": False,
        "responseHeaders": {"authorization": f"Bearer {SECRET}"},
        "providerID": "ollama-cloud"}},
})
OPENCODE_503_STREAM = json.dumps({
    "type": "error",
    "error": {"name": "APIError", "data": {
        "message": "Service Unavailable", "statusCode": 503, "isRetryable": True}},
})


# ── classifier ──────────────────────────────────────────────────────────────
class TestClassifier:
    @pytest.mark.parametrize("text,status", [
        (OPENCODE_401_STREAM, 401),
        ("RuntimeError: opencode_cli did not complete t: APIError: Unauthorized", 401),
        ('API Error: 401 {"type":"error","error":{"type":"authentication_error",'
         '"message":"invalid x-api-key"}}', 401),
        ("HTTP 403 Forbidden", 403),
        ('{"statusCode":403,"isRetryable":false}', 403),
    ])
    def test_auth_failures_are_recognised(self, text, status):
        hit = k.classify_provider_auth_failure(text)
        assert hit is not None and hit[0] == status
        assert SECRET not in hit[1]

    @pytest.mark.parametrize("text", [
        OPENCODE_503_STREAM,
        "ConnectionResetError: [Errno 104] Connection reset by peer",
        "APIError: Too Many Requests",
        "AssertionError: 3 != 4",
        'File "x.py", line 401, in invoke',
        "",
    ])
    def test_transient_and_ordinary_failures_are_not(self, text):
        assert k.classify_provider_auth_failure(text) is None

    def test_an_explicitly_retryable_401_is_left_to_the_retry_path(self):
        assert k.classify_provider_auth_failure(
            '{"statusCode":401,"isRetryable":true}') is None


# ── a throwaway board ───────────────────────────────────────────────────────
class _Conn:
    """sqlite3 behind the ``%s`` paramstyle the reflex writes."""

    def __init__(self, path):
        self._c = sqlite3.connect(str(path))
        self._c.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._c.commit()

    def close(self):
        self._c.commit()
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _Adapter:
    name = "opencode_cli"

    def __init__(self, raw, error):
        self._raw, self._error = raw, error

    def invoke(self, session):
        return SimpleNamespace(completed=False, output="", error=self._error,
                               structured={"raw_output": self._raw})


@pytest.fixture
def board(tmp_path, monkeypatch):
    db = tmp_path / "board.db"
    c = sqlite3.connect(str(db))
    c.execute(
        "CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, title TEXT, task_type TEXT, "
        "priority TEXT, status TEXT, description TEXT, executor_type TEXT, "
        "failure_count INTEGER DEFAULT 0, last_failure_reason TEXT, "
        "last_failure_at TEXT, updated_at TEXT)"
    )
    c.commit()
    c.close()

    moves, surfaced = [], []

    def _move(tid, status, actor="scheduler", reason=None, **_kw):
        moves.append((tid, status, reason or ""))
        with _Conn(db) as conn:
            conn.execute("UPDATE kanban_tasks SET status = %s WHERE id = %s", (status, tid))

    monkeypatch.setattr(k, "get_connection", lambda *a, **kw: _Conn(db))
    monkeypatch.setattr(k, "PROMPT_DIR", tmp_path)
    monkeypatch.setattr(k, "_move_task", _move)
    monkeypatch.setattr(k, "_get_task_timeout", lambda tid: 900)
    monkeypatch.setattr(k, "_close_execution", lambda *a, **kw: None)
    monkeypatch.setattr(k, "_send_notification", lambda *a, **kw: None)
    monkeypatch.setattr(k, "_record_dispatch_pid", lambda *a, **kw: None)
    monkeypatch.setattr(k, "_open_agent_invocation", lambda *a, **kw: None)
    monkeypatch.setattr(k, "_close_agent_invocation", lambda *a, **kw: None)
    monkeypatch.setattr(k, "_agent_session", lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(k, "_handle_provider_exhaustion", lambda *a, **kw: False)
    monkeypatch.setattr(k, "_hand_parked_task_to_pr_watcher", lambda *a, **kw: None)
    monkeypatch.setattr(k, "_write_handoff_record", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(
        k, "_audit_agent_execution",
        lambda event, tid, **d: surfaced.append(("audit", tid, d))
        if d.get("failure_class") == "provider_auth" else None,
    )
    import tools.agents.dispatch_policy as dp
    import tools.notifications.adapters.telegram as tg
    monkeypatch.setattr(dp, "enforce", lambda *a, **kw: None)
    monkeypatch.setattr(tg, "send", lambda title, body, **kw: surfaced.append(
        ("telegram", title, body)))
    monkeypatch.setenv("ICDEV_OPENCODE_MODEL", "ollama-cloud/kimi-k3")
    for d in (k._running, k._dispatch_times, k._dispatch_provider,
              k._provider_auth_surfaced):
        d.clear()
    yield SimpleNamespace(db=db, moves=moves, surfaced=surfaced, tmp=tmp_path)
    for d in (k._running, k._dispatch_times, k._dispatch_provider,
              k._provider_auth_surfaced):
        d.clear()


def _seed(board, tid):
    with _Conn(board.db) as conn:
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, task_type, priority, status, "
            "description, executor_type) VALUES (%s, %s, 'fix', 'high', "
            "'in_progress', 'x', 'opencode_cli')", (tid, tid))


def _run_once(board, tid, raw, error):
    if not _row(board, tid):
        _seed(board, tid)
    k._dispatch_via_agent_adapter(_Adapter(raw, error), {"id": tid}, "", "do it",
                                  str(board.tmp), board.tmp / f"{tid}.log")
    k._running[tid].wait(timeout=10)
    k._check_completed()


def _row(board, tid):
    with _Conn(board.db) as conn:
        r = conn.execute("SELECT * FROM kanban_tasks WHERE id = %s", (tid,)).fetchone()
    return dict(r) if r else None


# ── behaviour ───────────────────────────────────────────────────────────────
class TestAuthFailureIsParked:
    def test_401_is_held_not_requeued_and_says_why_without_the_key(self, board):
        _run_once(board, "lap-e2e-01", OPENCODE_401_STREAM, "APIError: Unauthorized")

        (tid, status, reason), = board.moves
        assert status != "backlog", "a 401 must not go straight back to the dispatch queue"
        row = _row(board, "lap-e2e-01")
        assert row["failure_count"] == 1
        lfr = row["last_failure_reason"]
        assert "opencode_cli" in lfr and "ollama-cloud/kimi-k3" in lfr and "401" in lfr
        assert "\n" not in lfr
        for text in (lfr, reason, *(json.dumps(s) for s in board.surfaced)):
            assert SECRET not in text and "Bearer" not in text

    def test_401_is_not_redispatchable_inside_the_cooldown(self, board):
        _run_once(board, "lap-e2e-01", OPENCODE_401_STREAM, "APIError: Unauthorized")
        assert k._check_token_exhausted_tasks() == []
        # one cycle of churn: in_progress -> parked, and nothing else.
        assert [m[1] for m in board.moves] == ["token_exhausted"]

    def test_an_auth_hold_never_counts_toward_decomposition(self, board):
        """The hold rides the token_exhausted lane; a credential is not task SIZE."""
        _run_once(board, "lap-e2e-01", OPENCODE_401_STREAM, "APIError: Unauthorized")
        (_tid, _status, reason), = board.moves
        with _Conn(board.db) as conn:
            conn.execute("CREATE TABLE kanban_status_transitions "
                         "(task_id TEXT, to_status TEXT, reason TEXT)")
            conn.execute("INSERT INTO kanban_status_transitions VALUES (%s, %s, %s)",
                         ("lap-e2e-01", "token_exhausted", reason))
        assert k._lifetime_exhaustion_count("lap-e2e-01") == 0

    def test_same_adapter_provider_on_n_tasks_surfaces_once_per_hour(self, board):
        for tid in ("lap-e2e-01", "lap-e2e-02", "lap-e2e-03"):
            _run_once(board, tid, OPENCODE_401_STREAM, "APIError: Unauthorized")
        assert len([s for s in board.surfaced if s[0] == "audit"]) == 1
        assert len([s for s in board.surfaced if s[0] == "telegram"]) == 1
        assert all(_row(board, t)["failure_count"] == 1
                   for t in ("lap-e2e-01", "lap-e2e-02", "lap-e2e-03"))


class TestTransientFailureStillRetries:
    def test_503_keeps_todays_backlog_retry(self, board):
        _run_once(board, "t-503", OPENCODE_503_STREAM, "APIError: Service Unavailable")
        (tid, status, reason), = board.moves
        assert status == "backlog"
        assert _row(board, "t-503")["failure_count"] == 0
        assert board.surfaced == []
