# CUI // SP-CTI
"""exa-bench-01: tools/agents/adapters/codex_cli.py — the Codex CLI harness.

Covers the card's binding acceptance criteria:

  1. all five AgentAdapter Protocol methods are implemented,
  2. ``available()`` reflects REAL CLI presence (PATH + PATHEXT + the
     ``~/.local/bin`` secondary probe + the ``$ICDEV_CODEX_CLI`` override),
  3. ``invoke()`` returns a POPULATED ``AgentResult`` — not a stub raise,
  4. the Windows 32767-char argv limit is avoided (prompt via stdin),
  5. no model id appears in the module,
  6. the tests run identically on Windows and Linux,
  7. ``codex_cli`` is enabled but NOT in ``fallback_order``.

No test touches a real ``codex`` binary: resolution is monkeypatched and
``subprocess.run`` is replaced by a recorder, so the suite is deterministic on
a host that has the CLI and on one that does not.
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from tools.agents import registry
from tools.agents.adapter_base import (
    AgentAdapter,
    AgentSession,
    NotInstalledError,
)
from tools.agents.adapters import codex_cli as cc


FAKE_CLI = "/opt/bin/codex"


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.reset()
    yield
    registry.reset()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Adapter env knobs never leak in from the shell.

    ``ICDEV_DISPATCH_*`` matters: a suite run by the kanban runner inherits
    those tags, so a test asserting they are ABSENT unless requested would
    otherwise pass locally and fail under dispatch.
    """
    for var in ("ICDEV_CODEX_CLI", "ICDEV_CODEX_MODEL", "ICDEV_CODEX_SANDBOX",
                "ICDEV_AGENT_ADAPTER", "ICDEV_DISPATCH_SOURCE",
                "ICDEV_DISPATCH_TASK_ID"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def installed(monkeypatch):
    """Pretend the CLI resolves, without touching PATH."""
    monkeypatch.setattr(cc, "resolve_codex_cli", lambda: FAKE_CLI)
    return FAKE_CLI


def _session(tmp_path: Path, **kwargs) -> AgentSession:
    return AgentSession(
        task_id=kwargs.pop("task_id", "exa-bench-01"),
        prompt=kwargs.pop("prompt", "Ship the thing."),
        working_dir=str(tmp_path),
        **kwargs,
    )


class _Recorder:
    """Stand-in for ``subprocess.run`` that captures argv/env/stdin."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0,
                 raises: BaseException | None = None):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.raises = raises
        self.argv = None
        self.kwargs = None
        self.stdin_text = ""

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        self.kwargs = kwargs
        stdin = kwargs.get("stdin")
        if stdin is not None and hasattr(stdin, "read"):
            self.stdin_text = stdin.read()
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            argv, self.returncode, stdout=self.stdout, stderr=self.stderr
        )


def _jsonl(*events) -> str:
    return "\n".join(json.dumps(e) for e in events)


_HAPPY_STREAM = _jsonl(
    {"id": "0", "msg": {"type": "session_configured", "session_id": "sess-7"}},
    {"id": "1", "msg": {"type": "agent_message", "message": "Reading the tree."}},
    {"id": "2", "msg": {"type": "exec_command_begin", "command": ["ls"]}},
    {"id": "2", "msg": {"type": "exec_command_end", "exit_code": 0}},
    {"id": "3", "msg": {"type": "token_count",
                        "input_tokens": 1200, "output_tokens": 340}},
    {"id": "4", "msg": {"type": "task_complete",
                        "last_agent_message": "Patched foo.py and ran the tests."}},
)


# ---------------------------------------------------------------------------
# 1. Protocol conformance + registration
# ---------------------------------------------------------------------------


def test_satisfies_agent_adapter_protocol():
    assert isinstance(cc.ADAPTER, AgentAdapter)
    assert cc.ADAPTER.name == "codex_cli"


def test_implements_all_five_protocol_methods():
    for method in ("available", "prepare_prompt", "invoke",
                   "detect_completion", "parse_response"):
        assert callable(getattr(cc.ADAPTER, method)), method


def test_registered_in_the_registry():
    assert "codex_cli" in registry.list_adapters()
    assert registry.get_adapter("codex_cli") is cc.ADAPTER


def test_pick_default_returns_it_when_forced(monkeypatch):
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "codex_cli")
    assert registry.pick_default() is cc.ADAPTER


# ---------------------------------------------------------------------------
# 7. Enabled, but nothing routes to it yet
# ---------------------------------------------------------------------------


def _config() -> dict:
    path = Path(registry._CONFIG_PATH)  # noqa: SLF001
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_enabled_in_the_yaml_config():
    assert "codex_cli" in (_config().get("enabled_adapters") or [])


def test_not_in_fallback_order_until_the_capability_probe_lands():
    """exa-bench-03 decides when it is ready; until then nothing falls back."""
    assert "codex_cli" not in (_config().get("fallback_order") or [])


def test_not_a_per_task_type_preference():
    prefs = _config().get("per_task_type_preference") or {}
    assert "codex_cli" not in prefs.values()


class _Stub:
    """Minimal stand-in — ``pick_default`` only ever calls ``available()``."""

    def __init__(self, name: str, ok: bool):
        self.name = name
        self._ok = ok

    def available(self) -> bool:
        return self._ok


def test_enabling_it_does_not_move_selection_for_existing_consumers(monkeypatch):
    """Hermetic: no real adapter is probed, so the result is host-independent."""
    claude, codex = _Stub("claude_cli", True), _Stub("codex_cli", True)
    monkeypatch.setattr(
        registry, "_REGISTRY", {"claude_cli": claude, "codex_cli": codex}
    )
    cfg = {
        "enabled_adapters": ["claude_cli", "codex_cli"],
        "per_task_type_preference": {"build": "claude_cli"},
        "fallback_order": ["claude_cli"],
    }
    assert registry.pick_default("build", config=cfg) is claude
    assert registry.pick_default("research", config=cfg) is claude


def test_it_is_still_reachable_as_a_last_resort(monkeypatch):
    """Enabled means selectable when literally nothing else resolves."""
    codex = _Stub("codex_cli", True)
    monkeypatch.setattr(registry, "_REGISTRY", {"codex_cli": codex})
    cfg = {"enabled_adapters": ["codex_cli"],
           "per_task_type_preference": {}, "fallback_order": []}
    assert registry.pick_default("build", config=cfg) is codex


# ---------------------------------------------------------------------------
# 2. available() reflects real CLI presence
# ---------------------------------------------------------------------------


def test_available_false_when_nothing_resolves(monkeypatch, tmp_path):
    monkeypatch.setattr(cc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cc.Path, "home", classmethod(lambda _cls: tmp_path))
    assert cc.resolve_codex_cli() is None
    assert cc.ADAPTER.available() is False


def test_available_true_when_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(cc.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        cc.shutil, "which", lambda name: FAKE_CLI if name == "codex" else None
    )
    assert cc.resolve_codex_cli() == FAKE_CLI
    assert cc.ADAPTER.available() is True


def test_falls_back_to_the_legacy_npm_name(monkeypatch, tmp_path):
    monkeypatch.setattr(cc.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        cc.shutil, "which",
        lambda name: "/usr/bin/openai-codex" if name == "openai-codex" else None,
    )
    assert cc.resolve_codex_cli() == "/usr/bin/openai-codex"


def test_env_override_accepts_an_explicit_path(monkeypatch, tmp_path):
    binary = tmp_path / "my-codex"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("ICDEV_CODEX_CLI", str(binary))
    monkeypatch.setattr(cc.shutil, "which", lambda _name: None)
    assert cc.resolve_codex_cli() == str(binary)


def test_env_override_accepts_a_bare_name(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_CODEX_CLI", "codex-nightly")
    monkeypatch.setattr(cc.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        cc.shutil, "which",
        lambda name: "/n/codex-nightly" if name == "codex-nightly" else None,
    )
    assert cc.resolve_codex_cli() == "/n/codex-nightly"


def test_pathext_secondary_probe_finds_the_suffixed_binary(monkeypatch, tmp_path):
    """The suffix-less ``~/.local/bin/codex`` never exists on Windows.

    Both branches are driven by the explicit ``is_windows`` argument rather
    than by patching ``os.name``: patching it makes ``pathlib`` hand out a
    ``WindowsPath`` on Linux, which raises on construction — so the Windows
    branch would be untestable on the CI runner. Verified in a Linux container.
    """
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "codex.cmd").write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setattr(cc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cc.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD;.BAT")

    assert cc.resolve_codex_cli(is_windows=True) == str(bin_dir / "codex.cmd")
    # ...and the same tree resolves to nothing under POSIX rules.
    assert cc.resolve_codex_cli(is_windows=False) is None


def test_posix_secondary_probe_finds_the_bare_name(monkeypatch, tmp_path):
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "codex").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(cc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cc.Path, "home", classmethod(lambda _cls: tmp_path))

    assert cc.resolve_codex_cli(is_windows=False) == str(bin_dir / "codex")


def test_pathext_suffixes_are_lowercased_and_semicolon_split(monkeypatch):
    """PATHEXT is semicolon-delimited on Windows regardless of ``os.pathsep``."""
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")
    names = [p.name for p in cc._pathext_candidates(  # noqa: SLF001
        Path("/anywhere/codex"), is_windows=True)]
    assert names == ["codex", "codex.exe", "codex.cmd"]

    posix = [p.name for p in cc._pathext_candidates(  # noqa: SLF001
        Path("/anywhere/codex"), is_windows=False)]
    assert posix == ["codex"]


def test_resolve_raises_not_installed_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(cc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cc.Path, "home", classmethod(lambda _cls: tmp_path))
    with pytest.raises(NotInstalledError):
        cc.ADAPTER.resolve()


# ---------------------------------------------------------------------------
# argv construction
# ---------------------------------------------------------------------------


def test_argv_runs_exec_with_json_and_reads_the_prompt_from_stdin(
    installed, tmp_path
):
    argv = cc.ADAPTER.build_argv(_session(tmp_path))
    assert argv[0] == FAKE_CLI
    assert argv[1] == "exec"
    assert "--json" in argv
    assert argv[-1] == "-", "the prompt must come from stdin, not argv"


def test_argv_omits_model_when_the_caller_did_not_choose_one(installed, tmp_path):
    assert "--model" not in cc.ADAPTER.build_argv(_session(tmp_path))


def test_argv_passes_the_callers_model_through(installed, tmp_path):
    argv = cc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"model_id": "operator-choice"})
    )
    assert argv[argv.index("--model") + 1] == "operator-choice"


def test_argv_takes_the_model_from_the_environment(installed, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_CODEX_MODEL", "env-choice")
    argv = cc.ADAPTER.build_argv(_session(tmp_path))
    assert argv[argv.index("--model") + 1] == "env-choice"


def test_metadata_model_beats_the_environment(installed, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_CODEX_MODEL", "env-choice")
    argv = cc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"model_id": "explicit"})
    )
    assert argv[argv.index("--model") + 1] == "explicit"


def test_sandbox_defaults_to_write_and_is_overridable(installed, tmp_path):
    argv = cc.ADAPTER.build_argv(_session(tmp_path))
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"

    argv = cc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"sandbox": "read-only"})
    )
    assert argv[argv.index("--sandbox") + 1] == "read-only"

    # "" opts out entirely, for a CLI build that predates the flag.
    argv = cc.ADAPTER.build_argv(_session(tmp_path, metadata={"sandbox": ""}))
    assert "--sandbox" not in argv


def test_extra_args_land_before_the_prompt_arg(installed, tmp_path):
    argv = cc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"extra_args": ["--color", "never"]})
    )
    assert argv[-3:] == ["--color", "never", "-"]


def test_skip_git_repo_check_is_opt_in(installed, tmp_path):
    assert "--skip-git-repo-check" not in cc.ADAPTER.build_argv(_session(tmp_path))
    argv = cc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"skip_git_repo_check": True})
    )
    assert "--skip-git-repo-check" in argv


def test_dispatch_tags_only_when_asked_for(installed, tmp_path):
    env = cc.ADAPTER.build_env(_session(tmp_path))
    assert "ICDEV_DISPATCH_SOURCE" not in env

    env = cc.ADAPTER.build_env(
        _session(tmp_path, metadata={"dispatch_source": "kanban",
                                     "env": {"FOO": "bar"}})
    )
    assert env["ICDEV_DISPATCH_SOURCE"] == "kanban"
    assert env["ICDEV_DISPATCH_TASK_ID"] == "exa-bench-01"
    assert env["FOO"] == "bar"


def test_prepare_prompt_prepends_the_system_prompt(tmp_path):
    session = _session(tmp_path, system_prompt="Be terse.")
    assert cc.ADAPTER.prepare_prompt(session) == "Be terse.\n\nShip the thing."
    assert cc.ADAPTER.prepare_prompt(_session(tmp_path)) == "Ship the thing."


# ---------------------------------------------------------------------------
# 4. Windows argv limit
# ---------------------------------------------------------------------------


def test_a_prompt_past_the_windows_argv_limit_goes_over_stdin(
    installed, tmp_path, monkeypatch
):
    """WinError 206: a real task prompt cannot ride on the command line."""
    huge = "x" * 40_000
    assert len(huge) > 32767

    recorder = _Recorder(stdout=_HAPPY_STREAM)
    monkeypatch.setattr(cc.subprocess, "run", recorder)

    cc.ADAPTER.invoke(_session(tmp_path, prompt=huge))

    assert huge in recorder.stdin_text
    assert sum(len(a) for a in recorder.argv) < 32767
    assert not any(huge[:100] in arg for arg in recorder.argv)
    assert recorder.kwargs["shell"] is False


def test_prompt_bytes_survive_unicode_and_crlf(installed, tmp_path, monkeypatch):
    """utf-8 + ``newline=''`` — the file is not re-encoded or line-translated."""
    prompt = "héllo — ✅\r\nsecond line\nthird"
    recorder = _Recorder(stdout=_HAPPY_STREAM)
    monkeypatch.setattr(cc.subprocess, "run", recorder)

    cc.ADAPTER.invoke(_session(tmp_path, prompt=prompt))

    assert recorder.stdin_text == prompt


def test_the_instruction_file_is_cleaned_up(installed, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    recorder = _Recorder(stdout=_HAPPY_STREAM)
    monkeypatch.setattr(cc.subprocess, "run", recorder)

    cc.ADAPTER.invoke(
        _session(tmp_path, metadata={"temp_dir": str(scratch)})
    )

    assert list(scratch.glob("*_codex_instr.txt")) == []


# ---------------------------------------------------------------------------
# 3. invoke() returns a populated AgentResult
# ---------------------------------------------------------------------------


def test_invoke_returns_a_populated_result(installed, tmp_path, monkeypatch):
    recorder = _Recorder(stdout=_HAPPY_STREAM)
    monkeypatch.setattr(cc.subprocess, "run", recorder)

    result = cc.ADAPTER.invoke(_session(tmp_path))

    assert result.task_id == "exa-bench-01"
    assert result.adapter_name == "codex_cli"
    assert result.completed is True
    assert result.exit_code == 0
    assert result.output == "Patched foo.py and ran the tests."
    assert result.error == ""
    assert result.duration_ms >= 0
    assert result.structured["session_id"] == "sess-7"
    assert result.structured["input_tokens"] == 1200
    assert result.structured["output_tokens"] == 340
    assert result.structured["tool_calls"] == 1
    assert result.structured["task_complete"] is True


def test_invoke_runs_in_the_sessions_working_dir(installed, tmp_path, monkeypatch):
    recorder = _Recorder(stdout=_HAPPY_STREAM)
    monkeypatch.setattr(cc.subprocess, "run", recorder)
    cc.ADAPTER.invoke(_session(tmp_path))
    assert recorder.kwargs["cwd"] == str(tmp_path)


def test_unreported_metrics_are_absent_not_zero(installed, tmp_path, monkeypatch):
    """A zero cost and an unreported cost are different facts."""
    recorder = _Recorder(stdout=_HAPPY_STREAM)
    monkeypatch.setattr(cc.subprocess, "run", recorder)

    structured = cc.ADAPTER.invoke(_session(tmp_path)).structured

    assert "total_cost_usd" not in structured
    assert "duration_api_ms" not in structured


def test_token_counts_absent_when_the_cli_did_not_report_them(
    installed, tmp_path, monkeypatch
):
    stream = _jsonl({"msg": {"type": "task_complete", "last_agent_message": "ok"}})
    monkeypatch.setattr(cc.subprocess, "run", _Recorder(stdout=stream))

    structured = cc.ADAPTER.invoke(_session(tmp_path)).structured

    assert "input_tokens" not in structured
    assert "output_tokens" not in structured


def test_non_zero_exit_is_reported_not_raised(installed, tmp_path, monkeypatch):
    recorder = _Recorder(stdout="", stderr="error: unexpected argument '--json'",
                         returncode=2)
    monkeypatch.setattr(cc.subprocess, "run", recorder)

    result = cc.ADAPTER.invoke(_session(tmp_path))

    assert result.completed is False
    assert result.exit_code == 2
    assert "unexpected argument" in result.error


def test_a_stream_error_is_not_completed(installed, tmp_path, monkeypatch):
    stream = _jsonl(
        {"msg": {"type": "agent_message", "message": "starting"}},
        {"msg": {"type": "error", "message": "model refused the request"}},
        {"msg": {"type": "task_complete", "last_agent_message": "gave up"}},
    )
    monkeypatch.setattr(cc.subprocess, "run", _Recorder(stdout=stream))

    result = cc.ADAPTER.invoke(_session(tmp_path))

    assert result.exit_code == 0
    assert result.completed is False
    assert result.structured["is_error"] is True


def test_timeout_is_reported_not_raised(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(
        cc.subprocess, "run",
        _Recorder(raises=subprocess.TimeoutExpired(cmd="codex", timeout=5)),
    )

    result = cc.ADAPTER.invoke(_session(tmp_path, timeout_seconds=5))

    assert result.completed is False
    assert result.exit_code == -1
    assert "timed out" in result.error


def test_a_missing_binary_raises_not_installed(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(
        cc.subprocess, "run", _Recorder(raises=FileNotFoundError("codex"))
    )
    with pytest.raises(NotInstalledError):
        cc.ADAPTER.invoke(_session(tmp_path))


def test_invoke_without_the_cli_raises_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(cc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cc.Path, "home", classmethod(lambda _cls: tmp_path))
    with pytest.raises(NotInstalledError):
        cc.ADAPTER.invoke(_session(tmp_path))


def test_plain_text_output_is_not_lost(installed, tmp_path, monkeypatch):
    """An older CLI that does not know ``--json`` still returns its answer."""
    monkeypatch.setattr(
        cc.subprocess, "run", _Recorder(stdout="I refactored the parser.\n")
    )
    result = cc.ADAPTER.invoke(_session(tmp_path))
    assert result.output == "I refactored the parser.\n"


# ---------------------------------------------------------------------------
# parsing: both event shapes, tolerantly
# ---------------------------------------------------------------------------


def test_parses_the_newer_item_event_shape():
    stream = _jsonl(
        {"type": "thread.started", "thread_id": "th-9"},
        {"type": "item.completed",
         "item": {"item_type": "agent_message", "text": "First reply"}},
        {"type": "item.completed",
         "item": {"item_type": "command_execution", "command": "ls"}},
        {"type": "item.completed",
         "item": {"item_type": "file_change", "unified_diff": "--- a\n+++ b\n"}},
        {"type": "turn.completed", "usage": {"input_tokens": 50,
                                             "output_tokens": 7}},
    )
    text, structured = cc._parse_codex_output(stream)  # noqa: SLF001

    assert text == "First reply"
    assert structured["task_complete"] is True
    assert structured["tool_calls"] == 2
    assert structured["input_tokens"] == 50
    assert structured["session_id"] == "th-9"
    assert structured["diff"] == "--- a\n+++ b\n"


def test_item_completed_does_not_count_as_task_completion():
    """A bare substring match on "complete" would end the run at message one."""
    stream = _jsonl(
        {"type": "item.completed",
         "item": {"item_type": "agent_message", "text": "still working"}},
    )
    _, structured = cc._parse_codex_output(stream)  # noqa: SLF001
    assert structured["task_complete"] is False


def test_streaming_deltas_do_not_duplicate_the_message():
    stream = _jsonl(
        {"msg": {"type": "agent_message_delta", "delta": "Pat"}},
        {"msg": {"type": "agent_message_delta", "delta": "ched"}},
        {"msg": {"type": "agent_message", "message": "Patched"}},
    )
    text, structured = cc._parse_codex_output(stream)  # noqa: SLF001
    assert text == "Patched"
    assert structured["turns"] == 1


def test_command_begin_and_end_count_as_one_tool_call():
    stream = _jsonl(
        {"msg": {"type": "exec_command_begin", "command": ["pytest"]}},
        {"msg": {"type": "exec_command_end", "exit_code": 0}},
    )
    _, structured = cc._parse_codex_output(stream)  # noqa: SLF001
    assert structured["tool_calls"] == 1


def test_unparseable_lines_are_skipped_not_fatal():
    stream = "warning: config not found\n" + _jsonl(
        {"msg": {"type": "task_complete", "last_agent_message": "fine"}}
    ) + "\n{not json}\n"
    text, structured = cc._parse_codex_output(stream)  # noqa: SLF001
    assert text == "fine"
    assert structured["events"] == 1


def test_parse_response_on_jsonl_and_on_plain_text():
    parsed = cc.ADAPTER.parse_response(_HAPPY_STREAM)
    assert parsed["content"] == "Patched foo.py and ran the tests."
    assert parsed["tool_call_count"] == 1
    assert parsed["tool_calls"] == []

    plain = cc.ADAPTER.parse_response("just words")
    assert plain["content"] == "just words"
    assert plain["diff"] == ""

    assert cc.ADAPTER.parse_response("")["content"] == ""


def test_detect_completion():
    assert cc.ADAPTER.detect_completion("") is False
    assert cc.ADAPTER.detect_completion(_HAPPY_STREAM) is True
    assert cc.ADAPTER.detect_completion("short") is False
    assert cc.ADAPTER.detect_completion("all set [DONE]") is True
    assert cc.ADAPTER.detect_completion("y" * 200) is True

    errored = _jsonl(
        {"msg": {"type": "error", "message": "boom"}},
        {"msg": {"type": "task_complete", "last_agent_message": "gave up"}},
    )
    assert cc.ADAPTER.detect_completion(errored) is False


# ---------------------------------------------------------------------------
# 5. LLM-agnostic: no model id in the module
# ---------------------------------------------------------------------------


def test_no_model_id_literal_in_the_module():
    """Mirrors tests/test_no_hardcoded_model_ids.py, scoped to this file.

    The model is the operator's choice, carried on the session or the
    environment. A literal here would pin one vendor into Python.
    """
    source = Path(cc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    selectors = {"model", "model_id", "model_name"}

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in selectors and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    offenders.append(kw.value.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) \
                        and target.id.lower().strip("_") in selectors \
                        and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str) \
                        and node.value.value:
                    offenders.append(node.value.value)

    assert offenders == []


def test_module_is_os_agnostic():
    """No hardcoded separators, no shell, and the one platform branch is two-sided."""
    source = Path(cc.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert 'encoding="utf-8"' in source
    assert 'newline=""' in source
    # os.name is compared, never assumed — and the non-nt path is the default.
    assert source.count('os.name == "nt"') == 1
    # ``~/.local/bin`` is composed with pathlib, never with a literal
    # separator, so the same code is correct on both OSes.
    assert '"/"' not in source and "'/'" not in source
    assert "\\\\" not in source


def test_mirrored_into_the_icdev_package():
    """The packaged copy is what a pip install ships (CLAUDE.md mirror rule)."""
    root = Path(__file__).resolve().parents[1]
    mirror = root / "icdev" / "tools" / "agents" / "adapters" / "codex_cli.py"
    assert mirror.is_file()
    assert mirror.read_text(encoding="utf-8") == \
        Path(cc.__file__).read_text(encoding="utf-8")
