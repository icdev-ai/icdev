"""omx-guard-07-d2: no adapter hands a metered Anthropic key to what it spawns.

Operator decision (2026-10-11): every Claude call is paid by the claude.ai
subscription. omx-adapt-04 scrubbed claude_cli / opencode_cli / pi_cli; this
pins EVERY registered adapter with a spawn env, plus the env claude_cli's
non-blocking ``spawn()`` actually passes to ``subprocess.Popen``.
"""
from __future__ import annotations

import pytest

from tools.agents import capability_matrix as cm
from tools.agents import registry
from tools.agents.adapters import claude_cli

METERED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _with_build_env():
    return [n for n in registry.list_adapters()
            if callable(getattr(registry.get_adapter(n), "build_env", None))]


@pytest.fixture
def parent_has_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-parent")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "parent-token")


def test_the_three_previously_unscrubbed_adapters_are_covered():
    assert {"codex_cli", "copilot_cli", "goose_cli"} <= set(_with_build_env())


@pytest.mark.parametrize("name", _with_build_env())
def test_every_adapter_spawn_env_drops_metered_keys(name, parent_has_keys):
    env = registry.get_adapter(name).build_env(cm._session(metadata={
        "env": {"ANTHROPIC_API_KEY": "sk-ant-passed", "ICDEV_KEEP": "1"}}))
    assert not any(var in env for var in METERED)
    assert env["ICDEV_KEEP"] == "1"


def test_claude_cli_spawned_process_env_lacks_metered_keys(parent_has_keys, monkeypatch, tmp_path):
    seen = {}

    def fake_popen(argv, **kwargs):
        seen.update(kwargs)
        return object()

    adapter = registry.get_adapter("claude_cli")
    monkeypatch.setattr(type(adapter), "build_argv", lambda self, session: ["claude"])
    monkeypatch.setattr(claude_cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(claude_cli, "_schedule_cleanup", lambda path, delay: None)

    adapter.spawn(cm._session(working_dir=str(tmp_path),
                              metadata={"temp_dir": str(tmp_path),
                                        "env": {"ANTHROPIC_AUTH_TOKEN": "passed"}}))
    assert seen["env"] is not None
    assert not any(var in seen["env"] for var in METERED)
