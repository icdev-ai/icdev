# CUI // SP-CTI
"""exa-bench-02: tools/agents/adapters/copilot_cli.py — the Copilot CLI harness.

Covers the card's binding acceptance criteria:

  1. all five AgentAdapter Protocol methods are implemented,
  2. ``available()`` is CORRECT — the ``return False and (...)`` short-circuit
     is gone, and ``gh`` on PATH does NOT count as this harness being present,
  3. ``invoke()`` returns a POPULATED ``AgentResult`` — not a stub raise,
  4. the Windows 32767-char argv limit is avoided (prompt via stdin),
  5. no model id appears in the module,
  6. the tests run identically on Windows and Linux,
  7. ``copilot_cli`` is enabled but NOT in ``fallback_order``.

No test touches a real ``copilot`` binary: resolution is monkeypatched or a
file is planted in ``tmp_path``, and ``subprocess.run`` is replaced by a
recorder, so the suite is deterministic on a host that has the CLI and on one
that does not.
"""
from __future__ import annotations

import ast
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
from tools.agents.adapters import copilot_cli as cp


FAKE_CLI = "/opt/bin/copilot"


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
    for var in ("ICDEV_COPILOT_CLI", "ICDEV_COPILOT_MODEL",
                "ICDEV_COPILOT_ALLOW_ALL", "ICDEV_COPILOT_ALLOW_TOOL",
                "ICDEV_AGENT_ADAPTER", "ICDEV_DISPATCH_SOURCE",
                "ICDEV_DISPATCH_TASK_ID", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def installed(monkeypatch):
    """Pretend the CLI resolves, without touching PATH."""
    monkeypatch.setattr(cp, "resolve_copilot_cli", lambda: FAKE_CLI)
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


_ANSWER = (
    "I inspected the branch and summarised the changes. Three files moved, "
    "the parser was rewritten, and the tests pass."
)


# ---------------------------------------------------------------------------
# 1. Protocol conformance + registration
# ---------------------------------------------------------------------------


def test_satisfies_agent_adapter_protocol():
    assert isinstance(cp.ADAPTER, AgentAdapter)
    assert cp.ADAPTER.name == "copilot_cli"


def test_implements_all_five_protocol_methods():
    for method in ("available", "prepare_prompt", "invoke",
                   "detect_completion", "parse_response"):
        assert callable(getattr(cp.ADAPTER, method)), method


def test_registered_in_the_registry():
    assert "copilot_cli" in registry.list_adapters()
    assert registry.get_adapter("copilot_cli") is cp.ADAPTER


def test_pick_default_returns_it_when_forced(monkeypatch):
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "copilot_cli")
    assert registry.pick_default() is cp.ADAPTER


# ---------------------------------------------------------------------------
# 2. available() is CORRECT — this is the whole bug
# ---------------------------------------------------------------------------


def test_the_short_circuit_stub_is_gone():
    """``return False and (...)`` could not report available under ANY input.

    Asserted on the AST rather than on behaviour because behaviour cannot
    distinguish "correctly reports absent" from "hardcoded to absent" on a host
    that does not have the CLI — which is every CI runner.
    """
    source = Path(cp.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    available = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "available"
    )
    constant_operands = [
        operand
        for node in ast.walk(available)
        if isinstance(node, ast.BoolOp)
        for operand in node.values
        if isinstance(operand, ast.Constant)
    ]
    assert constant_operands == [], "a constant in a BoolOp short-circuits"

    returns = [
        node for node in ast.walk(available) if isinstance(node, ast.Return)
    ]
    assert any(not isinstance(node.value, ast.Constant) for node in returns), \
        "available() must depend on something"


def test_gh_on_path_does_not_count_as_the_harness_being_installed(
    monkeypatch, tmp_path
):
    """The stub probed for ``gh``. ``gh copilot`` DOWNLOADS the CLI when absent.

    So ``gh`` present means "this host could go and fetch a harness", which is
    not what ``available()`` asks — and a Protocol that promises a cheap local
    check must not hand back an adapter whose first act is a network install.
    """
    monkeypatch.setattr(
        cp.shutil, "which",
        lambda name: "/usr/bin/gh" if name == "gh" else None,
    )
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))

    assert cp.resolve_copilot_cli() is None
    assert cp.ADAPTER.available() is False


def test_available_false_when_nothing_resolves(monkeypatch, tmp_path):
    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    assert cp.resolve_copilot_cli() is None
    assert cp.ADAPTER.available() is False


def test_available_true_when_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        cp.shutil, "which",
        lambda name: FAKE_CLI if name == "copilot" else None,
    )
    assert cp.resolve_copilot_cli() == FAKE_CLI
    assert cp.ADAPTER.available() is True


def test_env_override_accepts_an_explicit_path(monkeypatch, tmp_path):
    binary = tmp_path / "my-copilot"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("ICDEV_COPILOT_CLI", str(binary))
    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    assert cp.resolve_copilot_cli() == str(binary)


def test_env_override_accepts_a_bare_name(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_COPILOT_CLI", "copilot-nightly")
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        cp.shutil, "which",
        lambda name: "/n/copilot-nightly" if name == "copilot-nightly" else None,
    )
    assert cp.resolve_copilot_cli() == "/n/copilot-nightly"


def test_pathext_secondary_probe_finds_the_suffixed_binary(monkeypatch, tmp_path):
    """The suffix-less ``~/.local/bin/copilot`` never exists on Windows.

    Both branches are driven by the explicit ``is_windows`` argument rather
    than by patching ``os.name``: patching it makes ``pathlib`` hand out a
    ``WindowsPath`` on Linux, which raises on construction — so the Windows
    branch would be untestable on the CI runner.
    """
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "copilot.cmd").write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD;.BAT")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))

    assert cp.resolve_copilot_cli(is_windows=True) == str(bin_dir / "copilot.cmd")
    # ...and the same tree resolves to nothing under POSIX rules.
    assert cp.resolve_copilot_cli(is_windows=False) is None


def test_posix_secondary_probe_finds_the_bare_name(monkeypatch, tmp_path):
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "copilot").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))

    assert cp.resolve_copilot_cli(is_windows=False) == str(bin_dir / "copilot")


def test_pathext_suffixes_are_lowercased_and_semicolon_split(monkeypatch):
    """PATHEXT is semicolon-delimited on Windows regardless of ``os.pathsep``."""
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")
    names = [p.name for p in cp._pathext_candidates(  # noqa: SLF001
        Path("/anywhere/copilot"), is_windows=True)]
    assert names == ["copilot", "copilot.exe", "copilot.cmd"]

    posix = [p.name for p in cp._pathext_candidates(  # noqa: SLF001
        Path("/anywhere/copilot"), is_windows=False)]
    assert posix == ["copilot"]


def test_a_cli_gh_already_downloaded_does_count(monkeypatch, tmp_path):
    """``gh`` present is not installed; a binary ``gh`` FETCHED is installed."""
    managed = tmp_path / "AppData" / "GitHub CLI" / "copilot"
    managed.mkdir(parents=True)
    (managed / "copilot.exe").write_text("MZ", encoding="utf-8")

    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")

    assert cp.resolve_copilot_cli(is_windows=True) == str(managed / "copilot.exe")


def test_the_managed_path_may_be_the_binary_itself(monkeypatch, tmp_path):
    """Which of the two shapes ``gh`` writes has changed across releases."""
    data = tmp_path / "share"
    (data / "gh").mkdir(parents=True)
    (data / "gh" / "copilot").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))

    assert cp.resolve_copilot_cli(is_windows=False) == str(data / "gh" / "copilot")


def test_posix_managed_dir_defaults_under_the_home_share_dir(
    monkeypatch, tmp_path
):
    managed = tmp_path / ".local" / "share" / "gh" / "copilot"
    managed.mkdir(parents=True)
    (managed / "copilot").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))

    assert cp.resolve_copilot_cli(is_windows=False) == str(managed / "copilot")


def test_windows_managed_dir_is_skipped_without_localappdata(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    assert cp.resolve_copilot_cli(is_windows=True) is None


def test_resolve_raises_not_installed_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    with pytest.raises(NotInstalledError) as excinfo:
        cp.ADAPTER.resolve()
    assert "gh" in str(excinfo.value), "the message must explain the gh trap"


# ---------------------------------------------------------------------------
# argv construction
# ---------------------------------------------------------------------------


def test_argv_never_carries_the_prompt_flag(installed, tmp_path):
    """``-p`` would make the CLI IGNORE the piped prompt — silent truncation."""
    argv = cp.ADAPTER.build_argv(_session(tmp_path))
    assert argv[0] == FAKE_CLI
    assert "-p" not in argv
    assert "--prompt" not in argv
    assert not any(arg.startswith("--prompt=") for arg in argv)


def test_argv_suppresses_decoration_by_default(installed, tmp_path):
    assert "-s" in cp.ADAPTER.build_argv(_session(tmp_path))
    argv = cp.ADAPTER.build_argv(
        _session(tmp_path, metadata={"suppress_decoration": False})
    )
    assert "-s" not in argv


def test_no_ask_user_is_on_by_default(installed, tmp_path):
    """Stdin holds the prompt, so a clarifying question would hang the run."""
    assert "--no-ask-user" in cp.ADAPTER.build_argv(_session(tmp_path))
    argv = cp.ADAPTER.build_argv(
        _session(tmp_path, metadata={"no_ask_user": False})
    )
    assert "--no-ask-user" not in argv


def test_auto_approval_is_off_by_default(installed, tmp_path):
    """exa-bench-04 is auditing claude_cli's unconditional permission bypass.

    A new adapter must not add a second instance of it, so removing the tool
    confirmation is opt-in per session or per host — never a default.
    """
    assert "--allow-all-tools" not in cp.ADAPTER.build_argv(_session(tmp_path))


def test_auto_approval_opts_in_per_session_and_per_host(
    installed, tmp_path, monkeypatch
):
    argv = cp.ADAPTER.build_argv(
        _session(tmp_path, metadata={"allow_all_tools": True})
    )
    assert "--allow-all-tools" in argv

    monkeypatch.setenv("ICDEV_COPILOT_ALLOW_ALL", "1")
    assert "--allow-all-tools" in cp.ADAPTER.build_argv(_session(tmp_path))

    # An explicit False on the session still beats the host-wide switch.
    argv = cp.ADAPTER.build_argv(
        _session(tmp_path, metadata={"allow_all_tools": False})
    )
    assert "--allow-all-tools" not in argv


def test_narrow_tool_grants_pass_through(installed, tmp_path):
    argv = cp.ADAPTER.build_argv(_session(tmp_path, metadata={
        "allow_tool": ["shell(git)", "write"],
        "deny_tool": "shell(rm)",
        "add_dir": ["/srv/repo"],
        "secret_env_vars": "GH_TOKEN,COPILOT_GITHUB_TOKEN",
    }))
    assert "--allow-tool=shell(git)" in argv
    assert "--allow-tool=write" in argv
    assert "--deny-tool=shell(rm)" in argv
    assert "--add-dir=/srv/repo" in argv
    assert "--secret-env-vars=GH_TOKEN" in argv
    assert "--secret-env-vars=COPILOT_GITHUB_TOKEN" in argv


def test_allow_tool_can_come_from_the_environment(
    installed, tmp_path, monkeypatch
):
    monkeypatch.setenv("ICDEV_COPILOT_ALLOW_TOOL", "shell(git) , write")
    argv = cp.ADAPTER.build_argv(_session(tmp_path))
    assert "--allow-tool=shell(git)" in argv
    assert "--allow-tool=write" in argv


def test_argv_omits_model_when_the_caller_did_not_choose_one(installed, tmp_path):
    assert not any(
        arg.startswith("--model") for arg in cp.ADAPTER.build_argv(_session(tmp_path))
    )


def test_argv_passes_the_callers_model_through(installed, tmp_path):
    argv = cp.ADAPTER.build_argv(
        _session(tmp_path, metadata={"model_id": "operator-choice"})
    )
    assert "--model=operator-choice" in argv


def test_argv_takes_the_model_from_the_environment(
    installed, tmp_path, monkeypatch
):
    monkeypatch.setenv("ICDEV_COPILOT_MODEL", "env-choice")
    assert "--model=env-choice" in cp.ADAPTER.build_argv(_session(tmp_path))


def test_metadata_model_beats_the_environment(installed, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_COPILOT_MODEL", "env-choice")
    argv = cp.ADAPTER.build_argv(
        _session(tmp_path, metadata={"model_id": "explicit"})
    )
    assert "--model=explicit" in argv
    assert "--model=env-choice" not in argv


def test_extra_args_land_last(installed, tmp_path):
    argv = cp.ADAPTER.build_argv(
        _session(tmp_path, metadata={"extra_args": ["--no-color"]})
    )
    assert argv[-1] == "--no-color"


def test_dispatch_tags_only_when_asked_for(installed, tmp_path):
    env = cp.ADAPTER.build_env(_session(tmp_path))
    assert "ICDEV_DISPATCH_SOURCE" not in env

    env = cp.ADAPTER.build_env(
        _session(tmp_path, metadata={"dispatch_source": "kanban",
                                     "env": {"FOO": "bar"}})
    )
    assert env["ICDEV_DISPATCH_SOURCE"] == "kanban"
    assert env["ICDEV_DISPATCH_TASK_ID"] == "exa-bench-02"
    assert env["FOO"] == "bar"


def test_prepare_prompt_prepends_the_system_prompt(tmp_path):
    session = _session(tmp_path, system_prompt="Be terse.")
    assert cp.ADAPTER.prepare_prompt(session) == "Be terse.\n\nShip the thing."
    assert cp.ADAPTER.prepare_prompt(_session(tmp_path)) == "Ship the thing."


# ---------------------------------------------------------------------------
# 4. Windows argv limit
# ---------------------------------------------------------------------------


def test_a_prompt_past_the_windows_argv_limit_goes_over_stdin(
    installed, tmp_path, monkeypatch
):
    """WinError 206: a real task prompt cannot ride on the command line."""
    huge = "x" * 40_000
    assert len(huge) > 32767

    recorder = _Recorder(stdout=_ANSWER)
    monkeypatch.setattr(cp.subprocess, "run", recorder)

    cp.ADAPTER.invoke(_session(tmp_path, prompt=huge))

    assert huge in recorder.stdin_text
    assert sum(len(a) for a in recorder.argv) < 32767
    assert not any(huge[:100] in arg for arg in recorder.argv)
    assert recorder.kwargs["shell"] is False


def test_prompt_bytes_survive_unicode_and_crlf(installed, tmp_path, monkeypatch):
    """utf-8 + ``newline=''`` — the file is not re-encoded or line-translated."""
    prompt = "héllo — ✅\r\nsecond line\nthird"
    recorder = _Recorder(stdout=_ANSWER)
    monkeypatch.setattr(cp.subprocess, "run", recorder)

    cp.ADAPTER.invoke(_session(tmp_path, prompt=prompt))

    assert recorder.stdin_text == prompt


def test_the_instruction_file_is_cleaned_up(installed, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(cp.subprocess, "run", _Recorder(stdout=_ANSWER))

    cp.ADAPTER.invoke(_session(tmp_path, metadata={"temp_dir": str(scratch)}))

    assert list(scratch.glob("*_copilot_instr.txt")) == []


# ---------------------------------------------------------------------------
# 3. invoke() returns a populated AgentResult
# ---------------------------------------------------------------------------


def test_invoke_returns_a_populated_result(installed, tmp_path, monkeypatch):
    recorder = _Recorder(stdout=_ANSWER)
    monkeypatch.setattr(cp.subprocess, "run", recorder)

    result = cp.ADAPTER.invoke(_session(tmp_path))

    assert result.task_id == "exa-bench-02"
    assert result.adapter_name == "copilot_cli"
    assert result.completed is True
    assert result.exit_code == 0
    assert result.output == _ANSWER
    assert result.error == ""
    assert result.duration_ms >= 0


def test_invoke_runs_in_the_sessions_working_dir(installed, tmp_path, monkeypatch):
    recorder = _Recorder(stdout=_ANSWER)
    monkeypatch.setattr(cp.subprocess, "run", recorder)
    cp.ADAPTER.invoke(_session(tmp_path))
    assert recorder.kwargs["cwd"] == str(tmp_path)


def test_unreported_metrics_are_absent_not_zero(installed, tmp_path, monkeypatch):
    """A zero cost and an unreported cost are different facts.

    Copilot's programmatic mode is plain text — no envelope at all — so the
    flag says which of the two the exa-bench-03 probe is looking at.
    """
    monkeypatch.setattr(cp.subprocess, "run", _Recorder(stdout=_ANSWER))

    structured = cp.ADAPTER.invoke(_session(tmp_path)).structured

    assert structured["machine_readable"] is False
    for key in ("total_cost_usd", "input_tokens", "output_tokens",
                "turns", "tool_calls"):
        assert key not in structured


def test_non_zero_exit_is_reported_not_raised(installed, tmp_path, monkeypatch):
    recorder = _Recorder(stdout="", stderr="error: unknown option '--no-ask-user'",
                         returncode=2)
    monkeypatch.setattr(cp.subprocess, "run", recorder)

    result = cp.ADAPTER.invoke(_session(tmp_path))

    assert result.completed is False
    assert result.exit_code == 2
    assert "unknown option" in result.error


def test_a_missing_token_is_reported_not_raised(installed, tmp_path, monkeypatch):
    """No COPILOT_GITHUB_TOKEN is a backend failure, not "not installed"."""
    monkeypatch.setattr(
        cp.subprocess, "run",
        _Recorder(stderr="error: not authenticated", returncode=1),
    )
    result = cp.ADAPTER.invoke(_session(tmp_path))
    assert result.completed is False
    assert "not authenticated" in result.error


def test_timeout_is_reported_not_raised(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(
        cp.subprocess, "run",
        _Recorder(raises=subprocess.TimeoutExpired(cmd="copilot", timeout=5)),
    )

    result = cp.ADAPTER.invoke(_session(tmp_path, timeout_seconds=5))

    assert result.completed is False
    assert result.exit_code == -1
    assert "timed out" in result.error


def test_a_missing_binary_raises_not_installed(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(
        cp.subprocess, "run", _Recorder(raises=FileNotFoundError("copilot"))
    )
    with pytest.raises(NotInstalledError):
        cp.ADAPTER.invoke(_session(tmp_path))


def test_invoke_without_the_cli_raises_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(cp.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cp.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    with pytest.raises(NotInstalledError):
        cp.ADAPTER.invoke(_session(tmp_path))


# ---------------------------------------------------------------------------
# protocol tail
# ---------------------------------------------------------------------------


def test_detect_completion():
    assert cp.ADAPTER.detect_completion("") is False
    assert cp.ADAPTER.detect_completion("short") is False
    assert cp.ADAPTER.detect_completion("all set [DONE]") is True
    assert cp.ADAPTER.detect_completion(_ANSWER) is True


def test_parse_response_reports_only_what_the_harness_gives():
    """A fenced diff in prose is a patch DESCRIBED, not a patch applied.

    Mining one would give the cross-adapter comparison a column copilot has not
    earned, so ``diff`` stays empty.
    """
    described = "Here is the change:\n\n```diff\n--- a\n+++ b\n```\n"
    parsed = cp.ADAPTER.parse_response(described)
    assert parsed["content"] == described
    assert parsed["tool_calls"] == []
    assert parsed["diff"] == ""
    assert cp.ADAPTER.parse_response("")["content"] == ""


# ---------------------------------------------------------------------------
# 7. Enabled, but nothing routes to it yet
# ---------------------------------------------------------------------------


def _config() -> dict:
    path = Path(registry._CONFIG_PATH)  # noqa: SLF001
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_enabled_in_the_yaml_config():
    assert "copilot_cli" in (_config().get("enabled_adapters") or [])


def test_not_in_fallback_order_until_the_capability_probe_lands():
    """exa-bench-03 decides when it is ready; until then nothing falls back."""
    assert "copilot_cli" not in (_config().get("fallback_order") or [])


def test_not_a_per_task_type_preference():
    prefs = _config().get("per_task_type_preference") or {}
    assert "copilot_cli" not in prefs.values()


class _Stub:
    """Minimal stand-in — ``pick_default`` only ever calls ``available()``."""

    def __init__(self, name: str, ok: bool):
        self.name = name
        self._ok = ok

    def available(self) -> bool:
        return self._ok


def test_enabling_it_does_not_move_selection_for_existing_consumers(monkeypatch):
    """Hermetic: no real adapter is probed, so the result is host-independent."""
    claude, copilot = _Stub("claude_cli", True), _Stub("copilot_cli", True)
    monkeypatch.setattr(
        registry, "_REGISTRY", {"claude_cli": claude, "copilot_cli": copilot}
    )
    cfg = {
        "enabled_adapters": ["claude_cli", "copilot_cli"],
        "per_task_type_preference": {"build": "claude_cli"},
        "fallback_order": ["claude_cli"],
    }
    assert registry.pick_default("build", config=cfg) is claude
    assert registry.pick_default("research", config=cfg) is claude


# ---------------------------------------------------------------------------
# 5/6. LLM-agnostic and OS-agnostic
# ---------------------------------------------------------------------------


def test_no_model_id_literal_in_the_module():
    """Mirrors tests/test_no_hardcoded_model_ids.py, scoped to this file.

    The model is the operator's choice, carried on the session or the
    environment. A literal here would pin one vendor into Python.
    """
    source = Path(cp.__file__).read_text(encoding="utf-8")
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
    """No hardcoded separators, no shell, and the platform branch is two-sided."""
    source = Path(cp.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert 'encoding="utf-8"' in source
    assert 'newline=""' in source
    # os.name is compared once, in the two-sided helper, and never assumed.
    assert source.count('os.name == "nt"') == 1
    # Every path is composed with pathlib, never with a literal separator.
    assert '"/"' not in source and "'/'" not in source
    assert "\\\\" not in source


def test_mirrored_into_the_icdev_package():
    """The packaged copy is what a pip install ships (CLAUDE.md mirror rule)."""
    root = Path(__file__).resolve().parents[1]
    mirror = root / "icdev" / "tools" / "agents" / "adapters" / "copilot_cli.py"
    assert mirror.is_file()
    assert mirror.read_text(encoding="utf-8") == \
        Path(cp.__file__).read_text(encoding="utf-8")
