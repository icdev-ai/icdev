# CUI // SP-CTI
"""omx-select-02 -- the kanban RUNNER follows ``omarchy default agent``.

omx-select-01 made the interactive ``pick_default`` follow the Omarchy desktop
default, but ``_pick_chain_adapter`` handed the registry a config with no
``omarchy_agent_map`` (which skips that rung) and only knew the claude_cli /
local_agent tiers -- so on a laptop whose default is opencode the runner still
spawned claude_cli. Precedence now matches ``select_adapter``: env override,
then the Omarchy default, then the executor chain; the omx-guard-02 screen
still applies to whatever is picked.

A FAKE ``omarchy`` binary is put on PATH, as in
tests/agents/test_pick_default_omarchy.py, so the real subprocess read runs.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from tools.agents import guard_parity as gp
from tools.agents import registry

k = importlib.import_module("tools.genesis.reflexes.kanban")

DEFAULT_CHAIN = ["claude_cli", "gitlab", "github_actions", "ollama_local"]
GUARDED = {"claude_cli", "opencode_cli", "pi_cli"}


class _FakeAdapter:
    def __init__(self, name: str, available: bool = True):
        self.name = name
        self._available = available

    def available(self) -> bool:
        return self._available


@pytest.fixture()
def fakes(monkeypatch):
    adapters = {n: _FakeAdapter(n) for n in (
        "claude_cli", "local_agent", "opencode_cli", "pi_cli", "local_llm_router")}
    monkeypatch.setattr(registry, "_REGISTRY", adapters, raising=False)
    monkeypatch.setattr(registry, "_ensure_loaded", lambda: None)
    monkeypatch.delenv("ICDEV_AGENT_ADAPTER", raising=False)
    monkeypatch.delenv(gp.MODE_ENV, raising=False)
    registry._omarchy_default_agent.cache_clear()  # noqa: SLF001
    gp.reset_cache()
    monkeypatch.setattr(gp, "verdict_for",
                        lambda n, **kw: gp.GuardVerdict(n, n in GUARDED, "fake"))
    yield adapters
    registry._omarchy_default_agent.cache_clear()  # noqa: SLF001
    gp.reset_cache()


def _omarchy(tmp_path: Path, monkeypatch, agent: str) -> None:
    """Put a fake ``omarchy`` that prints ``agent`` first on PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if sys.platform == "win32":
        (bindir / "omarchy.cmd").write_text(
            "@echo off\r\nif defined FAKE_OMARCHY_AGENT echo %FAKE_OMARCHY_AGENT%\r\n",
            encoding="utf-8")
    else:
        script = bindir / "omarchy"
        script.write_text(
            '#!/bin/sh\n[ -n "$FAKE_OMARCHY_AGENT" ] && echo "$FAKE_OMARCHY_AGENT"\nexit 0\n',
            encoding="utf-8")
        script.chmod(0o755)
    import os
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_OMARCHY_AGENT", agent)
    registry._omarchy_default_agent.cache_clear()  # noqa: SLF001


def _no_omarchy(monkeypatch, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    registry._omarchy_default_agent.cache_clear()  # noqa: SLF001


# ── _pick_chain_adapter ─────────────────────────────────────────────────────
def test_omarchy_default_opencode_is_picked_over_the_chain(fakes, tmp_path, monkeypatch):
    _omarchy(tmp_path, monkeypatch, "opencode")
    assert k._pick_chain_adapter(DEFAULT_CHAIN, "build").name == "opencode_cli"


def test_omarchy_default_claude_picks_claude(fakes, tmp_path, monkeypatch):
    _omarchy(tmp_path, monkeypatch, "claude")
    assert k._pick_chain_adapter(DEFAULT_CHAIN, "build").name == "claude_cli"


def test_no_omarchy_binary_keeps_todays_chain_result(fakes, tmp_path, monkeypatch):
    _no_omarchy(monkeypatch, tmp_path)
    assert k._pick_chain_adapter(DEFAULT_CHAIN, "build").name == "claude_cli"
    # ...and an installed opencode is NOT reached through the last-resort rung.
    fakes["claude_cli"]._available = False
    assert k._pick_chain_adapter(DEFAULT_CHAIN, "build") is None


def test_unusable_omarchy_default_falls_through_to_the_chain(fakes, tmp_path, monkeypatch):
    _omarchy(tmp_path, monkeypatch, "opencode")
    fakes["opencode_cli"]._available = False
    assert k._pick_chain_adapter(DEFAULT_CHAIN, "build").name == "claude_cli"


def test_unmapped_omarchy_default_falls_through(fakes, tmp_path, monkeypatch):
    _omarchy(tmp_path, monkeypatch, "crush")
    assert k._pick_chain_adapter(DEFAULT_CHAIN, "build").name == "claude_cli"


def test_the_env_override_still_wins(fakes, tmp_path, monkeypatch):
    _omarchy(tmp_path, monkeypatch, "opencode")
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "local_agent")
    assert k._pick_chain_adapter(DEFAULT_CHAIN, "build").name == "local_agent"


def test_opencode_and_pi_are_adapter_backed_tiers():
    assert k._ADAPTER_TIERS["opencode_cli"] == "opencode_cli"
    assert k._ADAPTER_TIERS["pi_cli"] == "pi_cli"


# ── the dispatch loop and the guard gate ────────────────────────────────────
@pytest.fixture()
def dispatch(monkeypatch, tmp_path, fakes):
    """Drive ``_dispatch_to_claude`` far enough to see which adapter it spawns."""
    from tools.kanban import build_mode, model_override

    monkeypatch.setattr(build_mode, "_FLAG", tmp_path / "manual_build.json")
    monkeypatch.setattr(model_override, "_FLAG", tmp_path / "model.json")
    prompt = tmp_path / "p.md"
    prompt.write_text("do it", encoding="utf-8")

    spawned, moved = [], []

    def _via_adapter(adapter, task, *a, **kw):
        spawned.append(adapter.name)
        return True

    class _Conn:
        """A no-op connection, usable bare or as a context manager."""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *a, **kw):
            return self

        def cursor(self):
            return self

        def fetchone(self):
            return None

        def fetchall(self):
            return []

        def commit(self):
            return None

        def close(self):
            return None

    def _conn(*a, **kw):
        return _Conn()

    monkeypatch.setattr(k, "_dispatch_via_agent_adapter", _via_adapter)
    monkeypatch.setattr(k, "_dispatch_via_tool_runner", lambda *a, **kw: False)
    monkeypatch.setattr(k, "_build_effective_executor_chain", lambda c: list(DEFAULT_CHAIN))
    monkeypatch.setattr(k, "_dispatch_gitlab", lambda *a, **kw: False)
    monkeypatch.setattr(k, "_dispatch_github_actions", lambda *a, **kw: False)
    monkeypatch.setattr(k, "_dispatch_ollama_local", lambda *a, **kw: False)
    monkeypatch.setattr(k, "_dispatch_via_llm_router", lambda *a, **kw: None)
    monkeypatch.setattr(k, "_claude_code_available", lambda: True)
    monkeypatch.setattr(k, "_create_worktree", lambda tid: str(tmp_path))
    monkeypatch.setattr(k, "_pre_dispatch_check", lambda t: (False, ""))
    monkeypatch.setattr(k, "_had_recent_success", lambda tid, **kw: False)
    monkeypatch.setattr(k, "_has_open_pr", lambda tid: False)
    monkeypatch.setattr(k, "_set_executor_type", lambda *a, **kw: None)
    monkeypatch.setattr(k, "_move_task", lambda tid, st, **kw: moved.append(st))
    monkeypatch.setattr(k, "get_connection", _conn)

    from tools.kanban import dispatch_admission as da
    from tools.kanban import should_run as sr
    monkeypatch.setattr(da, "assess", lambda tid, **kw: da.Verdict(tid, da.ALLOW, "ok"))
    monkeypatch.setattr(sr, "assess",
                        lambda task: sr.classify(task["id"], {"a": {"verdict": sr.PROCEED, "reason": "r"}}))

    def run():
        k._dispatch_to_claude({"id": "omx-x-01", "title": "x", "task_type": "build"},
                              str(prompt))
        return spawned

    return run


def test_runner_dispatches_via_opencode_on_an_opencode_desktop(dispatch, tmp_path, monkeypatch):
    _omarchy(tmp_path, monkeypatch, "opencode")
    assert dispatch() == ["opencode_cli"]


def test_runner_dispatches_via_claude_off_omarchy(dispatch, tmp_path, monkeypatch):
    _no_omarchy(monkeypatch, tmp_path)
    assert dispatch() == ["claude_cli"]


def test_guard_gate_still_refuses_an_unguarded_omarchy_pick(dispatch, tmp_path, monkeypatch):
    _omarchy(tmp_path, monkeypatch, "opencode")
    monkeypatch.setattr(gp, "verdict_for",
                        lambda n, **kw: gp.GuardVerdict(n, n == "claude_cli", "fake"))
    # enforce falls back to the next guarded adapter in the chain, never opencode.
    assert dispatch() == ["claude_cli"]


def test_guard_gate_with_no_guarded_fallback_spawns_nothing(dispatch, tmp_path, monkeypatch):
    _omarchy(tmp_path, monkeypatch, "opencode")
    monkeypatch.setattr(gp, "verdict_for",
                        lambda n, **kw: gp.GuardVerdict(n, False, "fake"))
    assert dispatch() == []
