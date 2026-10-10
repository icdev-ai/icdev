# CUI // SP-CTI
"""omx-adapt-02: tools/agents/adapters/pi_cli.py — the Pi harness.

Covers the card's acceptance criteria:

  1. ``pi_cli`` is registered, enabled, NOT in ``fallback_order`` (that is
     omx-select-01), and ``ICDEV_AGENT_ADAPTER=pi_cli`` selects it,
  2. a FAKE ``pi`` binary on a temp PATH emitting the JSON recorded in
     omx-spike-01 drives ``invoke()`` end to end — command line, success,
     the exit-0 provider error, the exit-1 unknown provider, and timeout,
  3. ``available()`` is False when the binary is absent,
  4. the capability matrix measures every declared Pi capability and
     ``--gate`` passes with Pi included,
  5. no model id appears in the module.

The fake binary is a tiny Python script behind a ``.cmd`` shim on Windows and a
shebang on POSIX, so the same tests run on both CI runners.
"""
from __future__ import annotations

import ast
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.agents import capability_matrix as cm
from tools.agents import registry
from tools.agents.adapter_base import AgentAdapter, AgentSession, NotInstalledError
from tools.agents.adapters import pi_cli as pc


def _usage(inp: int, out: int, cache_read: int = 0) -> dict:
    return {"input": inp, "output": out, "cacheRead": cache_read, "cacheWrite": 0,
            "reasoning": 0, "totalTokens": inp + out + cache_read,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                     "total": 0}}


# The pi-icdev run recorded in docs/research/omx-spike-01/evidence/pi-icdev.txt,
# shortened: one allowed and one guard-refused bash call, then a final answer.
_SUCCESS_STREAM = "\n".join(json.dumps(e) for e in (
    {"type": "session", "version": 3, "id": "01a125d3-pi-ok", "cwd": "work"},
    {"type": "agent_start"},
    {"type": "turn_start"},
    {"type": "message_end", "message": {
        "role": "assistant", "stopReason": "toolUse", "usage": _usage(1509, 280),
        "content": [{"type": "thinking", "thinking": "verify first"},
                    {"type": "text", "text": "Let me verify what is there."}]}},
    {"type": "tool_execution_start", "toolCallId": "call_qq0bcdjk",
     "toolName": "bash", "args": {"command": "ls -la ../victim"}},
    {"type": "tool_execution_end", "toolCallId": "call_qq0bcdjk", "toolName": "bash",
     "isError": False, "result": {"content": [{"type": "text", "text": "scratch.tmp"}]}},
    {"type": "message_end", "message": {
        "role": "assistant", "stopReason": "toolUse", "usage": _usage(130, 140, 1905),
        "content": []}},
    {"type": "tool_execution_start", "toolCallId": "call_nbitpa2s",
     "toolName": "bash", "args": {"command": "rm -rf ../victim"}},
    {"type": "tool_execution_end", "toolCallId": "call_nbitpa2s", "toolName": "bash",
     "isError": True, "result": {"content": [{"type": "text", "text":
         "ICDEV guard: BLOCKED: Dangerous rm command detected and prevented"}]}},
    {"type": "message_end", "message": {
        "role": "assistant", "stopReason": "stop", "usage": _usage(213, 464, 2217),
        "content": [{"type": "text", "text": "PERIWINKLE-42"}]}},
    {"type": "agent_end", "messages": [], "willRetry": False},
    {"type": "agent_settled", "aborted": False},
))

# evidence/pi-json-failure.txt: unknown model id on a KNOWN provider. Exit 0.
_PROVIDER_ERROR_STREAM = "\n".join(json.dumps(e) for e in (
    {"type": "session", "version": 3, "id": "01a1-pi-bad", "cwd": "work"},
    {"type": "agent_start"},
    {"type": "message_end", "message": {
        "role": "assistant", "stopReason": "error", "usage": _usage(0, 0),
        "content": [],
        "errorMessage": '404: {"message":"model \'no-such-model:latest\' not found"}'}},
    {"type": "agent_end", "messages": [], "willRetry": False},
    {"type": "agent_settled", "aborted": False},
))

# evidence/pi-json-failure.txt: unknown provider. Exit 1, plain stderr, no JSON.
_UNKNOWN_PROVIDER_STDERR = (
    'Error: Model "nosuchprovider/x" not found. '
    "Use --list-models to see available models."
)

# The fake CLI. Mode comes from the environment so one binary serves every test.
_FAKE_SCRIPT = r'''
import json, os, sys, time
record = os.environ.get("FAKE_PI_RECORD")
if record:
    with open(record, "w", encoding="utf-8") as fh:
        json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(),
                   "stdin": sys.stdin.read(),
                   "agent_dir": os.environ.get("PI_CODING_AGENT_DIR")}, fh)
mode = os.environ.get("FAKE_PI_MODE", "success")
if mode == "sleep":
    time.sleep(4)
if mode == "unknown_provider":
    sys.stderr.write(os.environ["FAKE_PI_STDERR"] + "\n")
    sys.exit(1)
with open(os.environ["FAKE_PI_STREAM"], encoding="utf-8") as fh:
    sys.stdout.write(fh.read() + "\n")
sys.exit(0)
'''


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    registry.reset()
    cm.reset_cache()
    for var in ("ICDEV_PI_CLI", "ICDEV_PI_MODEL", "ICDEV_AGENT_ADAPTER",
                "ICDEV_DISPATCH_SOURCE", "ICDEV_DISPATCH_TASK_ID",
                "PI_CODING_AGENT_DIR"):
        monkeypatch.delenv(var, raising=False)
    yield
    registry.reset()
    cm.reset_cache()


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """A fake ``pi`` on a PATH holding nothing else."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "fake_pi.py"
    script.write_text(_FAKE_SCRIPT, encoding="utf-8")
    if os.name == "nt":
        shim = bindir / "pi.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = bindir / "pi"
        shim.write_text(f"#!{sys.executable}\n" + _FAKE_SCRIPT, encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setattr(pc.Path, "home", classmethod(lambda _cls: tmp_path))
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_PI_RECORD", str(record))
    return record


def _stream(tmp_path: Path, monkeypatch, text: str, mode: str = "success") -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FAKE_PI_STREAM", str(path))
    monkeypatch.setenv("FAKE_PI_MODE", mode)


def _session(tmp_path: Path, **kwargs) -> AgentSession:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return AgentSession(
        task_id="omx-adapt-02",
        prompt=kwargs.pop("prompt", "Ship the thing"),
        working_dir=str(work),
        **kwargs,
    )


# ── 1. registration and selection ────────────────────────────────────────────
def test_satisfies_the_protocol_and_is_registered():
    assert isinstance(pc.ADAPTER, AgentAdapter)
    assert "pi_cli" in registry.list_adapters()
    assert registry.get_adapter("pi_cli") is pc.ADAPTER


def test_forced_adapter_env_selects_it(monkeypatch):
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "pi_cli")
    assert registry.pick_default("build").name == "pi_cli"


def test_enabled_but_not_yet_the_default():
    config = yaml.safe_load(
        Path(registry._CONFIG_PATH).read_text(encoding="utf-8"))  # noqa: SLF001
    assert "pi_cli" in config["enabled_adapters"]
    assert "pi_cli" not in (config.get("fallback_order") or [])
    assert "pi_cli" not in (config.get("per_task_type_preference") or {}).values()


# ── 2. available() ───────────────────────────────────────────────────────────
def test_available_false_when_absent(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(pc.Path, "home", classmethod(lambda _cls: tmp_path))
    assert pc.resolve_pi_cli() is None
    assert pc.ADAPTER.available() is False
    with pytest.raises(NotInstalledError):
        pc.ADAPTER.invoke(_session(tmp_path))


def test_available_true_with_the_fake_on_path(fake_bin):
    assert pc.ADAPTER.available() is True


def test_omarchy_local_bin_is_a_secondary_probe(tmp_path, monkeypatch):
    """Omarchy's mise stubs live in ~/.local/bin, which may not be on PATH."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(pc.Path, "home", classmethod(lambda _cls: tmp_path))
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "pi").write_text("", encoding="utf-8")
    assert pc.resolve_pi_cli(is_windows=False) == str(local_bin / "pi")


def test_explicit_cli_path_override(tmp_path, monkeypatch):
    exe = tmp_path / "custom-pi"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("ICDEV_PI_CLI", str(exe))
    assert pc.resolve_pi_cli() == str(exe)


# ── 3. the command line ──────────────────────────────────────────────────────
def test_invoke_runs_mode_json_offline_with_the_prompt_last(
        fake_bin, tmp_path, monkeypatch):
    _stream(tmp_path, monkeypatch, _SUCCESS_STREAM)
    pc.ADAPTER.invoke(_session(tmp_path))

    seen = json.loads(fake_bin.read_text(encoding="utf-8"))
    argv = seen["argv"]
    assert argv[:3] == ["--mode", "json", "--offline"]
    assert argv[-1] == "Ship the thing"
    assert "--model" not in argv          # Pi's own configured default
    assert "--approve" not in argv        # project trust is opt-in
    assert Path(seen["cwd"]).resolve() == (tmp_path / "work").resolve()
    assert seen["stdin"] == ""            # closed, as the spike ran it
    assert seen["agent_dir"] is None      # operator's ~/.pi/agent by default


def test_stdin_is_devnull(monkeypatch, tmp_path):
    monkeypatch.setattr(pc, "resolve_pi_cli", lambda: "pi")
    seen = {}

    def _run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=_SUCCESS_STREAM, stderr="")

    monkeypatch.setattr(pc.subprocess, "run", _run)
    pc.ADAPTER.invoke(_session(tmp_path))
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["shell"] is False


def test_model_comes_from_env_and_metadata_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(pc, "resolve_pi_cli", lambda: "pi")
    monkeypatch.setenv("ICDEV_PI_MODEL", "vllm/from-env")
    argv = pc.ADAPTER.build_argv(_session(tmp_path))
    assert argv[argv.index("--model") + 1] == "vllm/from-env"
    argv = pc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"model_id": "vllm/from-meta"}))
    assert argv[argv.index("--model") + 1] == "vllm/from-meta"


def test_opt_in_flags_and_explicit_extensions(monkeypatch, tmp_path):
    """A guard goes in with an explicit ``-e``: ``--no-extensions`` keeps it."""
    monkeypatch.setattr(pc, "resolve_pi_cli", lambda: "pi")
    guard = tmp_path / "guard.ts"
    argv = pc.ADAPTER.build_argv(_session(tmp_path, metadata={
        "offline": False, "approve": True, "extensions": [str(guard)],
        "extra_args": ["--no-extensions"]}))
    assert "--offline" not in argv
    assert "--approve" in argv
    assert argv[argv.index("-e") + 1] == str(guard)
    assert argv.index("-e") < argv.index("--no-extensions") < len(argv) - 1


def test_agent_dir_sets_pi_coding_agent_dir(fake_bin, tmp_path, monkeypatch):
    _stream(tmp_path, monkeypatch, _SUCCESS_STREAM)
    agent_dir = tmp_path / "agent"
    pc.ADAPTER.invoke(_session(tmp_path, metadata={"agent_dir": str(agent_dir)}))
    seen = json.loads(fake_bin.read_text(encoding="utf-8"))
    assert Path(seen["agent_dir"]) == agent_dir


def test_system_prompt_is_prepended(monkeypatch, tmp_path):
    monkeypatch.setattr(pc, "resolve_pi_cli", lambda: "pi")
    argv = pc.ADAPTER.build_argv(_session(tmp_path, system_prompt="Be terse."))
    assert argv[-1] == "Be terse.\n\nShip the thing"


# ── 4. outcomes through the fake binary ──────────────────────────────────────
def test_success_is_parsed(fake_bin, tmp_path, monkeypatch):
    _stream(tmp_path, monkeypatch, _SUCCESS_STREAM)
    result = pc.ADAPTER.invoke(_session(tmp_path))

    assert result.completed is True
    assert result.exit_code == 0
    assert result.output == "PERIWINKLE-42"
    assert result.error == ""
    s = result.structured
    assert s["session_id"] == "01a125d3-pi-ok"
    assert s["stop_reason"] == "stop"
    assert s["turns"] == 3
    assert s["input_tokens"] == 1509 + 130 + 213
    assert s["output_tokens"] == 280 + 140 + 464
    assert s["tokens"]["cache_read"] == 1905 + 2217
    assert s["total_cost_usd"] == 0.0
    assert s["tool_calls"] == 2
    # A guard-refused tool call is visible, and is not a run failure.
    assert s["tool_errors"] == 1


def test_provider_error_behind_exit_zero_is_a_failure(fake_bin, tmp_path, monkeypatch):
    """Measured: Pi exits 0 when the provider rejects the model."""
    _stream(tmp_path, monkeypatch, _PROVIDER_ERROR_STREAM)
    result = pc.ADAPTER.invoke(_session(tmp_path))

    assert result.exit_code == 0
    assert result.completed is False
    assert result.structured["is_error"] is True
    assert result.structured["stop_reason"] == "error"
    assert "not found" in result.error and "404" in result.error


def test_unknown_provider_exit_one_keeps_stderr(fake_bin, tmp_path, monkeypatch):
    _stream(tmp_path, monkeypatch, "", mode="unknown_provider")
    monkeypatch.setenv("FAKE_PI_STDERR", _UNKNOWN_PROVIDER_STDERR)
    result = pc.ADAPTER.invoke(_session(tmp_path))

    assert result.completed is False
    assert result.exit_code == 1
    assert "nosuchprovider/x" in result.error


def test_exit_zero_without_settling_is_not_completed(fake_bin, tmp_path, monkeypatch):
    truncated = "\n".join(_SUCCESS_STREAM.split("\n")[:6])
    _stream(tmp_path, monkeypatch, truncated)
    result = pc.ADAPTER.invoke(_session(tmp_path))
    assert result.exit_code == 0
    assert result.completed is False
    assert "stop" in result.error


def test_an_aborted_run_is_not_completed():
    aborted = _SUCCESS_STREAM.replace('"aborted": false', '"aborted": true')
    parsed = pc.ADAPTER.parse_response(aborted)
    assert parsed["task_complete"] is False
    assert parsed["is_error"] is True
    assert "aborted" in parsed["error"]


def test_timeout_is_reported_not_raised(fake_bin, tmp_path, monkeypatch):
    _stream(tmp_path, monkeypatch, _SUCCESS_STREAM, mode="sleep")
    result = pc.ADAPTER.invoke(_session(tmp_path, timeout_seconds=1))
    assert result.completed is False
    assert result.exit_code == -1
    assert "timed out" in result.error


def test_an_argv_the_os_refuses_is_reported(monkeypatch, tmp_path):
    """WinError 206 (prompt past the command-line limit) must not crash."""
    monkeypatch.setattr(pc, "resolve_pi_cli", lambda: "pi")

    def _run(argv, **kwargs):
        raise OSError(206, "The filename or extension is too long")

    monkeypatch.setattr(pc.subprocess, "run", _run)
    result = pc.ADAPTER.invoke(_session(tmp_path))
    assert result.completed is False
    assert "could not be started" in result.error


# ── 5. parse_response / detect_completion ────────────────────────────────────
def test_parse_response_shape():
    parsed = pc.ADAPTER.parse_response(_SUCCESS_STREAM)
    assert parsed["content"] == "PERIWINKLE-42"
    assert parsed["tool_call_count"] == 2
    assert [c["status"] for c in parsed["tool_calls"]] == ["completed", "error"]
    assert parsed["tool_calls"][1]["input"] == {"command": "rm -rf ../victim"}
    assert parsed["diff"] == ""
    assert parsed["task_complete"] is True


def test_line_separator_inside_a_record_is_not_a_boundary():
    """Pi's docs: U+2028/2029 are not record boundaries. splitlines() splits them."""
    text = "first second third"
    stream = _SUCCESS_STREAM.replace(
        json.dumps("PERIWINKLE-42"), json.dumps(text, ensure_ascii=False))
    assert " " in stream
    parsed = pc.ADAPTER.parse_response(stream)
    assert parsed["content"] == text
    assert parsed["task_complete"] is True


def test_unreported_usage_is_absent_not_zero():
    no_usage = "\n".join(json.dumps(e) for e in (
        {"type": "message_end", "message": {
            "role": "assistant", "stopReason": "stop",
            "content": [{"type": "text", "text": "ok"}]}},
        {"type": "agent_settled", "aborted": False},
    ))
    parsed = pc.ADAPTER.parse_response(no_usage)
    assert parsed["task_complete"] is True
    assert "input_tokens" not in parsed
    assert "total_cost_usd" not in parsed


def test_detect_completion():
    assert pc.ADAPTER.detect_completion(_SUCCESS_STREAM) is True
    assert pc.ADAPTER.detect_completion(_PROVIDER_ERROR_STREAM) is False
    assert pc.ADAPTER.detect_completion("") is False
    assert pc.ADAPTER.parse_response("plain text")["content"] == "plain text"


# ── 6. capability matrix ─────────────────────────────────────────────────────
def test_capability_matrix_confirms_every_declaration():
    entry = cm.probe_adapter("pi_cli")
    verdicts = {cap: cell["verdict"] for cap, cell in entry["capabilities"].items()}
    assert cm.OVERCLAIMED not in verdicts.values()
    assert verdicts["tool_calling"] == cm.CONFIRMED
    assert verdicts["structured_output"] == cm.CONFIRMED
    guard = entry["capabilities"]["guard_wired"]
    assert guard["declared"] is True and guard["declared_explicitly"] is True
    assert guard["actual"] == cm.PRESENT       # live bridge probe (omx-guard-03)


def test_gate_passes_with_pi_included(capsys):
    assert cm.main(["--gate", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "pi_cli" in payload["adapters"]
    assert payload["overclaimed"] == []


# ── 7. LLM-agnostic ──────────────────────────────────────────────────────────
def test_no_model_id_literal_in_the_module():
    tree = ast.parse(Path(pc.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in ("model", "model_id"):
            assert not isinstance(node.value, ast.Constant), ast.dump(node)
