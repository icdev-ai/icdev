# CUI // SP-CTI
"""Tests for LLMRouter core-profile default integration."""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.llm.router import LLMRouter


def test_apply_profile_defaults_promotes_default_model(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_DEFAULT_MODEL", "local-llama")
    monkeypatch.delenv("ICDEV_LLM_PROVIDER", raising=False)

    router = LLMRouter.__new__(LLMRouter)
    router._config = {
        "models": {
            "claude-sonnet": {"provider": "anthropic"},
            "local-llama": {"provider": "ollama"},
        },
        "routing": {"default": {"chain": ["claude-sonnet", "local-llama"]}},
    }
    router._apply_profile_defaults()

    assert router._config["routing"]["default"]["chain"][0] == "local-llama"


def test_apply_profile_defaults_promotes_first_matching_provider(monkeypatch):
    monkeypatch.delenv("ICDEV_LLM_DEFAULT_MODEL", raising=False)
    monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")

    router = LLMRouter.__new__(LLMRouter)
    router._config = {
        "models": {
            "claude-sonnet": {"provider": "anthropic"},
            "local-llama": {"provider": "ollama"},
        },
        "routing": {"default": {"chain": ["claude-sonnet", "local-llama"]}},
    }
    router._apply_profile_defaults()

    assert router._config["routing"]["default"]["chain"][0] == "local-llama"


def test_apply_profile_defaults_creates_default_chain_when_missing(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_DEFAULT_MODEL", "local-llama")
    monkeypatch.delenv("ICDEV_LLM_PROVIDER", raising=False)

    router = LLMRouter.__new__(LLMRouter)
    router._config = {
        "models": {"local-llama": {"provider": "ollama"}},
        "routing": {},
    }
    router._apply_profile_defaults()

    assert router._config["routing"]["default"]["chain"] == ["local-llama"]


def test_apply_profile_defaults_noop_when_no_profile_env(monkeypatch):
    monkeypatch.delenv("ICDEV_LLM_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("ICDEV_LLM_PROVIDER", raising=False)

    router = LLMRouter.__new__(LLMRouter)
    router._config = {
        "models": {"claude-sonnet": {"provider": "anthropic"}},
        "routing": {"default": {"chain": ["claude-sonnet"]}},
    }
    router._apply_profile_defaults()

    assert router._config["routing"]["default"]["chain"] == ["claude-sonnet"]
