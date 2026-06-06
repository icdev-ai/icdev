# CUI // SP-CTI
"""Tests for the local Claude CLI LLM provider + router CLI-bridge activation."""
from __future__ import annotations

import subprocess
import types

import pytest

from tools.llm.cli_provider import CLIProvider
from tools.llm.provider import LLMRequest


def _fake_run(stdout, returncode=0, stderr=""):
    def _run(cmd, **kw):
        return types.SimpleNamespace(args=cmd, returncode=returncode, stdout=stdout, stderr=stderr)
    return _run


def test_check_availability(monkeypatch):
    p = CLIProvider()
    monkeypatch.setattr("shutil.which", lambda b: None)
    assert p.check_availability("claude-opus-4-8") is False
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude")
    assert p.check_availability("claude-opus-4-8") is True


def test_invoke_returns_text_and_structured(monkeypatch):
    p = CLIProvider(cli_binary="claude", soft_wait_seconds=30)
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude")
    monkeypatch.setattr("subprocess.run", _fake_run('["Slide A", "Slide B"]'))
    req = LLMRequest(messages=[{"role": "user", "content": "make titles"}],
                     system_prompt="Return JSON array", agent_id="t", skip_injection_scan=True)
    resp = p.invoke(req, "claude-opus-4-8", {})
    assert resp.provider == "cli" and resp.model_id == "claude-opus-4-8"
    assert resp.content.startswith("[")
    assert resp.structured_output == ["Slide A", "Slide B"]


def test_invoke_binary_missing_raises(monkeypatch):
    p = CLIProvider()
    monkeypatch.setattr("shutil.which", lambda b: None)
    monkeypatch.setattr("os.path.exists", lambda x: False)
    with pytest.raises(RuntimeError):
        p.invoke(LLMRequest(messages=[{"role": "user", "content": "hi"}]), "m", {})


def test_invoke_nonzero_exit_raises(monkeypatch):
    p = CLIProvider()
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude")
    monkeypatch.setattr("subprocess.run", _fake_run("", returncode=1, stderr="boom"))
    with pytest.raises(RuntimeError):
        p.invoke(LLMRequest(messages=[{"role": "user", "content": "hi"}]), "m", {})


def test_invoke_timeout_raises(monkeypatch):
    p = CLIProvider()
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/claude")
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
    monkeypatch.setattr("subprocess.run", _boom)
    with pytest.raises(TimeoutError):
        p.invoke(LLMRequest(messages=[{"role": "user", "content": "hi"}]), "m", {})


@pytest.fixture
def router():
    from tools.llm.router import LLMRouter
    return LLMRouter()


def test_router_creates_cli_provider(router):
    # cli_bridge provider (type: cli) is now creatable
    inst = router._get_provider("cli_bridge")
    assert inst is not None and inst.provider_name == "cli"


def test_router_prepends_cli_when_active(router, monkeypatch):
    base = router._get_chain_for_function("slides_outline_planning")
    assert "claude-cli" not in base  # off by default
    monkeypatch.setenv("ICDEV_CLI_BRIDGE", "true")
    activated = router._get_chain_for_function("slides_outline_planning")
    assert activated[0] == "claude-cli"
    assert activated[1:] == base  # original chain preserved after the CLI front
