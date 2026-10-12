# CUI // SP-CTI
"""omx-adapt-04 -- the loopback Claude bridge, driven like a harness drives it.

The ``claude`` subprocess is FAKED at ``subprocess_backend.subprocess.run`` --
the one place the bridge starts it -- so every test also proves the request
went through ``run_claude`` (argv, stdin prompt, scrubbed env) rather than a
second CLI wrapper. A real ``ThreadingHTTPServer`` on 127.0.0.1:0 serves the
requests; nothing touches the network or the claude.ai account.
"""
from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tools.llm.cli_bridge import http_bridge as hb
from tools.llm.cli_bridge import subprocess_backend as backend

TOKEN = "test-token-omx-adapt-04"
WRITE_TOOL = {"type": "function", "function": {
    "name": "write_file", "description": "Write a file.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                                                    "content": {"type": "string"}},
                   "required": ["path", "content"]}}}


def _envelope(structured=None, *, result="", is_error=False, usage=None):
    data = {"type": "result", "is_error": is_error, "result": result,
            "usage": usage or {"input_tokens": 12, "output_tokens": 7,
                               "cache_read_input_tokens": 300, "cache_creation_input_tokens": 40}}
    if structured is not None:
        data["structured_output"] = structured
    return json.dumps(data)


class FakeClaude:
    """Stands in for ``claude -p``; answers by what the prompt (stdin) contains."""

    def __init__(self, script):
        self.script = script          # callable(prompt) -> (returncode, stdout)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": list(cmd), "env": kwargs.get("env"), "input": kwargs.get("input")})
        rc, out = self.script(kwargs.get("input") or "")
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")


@pytest.fixture
def fake_claude(monkeypatch):
    monkeypatch.setattr(backend.shutil, "which", lambda _b: "/usr/bin/claude")

    def install(script):
        fake = FakeClaude(script)
        monkeypatch.setattr(backend.subprocess, "run", fake)
        return fake
    return install


@pytest.fixture
def bridge():
    """A served bridge; yields (base_url, state, recorded usage rows)."""
    recorded = []
    state = hb.BridgeState(TOKEN, max_concurrent=2, queue_wait=0.2,
                           recorder=lambda turn, run, error="": recorded.append((turn, run, error)))
    server = hb.make_server("127.0.0.1", 0, state=state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state, recorded
    finally:
        server.shutdown()
        server.server_close()


def _call(url, path, body=None, token=TOKEN):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


# ── the acceptance shape: a tool-using task, round-tripped ────────────────────

def test_tool_call_round_trip_like_a_harness(bridge, fake_claude, tmp_path):
    """Turn 1 asks for a tool, the CALLER runs it, turn 2 sees the result and answers."""
    target = tmp_path / "calc.py"

    def script(prompt):
        if "[TOOL RESULT" not in prompt:
            args = json.dumps({"path": str(target), "content": "def add(a, b):\n    return a + b\n"})
            return 0, _envelope({"kind": "tool_calls", "calls": [{"name": "write_file",
                                                                  "arguments_json": args}]})
        assert "wrote calc.py" in prompt
        return 0, _envelope({"kind": "final", "text": "Created calc.py with add()."})
    fake = fake_claude(script)
    url, _state, recorded = bridge

    messages = [{"role": "system", "content": "You are a coding agent."},
                {"role": "user", "content": "create calc.py defining add(a, b)"}]
    status, _h, raw = _call(url, "/v1/chat/completions",
                            {"model": "sonnet", "messages": messages, "tools": [WRITE_TOOL]})
    assert status == 200, raw
    first = json.loads(raw)
    choice = first["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["type"] == "function" and call["function"]["name"] == "write_file"

    # The harness -- not claude -- executes the tool.
    args = json.loads(call["function"]["arguments"])
    Path(args["path"]).write_text(args["content"], encoding="utf-8")
    messages += [choice["message"],
                 {"role": "tool", "tool_call_id": call["id"], "content": "wrote calc.py"}]
    status, _h, raw = _call(url, "/v1/chat/completions",
                            {"model": "sonnet", "messages": messages, "tools": [WRITE_TOOL]})
    assert status == 200, raw
    final = json.loads(raw)["choices"][0]
    assert final["finish_reason"] == "stop"
    assert final["message"]["content"] == "Created calc.py with add()."
    assert "def add" in target.read_text(encoding="utf-8")

    # Each turn was ONE claude -p, claude's own tools off, the turn schema-constrained.
    assert len(fake.calls) == 2
    cmd = fake.calls[0]["cmd"]
    assert cmd[:3] == ["claude", "--output-format", "json"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--json-schema" in cmd and cmd[cmd.index("--model") + 1] == "sonnet"
    assert '"write_file"' in fake.calls[0]["input"]          # tool definitions travel on stdin
    assert "[ASSISTANT TOOL CALLS]" in fake.calls[1]["input"]
    assert [err for _t, _r, err in recorded] == ["", ""]


def test_usage_is_reported_and_recorded_unpriced(bridge, fake_claude):
    fake_claude(lambda _p: (0, _envelope(result="hello")))
    url, _state, recorded = bridge
    status, _h, raw = _call(url, "/v1/chat/completions",
                            {"model": "haiku", "messages": [{"role": "user", "content": "hi"}]})
    assert status == 200, raw
    body = json.loads(raw)
    assert body["choices"][0]["message"]["content"] == "hello"
    usage = body["usage"]
    assert usage["prompt_tokens"] == 12 + 300 + 40 and usage["completion_tokens"] == 7
    assert usage["prompt_tokens_details"]["cached_tokens"] == 300
    assert usage["icdev"]["usd_basis"] == "unpriced"
    assert "cost" not in json.dumps(usage)
    (_turn, run, error), = recorded
    assert error == "" and run.cache_read_input_tokens == 300


def test_record_usage_writes_a_claude_bridge_job_row_without_a_price(monkeypatch):
    from tools.llm.cli_bridge import job_store
    rows = {}
    monkeypatch.setattr(job_store, "create_job", lambda **kw: rows.setdefault("create", kw) and "j1")
    monkeypatch.setattr(job_store, "complete_job",
                        lambda job_id, result, **kw: rows.setdefault("complete", (job_id, kw)))
    run = backend.ClaudeRun(ok=True, text="x", input_tokens=5, output_tokens=6,
                            cache_read_input_tokens=7, cache_creation_input_tokens=8)
    turn = hb.build_turn({"model": "sonnet", "messages": [{"role": "user", "content": "hi"}]})
    hb.record_usage(turn, run)
    assert rows["create"]["backend"] == hb.JOB_BACKEND
    assert "hi" not in rows["create"]["prompt"]                    # a summary, never the prompt
    assert rows["complete"] == ("j1", {"input_tokens": 5, "output_tokens": 6,
                                       "cache_read_input_tokens": 7,
                                       "cache_creation_input_tokens": 8})


# ── no metered key, ever ──────────────────────────────────────────────────────

def test_claude_subprocess_env_is_scrubbed_of_metered_keys(bridge, fake_claude, monkeypatch):
    for var in backend.METERED_ENV_VARS:
        monkeypatch.setenv(var, "sk-ant-metered")
    monkeypatch.setenv("ICDEV_PROBE_KEEP", "kept")
    fake = fake_claude(lambda _p: (0, _envelope(result="ok")))
    url, _state, _rec = bridge
    status, _h, raw = _call(url, "/v1/chat/completions",
                            {"messages": [{"role": "user", "content": "hi"}]})
    assert status == 200, raw
    env = fake.calls[0]["env"]
    assert env is not None, "claude must never inherit the raw environment"
    assert not set(backend.METERED_ENV_VARS) & set(env)
    assert env["ICDEV_PROBE_KEEP"] == "kept"


def test_job_worker_claude_env_is_scrubbed_too(fake_claude, monkeypatch):
    """The bridge's OWN background path (CLILLMProvider jobs) uses the same seam."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-metered")
    fake = fake_claude(lambda _p: (0, _envelope(result="ok")))
    run = backend.run_claude(["-p", "hi"])
    assert run.ok and run.text == "ok"
    assert "ANTHROPIC_API_KEY" not in fake.calls[0]["env"]


# ── loopback, token, concurrency ──────────────────────────────────────────────

@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com"])
def test_non_loopback_bind_is_refused(host):
    with pytest.raises(ValueError, match="loopback only"):
        hb.resolve_bind(host)


def test_loopback_binds_are_accepted():
    assert hb.resolve_bind("127.0.0.1") == "127.0.0.1"
    assert hb.resolve_bind("localhost") == "127.0.0.1"
    assert hb.resolve_bind("") == "127.0.0.1"


def test_server_listens_on_loopback(bridge):
    url, _state, _rec = bridge
    assert url.startswith("http://127.0.0.1:")


def test_token_is_required_and_health_is_open(bridge, fake_claude):
    fake = fake_claude(lambda _p: (0, _envelope(result="ok")))
    url, _state, _rec = bridge
    body = {"messages": [{"role": "user", "content": "hi"}]}
    assert _call(url, "/v1/chat/completions", body, token=None)[0] == 401
    assert _call(url, "/v1/chat/completions", body, token="wrong")[0] == 401
    assert _call(url, "/v1/models", token=None)[0] == 401
    assert fake.calls == []                          # nothing reached claude
    status, _h, raw = _call(url, "/v1/models")
    assert status == 200 and [m["id"] for m in json.loads(raw)["data"]] == list(hb.DEFAULT_MODELS)
    status, _h, raw = _call(url, "/health", token=None)
    assert status == 200 and json.loads(raw)["usd_basis"] == "unpriced"


def test_token_file_is_created_once_and_reused(tmp_path):
    path = tmp_path / "cfg" / "icdev" / hb.TOKEN_FILENAME
    first = hb.ensure_token(path)
    assert len(first) >= 32 and hb.ensure_token(path) == first
    assert hb.token_path({"ICDEV_CLAUDE_BRIDGE_TOKEN_FILE": str(path)}) == path


def test_concurrency_cap_answers_busy_not_hang(fake_claude):
    state = hb.BridgeState(TOKEN, max_concurrent=1, queue_wait=0.05, recorder=lambda *a, **k: None)
    state.slots.acquire()                            # the one slot is taken
    try:
        with pytest.raises(hb.BridgeError) as exc:
            state.complete({"messages": [{"role": "user", "content": "hi"}]})
    finally:
        state.slots.release()
    assert exc.value.status == 503 and exc.value.type == "bridge_busy"


# ── exhaustion is DISTINCT (omx-adapt-06 fails over on it) ────────────────────

@pytest.mark.parametrize("text,code", [
    ("Credit balance is too low", "credit_exhausted"),
    ("Third-party apps now draw from your extra usage, not your plan limits.", "credit_exhausted"),
    ("Claude AI usage limit reached|1760227200", "usage_limit"),
])
def test_quota_exhaustion_is_a_distinct_429(bridge, fake_claude, text, code):
    fake_claude(lambda _p: (0, _envelope(result=text, is_error=True)))
    url, state, recorded = bridge
    status, headers, raw = _call(url, "/v1/chat/completions",
                                 {"messages": [{"role": "user", "content": "hi"}]})
    assert status == 429
    err = json.loads(raw)["error"]
    assert err["type"] == "claude_quota_exhausted" and err["code"] == code and err["failover"] is True
    assert headers.get(hb.FAILOVER_HEADER) == "claude_quota_exhausted"
    assert state.health()["last_quota_error"]["code"] == code
    assert recorded[0][2].startswith("claude_quota_exhausted")


def test_nonzero_exit_with_quota_text_is_also_exhaustion(bridge, fake_claude):
    fake_claude(lambda _p: (1, _envelope(result="Credit balance is too low", is_error=True)))
    url, _state, _rec = bridge
    status, _h, raw = _call(url, "/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]})
    assert status == 429 and json.loads(raw)["error"]["code"] == "credit_exhausted"


@pytest.mark.parametrize("rc,stdout,status,type_", [
    (1, "segfault", 502, "claude_cli_error"),
    (0, _envelope(result="Invalid API key · Please run /login", is_error=True), 503, "claude_not_authenticated"),
    (0, _envelope(result="   "), 502, "claude_empty_response"),
])
def test_other_failures_are_not_quota_and_never_an_empty_200(bridge, fake_claude, rc, stdout, status, type_):
    fake_claude(lambda _p: (rc, stdout))
    url, _state, _rec = bridge
    got, headers, raw = _call(url, "/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]})
    err = json.loads(raw)["error"]
    assert (got, err["type"], err["failover"]) == (status, type_, False)
    assert hb.FAILOVER_HEADER not in headers


def test_missing_claude_binary_is_503(bridge, monkeypatch):
    monkeypatch.setattr(backend.shutil, "which", lambda _b: None)
    url, _state, _rec = bridge
    status, _h, raw = _call(url, "/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]})
    assert status == 503 and json.loads(raw)["error"]["type"] == "claude_cli_unavailable"


def test_malformed_tool_arguments_are_a_502(bridge, fake_claude):
    fake_claude(lambda _p: (0, _envelope({"kind": "tool_calls",
                                          "calls": [{"name": "write_file", "arguments_json": "not json"}]})))
    url, _state, _rec = bridge
    status, _h, raw = _call(url, "/v1/chat/completions",
                            {"messages": [{"role": "user", "content": "x"}], "tools": [WRITE_TOOL]})
    assert status == 502 and json.loads(raw)["error"]["type"] == "claude_malformed_tool_call"


# ── shape details the measured harnesses rely on ──────────────────────────────

def test_stream_true_is_served_as_buffered_sse(bridge, fake_claude):
    fake_claude(lambda _p: (0, _envelope({"kind": "tool_calls", "calls": [
        {"name": "write_file", "arguments_json": '{"path": "a", "content": "b"}'}]})))
    url, _state, _rec = bridge
    status, headers, raw = _call(url, "/v1/chat/completions",
                                 {"stream": True, "messages": [{"role": "user", "content": "x"}],
                                  "tools": [WRITE_TOOL]})
    assert status == 200 and headers["Content-Type"] == "text/event-stream"
    events = [line[6:] for line in raw.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    first, last = json.loads(events[0]), json.loads(events[1])
    assert first["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
    assert last["choices"][0]["finish_reason"] == "tool_calls"


def test_tool_choice_none_runs_without_the_schema(fake_claude):
    fake = fake_claude(lambda _p: (0, _envelope(result="plain")))
    state = hb.BridgeState(TOKEN, max_concurrent=1, recorder=lambda *a, **k: None)
    out = state.complete({"messages": [{"role": "user", "content": "x"}], "tools": [WRITE_TOOL],
                          "tool_choice": "none"})
    assert out["choices"][0]["message"]["content"] == "plain"
    assert "--json-schema" not in fake.calls[0]["cmd"]


def test_tool_definitions_are_trimmed_in_order():
    tools = [{"type": "function", "function": {"name": f"t{i}", "description": "d" * 200}}
             for i in range(50)]
    rendered, kept, dropped = hb.render_tools(tools, max_bytes=2_000)
    assert 0 < kept < 50 and kept + dropped == 50
    assert [t["name"] for t in json.loads(rendered)] == [f"t{i}" for i in range(kept)]


def test_provider_prefixed_model_is_reduced_to_the_alias():
    turn = hb.build_turn({"model": "icdev-claude/opus", "messages": [{"role": "user", "content": "x"}]})
    assert turn["args"][turn["args"].index("--model") + 1] == "opus"


# ── systemd unit ──────────────────────────────────────────────────────────────

def test_unit_runs_serve_and_blanks_metered_keys(tmp_path):
    unit = hb.render_unit("/usr/bin/python3", tmp_path, source_checkout=True, port=8765)
    assert "ExecStart=/usr/bin/python3 -m tools.cli claude-bridge serve --port 8765" in unit
    assert "Environment=ANTHROPIC_API_KEY=\n" in unit and "Environment=ANTHROPIC_AUTH_TOKEN=\n" in unit
    assert f"WorkingDirectory={tmp_path.as_posix()}" in unit
    packaged = hb.render_unit("/usr/bin/python3", tmp_path, source_checkout=False)
    assert "-m icdev.tools.cli claude-bridge serve" in packaged and "WorkingDirectory" not in packaged
