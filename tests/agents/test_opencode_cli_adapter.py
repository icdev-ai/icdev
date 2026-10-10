# CUI // SP-CTI
"""omx-adapt-01: tools/agents/adapters/opencode_cli.py — the opencode harness.

Covers the card's acceptance criteria:

  1. ``opencode_cli`` is registered, enabled, NOT in ``fallback_order`` (that is
     omx-select-01), and ``ICDEV_AGENT_ADAPTER=opencode_cli`` selects it,
  2. a FAKE ``opencode`` binary on a temp PATH emitting the JSON recorded in
     omx-spike-01 drives ``invoke()`` end to end — command line, success,
     failure and timeout,
  3. ``available()`` is False when the binary is absent,
  4. the capability matrix measures every declared opencode capability and
     ``--gate`` passes with opencode included,
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
from tools.agents.adapters import opencode_cli as oc


# The two streams recorded in docs/research/omx-spike-01/evidence/
# opencode-json-success-failure.jsonl.txt, ids shortened.
_SUCCESS_STREAM = "\n".join(json.dumps(e) for e in (
    {"type": "step_start", "timestamp": 1, "sessionID": "ses_ok",
     "part": {"sessionID": "ses_ok", "type": "step-start"}},
    {"type": "tool_use", "timestamp": 2, "sessionID": "ses_ok",
     "part": {"tool": "bash", "callID": "call_a",
              "state": {"status": "completed", "input": {"command": "ls"},
                        "output": "AGENTS.md"}}},
    {"type": "tool_use", "timestamp": 3, "sessionID": "ses_ok",
     "part": {"tool": "bash", "callID": "call_b",
              "state": {"status": "error", "input": {"command": "rm ../victim"},
                        "error": "ICDEV guard: BLOCKED: Dangerous rm command"}}},
    {"type": "step_finish", "timestamp": 4, "sessionID": "ses_ok",
     "part": {"reason": "tool-calls", "type": "step-finish",
              "tokens": {"total": 200, "input": 150, "output": 50, "reasoning": 0,
                         "cache": {"write": 0, "read": 10}}, "cost": 0}},
    {"type": "text", "timestamp": 5, "sessionID": "ses_ok",
     "part": {"type": "text", "text": "PERIWINKLE-42"}},
    {"type": "step_finish", "timestamp": 6, "sessionID": "ses_ok",
     "part": {"reason": "stop", "type": "step-finish",
              "tokens": {"total": 10333, "input": 10210, "output": 123,
                         "reasoning": 0, "cache": {"write": 0, "read": 0}},
              "cost": 0}},
))

_FAILURE_STREAM = json.dumps(
    {"type": "error", "timestamp": 1791635809989, "sessionID": "ses_bad",
     "error": {"name": "UnknownError",
               "data": {"message": "Unexpected server error. Check server "
                                   "logs for details.",
                        "ref": "err_94fda59e"}}}
)

# The fake CLI. Mode comes from the environment so one binary serves every test.
_FAKE_SCRIPT = r'''
import json, os, sys, time
record = os.environ.get("FAKE_OPENCODE_RECORD")
if record:
    with open(record, "w", encoding="utf-8") as fh:
        json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(),
                   "stdin": sys.stdin.read()}, fh)
mode = os.environ.get("FAKE_OPENCODE_MODE", "success")
if mode == "sleep":
    time.sleep(4)
with open(os.environ["FAKE_OPENCODE_STREAM"], encoding="utf-8") as fh:
    sys.stdout.write(fh.read() + "\n")
if mode == "failure":
    sys.stderr.write("WARN provider ollama: model not found\n")
    sys.exit(1)
sys.exit(0)
'''


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    registry.reset()
    cm.reset_cache()
    for var in ("ICDEV_OPENCODE_CLI", "ICDEV_OPENCODE_MODEL", "ICDEV_AGENT_ADAPTER",
                "ICDEV_DISPATCH_SOURCE", "ICDEV_DISPATCH_TASK_ID"):
        monkeypatch.delenv(var, raising=False)
    yield
    registry.reset()
    cm.reset_cache()


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """A fake ``opencode`` on a PATH holding nothing else."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "fake_opencode.py"
    script.write_text(_FAKE_SCRIPT, encoding="utf-8")
    if os.name == "nt":
        shim = bindir / "opencode.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = bindir / "opencode"
        shim.write_text(f"#!{sys.executable}\n" + _FAKE_SCRIPT, encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setattr(oc.Path, "home", classmethod(lambda _cls: tmp_path))
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_OPENCODE_RECORD", str(record))
    return record


def _stream(tmp_path: Path, monkeypatch, text: str, mode: str = "success") -> None:
    path = tmp_path / "stream.jsonl"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FAKE_OPENCODE_STREAM", str(path))
    monkeypatch.setenv("FAKE_OPENCODE_MODE", mode)


def _session(tmp_path: Path, **kwargs) -> AgentSession:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return AgentSession(
        task_id="omx-adapt-01",
        prompt=kwargs.pop("prompt", "Ship the thing"),
        working_dir=str(work),
        **kwargs,
    )


# ── 1. registration and selection ────────────────────────────────────────────
def test_satisfies_the_protocol_and_is_registered():
    assert isinstance(oc.ADAPTER, AgentAdapter)
    assert "opencode_cli" in registry.list_adapters()
    assert registry.get_adapter("opencode_cli") is oc.ADAPTER


def test_forced_adapter_env_selects_it(monkeypatch):
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "opencode_cli")
    assert registry.pick_default("build").name == "opencode_cli"


def test_enabled_but_not_yet_the_default():
    config = yaml.safe_load(
        Path(registry._CONFIG_PATH).read_text(encoding="utf-8"))  # noqa: SLF001
    assert "opencode_cli" in config["enabled_adapters"]
    assert "opencode_cli" not in (config.get("fallback_order") or [])
    assert "opencode_cli" not in (config.get("per_task_type_preference") or {}).values()


# ── 2. available() ───────────────────────────────────────────────────────────
def test_available_false_when_absent(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(oc.Path, "home", classmethod(lambda _cls: tmp_path))
    assert oc.resolve_opencode_cli() is None
    assert oc.ADAPTER.available() is False
    with pytest.raises(NotInstalledError):
        oc.ADAPTER.invoke(_session(tmp_path))


def test_available_true_with_the_fake_on_path(fake_bin):
    assert oc.ADAPTER.available() is True


def test_omarchy_local_bin_is_a_secondary_probe(tmp_path, monkeypatch):
    """Omarchy's mise stubs live in ~/.local/bin, which may not be on PATH."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(oc.Path, "home", classmethod(lambda _cls: tmp_path))
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "opencode").write_text("", encoding="utf-8")
    assert oc.resolve_opencode_cli(is_windows=False) == str(local_bin / "opencode")


# ── 3. the command line ──────────────────────────────────────────────────────
def test_invoke_runs_opencode_run_format_json_with_the_prompt_last(
        fake_bin, tmp_path, monkeypatch):
    _stream(tmp_path, monkeypatch, _SUCCESS_STREAM)
    oc.ADAPTER.invoke(_session(tmp_path))

    seen = json.loads(fake_bin.read_text(encoding="utf-8"))
    argv = seen["argv"]
    assert argv[:3] == ["run", "--format", "json"]
    assert argv[argv.index("--dir") + 1] == str(tmp_path / "work")
    assert argv[-1] == "Ship the thing"
    assert "--model" not in argv          # opencode's own default
    assert "--pure" not in argv           # would drop the guard plugin
    assert Path(seen["cwd"]).resolve() == (tmp_path / "work").resolve()
    assert seen["stdin"] == ""            # closed: an open stdin hangs opencode


def test_stdin_is_devnull(monkeypatch, tmp_path):
    """Measured in omx-spike-01: an inherited stdin hangs ``opencode run``."""
    monkeypatch.setattr(oc, "resolve_opencode_cli", lambda: "opencode")
    seen = {}

    def _run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout=_SUCCESS_STREAM, stderr="")

    monkeypatch.setattr(oc.subprocess, "run", _run)
    oc.ADAPTER.invoke(_session(tmp_path))
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["shell"] is False


def test_model_comes_from_env_and_metadata_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(oc, "resolve_opencode_cli", lambda: "opencode")
    monkeypatch.setenv("ICDEV_OPENCODE_MODEL", "vllm/from-env")
    argv = oc.ADAPTER.build_argv(_session(tmp_path))
    assert argv[argv.index("--model") + 1] == "vllm/from-env"
    argv = oc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"model_id": "vllm/from-meta"}))
    assert argv[argv.index("--model") + 1] == "vllm/from-meta"


def test_pure_is_refused_not_silently_dropped(monkeypatch, tmp_path):
    monkeypatch.setattr(oc, "resolve_opencode_cli", lambda: "opencode")
    with pytest.raises(ValueError, match="--pure"):
        oc.ADAPTER.build_argv(_session(tmp_path, metadata={"extra_args": ["--pure"]}))


def test_opt_in_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(oc, "resolve_opencode_cli", lambda: "opencode")
    default = oc.ADAPTER.build_argv(_session(tmp_path))
    assert "--auto" not in default
    assert "--print-logs" in default
    argv = oc.ADAPTER.build_argv(_session(
        tmp_path, metadata={"auto_approve": True, "print_logs": False}))
    assert "--auto" in argv and "--print-logs" not in argv


def test_system_prompt_is_prepended(monkeypatch, tmp_path):
    monkeypatch.setattr(oc, "resolve_opencode_cli", lambda: "opencode")
    argv = oc.ADAPTER.build_argv(_session(tmp_path, system_prompt="Be terse."))
    assert argv[-1] == "Be terse.\n\nShip the thing"


# ── 4. success / failure / timeout through the fake binary ──────────────────
def test_success_is_parsed(fake_bin, tmp_path, monkeypatch):
    _stream(tmp_path, monkeypatch, _SUCCESS_STREAM)
    result = oc.ADAPTER.invoke(_session(tmp_path))

    assert result.completed is True
    assert result.exit_code == 0
    assert result.output == "PERIWINKLE-42"
    assert result.error == ""
    s = result.structured
    assert s["session_id"] == "ses_ok"
    assert s["stop_reason"] == "stop"
    assert s["input_tokens"] == 150 + 10210
    assert s["output_tokens"] == 50 + 123
    assert s["tokens"]["cache_read"] == 10
    assert s["tool_calls"] == 2
    # A guard-refused tool call is visible, and is not a run failure.
    assert s["tool_errors"] == 1


def test_failure_is_reported_with_the_reason(fake_bin, tmp_path, monkeypatch):
    _stream(tmp_path, monkeypatch, _FAILURE_STREAM, mode="failure")
    result = oc.ADAPTER.invoke(_session(tmp_path))

    assert result.completed is False
    assert result.exit_code == 1
    assert result.structured["is_error"] is True
    assert "UnknownError" in result.error
    assert "err_94fda59e" in result.error
    # The opaque JSON error is why stderr (--print-logs) is kept.
    assert "model not found" in result.error


def test_exit_zero_without_a_stop_step_is_not_completed(fake_bin, tmp_path, monkeypatch):
    truncated = "\n".join(_SUCCESS_STREAM.splitlines()[:4])
    _stream(tmp_path, monkeypatch, truncated)
    result = oc.ADAPTER.invoke(_session(tmp_path))
    assert result.exit_code == 0
    assert result.completed is False
    assert "stop" in result.error


def test_timeout_is_reported_not_raised(fake_bin, tmp_path, monkeypatch):
    _stream(tmp_path, monkeypatch, _SUCCESS_STREAM, mode="sleep")
    result = oc.ADAPTER.invoke(_session(tmp_path, timeout_seconds=1))
    assert result.completed is False
    assert result.exit_code == -1
    assert "timed out" in result.error


def test_an_argv_the_os_refuses_is_reported(monkeypatch, tmp_path):
    """WinError 206 (prompt past the command-line limit) must not crash."""
    monkeypatch.setattr(oc, "resolve_opencode_cli", lambda: "opencode")

    def _run(argv, **kwargs):
        raise OSError(206, "The filename or extension is too long")

    monkeypatch.setattr(oc.subprocess, "run", _run)
    result = oc.ADAPTER.invoke(_session(tmp_path))
    assert result.completed is False
    assert "could not be started" in result.error


# ── 5. parse_response / detect_completion ────────────────────────────────────
def test_parse_response_shape():
    parsed = oc.ADAPTER.parse_response(_SUCCESS_STREAM)
    assert parsed["content"] == "PERIWINKLE-42"
    assert parsed["tool_call_count"] == 2
    assert [c["status"] for c in parsed["tool_calls"]] == ["completed", "error"]
    assert parsed["diff"] == ""
    assert parsed["task_complete"] is True

    failed = oc.ADAPTER.parse_response(_FAILURE_STREAM)
    assert failed["is_error"] is True
    assert failed["task_complete"] is False
    assert "UnknownError" in failed["error"]


def test_unreported_usage_is_absent_not_zero():
    parsed = oc.ADAPTER.parse_response(_FAILURE_STREAM)
    assert "input_tokens" not in parsed
    assert "total_cost_usd" not in parsed


def test_detect_completion():
    assert oc.ADAPTER.detect_completion(_SUCCESS_STREAM) is True
    assert oc.ADAPTER.detect_completion(_FAILURE_STREAM) is False
    assert oc.ADAPTER.detect_completion("") is False
    assert oc.ADAPTER.parse_response("plain text")["content"] == "plain text"


# ── 6. capability matrix ─────────────────────────────────────────────────────
def test_capability_matrix_confirms_every_declaration():
    entry = cm.probe_adapter("opencode_cli")
    verdicts = {cap: cell["verdict"] for cap, cell in entry["capabilities"].items()}
    assert cm.OVERCLAIMED not in verdicts.values()
    assert verdicts["tool_calling"] == cm.CONFIRMED
    assert verdicts["structured_output"] == cm.CONFIRMED
    guard = entry["capabilities"]["guard_wired"]
    assert guard["declared"] is True and guard["declared_explicitly"] is True
    # omx-guard-01: measured LIVE through the bridge the plugin spawns.
    assert guard["actual"] == cm.PRESENT
    assert verdicts["guard_wired"] == cm.CONFIRMED


def test_verify_guard_is_a_live_refusal_through_the_bridge():
    report = oc.ADAPTER.verify_guard()
    assert report["wired"] is True, report["reason"]
    assert "BLOCKED" in report["reason"]


def test_verify_guard_reports_a_bridge_that_allows(monkeypatch):
    """A bridge that waves the known-bad call through is NOT wired."""
    def _run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout='{"allowed": true, "reason": "passed"}', stderr="")

    monkeypatch.setattr(oc.subprocess, "run", _run)
    assert oc.ADAPTER.verify_guard()["wired"] is False


def test_invoke_installs_the_guard_plugin_into_the_run_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(oc, "resolve_opencode_cli", lambda: "opencode")
    monkeypatch.setattr(oc.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, stdout=_SUCCESS_STREAM,
                                                    stderr=""))
    session = _session(tmp_path)
    oc.ADAPTER.invoke(session)
    plugin = Path(session.working_dir) / ".opencode" / "plugin" / "icdev-guard.ts"
    text = plugin.read_text(encoding="utf-8")
    assert "tool.execute.before" in text and "harness_guard" in text
    assert "__ICDEV_ROOT__" not in text and "__ICDEV_PYTHON__" not in text


def test_invoke_refuses_to_run_unguarded(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise PermissionError("read-only project")

    ran = []
    monkeypatch.setattr(oc, "resolve_opencode_cli", lambda: "opencode")
    monkeypatch.setattr(oc, "install_guard", _boom)
    monkeypatch.setattr(oc.subprocess, "run", lambda *a, **k: ran.append(a))
    result = oc.ADAPTER.invoke(_session(tmp_path))
    assert result.completed is False and not ran
    assert "unguarded" in result.error


def test_gate_passes_with_opencode_included(capsys):
    assert cm.main(["--gate", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "opencode_cli" in payload["adapters"]
    assert payload["overclaimed"] == []


# ── 7. LLM-agnostic ──────────────────────────────────────────────────────────
def test_no_model_id_literal_in_the_module():
    tree = ast.parse(Path(oc.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in ("model", "model_id"):
            assert not isinstance(node.value, ast.Constant), ast.dump(node)
