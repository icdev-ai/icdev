# CUI // SP-CTI
"""SAG run observability (hgx-obs-01) — recording, joining, and the replay flag.

Three claims are under test, one per acceptance criterion:

  1. A SAG tool call dispatched through ``tools/agent_runtime/dispatch.py`` lands
     in ``runtime_invocations`` with ``surface="agent"``, so
     ``icdev runtime top --surface agent`` can see it.
  2. A run's spans join to its correlation id.
  3. With ``ICDEV_OBS_REPLAY`` off — the default — no argument VALUE and no tool
     RESULT is persisted anywhere.

Claim 3 is asserted the strict way: the test puts a distinctive secret in both
the tool arguments and the tool result and then scans the ENTIRE persisted row
for any fragment of it, rather than checking that two named columns are NULL.
A future column that started quietly storing a preview would fail this test,
which is the point — the privacy default is a property of the row, not of two
columns someone remembered to check.
"""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from tests._sql_compat import connect as _translating_connect
from tools.agent_runtime.discovery import ToolSpec
from tools.agent_runtime.dispatch import make_handler
from tools.observability import agent_trace, invocation_recorder

# A value that cannot occur by accident, so "does it appear anywhere in the row"
# is a meaningful question. Deliberately NOT matching a redactor pattern: this
# is about whether values are stored at all, not about whether they are cleaned.
SECRET = "zq7-marker-value-do-not-persist"

_RUNTIME_INVOCATIONS_DDL = """
CREATE TABLE IF NOT EXISTS runtime_invocations (
    id TEXT PRIMARY KEY,
    surface TEXT NOT NULL,
    name TEXT NOT NULL,
    session_id TEXT,
    project_id TEXT,
    parent_id TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER,
    status TEXT NOT NULL DEFAULT 'running',
    error_class TEXT,
    error_message TEXT,
    arg_keys TEXT,
    correlation_id TEXT,
    arg_values TEXT,
    result_preview TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS otel_spans (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'INTERNAL',
    start_time TEXT NOT NULL,
    end_time TEXT,
    duration_ms INTEGER DEFAULT 0,
    status_code TEXT DEFAULT 'UNSET',
    status_message TEXT,
    attributes TEXT,
    events TEXT,
    agent_id TEXT,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI'
);
"""


@pytest.fixture
def telemetry_db(tmp_path, monkeypatch):
    """A real SQLite database behind the real ``get_connection``.

    The recorder is not stubbed: these tests exercise the statements it actually
    issues, because the failure this feature exists to prevent — a column that
    does not exist in the live schema, so the INSERT raises and is swallowed by
    the recorder's own except — is invisible to a stubbed writer.
    """
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_RUNTIME_INVOCATIONS_DDL)
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_OBS_INVOCATIONS", "1")
    monkeypatch.delenv("ICDEV_OBS_REPLAY", raising=False)

    # Both latches are process-level caches. Leaving them set would make a later
    # test read another test's database shape.
    monkeypatch.setattr(invocation_recorder, "_table_missing", False, raising=False)
    monkeypatch.setattr(invocation_recorder, "_optional_columns", None, raising=False)
    return db_path


def _rows(db_path, table="runtime_invocations"):
    conn = _translating_connect(db_path)
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]  # nosec B608 — test-local table names
    finally:
        conn.close()


def _spec(name="read_thing", read_only=True):
    return ToolSpec(name=name, schema={}, source="decorated", read_only=read_only,
                    module="", handler="")


def _allow(_name, _input, _read_only):
    return True, ""


# ---------------------------------------------------------------------------
# AC 1 — SAG tool calls reach runtime_invocations with surface="agent"
# ---------------------------------------------------------------------------
def test_dispatch_records_agent_surface(telemetry_db):
    spec = _spec()
    spec.callable = lambda path: f"contents of {path}"
    handler = make_handler(spec, gate=_allow)

    assert handler({"path": "notes.md"}, None) == "contents of notes.md"

    rows = _rows(telemetry_db)
    assert len(rows) == 1
    assert rows[0]["surface"] == invocation_recorder.SURFACE_AGENT
    assert rows[0]["name"] == "read_thing"
    assert rows[0]["status"] == "ok"
    assert rows[0]["duration_ms"] is not None


def test_runtime_top_surface_agent_reports_the_call(telemetry_db):
    spec = _spec()
    spec.callable = lambda path: "ok"
    handler = make_handler(spec, gate=_allow)
    handler({"path": "a"}, None)
    handler({"path": "b"}, None)

    summary = invocation_recorder.summary(surface=invocation_recorder.SURFACE_AGENT)
    assert [(r["name"], r["calls"]) for r in summary] == [("read_thing", 2)]


def test_arg_key_names_are_recorded_but_never_the_values(telemetry_db):
    spec = _spec()
    spec.callable = lambda path, token: "ok"
    handler = make_handler(spec, gate=_allow)
    handler({"path": "notes.md", "token": SECRET}, None)

    row = _rows(telemetry_db)[0]
    assert sorted(json.loads(row["arg_keys"])) == ["path", "token"]
    assert SECRET not in json.dumps(row)


def test_blocked_call_is_recorded_as_an_error(telemetry_db):
    spec = _spec(name="write_thing", read_only=False)

    def deny(_name, _input, _read_only):
        return False, "mutation not approved"

    out = make_handler(spec, gate=deny)({"path": "x"}, None)
    assert out.startswith("blocked:")

    row = _rows(telemetry_db)[0]
    assert row["status"] == "error"
    assert row["error_class"] == "SafetyGateBlocked"
    assert "mutation not approved" in row["error_message"]


def test_failing_tool_is_recorded_as_an_error(telemetry_db):
    spec = _spec(name="broken")

    def boom(**_kwargs):
        raise ValueError("kaboom")

    spec.callable = boom
    out = make_handler(spec, gate=_allow)({}, None)
    assert "kaboom" in out

    row = _rows(telemetry_db)[0]
    assert row["status"] == "error"
    assert row["error_class"] == "ValueError"


def test_recording_never_breaks_the_tool_call(telemetry_db, monkeypatch):
    """A telemetry failure must cost the row, not the result."""
    def explode(*_a, **_k):
        raise RuntimeError("telemetry backend down")

    monkeypatch.setattr(invocation_recorder, "_open", explode)
    monkeypatch.setattr(invocation_recorder, "_close", explode)

    spec = _spec()
    spec.callable = lambda path: "still works"
    assert make_handler(spec, gate=_allow)({"path": "x"}, None) == "still works"


def test_ambient_correlation_id_is_written_to_the_row(telemetry_db):
    spec = _spec()
    spec.callable = lambda path: "ok"
    handler = make_handler(spec, gate=_allow)

    with agent_trace.correlation_scope("run-abc-123"):
        handler({"path": "x"}, None)

    assert _rows(telemetry_db)[0]["correlation_id"] == "run-abc-123"


# ---------------------------------------------------------------------------
# AC 3 — the replay flag is off by default, and off means nothing is stored
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe", " "])
def test_replay_is_off_for_anything_but_an_affirmative(monkeypatch, value):
    monkeypatch.setenv("ICDEV_OBS_REPLAY", value)
    assert invocation_recorder.replay_enabled() is False


def test_replay_is_off_when_unset(monkeypatch):
    monkeypatch.delenv("ICDEV_OBS_REPLAY", raising=False)
    assert invocation_recorder.replay_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_replay_is_on_only_for_an_affirmative(monkeypatch, value):
    monkeypatch.setenv("ICDEV_OBS_REPLAY", value)
    assert invocation_recorder.replay_enabled() is True


def test_flag_off_persists_no_argument_value_and_no_result(telemetry_db):
    spec = _spec()
    spec.callable = lambda token: f"result containing {SECRET}"
    make_handler(spec, gate=_allow)({"token": SECRET}, None)

    row = _rows(telemetry_db)[0]
    assert row["arg_values"] is None
    assert row["result_preview"] is None
    # The strict form: not one fragment of the secret anywhere in the row —
    # not a truncation, not a hash, not a length.
    assert SECRET not in json.dumps(row)
    assert "result containing" not in json.dumps(row)


def test_flag_off_helpers_return_none(monkeypatch):
    monkeypatch.delenv("ICDEV_OBS_REPLAY", raising=False)
    assert invocation_recorder.extract_arg_values({"token": SECRET}) is None
    assert invocation_recorder.capture_result(f"has {SECRET}") is None


def test_flag_on_persists_values_and_results(telemetry_db, monkeypatch):
    monkeypatch.setenv("ICDEV_OBS_REPLAY", "1")
    spec = _spec()
    spec.callable = lambda token: f"result containing {SECRET}"
    make_handler(spec, gate=_allow)({"token": SECRET}, None)

    row = _rows(telemetry_db)[0]
    assert SECRET in row["arg_values"]
    assert SECRET in row["result_preview"]


def test_flag_on_records_a_refusal_as_the_result(telemetry_db, monkeypatch):
    """A replay must show the refusal the model saw, not a gap where it was."""
    monkeypatch.setenv("ICDEV_OBS_REPLAY", "1")
    spec = _spec(name="write_thing", read_only=False)

    def deny(_name, _input, _read_only):
        return False, "mutation not approved"

    make_handler(spec, gate=deny)({"path": "x"}, None)
    assert "blocked: mutation not approved" in _rows(telemetry_db)[0]["result_preview"]


def test_flag_on_still_redacts(monkeypatch):
    """Replay widens what is stored; it does not switch the redactor off."""
    monkeypatch.setenv("ICDEV_OBS_REPLAY", "1")
    values = invocation_recorder.extract_arg_values(
        {"key": "AKIAIOSFODNN7EXAMPLE", "who": "person@example.mil"}
    )
    assert values is not None
    assert "AKIAIOSFODNN7EXAMPLE" not in values
    assert "person@example.mil" not in values
    assert "[REDACTED]" in values


def test_flag_on_truncates_a_large_result(monkeypatch):
    monkeypatch.setenv("ICDEV_OBS_REPLAY", "1")
    captured = invocation_recorder.capture_result("x" * 50_000)
    assert captured is not None
    assert len(captured) <= invocation_recorder._MAX_RESULT_CHARS


def test_missing_columns_degrade_instead_of_losing_every_row(tmp_path, monkeypatch):
    """A database that predates the migration still records invocations.

    Without the column probe, every INSERT would name ``correlation_id`` on a
    table that does not have it, raise, and be swallowed by the recorder's own
    except — turning a missing migration into total telemetry loss rather than
    into three absent columns.
    """
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE runtime_invocations ("
        " id TEXT PRIMARY KEY, surface TEXT NOT NULL, name TEXT NOT NULL,"
        " session_id TEXT, project_id TEXT, parent_id TEXT, started_at TEXT NOT NULL,"
        " completed_at TEXT, duration_ms INTEGER, status TEXT, error_class TEXT,"
        " error_message TEXT, arg_keys TEXT, classification TEXT, created_at TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(invocation_recorder, "_table_missing", False, raising=False)
    monkeypatch.setattr(invocation_recorder, "_optional_columns", None, raising=False)

    spec = _spec()
    spec.callable = lambda path: "ok"
    make_handler(spec, gate=_allow)({"path": "x"}, None)

    rows = _rows(db_path)
    assert len(rows) == 1
    assert rows[0]["surface"] == "agent"


# ---------------------------------------------------------------------------
# AC 2 — a run's spans join to its correlation id
# ---------------------------------------------------------------------------
class _FakeSpan:
    def __init__(self, name, parent, kind, attributes):
        self.name = name
        self.trace_id = getattr(parent, "trace_id", "") if parent else ""
        self.attributes = dict(attributes or {})
        self.status = "UNSET"
        self.ended = False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def set_status(self, code, message=""):
        self.status = code

    def add_event(self, name, attributes=None):
        pass

    def end(self):
        self.ended = True

    def _raw_status_code(self):
        return self.status


class _FakeTracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, parent=None, kind="INTERNAL", attributes=None):
        span = _FakeSpan(name, parent, kind, attributes)
        self.spans.append(span)
        return span

    def get_active_span(self):
        return None

    def flush(self):
        pass


@pytest.fixture
def fake_tracer(monkeypatch):
    from tools import observability

    tracer = _FakeTracer()
    monkeypatch.setattr(observability, "get_tracer", lambda: tracer)
    return tracer


def test_turn_tracer_emits_one_span_per_turn(fake_tracer):
    tracer = agent_trace.TurnTracer("run-1", session_id="sess-1")
    for turn in range(3):
        tracer.begin(turn)
    tracer.finish()

    assert [s.name for s in fake_tracer.spans] == ["agent.turn"] * 3
    assert all(s.ended for s in fake_tracer.spans)
    assert all(s.attributes[agent_trace.CORRELATION_ATTR] == "run-1"
               for s in fake_tracer.spans)
    assert [s.attributes["agent.turn"] for s in fake_tracer.spans] == [0, 1, 2]


def test_turn_spans_share_one_trace_id(fake_tracer):
    tracer = agent_trace.TurnTracer("run-1")
    tracer.begin(0)
    tracer.begin(1)
    tracer.finish()
    assert {s.trace_id for s in fake_tracer.spans} == {tracer.trace_id}


def test_turn_tracer_finish_is_idempotent(fake_tracer):
    tracer = agent_trace.TurnTracer("run-1")
    tracer.begin(0)
    tracer.finish()
    tracer.finish()  # every loop exit path calls it; a second call must be safe
    assert len(fake_tracer.spans) == 1


def test_turn_tracer_survives_a_broken_tracer(monkeypatch):
    from tools import observability

    class _Broken:
        def start_span(self, *_a, **_k):
            raise RuntimeError("tracer misconfigured")

    monkeypatch.setattr(observability, "get_tracer", lambda: _Broken())
    tracer = agent_trace.TurnTracer("run-1")
    tracer.begin(0)          # must not raise
    tracer.annotate(x=1)
    tracer.error("nope")
    tracer.finish()


def test_trace_id_reshapes_a_uuid_and_hashes_anything_else():
    assert agent_trace.trace_id_for("4bf92f35-77b5-8400-0000-00000000000a") == (
        "4bf92f3577b58400000000000000000a"
    )
    hashed = agent_trace.trace_id_for("kanban-task-hgx-obs-01")
    assert len(hashed) == 32
    assert int(hashed, 16) >= 0  # valid hex
    assert hashed == agent_trace.trace_id_for("kanban-task-hgx-obs-01")


def _insert_span(db_path, span_id, name, attributes, trace_id="t1"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO otel_spans (id, trace_id, name, start_time, duration_ms, "
        "status_code, attributes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (span_id, trace_id, name, f"2026-08-08T12:00:{span_id[-2:]}", 5, "OK",
         json.dumps(attributes)),
    )
    conn.commit()
    conn.close()


def test_spans_for_correlation_joins_turn_and_router_spans(telemetry_db):
    run = "run-join-me"
    _insert_span(telemetry_db, "span-01", "agent.turn",
                 {agent_trace.CORRELATION_ATTR: run, "agent.turn": 0})
    _insert_span(telemetry_db, "span-02", "gen_ai.invoke",
                 {agent_trace.CORRELATION_ATTR: run,
                  "gen_ai.request.model": "some-model"})
    _insert_span(telemetry_db, "span-03", "agent.turn",
                 {agent_trace.CORRELATION_ATTR: "a-different-run"})

    joined = agent_trace.spans_for_correlation(run)
    assert [s["id"] for s in joined] == ["span-01", "span-02"]
    assert joined[1]["attributes"]["gen_ai.request.model"] == "some-model"


def test_spans_for_correlation_rejects_an_incidental_text_match(telemetry_db):
    """The LIKE narrows the scan; the attribute is what decides membership."""
    run = "run-xyz"
    _insert_span(telemetry_db, "span-04", "gen_ai.invoke",
                 {"icdev.llm_function": f"mentions {run} in another field"})
    assert agent_trace.spans_for_correlation(run) == []


def test_spans_for_correlation_is_empty_without_an_id(telemetry_db):
    assert agent_trace.spans_for_correlation("") == []


# ---------------------------------------------------------------------------
# `icdev runtime trace <correlation-id>` — the join, as a command
# ---------------------------------------------------------------------------
def test_runtime_trace_renders_the_run(telemetry_db, capsys):
    from tools.cli import runtime as runtime_cli

    run = "run-cli-01"
    _insert_span(telemetry_db, "span-11", "agent.turn",
                 {agent_trace.CORRELATION_ATTR: run, "agent.turn": 0,
                  "agent.tool_call_count": 2})
    _insert_span(telemetry_db, "span-12", "gen_ai.invoke",
                 {agent_trace.CORRELATION_ATTR: run,
                  "gen_ai.request.model": "a-model",
                  "icdev.llm_function": "code_generation"})

    assert runtime_cli.main(["trace", run]) == 0
    out = capsys.readouterr().out
    assert "agent.turn" in out
    assert "gen_ai.invoke" in out
    assert "a-model" in out
    assert "2 span(s)  1 turn(s)" in out


def test_runtime_trace_json_is_machine_readable(telemetry_db, capsys):
    from tools.cli import runtime as runtime_cli

    run = "run-cli-02"
    _insert_span(telemetry_db, "span-13", "agent.turn",
                 {agent_trace.CORRELATION_ATTR: run, "agent.turn": 0})

    assert runtime_cli.main(["trace", run, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [s["name"] for s in payload] == ["agent.turn"]
    assert payload[0]["attributes"][agent_trace.CORRELATION_ATTR] == run


def test_runtime_trace_names_the_backend_when_empty(telemetry_db, capsys):
    """An empty result and a wrong database must not look identical."""
    from tools.cli import runtime as runtime_cli

    assert runtime_cli.main(["trace", "no-such-run"]) == 0
    assert "no spans recorded" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Correlation propagation across the loop's thread pool
# ---------------------------------------------------------------------------
def test_correlation_scope_sets_and_restores():
    assert agent_trace.current_correlation_id() == ""
    with agent_trace.correlation_scope("run-9"):
        assert agent_trace.current_correlation_id() == "run-9"
    assert agent_trace.current_correlation_id() == ""


def test_submit_with_context_carries_the_correlation_into_a_worker():
    """A bare submit loses it; that is the whole reason the helper exists."""
    import concurrent.futures as futures

    with futures.ThreadPoolExecutor(max_workers=1) as executor:
        with agent_trace.correlation_scope("run-threaded"):
            plain = executor.submit(agent_trace.current_correlation_id).result()
            carried = agent_trace.submit_with_context(
                executor, agent_trace.current_correlation_id
            ).result()

    assert plain == ""
    assert carried == "run-threaded"


def test_submit_with_context_still_runs_the_call_if_propagation_fails(monkeypatch):
    import concurrent.futures as futures

    monkeypatch.setattr(agent_trace.contextvars, "copy_context",
                        lambda: (_ for _ in ()).throw(RuntimeError("no context")))
    with futures.ThreadPoolExecutor(max_workers=1) as executor:
        assert agent_trace.submit_with_context(executor, lambda: "ran").result() == "ran"


def test_a_parallel_tool_call_records_against_its_run(telemetry_db):
    """End to end: the loop's pool worker records with the run's correlation id."""
    import concurrent.futures as futures

    spec = _spec()
    spec.callable = lambda path: "ok"
    handler = make_handler(spec, gate=_allow)

    # One worker, two submissions: enough to prove the context crosses the
    # thread boundary, without racing two writers on one SQLite file. The
    # recorder is best-effort by design, so a lock contention there would drop a
    # row and make this test flaky about something it is not testing.
    with futures.ThreadPoolExecutor(max_workers=1) as executor:
        with agent_trace.correlation_scope("run-parallel"):
            futs = [
                agent_trace.submit_with_context(executor, handler, {"path": p}, None)
                for p in ("a", "b")
            ]
            for fut in futs:
                fut.result()

    rows = _rows(telemetry_db)
    assert len(rows) == 2
    assert {r["correlation_id"] for r in rows} == {"run-parallel"}


# ---------------------------------------------------------------------------
# The loop hands its correlation id to the router
# ---------------------------------------------------------------------------
class _StubResponse:
    content = "done"
    tool_calls: list = []
    stop_reason = "end_turn"
    input_tokens = 3
    output_tokens = 4
    cost_usd = 0.0
    model_id = "stub-model"
    provider = "stub"


class _StubProvider:
    provider_name = "stub"


class _RouterBase:
    """Enough router for ``_check_tool_support`` to pass the capability guard."""

    def get_provider_for_function(self, _function):
        return _StubProvider(), "stub-model", {"supports_tools": True}


class _StubRouter(_RouterBase):
    """Captures the request so the correlation wiring can be asserted."""

    def __init__(self):
        self.requests = []

    def invoke(self, _function, request):
        self.requests.append(request)
        return _StubResponse()


def test_loop_stamps_its_correlation_id_onto_every_llm_request(fake_tracer):
    from icdev.tools.llm.agent_loop import run_agent_loop

    router = _StubRouter()
    result = run_agent_loop(
        router, system_prompt="s", user_prompt="u", tools=[], tool_handlers={},
        max_iterations=1, _record_harness_decision=False,
    )
    assert router.requests[0].correlation_id == result.trace_id


def test_loop_honours_a_caller_supplied_correlation_id(fake_tracer):
    from icdev.tools.llm.agent_loop import run_agent_loop

    router = _StubRouter()
    result = run_agent_loop(
        router, system_prompt="s", user_prompt="u", tools=[], tool_handlers={},
        max_iterations=1, correlation_id="task-hgx-obs-01",
        _record_harness_decision=False,
    )
    assert result.trace_id == "task-hgx-obs-01"
    assert router.requests[0].correlation_id == "task-hgx-obs-01"


def test_loop_emits_a_turn_span_carrying_the_correlation_id(fake_tracer):
    from icdev.tools.llm.agent_loop import run_agent_loop

    run_agent_loop(
        _StubRouter(), system_prompt="s", user_prompt="u", tools=[],
        tool_handlers={}, max_iterations=1, correlation_id="task-span-01",
        _record_harness_decision=False,
    )
    turns = [s for s in fake_tracer.spans if s.name == agent_trace.TURN_SPAN]
    assert len(turns) == 1
    assert turns[0].attributes[agent_trace.CORRELATION_ATTR] == "task-span-01"
    assert turns[0].attributes["gen_ai.response.model"] == "stub-model"
    assert turns[0].ended is True


def test_a_tool_call_inside_the_loop_records_against_the_run(telemetry_db, fake_tracer):
    """The join, end to end: one run id on both the span and the invocation."""
    from icdev.tools.llm.agent_loop import run_agent_loop

    spec = _spec()
    spec.callable = lambda path: "file contents"
    handler = make_handler(spec, gate=_allow)

    calls = {"n": 0}

    class _ToolThenStopRouter(_RouterBase):
        def invoke(self, _function, _request):
            calls["n"] += 1
            response = _StubResponse()
            if calls["n"] == 1:
                response.tool_calls = [
                    {"id": "tc-1", "name": "read_thing", "input": {"path": "notes.md"}}
                ]
            return response

    result = run_agent_loop(
        _ToolThenStopRouter(), system_prompt="s", user_prompt="u",
        tools=[{"function": {"name": "read_thing", "is_read_only": True}}],
        tool_handlers={"read_thing": handler}, max_iterations=3,
        correlation_id="run-end-to-end", _record_harness_decision=False,
    )

    assert result.trace_id == "run-end-to-end"
    rows = _rows(telemetry_db)
    assert [r["name"] for r in rows] == ["read_thing"]
    assert rows[0]["surface"] == "agent"
    assert rows[0]["correlation_id"] == "run-end-to-end"


def test_stop_event_still_ends_the_open_turn_span(fake_tracer):
    """Every loop exit path must close its span, including an early break."""
    from icdev.tools.llm.agent_loop import run_agent_loop

    stop = threading.Event()

    class _StoppingRouter(_RouterBase):
        def invoke(self, _function, _request):
            stop.set()
            return _StubResponse()

    run_agent_loop(
        _StoppingRouter(), system_prompt="s", user_prompt="u", tools=[],
        tool_handlers={}, max_iterations=5, stop_event=stop,
        correlation_id="run-stopped", _record_harness_decision=False,
    )
    turns = [s for s in fake_tracer.spans if s.name == agent_trace.TURN_SPAN]
    assert turns and all(s.ended for s in turns)
