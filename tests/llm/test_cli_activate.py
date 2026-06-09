# CUI // SP-CTI
"""Unit tests for tools/llm/cli_bridge/activate.py — CLI bridge auto-enable.

Offline. The air-gap probe and the cloud-key probe are replaced with fakes via
the shim-aware monkeypatch rule: import the real module object with
``importlib.import_module`` and ``setattr`` the symbol on it, NOT pytest's
string-form ``monkeypatch.setattr("tools...")`` (which can bind to the wrong
tools.* vs icdev.tools.* object and let the real probe run).

Covers (uclb-prov-06):
  - should_enable() is True when there is no cloud key AND not air-gapped
  - should_enable() is False when a cloud key is present (and not air-gapped)
  - cli_bridge_enabled() honors the context override (cli_bridge_override)
    and falls back to should_enable() otherwise
  - cli_bridge_enabled() ignores the ICDEV_CLI_BRIDGE env var (regression
    guard for the env-var path removal)
  - prepend_cli_to_chains() puts 'claude-cli' first in every chain and is
    idempotent (no duplicates, original config untouched)
"""
from __future__ import annotations

import importlib
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

activate = importlib.import_module("tools.llm.cli_bridge.activate")
airgap_pkg = importlib.import_module("tools.airgap")


# ────────────────────────────────────────────────────────────────────────────
# Fixtures — shim-aware patch of the two probes should_enable() consults
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def patch_probes():
    """Yield a setter (has_key, airgap) -> patches the real module objects.

    is_airgap is patched on the ``tools.airgap`` package because should_enable()
    does ``from tools.airgap import is_airgap`` at call time. _has_cloud_key is
    patched on the activate module itself.
    """
    real_has_key = activate._has_cloud_key
    real_airgap = getattr(airgap_pkg, "is_airgap")

    def _set(has_key: bool, airgap: bool):
        setattr(activate, "_has_cloud_key", lambda: has_key)
        setattr(airgap_pkg, "is_airgap", lambda: airgap)

    yield _set

    setattr(activate, "_has_cloud_key", real_has_key)
    setattr(airgap_pkg, "is_airgap", real_airgap)


# ────────────────────────────────────────────────────────────────────────────
# should_enable()
# ────────────────────────────────────────────────────────────────────────────


def test_should_enable_true_when_no_key_and_not_airgap(patch_probes):
    patch_probes(has_key=False, airgap=False)
    assert activate.should_enable() is True


def test_should_enable_false_when_key_present(patch_probes):
    patch_probes(has_key=True, airgap=False)
    assert activate.should_enable() is False


def test_should_enable_true_when_airgapped_even_with_key(patch_probes):
    # Air-gap short-circuits before the key check.
    patch_probes(has_key=True, airgap=True)
    assert activate.should_enable() is True


def test_has_cloud_key_detects_env_var(monkeypatch):
    # Integration of the real _has_cloud_key against an env-var key.
    for var in activate.CLOUD_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    assert activate._has_cloud_key() is True


# ────────────────────────────────────────────────────────────────────────────
# cli_bridge_enabled() — context override (cli_bridge_override) layered on
# should_enable(). The ICDEV_CLI_BRIDGE env var is no longer consulted; the
# context-scoped override is the sole per-request override path.
# ────────────────────────────────────────────────────────────────────────────


def test_cli_bridge_enabled_context_override_force_disable(patch_probes):
    patch_probes(has_key=False, airgap=True)  # would otherwise enable
    token = activate.cli_bridge_override(False)
    try:
        assert activate.cli_bridge_enabled() is False
    finally:
        activate.reset_cli_bridge_override(token)


def test_cli_bridge_enabled_context_override_force_enable(patch_probes):
    patch_probes(has_key=True, airgap=False)  # would otherwise disable
    token = activate.cli_bridge_override(True)
    try:
        assert activate.cli_bridge_enabled() is True
    finally:
        activate.reset_cli_bridge_override(token)


def test_cli_bridge_enabled_defaults_to_should_enable(patch_probes):
    # No context override set → fall through to should_enable().
    patch_probes(has_key=False, airgap=False)
    assert activate.cli_bridge_enabled() is True
    patch_probes(has_key=True, airgap=False)
    assert activate.cli_bridge_enabled() is False


def test_cli_bridge_enabled_ignores_env_var(monkeypatch, patch_probes):
    # Regression: the ICDEV_CLI_BRIDGE env var is no longer consulted. Even
    # if it is set to "true", the result must follow should_enable() when
    # no context override is active.
    patch_probes(has_key=True, airgap=False)  # would otherwise disable
    monkeypatch.setenv("ICDEV_CLI_BRIDGE", "true")
    assert activate.cli_bridge_enabled() is False


# ────────────────────────────────────────────────────────────────────────────
# prepend_cli_to_chains()
# ────────────────────────────────────────────────────────────────────────────


def _sample_config():
    return {
        "routing": {
            "code_generation": {"chain": ["sonnet", "gpt-4o"]},
            "summarization": {"chain": ["haiku"]},
        }
    }


def test_prepend_puts_cli_first_in_every_chain():
    cfg = _sample_config()
    out = activate.prepend_cli_to_chains(cfg)
    for route in out["routing"].values():
        assert route["chain"][0] == "claude-cli"
    # Existing models preserved as fallbacks, order intact after the prepend.
    assert out["routing"]["code_generation"]["chain"] == ["claude-cli", "sonnet", "gpt-4o"]


def test_prepend_does_not_mutate_original():
    cfg = _sample_config()
    activate.prepend_cli_to_chains(cfg)
    # Original is deep-copied — untouched.
    assert cfg["routing"]["code_generation"]["chain"] == ["sonnet", "gpt-4o"]


def test_prepend_is_idempotent():
    cfg = _sample_config()
    once = activate.prepend_cli_to_chains(cfg)
    twice = activate.prepend_cli_to_chains(once)
    assert once == twice
    for route in twice["routing"].values():
        chain = route["chain"]
        assert chain[0] == "claude-cli"
        assert chain.count("claude-cli") == 1  # never doubled


def test_prepend_dedups_existing_cli_occurrence():
    cfg = {"routing": {"f": {"chain": ["sonnet", "claude-cli", "haiku"]}}}
    out = activate.prepend_cli_to_chains(cfg)
    assert out["routing"]["f"]["chain"] == ["claude-cli", "sonnet", "haiku"]


def test_prepend_handles_missing_or_malformed_routing():
    # No routing key at all.
    assert activate.prepend_cli_to_chains({}) == {}
    # routing not a dict.
    assert activate.prepend_cli_to_chains({"routing": []}) == {"routing": []}
    # route entries without a list chain are skipped, not crashed.
    cfg = {"routing": {"a": {"chain": "oops"}, "b": "nope"}}
    out = activate.prepend_cli_to_chains(cfg)
    assert out == cfg


def test_prepend_respects_custom_model_name():
    cfg = _sample_config()
    out = activate.prepend_cli_to_chains(cfg, model_name="local-cli")
    assert out["routing"]["summarization"]["chain"][0] == "local-cli"
