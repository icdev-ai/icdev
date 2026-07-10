# CUI // SP-CTI
"""One LLM config, resolved one way, and the CLI bridge must never front CUI chains."""
import importlib

import pytest
import yaml

from tools.llm.cli_bridge.activate import (
    CLI_MODEL_NAME,
    is_local_only_chain,
    prepend_cli_to_chains,
)
from tools.llm.config_path import CONFIG_ENV_VAR, describe_resolution, resolve_llm_config_path


# ── Single source of truth ────────────────────────────────────────────────────

class TestSingleSource:
    def test_both_routers_resolve_the_same_config(self):
        """tools.llm.router and icdev.tools.llm.router are distinct module objects.

        Each used to compute BASE_DIR from its own __file__, so they read
        <repo>/args/llm_config.yaml and <repo>/icdev/args/llm_config.yaml — two files
        that had drifted on model ids, providers and tier2_model.
        """
        a = importlib.import_module("tools.llm.router")
        b = importlib.import_module("icdev.tools.llm.router")
        assert a is not b, "guard: if these ever merge, this test is meaningless"
        assert a.DEFAULT_CONFIG_PATH == b.DEFAULT_CONFIG_PATH

    def test_packaged_copy_matches_canonical(self):
        """icdev/args/llm_config.yaml is a generated mirror; hand edits get reverted."""
        canonical = resolve_llm_config_path()
        packaged = describe_resolution()["packaged_default"]
        assert yaml.safe_load(open(canonical, encoding="utf-8")) == yaml.safe_load(
            open(packaged, encoding="utf-8")
        ), "args/llm_config.yaml and icdev/args/llm_config.yaml have drifted"

    def test_env_override_wins(self, monkeypatch, tmp_path):
        override = tmp_path / "custom.yaml"
        override.write_text("models: {}\n", encoding="utf-8")
        monkeypatch.setenv(CONFIG_ENV_VAR, str(override))
        assert resolve_llm_config_path() == override.resolve()
        assert describe_resolution()["source"] == "env"

    def test_project_config_used_when_no_override(self, monkeypatch):
        monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
        assert describe_resolution()["source"] == "project"
        assert resolve_llm_config_path().is_file()


# ── Config integrity ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load(open(resolve_llm_config_path(), encoding="utf-8"))


class TestConfigIntegrity:
    def test_every_model_names_a_registered_provider(self, cfg):
        unknown = {n: m["provider"] for n, m in cfg["models"].items()
                   if m.get("provider") not in cfg["providers"]}
        assert not unknown, f"models reference unregistered providers: {unknown}"

    def test_no_routing_chain_references_an_unknown_model(self, cfg):
        models = set(cfg["models"])
        dangling = {fn: [m for m in r.get("chain", []) if m not in models]
                    for fn, r in cfg["routing"].items()}
        dangling = {k: v for k, v in dangling.items() if v}
        assert not dangling, f"dangling chain models: {dangling}"

    def test_orchestration_debaters_are_registered_models(self, cfg):
        """`openai-gpt4o` was listed as a debater; the model is named `gpt-4o`, so the
        debater silently dropped out of every Chain-of-Debate."""
        models = set(cfg["models"])
        for engine in ("cod", "council"):
            block = cfg["chain_orchestration"].get(engine) or {}
            for key in ("debater_models", "advisor_models"):
                for m in block.get(key) or []:
                    assert m in models, f"chain_orchestration.{engine}.{key}: {m!r} not a model"

    def test_two_tier_models_are_registered(self, cfg):
        for tier in ("tier1_model", "tier2_model"):
            assert cfg["two_tier"][tier] in cfg["models"]

    def test_claude_models_declare_the_api_provider(self, cfg):
        """Portability: the router skips a model whose provider has no api_key_env set
        and falls through the chain, so `anthropic` works with a key and the CLI bridge
        covers environments without one. Hardcoding `claude-cli` pins every environment
        to the CLI even where a key exists."""
        for name in ("claude-opus", "claude-sonnet", "claude-haiku"):
            assert cfg["models"][name]["provider"] == "anthropic"


# ── CLI bridge must not front CUI chains ──────────────────────────────────────

def _cfg():
    return {
        "providers": {
            "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
            "ollama_cloud": {"type": "ollama", "base_url": "https://ollama.com",
                             "api_key_env": "OLLAMA_API_KEY"},
            "cli": {"type": "cli", "cli_binary": "claude"},
            "anthropic": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
        },
        "models": {
            "qwen3-local": {"provider": "ollama", "model_id": "qwen3.5:latest"},
            "llama-local": {"provider": "ollama", "model_id": "qwen3:4b"},
            "kimi-cloud": {"provider": "ollama_cloud", "model_id": "kimi-k2.6:cloud"},
            "claude-sonnet": {"provider": "anthropic", "model_id": "claude-sonnet-4-6"},
            CLI_MODEL_NAME: {"provider": "cli", "model_id": "claude-cli"},
        },
        "routing": {
            "proposal_drafting": {"chain": ["qwen3-local", "llama-local"]},   # CUI
            "rfi_writer_drafting": {"chain": ["qwen3-local", "llama-local"]},  # CUI
            "requirement_extraction": {"chain": ["kimi-cloud", "qwen3-local"]},
            "code_generation": {"chain": ["qwen3-local", "claude-sonnet"]},
        },
    }


class TestLocalOnlyDetection:
    def test_all_ollama_chain_is_local_only(self):
        c = _cfg()
        assert is_local_only_chain(["qwen3-local", "llama-local"], c["models"], c["providers"])

    def test_ollama_cloud_is_not_local(self):
        """ollama and ollama_cloud share type: ollama — the api_key_env distinguishes them."""
        c = _cfg()
        assert not is_local_only_chain(["kimi-cloud", "qwen3-local"], c["models"], c["providers"])

    def test_chain_with_a_cloud_model_is_not_local_only(self):
        c = _cfg()
        assert not is_local_only_chain(["qwen3-local", "claude-sonnet"], c["models"], c["providers"])

    def test_unknown_model_is_not_assumed_local(self):
        c = _cfg()
        assert not is_local_only_chain(["nope"], c["models"], c["providers"])

    def test_empty_chain_is_not_local_only(self):
        c = _cfg()
        assert not is_local_only_chain([], c["models"], c["providers"])


class TestPrependSkipsCuiChains:
    def test_cui_chains_are_left_alone(self):
        """proposal_drafting is declared LOCAL ONLY because its content is CUI.
        Prepending claude-cli would route it out through the Claude CLI."""
        out = prepend_cli_to_chains(_cfg())
        for fn in ("proposal_drafting", "rfi_writer_drafting"):
            assert CLI_MODEL_NAME not in out["routing"][fn]["chain"], f"{fn} leaked to the CLI"
            assert out["routing"][fn]["chain"] == ["qwen3-local", "llama-local"]

    def test_non_local_chains_get_the_bridge(self):
        out = prepend_cli_to_chains(_cfg())
        for fn in ("requirement_extraction", "code_generation"):
            assert out["routing"][fn]["chain"][0] == CLI_MODEL_NAME

    def test_idempotent(self):
        once = prepend_cli_to_chains(_cfg())
        twice = prepend_cli_to_chains(once)
        assert once == twice
        assert twice["routing"]["code_generation"]["chain"].count(CLI_MODEL_NAME) == 1

    def test_original_config_untouched(self):
        c = _cfg()
        prepend_cli_to_chains(c)
        assert c["routing"]["code_generation"]["chain"] == ["qwen3-local", "claude-sonnet"]

    def test_real_config_cui_functions_never_reach_the_cli(self, cfg):
        """The property that matters, asserted against the shipped config."""
        out = prepend_cli_to_chains(cfg)
        cui = [fn for fn, r in cfg["routing"].items()
               if is_local_only_chain(r.get("chain", []), cfg["models"], cfg["providers"])]
        assert cui, "expected some local-only chains in the shipped config"
        for fn in cui:
            assert CLI_MODEL_NAME not in out["routing"][fn]["chain"]
