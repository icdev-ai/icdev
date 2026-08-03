# CUI // SP-CTI
"""OPT-62: mid-run message injection for kanban LocalPythonTaskExecutor.

Covers:
1. queue → drain roundtrip via the OPT-61 primitives.
2. _dispatch_via_llm_router._runner loop actually drains the queue
   between LLM calls and feeds injected messages into the next request.
3. POST /api/kanban/tasks/<id>/message returns 409 when the target task
   is not in a running state.
"""
from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.airgap import hook_compat
from tools.genesis.reflexes import kanban


# ────────────────────────────────────────────────────────────────────────────
# 1. queue + drain roundtrip (OPT-61 primitive, OPT-62 consumer)
# ────────────────────────────────────────────────────────────────────────────


def test_message_queue_fifo_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)
    hook_compat.queue_message("task-inj", "run lint after", sender="sov")
    hook_compat.queue_message("task-inj", "also bandit", sender="sov")

    msgs = hook_compat.check_message_queue("task-inj")
    assert len(msgs) == 2
    assert msgs[0]["content"] == "run lint after"
    assert msgs[0]["sender"] == "sov"
    assert msgs[1]["content"] == "also bandit"

    # Drain is one-shot
    assert hook_compat.check_message_queue("task-inj") == []


# ────────────────────────────────────────────────────────────────────────────
# 2. executor loop drains queue between LLM calls
# ────────────────────────────────────────────────────────────────────────────


def _pin_text_only_dispatch(monkeypatch):
    """Pin _dispatch_via_llm_router to the text-only path these tests cover.

    Its first statement is a Phase 3b opt-in: when ``KANBAN_RUBRIC_LOOP`` is
    truthy it hands off to ``_dispatch_via_rubric_loop`` and returns, so the
    injected LLMRouter is never invoked and the drain loop under test never
    runs. The toggle is read from ``os.environ`` at call time, and the kanban
    scheduler exports it — so these tests pass in a plain shell and fail in
    any process that inherits the scheduler's environment, with no fixture
    difference to explain it. Delete it for the duration of the test rather
    than stubbing ``_rubric_loop_enabled``, so the real branch is exercised.
    """
    monkeypatch.delenv("KANBAN_RUBRIC_LOOP", raising=False)


class _FakeResponse:
    def __init__(self, content):
        self.content = content
        self.provider = "fake"
        self.model_id = "fake-model"
        self.input_tokens = 0
        self.output_tokens = 0
        self.duration_ms = 1


class _FakeRouter:
    """Records invocations and queues a message on the first call so the
    second iteration has something to drain — exactly mimicking a user
    pressing the 💬 button mid-run."""

    def __init__(self):
        self.calls = []

    def invoke(self, fn_name, request):
        self.calls.append({
            "fn": fn_name,
            "messages": list(request.messages),
        })
        if len(self.calls) == 1:
            hook_compat.queue_message(
                "task-loop", "also add ruff check", sender="dashboard"
            )
        return _FakeResponse(f"response-{len(self.calls)}")


def test_executor_loop_drains_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)
    _pin_text_only_dispatch(monkeypatch)

    fake_router = _FakeRouter()

    # Patch the router class the _runner imports lazily
    import tools.llm.router as router_mod
    monkeypatch.setattr(
        router_mod, "LLMRouter", lambda *a, **kw: fake_router
    )

    task = {"id": "task-loop", "title": "loop test"}
    task_log = tmp_path / "task-loop.log"
    prompt_path = tmp_path / "task-loop.md"
    prompt_path.write_text("do the thing", encoding="utf-8")

    kanban._dispatch_via_llm_router(
        task, str(prompt_path), "do the thing",
        str(tmp_path), task_log,
    )

    # Wait for handle thread to finish (with a hard timeout)
    handle = kanban._running.get("task-loop")
    assert handle is not None
    for _ in range(50):
        if handle.poll() is not None:
            break
        time.sleep(0.05)
    assert handle.poll() is not None, "runner thread did not finish in time"

    try:
        # Two invocations: initial + one after drain
        assert len(fake_router.calls) == 2, (
            f"expected 2 invocations, got {len(fake_router.calls)}"
        )
        second_msgs = fake_router.calls[1]["messages"]
        assert any(
            "also add ruff check" in (m.get("content") or "")
            for m in second_msgs
        ), f"injected message missing from 2nd request: {second_msgs}"
        # The injected message is tagged with sender
        assert any(
            "dashboard" in (m.get("content") or "")
            for m in second_msgs
        )
        # Log shows the OPT-62 marker
        log_text = task_log.read_text(encoding="utf-8", errors="replace")
        assert "[OPT-62]" in log_text
        assert "iteration 2/10" in log_text
    finally:
        kanban._running.pop("task-loop", None)
        kanban._dispatch_times.pop("task-loop", None)


def test_executor_loop_stops_when_queue_empty(monkeypatch, tmp_path):
    """If no messages are queued, the loop exits after exactly one call."""
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)
    _pin_text_only_dispatch(monkeypatch)

    class _QuietRouter:
        def __init__(self):
            self.calls = []

        def invoke(self, fn_name, request):
            self.calls.append(1)
            return _FakeResponse("done")

    quiet = _QuietRouter()
    import tools.llm.router as router_mod
    monkeypatch.setattr(router_mod, "LLMRouter", lambda *a, **kw: quiet)

    task = {"id": "task-quiet", "title": "quiet"}
    task_log = tmp_path / "task-quiet.log"
    prompt_path = tmp_path / "task-quiet.md"
    prompt_path.write_text("do thing", encoding="utf-8")

    kanban._dispatch_via_llm_router(
        task, str(prompt_path), "do thing", str(tmp_path), task_log,
    )
    handle = kanban._running.get("task-quiet")
    for _ in range(50):
        if handle.poll() is not None:
            break
        time.sleep(0.05)

    try:
        assert len(quiet.calls) == 1
    finally:
        kanban._running.pop("task-quiet", None)
        kanban._dispatch_times.pop("task-quiet", None)


# ────────────────────────────────────────────────────────────────────────────
# 3. POST /api/kanban/tasks/<id>/message — rejection path
# ────────────────────────────────────────────────────────────────────────────


def test_inject_message_rejects_done_task(monkeypatch, tmp_path):
    """Task in 'done' status → 409 Conflict."""
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)

    from tools.dashboard.api.kanban import kanban_api
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(kanban_api)

    # Stub get_connection used by the API module so we don't touch icdev.db
    import tools.dashboard.api.kanban as api_mod

    class _FakeRow(dict):
        def __getitem__(self, k):
            return dict.__getitem__(self, k)

    class _FakeConn:
        def __init__(self, row):
            self._row = row

        def execute(self, sql, params=()):
            class _Cur:
                def __init__(self, row):
                    self._row = row

                def fetchone(self_):
                    return self_._row

            return _Cur(self._row)

        def close(self):
            pass

    def fake_conn_done():
        return _FakeConn(_FakeRow(status="done", title="finished task"))

    monkeypatch.setattr(api_mod, "get_connection", fake_conn_done)

    client = app.test_client()
    r = client.post(
        "/api/kanban/tasks/task-done-xyz/message",
        json={"message": "hello"},
    )
    assert r.status_code == 409
    body = r.get_json()
    assert body["status"] == "done"


def test_inject_message_accepts_running_task(monkeypatch, tmp_path):
    """Task in 'in_progress' → 200 with queue confirmation."""
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)

    from tools.dashboard.api.kanban import kanban_api
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(kanban_api)

    import tools.dashboard.api.kanban as api_mod

    class _FakeRow(dict):
        def __getitem__(self, k):
            return dict.__getitem__(self, k)

    class _FakeConn:
        def __init__(self, row):
            self._row = row

        def execute(self, sql, params=()):
            class _Cur:
                def __init__(self, row):
                    self._row = row

                def fetchone(self_):
                    return self_._row

            return _Cur(self._row)

        def close(self):
            pass

    def fake_conn_running():
        return _FakeConn(_FakeRow(status="in_progress", title="running task"))

    monkeypatch.setattr(api_mod, "get_connection", fake_conn_running)

    client = app.test_client()
    r = client.post(
        "/api/kanban/tasks/task-live-xyz/message",
        json={"message": "also run bandit", "sender": "sovanna"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "queued"
    assert body["sender"] == "sovanna"
    assert "poll_at" in body

    # Verify the message actually landed in the queue file
    msgs = hook_compat.check_message_queue("task-live-xyz")
    assert len(msgs) == 1
    assert msgs[0]["content"] == "also run bandit"


def test_inject_message_rejects_empty_body(monkeypatch, tmp_path):
    monkeypatch.setattr(hook_compat, "MESSAGE_QUEUE_DIR", tmp_path)

    from tools.dashboard.api.kanban import kanban_api
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(kanban_api)

    client = app.test_client()
    r = client.post(
        "/api/kanban/tasks/task-empty/message",
        json={"message": ""},
    )
    assert r.status_code == 400
    assert "required" in r.get_json()["error"]
