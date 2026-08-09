# CUI // SP-CTI
"""hgx-exec-03: the runner picks its executor through the adapter registry.

Two things were true before this and should never be true again:

1. ``tools/agents/registry.pick_default()`` — a complete precedence chain with
   an env override — had ZERO callers. An abstraction nobody calls is not an
   abstraction, it is dead code that reads like one.
2. There were two independent implementations of the same
   ``claude --dangerously-skip-permissions --max-turns`` shellout, and only the
   kanban one carried the Windows command-line workaround, the stop-hook env
   tags and the model override.

The load-bearing test here is
``test_default_resolution_is_unchanged_for_every_task_type``: routing through
the registry must NOT quietly hand build tasks to a different executor, and
``args/agent_adapters.yaml`` maps ``chore`` to ``local_llm_router`` — so a naive
``pick_default(task_type)`` would have flipped the default for a large share of
the board.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agents import registry  # noqa: E402
from tools.agents.adapter_base import AgentResult, AgentSession  # noqa: E402
from tools.agents.adapters import claude_cli as claude_mod  # noqa: E402
from tools.genesis.reflexes import kanban as k  # noqa: E402

DEFAULT_CHAIN = ["claude_cli", "gitlab", "github_actions", "ollama_local"]


class _FakeAdapter:
    """Minimal AgentAdapter: invoke-only, no spawn."""

    def __init__(self, name: str, available: bool = True,
                 completed: bool = True):
        self.name = name
        self._available = available
        self._completed = completed
        self.sessions: list = []

    def available(self) -> bool:
        return self._available

    def prepare_prompt(self, session):
        return session.prompt

    def invoke(self, session) -> AgentResult:
        self.sessions.append(session)
        return AgentResult(task_id=session.task_id, adapter_name=self.name,
                           completed=self._completed, output="ok")

    def detect_completion(self, output: str) -> bool:
        return bool(output)

    def parse_response(self, raw: str):
        return {"content": raw, "tool_calls": [], "diff": ""}


@pytest.fixture()
def fake_registry(monkeypatch):
    """Register fakes so no test needs a real claude binary."""
    adapters = {
        "claude_cli": _FakeAdapter("claude_cli"),
        "local_agent": _FakeAdapter("local_agent"),
        "local_llm_router": _FakeAdapter("local_llm_router"),
    }
    monkeypatch.setattr(registry, "_REGISTRY", adapters, raising=False)
    monkeypatch.setattr(registry, "_ensure_loaded", lambda: None)
    monkeypatch.delenv("ICDEV_AGENT_ADAPTER", raising=False)
    return adapters


# ---------------------------------------------------------------------------
# pick_default() has a real caller, and the default is unchanged
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_type",
                         ["build", "fix", "deploy", "test", "chore", "research"])
def test_default_resolution_is_unchanged_for_every_task_type(
        fake_registry, task_type):
    """With the CLI present, claude_cli is still selected — for EVERY type.

    args/agent_adapters.yaml prefers local_llm_router for chore/research. That
    table governs standalone consumers of the registry; the kanban runner's
    order is its executor chain, and the chain says claude_cli first.
    """
    adapter = k._pick_chain_adapter(DEFAULT_CHAIN, task_type)
    assert adapter is not None and adapter.name == "claude_cli"


def test_the_env_var_overrides_the_chain(fake_registry, monkeypatch):
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "local_agent")
    adapter = k._pick_chain_adapter(DEFAULT_CHAIN, "build")
    assert adapter.name == "local_agent"
    assert k._agent_adapter_override() == "local_agent"


def test_an_override_naming_an_off_chain_adapter_is_still_honoured(
        fake_registry, monkeypatch):
    """The env var is the escape hatch; it outranks the chain entirely."""
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "local_llm_router")
    assert k._pick_chain_adapter(DEFAULT_CHAIN, "build").name == "local_llm_router"


def test_an_unavailable_claude_falls_through_to_the_next_chain_tier(
        fake_registry):
    """No adapter available means the runner walks on to gitlab, as before."""
    fake_registry["claude_cli"]._available = False
    assert k._pick_chain_adapter(["claude_cli", "gitlab"], "build") is None


def test_local_agent_is_a_legal_chain_entry(fake_registry):
    """It was not even nameable before hgx-exec-03."""
    assert "local_agent" in k._ADAPTER_TIERS
    fake_registry["claude_cli"]._available = False
    adapter = k._pick_chain_adapter(["claude_cli", "local_agent"], "build")
    assert adapter is not None and adapter.name == "local_agent"


def test_the_configured_chain_lists_local_agent_as_supported():
    """args/strategos_config.yaml must document it, or nobody can set it."""
    import yaml

    text = (ROOT / "args" / "strategos_config.yaml").read_text(encoding="utf-8")
    assert "'local_agent'" in text, "local_agent is not a documented chain value"

    cfg = yaml.safe_load(text) or {}
    chain = cfg.get("executor", {}).get("fallback_chain") or []
    assert chain[0] == "claude_cli", (
        "hgx-exec-03 must NOT change the default executor — that is hgx-exec-04's "
        "decision to make, against parity numbers"
    )
    assert "local_agent" not in chain


# ---------------------------------------------------------------------------
# invoke-only adapters run behind a Popen-compatible handle
# ---------------------------------------------------------------------------

def test_an_invoke_only_adapter_is_dispatched_on_a_thread(
        fake_registry, tmp_path, monkeypatch):
    monkeypatch.setattr(k, "_get_task_timeout", lambda _tid: 60)
    monkeypatch.setattr(k, "_selected_model", lambda: None)
    monkeypatch.setattr(k, "_running", {}, raising=False)
    monkeypatch.setattr(k, "_dispatch_times", {}, raising=False)
    monkeypatch.setattr(k, "_agent_invocations", {}, raising=False)
    monkeypatch.setattr(k, "_audit_agent_execution", lambda *a, **kw: None)

    log = tmp_path / "task.log"
    adapter = fake_registry["local_agent"]
    ok = k._dispatch_via_agent_adapter(
        adapter, {"id": "ha-01", "task_type": "build"}, "/p.md",
        "build it", str(tmp_path), log,
    )

    assert ok is True
    handle = k._running["ha-01"]
    assert handle.wait(timeout=10) == 0, "a completed invoke must exit 0"
    assert adapter.sessions[0].prompt == "build it"
    assert "ok" in log.read_text(encoding="utf-8")


def test_an_incomplete_invoke_fails_the_task_rather_than_passing_it(
        fake_registry, tmp_path, monkeypatch):
    """completed=False must surface as a non-zero exit, or the verification
    chain would mark an unbuilt task done."""
    monkeypatch.setattr(k, "_get_task_timeout", lambda _tid: 60)
    monkeypatch.setattr(k, "_selected_model", lambda: None)
    monkeypatch.setattr(k, "_running", {}, raising=False)
    monkeypatch.setattr(k, "_dispatch_times", {}, raising=False)
    monkeypatch.setattr(k, "_agent_invocations", {}, raising=False)
    monkeypatch.setattr(k, "_audit_agent_execution", lambda *a, **kw: None)

    adapter = _FakeAdapter("local_agent", completed=False)
    k._dispatch_via_agent_adapter(
        adapter, {"id": "ha-02"}, "/p.md", "build it",
        str(tmp_path), tmp_path / "t.log",
    )
    assert k._running["ha-02"].wait(timeout=10) == 1


# ---------------------------------------------------------------------------
# the one shellout, and its hardening
# ---------------------------------------------------------------------------

def test_the_argv_carries_the_headless_flags(monkeypatch):
    monkeypatch.setattr(claude_mod.ADAPTER, "resolve", lambda: "/usr/bin/claude")
    argv = claude_mod.ADAPTER.build_argv(
        AgentSession(task_id="t", prompt="p", working_dir="/wd", max_turns=42))
    assert argv[0] == "/usr/bin/claude"
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--max-turns") + 1] == "42"
    # json, not text (hgx-exec-04). The CLI is the only executor that knows what
    # a session COST, and the envelope is where it says so — without it the
    # adapter reported a duration and nothing else, so a cost comparison against
    # another adapter had one column permanently empty. This assertion said
    # "text" until the envelope landed and then failed on main for a day,
    # unnoticed, because this file is not in the CI test job's allowlist.
    assert argv[argv.index("--output-format") + 1] == "json"


def test_the_json_envelope_is_the_reason_for_that_flag(monkeypatch):
    """Pin the REASON, so the flag cannot be reverted while the tests stay green.

    Asserting the literal "json" alone would pass just as happily if the parser
    were deleted and the flag left dangling.
    """
    text, structured = claude_mod._parse_cli_json(
        '{"result": "done", "subtype": "success", "is_error": false, '
        '"num_turns": 3, "session_id": "s1", "total_cost_usd": 0.42, '
        '"usage": {"input_tokens": 10, "cache_read_input_tokens": 5, '
        '"output_tokens": 7}}'
    )
    assert text == "done"
    assert structured["total_cost_usd"] == 0.42
    assert structured["turns"] == 3
    # cache reads are input tokens too — billed differently, still consumed
    assert structured["input_tokens"] == 15
    assert structured["output_tokens"] == 7


def test_a_cli_that_prints_prose_still_works(monkeypatch):
    """An older CLI, or one that printed something else, must degrade — not raise.

    This is what makes the flag safe to set unconditionally.
    """
    text, structured = claude_mod._parse_cli_json("just some prose\n")
    assert text == "just some prose\n"
    assert structured == {}


def test_the_prompt_goes_over_stdin_not_argv(monkeypatch, tmp_path):
    """The Windows 32767-char command-line limit (WinError 206) is why."""
    monkeypatch.setattr(claude_mod.ADAPTER, "resolve", lambda: "/usr/bin/claude")
    huge = "x" * 40000
    session = AgentSession(task_id="t", prompt=huge, working_dir=str(tmp_path),
                           metadata={"temp_dir": str(tmp_path / "scratch")})
    argv = claude_mod.ADAPTER.build_argv(session)
    assert not any(len(a) > 1000 for a in argv), "the prompt must not be on argv"

    path = claude_mod.ADAPTER._write_stdin(session)
    try:
        assert open(Path(path), encoding="utf-8", newline="").read() == huge
    finally:
        Path(path).unlink()


def test_dispatch_source_tags_the_env_so_commits_are_attributed():
    session = AgentSession(task_id="t-9", prompt="p", working_dir="/wd",
                           metadata={"dispatch_source": "genesis_scheduler"})
    env = claude_mod.ADAPTER.build_env(session)
    assert env["ICDEV_DISPATCH_SOURCE"] == "genesis_scheduler"
    assert env["ICDEV_DISPATCH_TASK_ID"] == "t-9"


def test_an_untagged_session_leaves_the_dispatch_env_alone(monkeypatch):
    """A review-only session makes no commits, so it must not claim any."""
    # Deleted rather than assumed absent: this suite may itself be running
    # inside a dispatched task, which inherits both.
    monkeypatch.delenv("ICDEV_DISPATCH_SOURCE", raising=False)
    monkeypatch.delenv("ICDEV_DISPATCH_TASK_ID", raising=False)
    env = claude_mod.ADAPTER.build_env(
        AgentSession(task_id="t", prompt="p", working_dir="/wd"))
    assert "ICDEV_DISPATCH_SOURCE" not in env
    assert "ICDEV_DISPATCH_TASK_ID" not in env


def test_a_missing_cli_raises_not_installed_rather_than_guessing(monkeypatch):
    from tools.agents.adapter_base import NotInstalledError

    monkeypatch.setattr(claude_mod, "resolve_claude_cli", lambda: None)
    with pytest.raises(NotInstalledError):
        claude_mod.ADAPTER.resolve()


def test_invoke_is_blocking_and_reports_the_exit_code(monkeypatch, tmp_path):
    """The protocol method still works — it is what non-runner callers use."""
    monkeypatch.setattr(claude_mod.ADAPTER, "resolve", lambda: "/usr/bin/claude")

    class _Done:
        returncode = 0
        stdout = "APPROVED: looks right" + "." * 200
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Done())
    result = claude_mod.ADAPTER.invoke(AgentSession(
        task_id="t", prompt="p", working_dir=str(tmp_path),
        metadata={"temp_dir": str(tmp_path)}))
    assert result.exit_code == 0 and result.completed is True
    assert "APPROVED" in result.output
    assert not list(tmp_path.glob("*_instr.txt")), "the temp prompt must be removed"
