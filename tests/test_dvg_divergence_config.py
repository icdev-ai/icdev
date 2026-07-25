# CUI // SP-CTI
"""Shipped-config guards for Divergence mode (dvg-core-02).

Divergence is registered in args/llm_config.yaml the same way cot/cod/council
are, but with one non-negotiable invariant: it is OPT-IN. Cost is the headline
risk -- upstream reports ~10 agent calls and 5-10x the spend of a direct answer
-- so the mode must ship disabled and never become a default generation path.
These tests pin that invariant against the REAL config, plus the routing roles
and the exclusion-list wiring, so a future edit that flips the default (or drops
a role) fails loudly.
"""
import yaml

from tools.llm.config_path import resolve_llm_config_path


def _cfg():
    return yaml.safe_load(open(resolve_llm_config_path(), encoding="utf-8"))


class TestDivergenceShippedConfig:
    def test_divergence_mode_registered(self):
        chain = _cfg()["chain_orchestration"]
        assert "divergence" in chain, "divergence mode must be registered alongside cot/cod/council"
        div = chain["divergence"]
        for key in ("num_branches", "frame_set", "branch_pool_role", "critic_role", "excluded_functions"):
            assert key in div, f"divergence config missing '{key}'"

    def test_divergence_disabled_by_default(self):
        """The whole point: divergence must SHIP disabled. This test failing means
        someone made an expensive fan-out the default path -- do not 'fix' it by
        editing the test."""
        assert _cfg()["chain_orchestration"]["divergence"]["enabled"] is False

    def test_no_per_function_ships_enabled(self):
        """No function may be opted-in at ship time either -- opt-in is a caller
        decision, not a default."""
        per_fn = _cfg()["chain_orchestration"]["divergence"].get("per_function") or {}
        enabled_fns = [fn for fn, o in per_fn.items() if isinstance(o, dict) and o.get("enabled") is True]
        assert not enabled_fns, f"functions shipped with divergence enabled: {enabled_fns}"

    def test_divergence_routing_roles_present(self):
        routing = _cfg()["routing"]
        div = _cfg()["chain_orchestration"]["divergence"]
        assert div["branch_pool_role"] in routing, "branch_pool_role must resolve to a routing chain"
        assert div["critic_role"] in routing, "critic_role must resolve to a routing chain"
        # Every model in the branch pool must be a registered model.
        models = set(_cfg()["models"].keys())
        for m in routing[div["branch_pool_role"]]["chain"]:
            assert m in models, f"branch pool references unregistered model '{m}'"

    def test_exclusions_honored_by_is_excluded(self):
        """A configured divergence exclusion is a hard block enforced by
        _is_excluded, and it must win even if a function is otherwise enabled."""
        from tools.llm.chain_orchestrator import ChainOrchestrator
        from tools.llm.router import LLMRouter

        orch = ChainOrchestrator(router=LLMRouter())
        # Inject an exclusion into the loaded config and confirm it trips.
        orch._config.setdefault("divergence", {}).setdefault("excluded_functions", []).append("some_blocked_fn")
        assert orch._is_excluded("some_blocked_fn", "divergence") is True
        assert orch._is_excluded("not_blocked_fn", "divergence") is False
