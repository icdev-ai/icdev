# CUI // SP-CTI
"""omx-select-01 -- pick_default follows ``omarchy default agent``.

A FAKE ``omarchy`` binary is put on PATH (a ``.cmd`` on Windows, a ``sh``
script elsewhere) that prints whatever ``FAKE_OMARCHY_AGENT`` holds, exactly
as ``bin/omarchy-default-agent`` prints the stored default when called with no
argument -- and prints nothing when no default is set.

Every adapter is made ``available()`` so these tests measure the precedence
of the rungs, not what happens to be installed on the host running them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

from tools.agents import registry


MAP = {
    "opencode": "opencode_cli",
    "claude": "claude_cli",
    "codex": "codex_cli",
    "pi": "pi_cli",
    "copilot": "copilot_cli",
}
CONFIG = {
    "enabled_adapters": [
        "opencode_cli", "claude_cli", "codex_cli", "pi_cli", "copilot_cli",
        "local_llm_router",
    ],
    "per_task_type_preference": {"build": "claude_cli", "research": "local_llm_router"},
    "fallback_order": ["opencode_cli", "claude_cli", "local_llm_router"],
    "omarchy_agent_map": MAP,
}


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.delenv("ICDEV_AGENT_ADAPTER", raising=False)
    registry.reset()
    registry._ensure_loaded()  # noqa: SLF001
    for name in registry.list_adapters():
        monkeypatch.setattr(registry.get_adapter(name), "available", lambda: True)
    yield
    registry.reset()


def _install_fake_omarchy(tmp_path: Path, monkeypatch, agent: str) -> Path:
    """Put a fake ``omarchy`` alone on PATH; return the file it logs argv to."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    argv_log = tmp_path / "omarchy-argv.txt"
    if sys.platform == "win32":
        script = bindir / "omarchy.cmd"
        script.write_text(
            "@echo off\r\n"
            'echo %*>"%FAKE_OMARCHY_ARGV%"\r\n'
            "if defined FAKE_OMARCHY_AGENT echo %FAKE_OMARCHY_AGENT%\r\n",
            encoding="utf-8",
        )
    else:
        script = bindir / "omarchy"
        script.write_text(
            "#!/bin/sh\n"
            'echo "$*" > "$FAKE_OMARCHY_ARGV"\n'
            '[ -n "$FAKE_OMARCHY_AGENT" ] && echo "$FAKE_OMARCHY_AGENT"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("FAKE_OMARCHY_ARGV", str(argv_log))
    if agent:
        monkeypatch.setenv("FAKE_OMARCHY_AGENT", agent)
    else:
        monkeypatch.delenv("FAKE_OMARCHY_AGENT", raising=False)
    return argv_log


def _no_omarchy(tmp_path: Path, monkeypatch) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


# ── 1. the omarchy rung ──────────────────────────────────────────────────────
@pytest.mark.parametrize("agent,adapter", sorted(MAP.items()))
def test_each_mapped_omarchy_default_selects_its_adapter(
        tmp_path, monkeypatch, agent, adapter):
    argv_log = _install_fake_omarchy(tmp_path, monkeypatch, agent)
    sel = registry.select_adapter("build", config=CONFIG)
    assert sel.adapter.name == adapter
    assert sel.source == registry.SOURCE_OMARCHY
    assert registry.pick_default("build", config=CONFIG).name == adapter
    # read-only form: no argument after `default agent`, so nothing is SET
    assert argv_log.read_text(encoding="utf-8").split() == ["default", "agent"]


def test_omarchy_read_is_cached_for_the_process(tmp_path, monkeypatch):
    argv_log = _install_fake_omarchy(tmp_path, monkeypatch, "pi")
    assert registry.select_adapter("build", config=CONFIG).adapter.name == "pi_cli"
    argv_log.unlink()
    monkeypatch.setenv("FAKE_OMARCHY_AGENT", "codex")
    assert registry.select_adapter("build", config=CONFIG).adapter.name == "pi_cli"
    assert not argv_log.exists()  # the binary was not run a second time


# ── 2. env still wins ────────────────────────────────────────────────────────
def test_env_override_beats_the_omarchy_default(tmp_path, monkeypatch):
    _install_fake_omarchy(tmp_path, monkeypatch, "opencode")
    monkeypatch.setenv("ICDEV_AGENT_ADAPTER", "claude_cli")
    sel = registry.select_adapter("build", config=CONFIG)
    assert (sel.adapter.name, sel.source) == ("claude_cli", registry.SOURCE_ENV)


# ── 3. everything that falls through ─────────────────────────────────────────
def test_absent_binary_falls_through_silently(tmp_path, monkeypatch, caplog):
    _no_omarchy(tmp_path, monkeypatch)
    sel = registry.select_adapter("build", config=CONFIG)
    assert (sel.adapter.name, sel.source) == ("claude_cli", registry.SOURCE_PREFERENCE)
    sel = registry.select_adapter("unknown-type", config=CONFIG)
    assert (sel.adapter.name, sel.source) == ("opencode_cli", registry.SOURCE_FALLBACK)
    assert not [r for r in caplog.records if r.levelno >= 30]


def test_unmapped_name_falls_through(tmp_path, monkeypatch):
    _install_fake_omarchy(tmp_path, monkeypatch, "grok")
    sel = registry.select_adapter("build", config=CONFIG)
    assert (sel.adapter.name, sel.source) == ("claude_cli", registry.SOURCE_PREFERENCE)


def test_unset_default_falls_through(tmp_path, monkeypatch):
    _install_fake_omarchy(tmp_path, monkeypatch, "")
    sel = registry.select_adapter("research", config=CONFIG)
    assert (sel.adapter.name, sel.source) == ("local_llm_router",
                                              registry.SOURCE_PREFERENCE)


def test_mapped_but_unavailable_adapter_falls_through(tmp_path, monkeypatch):
    _install_fake_omarchy(tmp_path, monkeypatch, "pi")
    monkeypatch.setattr(registry.get_adapter("pi_cli"), "available", lambda: False)
    sel = registry.select_adapter("build", config=CONFIG)
    assert sel.source == registry.SOURCE_PREFERENCE


def test_config_without_a_map_skips_the_omarchy_rung(tmp_path, monkeypatch):
    """The kanban runner's derived config carries no map: its chain decides."""
    _install_fake_omarchy(tmp_path, monkeypatch, "opencode")
    cfg = {k: v for k, v in CONFIG.items() if k != "omarchy_agent_map"}
    sel = registry.select_adapter("build", config=cfg)
    assert (sel.adapter.name, sel.source) == ("claude_cli", registry.SOURCE_PREFERENCE)


# ── 4. the shipped config ────────────────────────────────────────────────────
def test_shipped_config_leads_fallback_with_opencode_and_maps_omarchy():
    cfg = yaml.safe_load(Path(registry._CONFIG_PATH).read_text(encoding="utf-8"))  # noqa: SLF001
    assert cfg["fallback_order"][0] == "opencode_cli"
    assert cfg["omarchy_agent_map"]["opencode"] == "opencode_cli"
    assert cfg["omarchy_agent_map"]["claude"] == "claude_cli"
    # this host keeps file-editing work on claude_cli (opencode not installed)
    assert cfg["per_task_type_preference"]["build"] == "claude_cli"
    assert cfg["per_task_type_preference"]["fix"] == "claude_cli"
    for adapter in cfg["omarchy_agent_map"].values():
        assert adapter in registry.list_adapters()


def test_not_on_omarchy_is_not_an_error(tmp_path, monkeypatch):
    _no_omarchy(tmp_path, monkeypatch)
    assert registry._omarchy_default_agent() == ""  # noqa: SLF001
    assert os.environ["PATH"].endswith("empty-bin")
