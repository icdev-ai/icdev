# CUI // SP-CTI
"""Unit tests for tools/llm/cli_bridge/cli_provider.py (CLILLMProvider).

All tests are offline — ``subprocess.run`` is replaced with a fake so the
real ``claude`` CLI is never spawned. Per the shim-aware monkeypatch rule
(tools.* vs icdev.tools.* are distinct module objects), the fake is installed
by importing the module under test with ``importlib.import_module`` and
``setattr``-ing its ``subprocess`` reference — NOT via pytest's string-form
``monkeypatch.setattr("tools...")`` which can patch the wrong object and let a
real subprocess through.

Covers (uclb-prov-06):
  - invoke() returns an LLMResponse whose content is the fake claude stdout
  - a subprocess failure (non-zero exit, missing binary, timeout) raises
    LLMUnavailableError rather than crashing
"""
from __future__ import annotations

import importlib
import pathlib
import subprocess
import sys
import types

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Module under test + the contracts it returns/raises.
cli_provider = importlib.import_module("tools.llm.cli_bridge.cli_provider")
from tools.llm.provider import LLMRequest, LLMResponse  # noqa: E402
from tools.llm.router import LLMUnavailableError  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _fake_completed(stdout="", stderr="", returncode=0):
    """Mimic subprocess.CompletedProcess just enough for the provider."""
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _install_fake_subprocess(run_impl):
    """Shim-aware: replace the cli_provider module's ``subprocess`` reference.

    Returns a teardown callable that restores the real module. We swap a
    SimpleNamespace that exposes ``run`` plus the exception types the provider
    references, so ``subprocess.TimeoutExpired`` keeps working inside invoke().
    """
    real = cli_provider.subprocess
    fake = types.SimpleNamespace(
        run=run_impl,
        TimeoutExpired=subprocess.TimeoutExpired,
        CalledProcessError=subprocess.CalledProcessError,
    )
    setattr(cli_provider, "subprocess", fake)
    return lambda: setattr(cli_provider, "subprocess", real)


@pytest.fixture
def provider():
    return cli_provider.CLILLMProvider(cli_binary="claude", soft_wait_seconds=5)


@pytest.fixture
def request_obj():
    return LLMRequest(messages=[{"role": "user", "content": "ping"}], system_prompt="be terse")


# ────────────────────────────────────────────────────────────────────────────
# invoke() — happy path
# ────────────────────────────────────────────────────────────────────────────


def test_invoke_returns_llmresponse_with_cli_stdout(provider, request_obj):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _fake_completed(stdout="hello from fake claude\n", returncode=0)

    restore = _install_fake_subprocess(fake_run)
    try:
        resp = provider.invoke(request_obj, model_id="claude-cli", model_config={})
    finally:
        restore()

    assert isinstance(resp, LLMResponse)
    assert resp.content == "hello from fake claude"  # stdout, stripped
    assert resp.provider == "cli"
    assert resp.model_id == "claude-cli"
    assert resp.stop_reason == "stop"
    # The flattened prompt (system + user text) is passed to the CLI argv.
    assert provider._cli_binary in captured["cmd"]
    joined = " ".join(captured["cmd"])
    assert "ping" in joined and "be terse" in joined


def test_invoke_defaults_model_id_when_blank(provider, request_obj):
    restore = _install_fake_subprocess(lambda cmd, **kw: _fake_completed(stdout="ok", returncode=0))
    try:
        resp = provider.invoke(request_obj, model_id="", model_config={})
    finally:
        restore()
    assert resp.model_id == cli_provider.DEFAULT_MODEL_ID


# ────────────────────────────────────────────────────────────────────────────
# invoke() — failure modes all raise LLMUnavailableError (no crash)
# ────────────────────────────────────────────────────────────────────────────


def test_invoke_nonzero_exit_raises_unavailable(provider, request_obj):
    def fake_run(cmd, **kwargs):
        return _fake_completed(stdout="", stderr="boom", returncode=1)

    restore = _install_fake_subprocess(fake_run)
    try:
        with pytest.raises(LLMUnavailableError) as excinfo:
            provider.invoke(request_obj, model_id="claude-cli", model_config={})
    finally:
        restore()
    assert "exited 1" in str(excinfo.value)


def test_invoke_missing_binary_raises_unavailable(provider, request_obj):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("no such file: claude")

    restore = _install_fake_subprocess(fake_run)
    try:
        with pytest.raises(LLMUnavailableError):
            provider.invoke(request_obj, model_id="claude-cli", model_config={})
    finally:
        restore()


def test_invoke_timeout_raises_unavailable(provider, request_obj):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    restore = _install_fake_subprocess(fake_run)
    try:
        with pytest.raises(LLMUnavailableError) as excinfo:
            provider.invoke(request_obj, model_id="claude-cli", model_config={})
    finally:
        restore()
    assert "timed out" in str(excinfo.value)


def test_invoke_unexpected_error_raises_unavailable(provider, request_obj):
    def fake_run(cmd, **kwargs):
        raise OSError("pipe exploded")

    restore = _install_fake_subprocess(fake_run)
    try:
        with pytest.raises(LLMUnavailableError):
            provider.invoke(request_obj, model_id="claude-cli", model_config={})
    finally:
        restore()


# ────────────────────────────────────────────────────────────────────────────
# misc surface
# ────────────────────────────────────────────────────────────────────────────


def test_provider_name_is_cli(provider):
    assert provider.provider_name == "cli"


def test_check_availability_reflects_path_resolution(provider):
    real_which = cli_provider.shutil.which
    try:
        cli_provider.shutil.which = lambda name: "/usr/bin/claude"
        assert provider.check_availability("claude-cli") is True
        cli_provider.shutil.which = lambda name: None
        assert provider.check_availability("claude-cli") is False
    finally:
        cli_provider.shutil.which = real_which


def test_constructor_coerces_bad_soft_wait():
    p = cli_provider.CLILLMProvider(soft_wait_seconds="not-an-int")
    assert p._soft_wait_seconds == 60
