# CUI // SP-CTI
"""exa-bench-02: tools/agents/adapters/goose_cli.py — the Block Goose harness.

Goose is the harness both omnigent and buzz integrate, and no adapter for it
existed anywhere in the tree. Covers the card's binding acceptance criteria:

  1. all five AgentAdapter Protocol methods are implemented,
  2. ``available()`` reflects REAL CLI presence (PATH + PATHEXT + the
     ``~/.local/bin`` secondary probe + the ``$ICDEV_GOOSE_CLI`` override),
  3. ``invoke()`` returns a POPULATED ``AgentResult`` — not a stub raise,
  4. the Windows 32767-char argv limit is avoided (instructions via stdin),
  5. no model id and no provider name appear in the module,
  6. the tests run identically on Windows and Linux,
  7. ``goose_cli`` is enabled but NOT in ``fallback_order``.

``_REAL_ENVELOPE`` below is not invented: it is the verbatim stdout of a live
``goose run --output-format json`` on goose 1.28.0. No test spawns the binary —
``subprocess.run`` is a recorder — so the suite is deterministic on a host that
has Goose and on one that does not.
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
from tools.agents.adapters import goose_cli as gc


FAKE_CLI = "/opt/bin/goose"

# Captured verbatim from goose 1.28.0:
#   goose run --no-session --no-profile --quiet --output-format json -t "..."
_REAL_ENVELOPE = json.dumps({
    "messages": [
        {"id": None, "role": "user", "created": 1786535179,
         "content": [{"type": "text", "text": "Reply with exactly: OK"}],
         "metadata": {"userVisible": True, "agentVisible": True}},
        {"id": "b989f2c0-10b1-4b22-8e34-690906ae6281", "role": "assistant",
         "created": 1786535180,
         "content": [{"type": "text", "text": "OK"}],
         "metadata": {"userVisible": True, "agentVisible": True}},
    ],
    "metadata": {"total_tokens": 6, "status": "completed"},
}, indent=2)

# The banner Goose prints to STDOUT ahead of the envelope when --quiet is off.
_BANNER = (
    "    __( O)>  ● new session · provider model\n"
    "   \\____)    20260812_1 · /tmp/work\n"
    "     L L     goose is ready\n"
)


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
    for var in ("ICDEV_GOOSE_CLI", "ICDEV_GOOSE_MODEL", "ICDEV_GOOSE_PROVIDER",
                "ICDEV_AGENT_ADAPTER", "ICDEV_DISPATCH_SOURCE",
                "ICDEV_DISPATCH_TASK_ID"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def installed(monkeypatch):
    """Pretend the CLI resolves, without touching PATH."""
    monkeypatch.setattr(gc, "resolve_goose_cli", lambda: FAKE_CLI)
    return FAKE_CLI


def _session(tmp_path: Path, **kwargs) -> AgentSession:
    return AgentSession(
        task_id=kwargs.pop("task_id", "exa-bench-02"),
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


def _envelope(messages, meta) -> str:
    return json.dumps({"messages": messages, "metadata": meta})


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# 1. Protocol conformance + registration
# ---------------------------------------------------------------------------


def test_satisfies_agent_adapter_protocol():
    assert isinstance(gc.ADAPTER, AgentAdapter)
    assert gc.ADAPTER.name == "goose_cli"


def test_implements_all_five_protocol_methods():
    for method in ("available", "prepare_prompt", "invoke",
                   "detect_completion", "parse_response"):
        assert callable(getattr(gc.ADAPTER, method)), method


def test_registered_in_the_registry():
    assert "goose_cli" in registry.list_adapters()
    assert registry.get_adapter("goose_cli") is gc.ADAPTER


def test_pick_default_returns_it_when_forced(monkeypatch):
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "goose_cli")
    assert registry.pick_default() is gc.ADAPTER


# ---------------------------------------------------------------------------
# 2. available() reflects real CLI presence
# ---------------------------------------------------------------------------


def test_available_false_when_nothing_resolves(monkeypatch, tmp_path):
    monkeypatch.setattr(gc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gc.Path, "home", classmethod(lambda _cls: tmp_path))
    assert gc.resolve_goose_cli() is None
    assert gc.ADAPTER.available() is False


def test_available_true_when_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(gc.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        gc.shutil, "which", lambda name: FAKE_CLI if name == "goose" else None
    )
    assert gc.resolve_goose_cli() == FAKE_CLI
    assert gc.ADAPTER.available() is True


def test_env_override_accepts_an_explicit_path(monkeypatch, tmp_path):
    binary = tmp_path / "my-goose"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("ICDEV_GOOSE_CLI", str(binary))
    monkeypatch.setattr(gc.shutil, "which", lambda _name: None)
    assert gc.resolve_goose_cli() == str(binary)


def test_env_override_accepts_a_bare_name(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_GOOSE_CLI", "goose-canary")
    monkeypatch.setattr(gc.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        gc.shutil, "which",
        lambda name: "/n/goose-canary" if name == "goose-canary" else None,
    )
    assert gc.resolve_goose_cli() == "/n/goose-canary"


def test_pathext_secondary_probe_finds_the_suffixed_binary(monkeypatch, tmp_path):
    """Goose's own installer puts the binary in ``~/.local/bin``.

    On Windows that is ``goose.exe``; the suffix-less name never exists. Both
    branches are driven by the explicit ``is_windows`` argument rather than by
    patching ``os.name``: patching it makes ``pathlib`` hand out a
    ``WindowsPath`` on Linux, which raises on construction.
    """
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "goose.exe").write_text("MZ", encoding="utf-8")

    monkeypatch.setattr(gc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gc.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD;.BAT")

    assert gc.resolve_goose_cli(is_windows=True) == str(bin_dir / "goose.exe")
    # ...and the same tree resolves to nothing under POSIX rules.
    assert gc.resolve_goose_cli(is_windows=False) is None


def test_posix_secondary_probe_finds_the_bare_name(monkeypatch, tmp_path):
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "goose").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(gc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gc.Path, "home", classmethod(lambda _cls: tmp_path))

    assert gc.resolve_goose_cli(is_windows=False) == str(bin_dir / "goose")


def test_pathext_suffixes_are_lowercased_and_semicolon_split(monkeypatch):
    """PATHEXT is semicolon-delimited on Windows regardless of ``os.pathsep``."""
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")
    names = [p.name for p in gc._pathext_candidates(  # noqa: SLF001
        Path("/anywhere/goose"), is_windows=True)]
    assert names == ["goose", "goose.exe", "goose.cmd"]

    posix = [p.name for p in gc._pathext_candidates(  # noqa: SLF001
        Path("/anywhere/goose"), is_windows=False)]
    assert posix == ["goose"]


def test_resolve_raises_not_installed_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(gc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gc.Path, "home", classmethod(lambda _cls: tmp_path))
    with pytest.raises(NotInstalledError):
        gc.ADAPTER.resolve()


# ---------------------------------------------------------------------------
# argv construction
# ---------------------------------------------------------------------------


def test_argv_runs_the_non_interactive_subcommand_with_json(installed, tmp_path):
    argv = gc.ADAPTER.build_argv(_session(tmp_path))
    assert argv[0] == FAKE_CLI
    assert argv[1] == "run"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[-2:] == ["-i", "-"], "instructions must come from stdin"


def test_automated_run_defaults_are_on(installed, tmp_path):
    """``--no-session`` is the vendor's own "useful for automated runs" switch."""
    argv = gc.ADAPTER.build_argv(_session(tmp_path))
    assert "--no-session" in argv
    assert "--quiet" in argv
    # ...and each is opt-out-able for an operator on a build that lacks it.
    argv = gc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"no_session": False, "quiet": False})
    )
    assert "--no-session" not in argv
    assert "--quiet" not in argv


def test_max_turns_comes_from_the_session(installed, tmp_path):
    """Goose honours the Protocol's turn budget; codex_cli has no equivalent."""
    argv = gc.ADAPTER.build_argv(_session(tmp_path, max_turns=7))
    assert argv[argv.index("--max-turns") + 1] == "7"


def test_profile_and_builtins_are_opt_in(installed, tmp_path):
    assert "--no-profile" not in gc.ADAPTER.build_argv(_session(tmp_path))
    argv = gc.ADAPTER.build_argv(_session(tmp_path, metadata={
        "no_profile": True, "with_builtin": "developer,memory",
        "max_tool_repetitions": 3,
    }))
    assert "--no-profile" in argv
    assert argv[argv.index("--with-builtin") + 1] == "developer"
    assert "memory" in argv
    assert argv[argv.index("--max-tool-repetitions") + 1] == "3"


def test_argv_omits_model_and_provider_when_the_caller_chose_neither(
    installed, tmp_path
):
    argv = gc.ADAPTER.build_argv(_session(tmp_path))
    assert "--model" not in argv
    assert "--provider" not in argv


def test_argv_passes_the_callers_model_and_provider_through(installed, tmp_path):
    argv = gc.ADAPTER.build_argv(_session(tmp_path, metadata={
        "model_id": "operator-choice", "provider": "operator-provider",
    }))
    assert argv[argv.index("--model") + 1] == "operator-choice"
    assert argv[argv.index("--provider") + 1] == "operator-provider"


def test_argv_takes_model_and_provider_from_the_environment(
    installed, tmp_path, monkeypatch
):
    monkeypatch.setenv("ICDEV_GOOSE_MODEL", "env-model")
    monkeypatch.setenv("ICDEV_GOOSE_PROVIDER", "env-provider")
    argv = gc.ADAPTER.build_argv(_session(tmp_path))
    assert argv[argv.index("--model") + 1] == "env-model"
    assert argv[argv.index("--provider") + 1] == "env-provider"


def test_metadata_beats_the_environment(installed, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_GOOSE_MODEL", "env-model")
    argv = gc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"model_id": "explicit"})
    )
    assert argv[argv.index("--model") + 1] == "explicit"


def test_extra_args_land_before_the_stdin_marker(installed, tmp_path):
    argv = gc.ADAPTER.build_argv(
        _session(tmp_path, metadata={"extra_args": ["--debug"]})
    )
    assert argv[-3:] == ["--debug", "-i", "-"]


def test_dispatch_tags_only_when_asked_for(installed, tmp_path):
    env = gc.ADAPTER.build_env(_session(tmp_path))
    assert "ICDEV_DISPATCH_SOURCE" not in env

    env = gc.ADAPTER.build_env(
        _session(tmp_path, metadata={"dispatch_source": "kanban",
                                     "env": {"FOO": "bar"}})
    )
    assert env["ICDEV_DISPATCH_SOURCE"] == "kanban"
    assert env["ICDEV_DISPATCH_TASK_ID"] == "exa-bench-02"
    assert env["FOO"] == "bar"


def test_system_prompt_is_prepended_not_put_on_argv(installed, tmp_path):
    """Goose HAS a ``--system`` flag; using it would risk the argv limit.

    System-plus-task is exactly the size that trips WinError 206, so both go
    over stdin. An operator who wants the native slot has ``extra_args``.
    """
    session = _session(tmp_path, system_prompt="Be terse.")
    assert gc.ADAPTER.prepare_prompt(session) == "Be terse.\n\nShip the thing."
    assert "--system" not in gc.ADAPTER.build_argv(session)
    assert gc.ADAPTER.prepare_prompt(_session(tmp_path)) == "Ship the thing."


# ---------------------------------------------------------------------------
# 4. Windows argv limit
# ---------------------------------------------------------------------------


def test_a_prompt_past_the_windows_argv_limit_goes_over_stdin(
    installed, tmp_path, monkeypatch
):
    """WinError 206: a real task prompt cannot ride on ``-t``."""
    huge = "x" * 40_000
    assert len(huge) > 32767

    recorder = _Recorder(stdout=_REAL_ENVELOPE)
    monkeypatch.setattr(gc.subprocess, "run", recorder)

    gc.ADAPTER.invoke(_session(tmp_path, prompt=huge))

    assert huge in recorder.stdin_text
    assert sum(len(a) for a in recorder.argv) < 32767
    assert "-t" not in recorder.argv
    assert not any(huge[:100] in arg for arg in recorder.argv)
    assert recorder.kwargs["shell"] is False


def test_prompt_bytes_survive_unicode_and_crlf(installed, tmp_path, monkeypatch):
    """utf-8 + ``newline=''`` — the file is not re-encoded or line-translated."""
    prompt = "héllo — ✅\r\nsecond line\nthird"
    recorder = _Recorder(stdout=_REAL_ENVELOPE)
    monkeypatch.setattr(gc.subprocess, "run", recorder)

    gc.ADAPTER.invoke(_session(tmp_path, prompt=prompt))

    assert recorder.stdin_text == prompt


def test_the_instruction_file_is_cleaned_up(installed, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(gc.subprocess, "run", _Recorder(stdout=_REAL_ENVELOPE))

    gc.ADAPTER.invoke(_session(tmp_path, metadata={"temp_dir": str(scratch)}))

    assert list(scratch.glob("*_goose_instr.txt")) == []


# ---------------------------------------------------------------------------
# 3. invoke() returns a populated AgentResult
# ---------------------------------------------------------------------------


def test_invoke_returns_a_populated_result(installed, tmp_path, monkeypatch):
    """Against the envelope goose 1.28.0 really emits."""
    monkeypatch.setattr(gc.subprocess, "run", _Recorder(stdout=_REAL_ENVELOPE))

    result = gc.ADAPTER.invoke(_session(tmp_path))

    assert result.task_id == "exa-bench-02"
    assert result.adapter_name == "goose_cli"
    assert result.completed is True
    assert result.exit_code == 0
    assert result.output == "OK"
    assert result.error == ""
    assert result.duration_ms >= 0
    assert result.structured["status"] == "completed"
    assert result.structured["task_complete"] is True
    assert result.structured["turns"] == 1
    assert result.structured["messages"] == 2
    assert result.structured["total_tokens"] == 6
    assert result.structured["final_message"] == "OK"


def test_invoke_runs_in_the_sessions_working_dir(installed, tmp_path, monkeypatch):
    recorder = _Recorder(stdout=_REAL_ENVELOPE)
    monkeypatch.setattr(gc.subprocess, "run", recorder)
    gc.ADAPTER.invoke(_session(tmp_path))
    assert recorder.kwargs["cwd"] == str(tmp_path)


def test_a_short_answer_is_still_complete(installed, tmp_path, monkeypatch):
    """The envelope's status is authoritative — "OK" is 2 chars, and done.

    The length heuristic the text-only adapters fall back on would call this
    unfinished; reading the status is the whole point of having an envelope.
    """
    monkeypatch.setattr(gc.subprocess, "run", _Recorder(stdout=_REAL_ENVELOPE))
    result = gc.ADAPTER.invoke(_session(tmp_path))
    assert result.completed is True
    assert len(result.output) < 100


def test_unreported_metrics_are_absent_not_zero(installed, tmp_path, monkeypatch):
    """A zero cost and an unreported cost are different facts."""
    monkeypatch.setattr(gc.subprocess, "run", _Recorder(stdout=_REAL_ENVELOPE))

    structured = gc.ADAPTER.invoke(_session(tmp_path)).structured

    for key in ("total_cost_usd", "duration_api_ms",
                "input_tokens", "output_tokens"):
        assert key not in structured


def test_token_count_absent_when_the_envelope_omitted_it(
    installed, tmp_path, monkeypatch
):
    stream = _envelope([_assistant("done")], {"status": "completed"})
    monkeypatch.setattr(gc.subprocess, "run", _Recorder(stdout=stream))

    structured = gc.ADAPTER.invoke(_session(tmp_path)).structured

    assert "total_tokens" not in structured


def test_the_unconfigured_model_panic_is_reported_not_raised(
    installed, tmp_path, monkeypatch
):
    """Observed on goose 1.28.0: exit 101, a Rust panic on stderr, no stdout."""
    panic = ("thread 'main' panicked at crates/goose-cli/src/session/"
             "builder.rs:371:10:\nNo model configured. Run 'goose configure'")
    monkeypatch.setattr(
        gc.subprocess, "run", _Recorder(stdout="", stderr=panic, returncode=101)
    )

    result = gc.ADAPTER.invoke(_session(tmp_path))

    assert result.completed is False
    assert result.exit_code == 101
    assert "No model configured" in result.error


def test_an_error_status_is_not_completed(installed, tmp_path, monkeypatch):
    stream = _envelope([_assistant("I gave up.")], {"status": "error"})
    monkeypatch.setattr(gc.subprocess, "run", _Recorder(stdout=stream))

    result = gc.ADAPTER.invoke(_session(tmp_path))

    assert result.exit_code == 0
    assert result.completed is False
    assert result.structured["is_error"] is True


def test_an_unknown_status_is_reported_verbatim_not_forced(
    installed, tmp_path, monkeypatch
):
    """A closed vocabulary this adapter invented would mislabel a new status."""
    stream = _envelope([_assistant("hit the ceiling")],
                       {"status": "max_turns_reached"})
    monkeypatch.setattr(gc.subprocess, "run", _Recorder(stdout=stream))

    structured = gc.ADAPTER.invoke(_session(tmp_path)).structured

    assert structured["status"] == "max_turns_reached"
    assert structured["task_complete"] is False
    assert structured["is_error"] is False


def test_timeout_is_reported_not_raised(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(
        gc.subprocess, "run",
        _Recorder(raises=subprocess.TimeoutExpired(cmd="goose", timeout=5)),
    )

    result = gc.ADAPTER.invoke(_session(tmp_path, timeout_seconds=5))

    assert result.completed is False
    assert result.exit_code == -1
    assert "timed out" in result.error


def test_a_missing_binary_raises_not_installed(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(
        gc.subprocess, "run", _Recorder(raises=FileNotFoundError("goose"))
    )
    with pytest.raises(NotInstalledError):
        gc.ADAPTER.invoke(_session(tmp_path))


def test_invoke_without_the_cli_raises_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(gc.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gc.Path, "home", classmethod(lambda _cls: tmp_path))
    with pytest.raises(NotInstalledError):
        gc.ADAPTER.invoke(_session(tmp_path))


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_the_banner_does_not_hide_the_envelope():
    """Goose prints its banner to STDOUT, so the JSON does not start at byte 0."""
    text, structured = gc._parse_goose_output(  # noqa: SLF001
        _BANNER + _REAL_ENVELOPE)
    assert text == "OK"
    assert structured["task_complete"] is True


def test_a_brace_inside_a_message_does_not_end_the_document():
    """String-aware brace counting: agent text routinely contains braces."""
    stream = _envelope(
        [_assistant('use {"a": 1} and then }{ this')],
        {"status": "completed", "total_tokens": 9},
    )
    text, structured = gc._parse_goose_output(stream)  # noqa: SLF001
    assert text == 'use {"a": 1} and then }{ this'
    assert structured["total_tokens"] == 9


def test_an_escaped_quote_does_not_end_the_string():
    stream = _envelope([_assistant('he said \\"no\\" loudly')],
                       {"status": "completed"})
    text, _ = gc._parse_goose_output(stream)  # noqa: SLF001
    assert "no" in text


def test_every_assistant_turn_is_kept_and_the_last_is_flagged():
    """There is no ``result`` field to prefer, so the transcript IS the output."""
    stream = _envelope(
        [{"role": "user", "content": [{"type": "text", "text": "go"}]},
         _assistant("Reading the tree."),
         _assistant("Patched foo.py.")],
        {"status": "completed"},
    )
    text, structured = gc._parse_goose_output(stream)  # noqa: SLF001
    assert text == "Reading the tree.\n\nPatched foo.py."
    assert structured["turns"] == 2
    assert structured["final_message"] == "Patched foo.py."


def test_user_messages_are_not_echoed_back_as_output():
    text, _ = gc._parse_goose_output(_REAL_ENVELOPE)  # noqa: SLF001
    assert "Reply with exactly" not in text


def test_tool_requests_are_counted():
    stream = _envelope(
        [{"role": "assistant", "content": [
            {"type": "toolRequest", "id": "1"},
            {"type": "text", "text": "Listing files."}]},
         {"role": "user", "content": [{"type": "toolResponse", "id": "1"}]}],
        {"status": "completed"},
    )
    _, structured = gc._parse_goose_output(stream)  # noqa: SLF001
    assert structured["tool_calls"] == 1


def test_plain_text_output_is_not_lost():
    """A build that printed something else still returns its answer."""
    text, structured = gc._parse_goose_output(  # noqa: SLF001
        "I refactored the parser.\n")
    assert text == "I refactored the parser.\n"
    assert structured == {}


def test_a_truncated_envelope_degrades_instead_of_raising():
    truncated = _REAL_ENVELOPE[:80]
    text, structured = gc._parse_goose_output(truncated)  # noqa: SLF001
    assert text == truncated
    assert structured == {}


def test_parse_response_on_the_envelope_and_on_plain_text():
    parsed = gc.ADAPTER.parse_response(_REAL_ENVELOPE)
    assert parsed["content"] == "OK"
    assert parsed["tool_calls"] == []
    assert parsed["tool_call_count"] == 0
    assert parsed["diff"] == ""

    plain = gc.ADAPTER.parse_response("just words")
    assert plain["content"] == "just words"

    assert gc.ADAPTER.parse_response("")["content"] == ""


def test_detect_completion():
    assert gc.ADAPTER.detect_completion("") is False
    assert gc.ADAPTER.detect_completion(_REAL_ENVELOPE) is True
    assert gc.ADAPTER.detect_completion("short") is False
    assert gc.ADAPTER.detect_completion("all set [DONE]") is True
    assert gc.ADAPTER.detect_completion("y" * 200) is True

    errored = _envelope([_assistant("gave up")], {"status": "error"})
    assert gc.ADAPTER.detect_completion(errored) is False


# ---------------------------------------------------------------------------
# 7. Enabled, but nothing routes to it yet
# ---------------------------------------------------------------------------


def _config() -> dict:
    path = Path(registry._CONFIG_PATH)  # noqa: SLF001
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_enabled_in_the_yaml_config():
    assert "goose_cli" in (_config().get("enabled_adapters") or [])


def test_not_in_fallback_order_until_the_capability_probe_lands():
    """exa-bench-03 decides when it is ready; until then nothing falls back."""
    assert "goose_cli" not in (_config().get("fallback_order") or [])


def test_not_a_per_task_type_preference():
    prefs = _config().get("per_task_type_preference") or {}
    assert "goose_cli" not in prefs.values()


class _Stub:
    """Minimal stand-in — ``pick_default`` only ever calls ``available()``."""

    def __init__(self, name: str, ok: bool):
        self.name = name
        self._ok = ok

    def available(self) -> bool:
        return self._ok


def test_enabling_it_does_not_move_selection_for_existing_consumers(monkeypatch):
    """Hermetic: no real adapter is probed, so the result is host-independent."""
    claude, goose = _Stub("claude_cli", True), _Stub("goose_cli", True)
    monkeypatch.setattr(
        registry, "_REGISTRY", {"claude_cli": claude, "goose_cli": goose}
    )
    cfg = {
        "enabled_adapters": ["claude_cli", "goose_cli"],
        "per_task_type_preference": {"build": "claude_cli"},
        "fallback_order": ["claude_cli"],
    }
    assert registry.pick_default("build", config=cfg) is claude
    assert registry.pick_default("research", config=cfg) is claude


def test_it_is_still_reachable_as_a_last_resort(monkeypatch):
    """Enabled means selectable when literally nothing else resolves."""
    goose = _Stub("goose_cli", True)
    monkeypatch.setattr(registry, "_REGISTRY", {"goose_cli": goose})
    cfg = {"enabled_adapters": ["goose_cli"],
           "per_task_type_preference": {}, "fallback_order": []}
    assert registry.pick_default("build", config=cfg) is goose


# ---------------------------------------------------------------------------
# 5/6. LLM-agnostic and OS-agnostic
# ---------------------------------------------------------------------------


def test_no_model_id_literal_in_the_module():
    """Mirrors tests/test_no_hardcoded_model_ids.py, scoped to this file.

    Goose takes a provider AND a model; both are the operator's choice, carried
    on the session or the environment. A literal here would pin one vendor into
    Python.
    """
    source = Path(gc.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    selectors = {"model", "model_id", "model_name", "provider"}

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
    """No hardcoded separators, no shell, and the platform branch is two-sided."""
    source = Path(gc.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert 'encoding="utf-8"' in source
    assert 'newline=""' in source
    # os.name is compared once, in the two-sided helper, and never assumed.
    assert source.count('os.name == "nt"') == 1
    # Every path is composed with pathlib, never with a literal separator.
    assert '"/"' not in source and "'/'" not in source
    # The one escaped backslash is JSON's string-escape character in the
    # envelope scanner — not a Windows path separator.
    assert source.count('"\\\\"') == 1


def test_mirrored_into_the_icdev_package():
    """The packaged copy is what a pip install ships (CLAUDE.md mirror rule)."""
    root = Path(__file__).resolve().parents[1]
    mirror = root / "icdev" / "tools" / "agents" / "adapters" / "goose_cli.py"
    assert mirror.is_file()
    assert mirror.read_text(encoding="utf-8") == \
        Path(gc.__file__).read_text(encoding="utf-8")
