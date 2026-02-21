# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for the ICDEV LLM Router (tools/llm/router.py).

Validates config loading, provider resolution, fallback chains,
availability cache TTL, invoke with mock providers, and effort
level configuration.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

try:
    from tools.llm.router import LLMRouter, _expand_env
    from tools.llm.provider import LLMProvider, LLMRequest, LLMResponse
    _IMPORT_OK = True
except ImportError:
    _IMPORT_OK = False

pytestmark = pytest.mark.skipif(not _IMPORT_OK, reason="tools.llm.router not available")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    providers=None, models=None, routing=None, settings=None, embeddings=None,
):
    """Build a minimal llm_config dict for testing."""
    return {
        "providers": providers or {},
        "models": models or {},
        "routing": routing or {},
        "settings": settings or {},
        "embeddings": embeddings or {},
    }


class MockProvider(LLMProvider):
    """A mock LLM provider for testing."""

    def __init__(self, name="mock", available=True, response_text="mock response"):
        self._name = name
        self._available = available
        self._response_text = response_text

    @property
    def provider_name(self):
        return self._name

    def invoke(self, request, model_id, model_config):
        if not self._available:
            raise RuntimeError(f"Provider {self._name} unavailable")
        return LLMResponse(
            content=self._response_text,
            model_id=model_id,
            provider=self._name,
            input_tokens=10,
            output_tokens=20,
        )

    def check_availability(self, model_id):
        return self._available


# ---------------------------------------------------------------------------
# Config Loading Tests
# ---------------------------------------------------------------------------

class TestConfigLoading:
    """Verify the router loads YAML configuration correctly."""

    def test_missing_config_produces_empty(self, tmp_path):
        missing = tmp_path / "nonexistent.yaml"
        router = LLMRouter(config_path=str(missing))
        assert router._config == {}

    def test_valid_config_loads(self, tmp_path):
        config_file = tmp_path / "llm_config.yaml"
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(
            providers={"test_provider": {"type": "ollama"}},
            models={"test_model": {"provider": "test_provider", "model_id": "m1"}},
            routing={"default": {"chain": ["test_model"]}},
        )
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        assert "test_provider" in router._config.get("providers", {})

    def test_cache_ttl_from_config(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(settings={"availability_cache_ttl_seconds": 600})
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        assert router._cache_ttl == 600.0


# ---------------------------------------------------------------------------
# Environment Variable Expansion
# ---------------------------------------------------------------------------

class TestEnvExpansion:
    """Verify ${VAR:-default} expansion in config values."""

    def test_expand_with_default(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR_123", raising=False)
        result = _expand_env("${NONEXISTENT_VAR_123:-fallback}")
        assert result == "fallback"

    def test_expand_with_env_set(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_VAR", "actual_value")
        result = _expand_env("${MY_TEST_VAR:-fallback}")
        assert result == "actual_value"

    def test_expand_non_string(self):
        assert _expand_env(42) == 42
        assert _expand_env(None) is None

    def test_expand_plain_string(self):
        assert _expand_env("no-variables-here") == "no-variables-here"


# ---------------------------------------------------------------------------
# Provider Resolution Tests
# ---------------------------------------------------------------------------

class TestProviderResolution:
    """Verify get_provider_for_function resolves the correct provider."""

    def test_no_routing_returns_none(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config()
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        provider, model_id, model_cfg = router.get_provider_for_function("code_generation")
        assert provider is None
        assert model_id == ""

    def test_empty_chain_returns_none(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(
            routing={"code_generation": {"chain": []}},
        )
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        provider, _, _ = router.get_provider_for_function("code_generation")
        assert provider is None


# ---------------------------------------------------------------------------
# Availability Cache Tests
# ---------------------------------------------------------------------------

class TestAvailabilityCache:
    """Verify the availability cache with TTL."""

    def test_cache_stores_result(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(
            providers={"p1": {"type": "ollama"}},
            models={"m1": {"provider": "p1", "model_id": "test"}},
            routing={"default": {"chain": ["m1"]}},
            settings={"availability_cache_ttl_seconds": 1800},
        )
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        # Manually set cache
        router._availability_cache["m1"] = True
        router._availability_cache_time = time.time()
        assert router._check_model_available("m1") is True

    def test_cache_expires_after_ttl(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(
            providers={"p1": {"type": "ollama"}},
            models={"m1": {"provider": "p1", "model_id": "test"}},
            settings={"availability_cache_ttl_seconds": 0.01},
        )
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        # Seed cache with an old entry
        router._availability_cache["m1"] = True
        router._availability_cache_time = time.time() - 100.0  # well past TTL
        old_cache_time = router._availability_cache_time
        # When we check, cache should be cleared and refreshed
        router._check_model_available("m1")
        # The cache time should have been reset (newer than old value)
        assert router._availability_cache_time > old_cache_time


# ---------------------------------------------------------------------------
# Invoke with Mock Provider
# ---------------------------------------------------------------------------

class TestInvoke:
    """Verify invoke walks the fallback chain and returns responses."""

    def _setup_router_with_mock(self, tmp_path, mock_provider):
        """Helper: create a router and inject a mock provider."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(
            providers={"mock_p": {"type": "ollama"}},
            models={"mock_m": {"provider": "mock_p", "model_id": "mock-model"}},
            routing={
                "test_func": {"chain": ["mock_m"], "effort": "high"},
                "default": {"chain": ["mock_m"]},
            },
        )
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        # Inject mock provider
        router._providers["mock_p"] = mock_provider
        router._availability_cache["mock_m"] = True
        router._availability_cache_time = time.time()
        return router

    def test_invoke_returns_response(self, tmp_path):
        mock = MockProvider(available=True, response_text="hello world")
        router = self._setup_router_with_mock(tmp_path, mock)
        req = LLMRequest(messages=[{"role": "user", "content": "test"}])
        resp = router.invoke("test_func", req)
        assert resp.content == "hello world"
        assert resp.provider == "mock"

    def test_invoke_applies_effort(self, tmp_path):
        mock = MockProvider(available=True)
        router = self._setup_router_with_mock(tmp_path, mock)
        req = LLMRequest(messages=[{"role": "user", "content": "test"}])
        router.invoke("test_func", req)
        # Effort should be set from config
        assert req.effort == "high"

    def test_invoke_raises_when_all_fail(self, tmp_path):
        mock = MockProvider(available=False)
        router = self._setup_router_with_mock(tmp_path, mock)
        req = LLMRequest(messages=[{"role": "user", "content": "test"}])
        with pytest.raises(RuntimeError, match="All providers"):
            router.invoke("test_func", req)

    def test_invoke_fallback_to_next(self, tmp_path):
        """When first provider fails, second should succeed."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")

        failing = MockProvider(name="fail", available=False)
        working = MockProvider(name="work", available=True, response_text="fallback ok")

        cfg = _make_config(
            providers={
                "p_fail": {"type": "ollama"},
                "p_work": {"type": "ollama"},
            },
            models={
                "m_fail": {"provider": "p_fail", "model_id": "fail-model"},
                "m_work": {"provider": "p_work", "model_id": "work-model"},
            },
            routing={"test_func": {"chain": ["m_fail", "m_work"]}},
        )
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")

        router = LLMRouter(config_path=str(config_file))
        router._providers["p_fail"] = failing
        router._providers["p_work"] = working
        router._availability_cache["m_fail"] = True
        router._availability_cache["m_work"] = True
        router._availability_cache_time = time.time()

        req = LLMRequest(messages=[{"role": "user", "content": "test"}])
        resp = router.invoke("test_func", req)
        assert resp.content == "fallback ok"
        assert resp.provider == "work"


# ---------------------------------------------------------------------------
# Effort Level Configuration
# ---------------------------------------------------------------------------

class TestEffortLevel:
    """Verify get_effort returns the configured effort for a function."""

    def test_effort_from_config(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(
            routing={
                "code_generation": {"chain": [], "effort": "max"},
                "nlq_sql": {"chain": [], "effort": "low"},
                "default": {"chain": [], "effort": "medium"},
            },
        )
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        assert router.get_effort("code_generation") == "max"
        assert router.get_effort("nlq_sql") == "low"

    def test_effort_default_fallback(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(
            routing={"default": {"chain": [], "effort": "medium"}},
        )
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        assert router.get_effort("unknown_function") == "medium"


# ---------------------------------------------------------------------------
# Model Pricing Lookup
# ---------------------------------------------------------------------------

class TestModelPricing:
    """Verify pricing lookup for models."""

    def test_get_model_pricing(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(
            models={
                "m1": {
                    "provider": "p1",
                    "model_id": "claude-sonnet",
                    "pricing": {"input_per_1k": 0.003, "output_per_1k": 0.015},
                },
            },
        )
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        pricing = router.get_model_pricing("claude-sonnet")
        assert pricing["input_per_1k"] == 0.003

    def test_get_model_pricing_unknown(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config()
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        pricing = router.get_model_pricing("nonexistent-model")
        assert pricing == {}

    def test_get_all_model_pricing(self, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not available")
        cfg = _make_config(
            models={
                "m1": {"provider": "p1", "model_id": "a", "pricing": {"cost": 1}},
                "m2": {"provider": "p1", "model_id": "b", "pricing": {"cost": 2}},
            },
        )
        config_file = tmp_path / "llm_config.yaml"
        config_file.write_text(yaml.dump(cfg), encoding="utf-8")
        router = LLMRouter(config_path=str(config_file))
        all_pricing = router.get_all_model_pricing()
        assert "a" in all_pricing
        assert "b" in all_pricing


# CUI // SP-CTI
