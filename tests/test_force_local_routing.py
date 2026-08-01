# CUI // SP-CTI
"""force_local: the content-bearing CUI functions must never reach a cloud provider.

prem-p0-02. These functions used to be protected by *pinning* the chain to
[qwen3-local, llama-local] in args/llm_config.yaml. A pin is an unenforced,
hand-maintained model list: nothing stopped a cloud model being added to it, and
nothing stopped the CLI bridge prepending `claude-cli` to it at invoke time.

`force_local: true` replaces the pin with an enforced, fail-closed declaration.
"""
import pytest
import yaml

from tools.llm.cli_bridge.activate import (
    CLI_MODEL_NAME,
    _cli_bridge_override,
    cli_bridge_override,
    is_local_only_model,
)
from tools.llm.config_path import resolve_llm_config_path
from tools.llm.router import ForceLocalViolation, LLMRouter

# Providers mirroring the shipped config: `ollama` and `ollama_cloud` share
# `type: ollama` — only the api_key_env tells them apart.
PROVIDERS = {
    "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
    "ollama_cloud": {
        "type": "ollama",
        "base_url": "https://ollama.com",
        "api_key_env": "OLLAMA_API_KEY",
    },
    "cli": {"type": "cli", "cli_binary": "claude"},
    "anthropic": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
}

MODELS = {
    "qwen3-local": {"provider": "ollama", "model_id": "qwen3.5:latest"},
    "llama-local": {"provider": "ollama", "model_id": "llama3:latest"},
    "kimi-cloud": {"provider": "ollama_cloud", "model_id": "kimi-k2.6:cloud"},
    "claude-sonnet": {"provider": "anthropic", "model_id": "claude-sonnet-4"},
    CLI_MODEL_NAME: {"provider": "cli", "model_id": "claude-cli"},
}


def _router(tmp_path, routing):
    cfg = {"providers": PROVIDERS, "models": MODELS, "routing": routing, "settings": {}}
    path = tmp_path / "llm_config.yaml"
    path.write_text(yaml.dump(cfg), encoding="utf-8")
    return LLMRouter(config_path=str(path))


@pytest.fixture
def no_bridge_override():
    """Always unwind the CLI-bridge override, however the test set it.

    It is a ContextVar, so a value leaked out of one test poisons every sibling
    that runs after it in the same context — and the poison is "route through the
    Claude CLI", i.e. a cloud egress path. Snapshot and restore unconditionally
    rather than trusting each test to hand back its Token.
    """
    before = _cli_bridge_override.get()
    try:
        yield
    finally:
        _cli_bridge_override.set(before)


# ── The definition of "local" ────────────────────────────────────────────────

class TestLocality:
    def test_ollama_cloud_is_not_local(self):
        """`ollama_cloud` speaks the Ollama protocol but is a cloud API."""
        assert not is_local_only_model("kimi-cloud", MODELS, PROVIDERS)

    def test_ollama_is_local(self):
        assert is_local_only_model("qwen3-local", MODELS, PROVIDERS)

    def test_cli_is_not_local(self):
        assert not is_local_only_model(CLI_MODEL_NAME, MODELS, PROVIDERS)

    def test_unknown_model_is_not_local(self):
        """Fail closed: a model we cannot resolve is not proven local."""
        assert not is_local_only_model("who-knows", MODELS, PROVIDERS)


# ── Enforcement ──────────────────────────────────────────────────────────────

class TestForceLocalEnforcement:
    def test_cloud_model_is_stripped_from_a_force_local_chain(self, tmp_path):
        """The pin could not do this: a cloud model in the list just got used."""
        r = _router(tmp_path, {
            "proposal_drafting": {
                "chain": ["qwen3-local", "kimi-cloud", "claude-sonnet"],
                "force_local": True,
            }
        })
        assert r._get_chain_for_function("proposal_drafting") == ["qwen3-local"]

    def test_no_local_model_left_fails_closed(self, tmp_path):
        """Refuse the call. Never silently fall through to cloud."""
        r = _router(tmp_path, {
            "proposal_drafting": {"chain": ["kimi-cloud"], "force_local": True}
        })
        with pytest.raises(ForceLocalViolation) as exc:
            r._get_chain_for_function("proposal_drafting")
        assert "proposal_drafting" in str(exc.value)

    def test_non_force_local_function_is_untouched(self, tmp_path):
        r = _router(tmp_path, {
            "code_generation": {"chain": ["qwen3-local", "claude-sonnet"]}
        })
        assert r._get_chain_for_function("code_generation") == ["qwen3-local", "claude-sonnet"]

    def test_is_force_local_reads_the_declaration(self, tmp_path):
        r = _router(tmp_path, {
            "proposal_drafting": {"chain": ["qwen3-local"], "force_local": True},
            "code_generation": {"chain": ["qwen3-local"]},
        })
        assert r.is_force_local("proposal_drafting")
        assert not r.is_force_local("code_generation")


# ── The hole the pin never closed ────────────────────────────────────────────

class TestCliBridgeCannotFrontCuiChains:
    """prepend_cli_to_chains() (config-rewrite time) already skipped local-only
    chains. apply_cli_bridge_override() (invoke time, per-request toggle) did not:
    it prepends `claude-cli` to whatever chain it is handed. force_local is enforced
    AFTER that override, which is what closes it."""

    def test_invoke_time_override_cannot_front_a_force_local_chain(
        self, tmp_path, no_bridge_override
    ):
        r = _router(tmp_path, {
            "proposal_drafting": {
                "chain": ["qwen3-local", "llama-local"],
                "force_local": True,
            }
        })
        cli_bridge_override(True)  # per-page toggle: "route me through the CLI"
        chain = r._get_chain_for_function("proposal_drafting")
        assert CLI_MODEL_NAME not in chain, "CUI chain fronted by the Claude CLI"
        assert chain == ["qwen3-local", "llama-local"]

    def test_the_bridge_still_works_for_non_cui_functions(
        self, tmp_path, no_bridge_override
    ):
        """Guard against fixing the leak by breaking the feature."""
        r = _router(tmp_path, {"code_generation": {"chain": ["qwen3-local"]}})
        cli_bridge_override(True)
        assert r._get_chain_for_function("code_generation")[0] == CLI_MODEL_NAME


# ── The shipped config ───────────────────────────────────────────────────────

# The functions that carried the local-only pin before prem-p0-02.
FORMERLY_PINNED = [
    "proposal_drafting",
    "bid_scoring",
    "color_review",
    "rfi_writer_drafting",
    "rfi_editor_drafting",
    "rfi_reviewer_review",
    "rfi_researcher_knowledge",
    "rfi_compliance_assessment",
]


@pytest.fixture(scope="module")
def shipped():
    return yaml.safe_load(open(resolve_llm_config_path(), encoding="utf-8"))


class TestShippedConfig:
    @pytest.mark.parametrize("fn", FORMERLY_PINNED)
    def test_every_formerly_pinned_function_is_force_local(self, fn, shipped):
        """Replacing the pin must not drop the guarantee it carried."""
        route = shipped["routing"].get(fn)
        assert route, f"{fn} vanished from routing"
        assert route.get("force_local") is True, f"{fn} lost its local-only guarantee"

    @pytest.mark.parametrize("fn", FORMERLY_PINNED)
    def test_every_force_local_chain_can_actually_run_locally(self, fn, shipped):
        """force_local + an all-cloud chain would fail closed at invoke time —
        correct, but a config bug. Catch it here instead."""
        chain = shipped["routing"][fn]["chain"]
        local = [m for m in chain if is_local_only_model(m, shipped["models"], shipped["providers"])]
        assert local, f"{fn} is force_local but has no local model to run on"

    def test_no_force_local_function_lists_a_cloud_model(self, shipped):
        """Defence in depth: the chains stay local-first, so even an enforcement
        bug cannot leak. If you add a cloud model here, say why."""
        for fn, route in shipped["routing"].items():
            if not route.get("force_local"):
                continue
            cloud = [
                m for m in route.get("chain", [])
                if not is_local_only_model(m, shipped["models"], shipped["providers"])
            ]
            assert not cloud, f"{fn} is force_local but lists cloud model(s) {cloud}"
