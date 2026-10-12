# CUI // SP-CTI
"""omx-guard-07-d4 -- under subscription_only, LLMRouter resolves Claude to claude-cli."""
from __future__ import annotations

import pytest
import yaml

from tools.llm import anthropic_billing as ab
from tools.llm.cli_bridge.cli_provider import CLILLMProvider
from tools.llm.anthropic_provider import AnthropicLLMProvider
from tools.llm.router import LLMRouter

CONFIG = {
    "providers": {
        "anthropic": {"type": "anthropic", "locality": "cloud",
                      "api_key_env": "ANTHROPIC_API_KEY"},
        "claude-cli": {"type": "cli", "locality": "cloud", "cli_binary": "claude"},
        "ollama": {"type": "ollama", "locality": "local",
                   "base_url": "http://localhost:11434"},
    },
    "models": {
        "claude-3-5-sonnet": {"provider": "anthropic", "model_id": "claude-3-5-sonnet"},
        "claude-cli": {"provider": "claude-cli", "model_id": "claude-cli"},
        "llama-local": {"provider": "ollama", "model_id": "llama3"},
    },
    "routing": {
        "default": {"chain": ["claude-3-5-sonnet", "llama-local"]},
        "late_cli": {"chain": ["claude-3-5-sonnet", "llama-local", "claude-cli"]},
    },
}


@pytest.fixture
def router_for(tmp_path, monkeypatch):
    cfg_path = tmp_path / "llm_config.yaml"
    cfg_path.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    # The bridge auto-prepend would mask the policy; isolate the router from it.
    monkeypatch.setattr(LLMRouter, "_maybe_activate_cli_bridge", lambda self: None)
    monkeypatch.setattr(LLMRouter, "_check_model_available", lambda self, name: True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")

    def build(mode):
        policy = tmp_path / "guard_anthropic.yaml"
        policy.write_text(f"anthropic_billing: {mode}\n", encoding="utf-8")
        monkeypatch.setattr(ab, "policy_path", lambda: policy)
        return LLMRouter(config_path=cfg_path)

    return build


def test_policy_on_resolves_claude_model_to_cli_provider(router_for):
    router = router_for("subscription_only")
    provider, model_id, _cfg = router.resolve_model("claude-3-5-sonnet")
    assert isinstance(provider, CLILLMProvider)
    assert not isinstance(provider, AnthropicLLMProvider)
    assert model_id == "claude-cli"


def test_policy_on_chain_resolution_skips_direct_anthropic(router_for):
    router = router_for("subscription_only")
    provider, model_id, _cfg = router.get_provider_for_function("default")
    assert isinstance(provider, CLILLMProvider)
    assert router._get_chain_for_function("default") == ["claude-cli", "llama-local"]
    # A claude-cli already in the chain is not duplicated.
    assert router._get_chain_for_function("late_cli") == ["claude-cli", "llama-local"]


def test_allow_metered_keeps_the_anthropic_provider(router_for):
    router = router_for("allow_metered")
    provider, model_id, _cfg = router.resolve_model("claude-3-5-sonnet")
    assert isinstance(provider, AnthropicLLMProvider)
    assert model_id == "claude-3-5-sonnet"
    assert router._get_chain_for_function("default") == ["claude-3-5-sonnet", "llama-local"]
