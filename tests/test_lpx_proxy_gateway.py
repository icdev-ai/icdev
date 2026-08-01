# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for the opt-in LLM proxy gateway resolution (lpx-proxy-03).

Verifies the gateway is OFF by default, redirects only cloud provider types when
enabled, leaves ollama/bedrock untouched, never mutates the input config, and
does not read proxy_resolver's ICDEV_LLM_PROXY egress var.
"""

import pytest

from tools.llm.proxy_gateway import (
    apply_gateway_to_provider_cfg,
    is_proxy_enabled,
    proxy_base_url,
    ENV_ENABLED,
    ENV_BASE_URL,
    ENV_VIRTUAL_KEY,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (ENV_ENABLED, ENV_BASE_URL, ENV_VIRTUAL_KEY):
        monkeypatch.delenv(var, raising=False)
    yield


def test_disabled_by_default():
    assert is_proxy_enabled() is False
    cfg = {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}
    # Returns the SAME object untouched when disabled.
    assert apply_gateway_to_provider_cfg("anthropic", cfg) is cfg


@pytest.mark.parametrize("val", ["true", "1", "yes", "on", "TRUE", "On"])
def test_enabled_flag_truthy(monkeypatch, val):
    monkeypatch.setenv(ENV_ENABLED, val)
    assert is_proxy_enabled() is True


@pytest.mark.parametrize("val", ["false", "0", "no", "", "off", "maybe"])
def test_enabled_flag_falsey(monkeypatch, val):
    monkeypatch.setenv(ENV_ENABLED, val)
    assert is_proxy_enabled() is False


def test_anthropic_redirected_when_enabled(monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "true")
    monkeypatch.setenv(ENV_BASE_URL, "http://proxy:4000")
    cfg = {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY", "base_url": "https://api.anthropic.com"}
    out = apply_gateway_to_provider_cfg("anthropic", cfg)
    assert out is not cfg  # copy, not mutation
    assert out["base_url"] == "http://proxy:4000"  # anthropic base has no /v1 suffix
    assert out["api_key_env"] == ENV_VIRTUAL_KEY
    # Original is untouched — real key env still present.
    assert cfg["api_key_env"] == "ANTHROPIC_API_KEY"
    assert cfg["base_url"] == "https://api.anthropic.com"


def test_openai_gets_v1_suffix(monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "true")
    monkeypatch.setenv(ENV_BASE_URL, "http://proxy:4000")
    cfg = {"type": "openai", "api_key_env": "OPENAI_API_KEY"}
    out = apply_gateway_to_provider_cfg("openai", cfg)
    assert out["base_url"] == "http://proxy:4000/v1"
    assert out["api_key_env"] == ENV_VIRTUAL_KEY


@pytest.mark.parametrize("ptype", ["ollama", "bedrock"])
def test_local_and_cui_types_never_redirected(monkeypatch, ptype):
    """Air-gap (ollama) and GovCloud/CUI (bedrock) paths are never proxied."""
    monkeypatch.setenv(ENV_ENABLED, "true")
    cfg = {"type": ptype, "base_url": "http://localhost:11434"}
    assert apply_gateway_to_provider_cfg(ptype, cfg) is cfg


def test_per_provider_virtual_key_override(monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "true")
    cfg = {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY", "proxy_api_key_env": "MY_VKEY"}
    out = apply_gateway_to_provider_cfg("anthropic", cfg)
    assert out["api_key_env"] == "MY_VKEY"


def test_default_base_url_is_loopback(monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "true")
    assert proxy_base_url() == "http://localhost:4000"


def test_does_not_read_egress_proxy_var(monkeypatch):
    """The gateway must not be triggered by proxy_resolver's ICDEV_LLM_PROXY."""
    monkeypatch.setenv("ICDEV_LLM_PROXY", "http://corp-egress:8080")
    assert is_proxy_enabled() is False
