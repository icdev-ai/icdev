# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""lpx-egress-01 (CRITICAL air-gap safety property).

With the LLM proxy flag OFF, ICDEV must run fully air-gapped: nothing may attempt
to reach a proxy, no proxy env var may be required, and no import may fail because
the proxy package/container is absent. These are blocking guarantees, not
nice-to-haves.
"""

import pytest

# The gateway module must import with zero proxy package/container present.
from tools.llm.proxy_gateway import (
    apply_gateway_to_provider_cfg,
    is_proxy_enabled,
    ENV_ENABLED,
    ENV_BASE_URL,
    ENV_VIRTUAL_KEY,
)


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch):
    """Simulate the default deployment: proxy flag OFF, no proxy vars set."""
    for var in (ENV_ENABLED, ENV_BASE_URL, ENV_VIRTUAL_KEY):
        monkeypatch.delenv(var, raising=False)
    yield


def test_proxy_disabled_by_default():
    assert is_proxy_enabled() is False


def test_gateway_is_noop_without_any_proxy_env():
    """No proxy env var is required — the gateway passes configs through."""
    cfg = {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY", "base_url": "https://api.anthropic.com"}
    assert apply_gateway_to_provider_cfg("anthropic", cfg) is cfg


def test_airgap_provider_type_never_redirected_even_if_flag_on(monkeypatch):
    """Even if the flag were toggled on, the local (ollama) path is never sent to
    a proxy — air-gap routing cannot become a cloud egress path."""
    monkeypatch.setenv(ENV_ENABLED, "true")
    cfg = {"type": "ollama", "base_url": "http://localhost:11434"}
    assert apply_gateway_to_provider_cfg("ollama", cfg) is cfg


def test_detect_environment_needs_no_network_and_no_proxy():
    """`tools.airgap` environment detection runs without a network call or any
    proxy dependency (mirrors `python -m tools.airgap --detect --json`)."""
    from tools.airgap import detect_environment

    info = detect_environment(use_cache=True)
    assert isinstance(info, dict)
    # Detection must not have required any proxy env var.
    import os

    assert ENV_ENABLED not in os.environ


def test_airgap_activation_introduces_no_proxy_dependency(monkeypatch):
    """Activating air-gap mode must not require or set any proxy env var, and the
    local provider it routes to must resolve to a LOCAL base_url, never a proxy."""
    for var in (ENV_ENABLED, ENV_BASE_URL, ENV_VIRTUAL_KEY):
        monkeypatch.delenv(var, raising=False)

    from tools.llm.router import LLMRouter
    from tools.airgap import activate_airgap, deactivate_airgap

    router = LLMRouter()
    try:
        result = activate_airgap(router)
        assert result.get("status") in {"activated", "already_active"}
        # Activation set no proxy env var.
        import os

        assert ENV_ENABLED not in os.environ
        assert ENV_BASE_URL not in os.environ

        # No provider may be redirected to the LLM proxy gateway (flag is off),
        # so none resolves to the proxy port. (ollama_cloud legitimately points
        # at https://ollama.com — the documented ollama vs ollama_cloud split —
        # so we do NOT assert every ollama-type is local, only that nothing is
        # sent to the proxy.)
        providers = router._config.get("providers", {})
        for name in providers:
            inst = router._get_provider(name)
            if inst is None:
                continue
            base = getattr(inst, "_base_url", getattr(inst, "base_url", "")) or ""
            assert ":4000" not in base, f"provider {name} unexpectedly resolved to a proxy endpoint {base!r}"

        # The LOCAL ollama provider specifically must stay local.
        local = router._get_provider("ollama")
        if local is not None:
            base = getattr(local, "_base_url", getattr(local, "base_url", "")) or ""
            assert any(t in base for t in ("localhost", "127.0.0.1", "11434", "host.docker.internal")), (
                f"local ollama provider resolved to non-local {base!r}"
            )
    finally:
        deactivate_airgap(router)
