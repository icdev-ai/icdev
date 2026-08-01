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

    def test_divergence_declares_its_own_caps(self):
        """cost_cap_usd / timeout_seconds are declared at the MODE level, not
        inherited. Inheriting the outer single-call backstop would hand
        _check_module_budget a pre-flight estimate ~num_branches too small."""
        div = _cfg()["chain_orchestration"]["divergence"]
        assert "cost_cap_usd" in div, "divergence must declare a fan-out-aware cost cap"
        assert "timeout_seconds" in div, "divergence must declare its own timeout"

    def test_cost_cap_covers_the_full_fan_out(self):
        """The invariant that makes the cap honest: the mode's cap must cover
        num_branches worth of single-call spend. Raising num_branches without
        raising cost_cap_usd fails here — which is the point."""
        chain = _cfg()["chain_orchestration"]
        div = chain["divergence"]
        outer_cap = chain["cost_cap_usd"]
        assert div["cost_cap_usd"] >= div["num_branches"] * outer_cap, (
            f"cost_cap_usd {div['cost_cap_usd']} under-reserves a "
            f"{div['num_branches']}-branch fan-out at {outer_cap}/call"
        )
        assert div["timeout_seconds"] >= chain["timeout_seconds"]

    def test_mode_level_caps_actually_resolve(self):
        """Guard the MECHANISM: _get_function_config must read the mode rung.
        Before dvg-core-02 it only read the outer config, so these keys would
        have been silently ignored — present in YAML, dead at runtime."""
        from tools.llm.chain_orchestrator import ChainOrchestrator
        from tools.llm.router import LLMRouter

        orch = ChainOrchestrator(router=LLMRouter())
        orch._config = {
            "cost_cap_usd": 0.50,
            "timeout_seconds": 120,
            "divergence": {"cost_cap_usd": 3.00, "timeout_seconds": 180, "per_function": {}},
        }
        cfg = orch._get_function_config("some_fn", "divergence")
        assert cfg["cost_cap_usd"] == 3.00
        assert cfg["timeout_seconds"] == 180

        # per_function still wins over the mode rung (narrowest scope).
        orch._config["divergence"]["per_function"] = {"some_fn": {"cost_cap_usd": 0.10}}
        assert orch._get_function_config("some_fn", "divergence")["cost_cap_usd"] == 0.10

        # Modes that omit the keys keep inheriting the outer backstop unchanged.
        orch._config["cot"] = {"per_function": {}}
        assert orch._get_function_config("some_fn", "cot")["cost_cap_usd"] == 0.50

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
