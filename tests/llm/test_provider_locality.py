# CUI // SP-CTI
"""omx-vllm-01: ONE declared locality definition for the CUI egress boundary.

Before: `_is_local_only_provider` was `type == "ollama" and not api_key_env`, so
every vLLM model was CLOUD; and the router's `prefer_local` admitted ANY
`openai_compatible` provider, so api.mistral.ai counted as local. Now each
provider DECLARES `locality:` in args/llm_config.yaml and both callers read it
through the same function.
"""
from pathlib import Path

import yaml

from tools.llm.cli_bridge.activate import (
    is_local_only_model,
    locality_findings,
)
from tools.llm.router import LLMRouter

SHIPPED_CONFIG = Path(__file__).resolve().parents[2] / "args" / "llm_config.yaml"

PROVIDERS = {
    "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
    "vllm": {"type": "openai_compatible", "locality": "local",
             "base_url": "${VLLM_BASE_URL:-http://localhost:8000/v1}", "api_key_env": "VLLM_API_KEY"},
    "localai": {"type": "openai_compatible", "locality": "local",
                "base_url": "http://127.0.0.1:8080/v1", "api_key_env": "LOCALAI_API_KEY"},
    "mistral_vllm": {"type": "openai_compatible", "locality": "local",
                     "base_url": "http://192.168.1.20:8100/v1"},
    "mistral": {"type": "openai_compatible", "locality": "cloud",
                "base_url": "https://api.mistral.ai/v1", "api_key_env": "MISTRAL_API_KEY"},
    "undeclared": {"type": "openai_compatible", "base_url": "http://localhost:9000/v1"},
    "lan": {"type": "openai_compatible", "locality": "private", "base_url": "http://gpu01.lan:8000/v1"},
}
MODELS = {
    "vllm-model": {"provider": "vllm", "model_id": "qwen"},
    "localai-model": {"provider": "localai", "model_id": "llama"},
    "mistral-vllm-model": {"provider": "mistral_vllm", "model_id": "leanstral"},
    "mistral-cloud": {"provider": "mistral", "model_id": "mistral-small"},
    "undeclared-model": {"provider": "undeclared", "model_id": "x"},
    "lan-model": {"provider": "lan", "model_id": "y"},
    "ollama-model": {"provider": "ollama", "model_id": "qwen3.5:latest"},
}


class TestIsLocalOnlyModel:
    def test_vllm_localai_and_mistral_vllm_are_local(self, monkeypatch):
        monkeypatch.delenv("VLLM_BASE_URL", raising=False)
        for name in ("vllm-model", "localai-model", "mistral-vllm-model"):
            assert is_local_only_model(name, MODELS, PROVIDERS), name

    def test_cloud_mistral_is_not_local(self):
        assert not is_local_only_model("mistral-cloud", MODELS, PROVIDERS)

    def test_undeclared_openai_compatible_is_not_local(self):
        """Even on localhost: an undeclared non-Ollama provider fails closed."""
        assert not is_local_only_model("undeclared-model", MODELS, PROVIDERS)

    def test_undeclared_ollama_keeps_the_historical_rule(self):
        assert is_local_only_model("ollama-model", MODELS, PROVIDERS)

    def test_local_declaration_cannot_launder_a_public_host(self, monkeypatch):
        monkeypatch.setenv("VLLM_BASE_URL", "https://api.example.com/v1")
        assert not is_local_only_model("vllm-model", MODELS, PROVIDERS)

    def test_private_needs_the_operator_opt_in(self, monkeypatch):
        monkeypatch.delenv("ICDEV_PRIVATE_IS_LOCAL", raising=False)
        assert not is_local_only_model("lan-model", MODELS, PROVIDERS)
        monkeypatch.setenv("ICDEV_PRIVATE_IS_LOCAL", "1")
        assert is_local_only_model("lan-model", MODELS, PROVIDERS)


class TestShippedConfig:
    def test_shipped_providers_declare_the_expected_locality(self, monkeypatch):
        for var in ("VLLM_BASE_URL", "LOCALAI_BASE_URL", "MISTRAL_VLLM_BASE_URL"):
            monkeypatch.delenv(var, raising=False)
        cfg = yaml.safe_load(SHIPPED_CONFIG.read_text(encoding="utf-8"))
        providers = cfg["providers"]
        for name in providers:
            assert "locality" in providers[name], f"{name} declares no locality"
        models = {p: {"provider": p} for p in providers}
        for name in ("vllm", "localai", "mistral_vllm", "ollama"):
            assert is_local_only_model(name, models, providers), name
        for name in ("mistral", "openai", "anthropic", "gemini", "bedrock", "ollama_cloud", "gateway"):
            assert not is_local_only_model(name, models, providers), name
        assert locality_findings(providers, env={}) == []


class TestPreferLocal:
    def _router(self, tmp_path):
        cfg = {"providers": PROVIDERS, "models": MODELS, "routing": {},
               "settings": {"prefer_local": True}}
        path = tmp_path / "llm_config.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        return LLMRouter(config_path=str(path))

    def test_prefer_local_excludes_cloud_mistral(self, tmp_path, monkeypatch):
        router = self._router(tmp_path)
        probed = []

        class _Provider:
            def check_availability(self, model_id):
                probed.append(model_id)
                return True

        monkeypatch.setattr(router, "_get_provider", lambda name: _Provider())
        monkeypatch.delenv("VLLM_BASE_URL", raising=False)
        assert router._check_model_available("mistral-cloud") is False
        assert router._check_model_available("undeclared-model") is False
        assert router._check_model_available("vllm-model") is True
        assert probed == ["qwen"]


class TestPublicHostDeclaredLocalIsFlagged:
    def test_flagged(self):
        providers = {
            "rogue": {"type": "openai_compatible", "locality": "local",
                      "base_url": "https://api.mistral.ai/v1"},
            "ok": {"type": "openai_compatible", "locality": "local", "base_url": "http://[::1]:8000"},
            "bad_value": {"type": "openai_compatible", "locality": "nearby"},
            "pub_private": {"type": "openai_compatible", "locality": "private", "base_url": "http://8.8.8.8"},
        }
        findings = locality_findings(providers, env={})
        assert len(findings) == 3
        assert any(f.startswith("rogue:") and "api.mistral.ai" in f for f in findings)
        assert any(f.startswith("bad_value:") for f in findings)
        assert any(f.startswith("pub_private:") for f in findings)

    def test_coherence_check_passes_on_shipped_config(self):
        from tools.workflow.coherence_checker import CHECK_REGISTRY

        result = CHECK_REGISTRY["provider_locality"]()
        assert result.status in ("pass", "warn"), result.actual
