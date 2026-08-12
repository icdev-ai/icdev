# CUI // SP-CTI
"""cost_budget is a DOWNGRADE gate, not a fifth blocking layer (exa-policy-04).

The four acceptance criteria, one class each:

  1. crossing a soft threshold ASKs — ONCE per threshold, not once per call
  2. reaching the hard limit DOWNGRADES the tier instead of failing the call
  3. the downgrade can reach the LOCAL tier (air-gap constraint)
  4. the behaviour is config-declared, with no model id in Python
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tools.llm import cost_budget as cb

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- A config that names no real model, so these tests never track vendor drift.
CONFIG = {
    # Locality is decided by cli_bridge.activate._is_local_only_provider:
    # `type: ollama` and NO api_key_env. A provider spec that merely looks local
    # (e.g. {"local_only": true}) is correctly NOT local — that fail-closed rule
    # is the CUI egress boundary, so the fixture has to satisfy the real one.
    "providers": {
        "ollama": {"type": "ollama"},
        "cloudco": {"type": "openai", "api_key_env": "CLOUDCO_API_KEY"},
    },
    "models": {
        "cheap-local": {"provider": "ollama", "pricing": {"input_per_1k": 0.0, "output_per_1k": 0.0}},
        "free-cloud": {"provider": "cloudco", "pricing": {"input_per_1k": 0.0, "output_per_1k": 0.0}},
        "mid-cloud": {"provider": "cloudco", "pricing": {"input_per_1k": 0.001, "output_per_1k": 0.002}},
        "dear-cloud": {"provider": "cloudco", "pricing": {"input_per_1k": 0.01, "output_per_1k": 0.03}},
        "unpriced-cloud": {"provider": "cloudco"},
    },
    "routing": {
        "default": {"chain": ["dear-cloud", "cheap-local"]},
        "widget_generation": {
            "chain": ["dear-cloud", "mid-cloud", "free-cloud", "cheap-local", "unpriced-cloud"]
        },
    },
    "cost_budget": {
        "enabled": True,
        "scope": "global",
        "period": "monthly",
        "limit_usd": 100.0,
        "soft_thresholds": [0.5, 0.8],
        "hard_action": "downgrade",
        "downgrade": {"max_blended_per_1k": 0.0, "prefer_local": True},
        "ask": {"approver": "record", "on_denied": "downgrade"},
        "per_function": {},
    },
}

CHAIN = CONFIG["routing"]["widget_generation"]["chain"]


@pytest.fixture
def spend(monkeypatch):
    """Drive `read_spend` directly. Returns a setter for dollars spent."""
    state = {"usd": 0.0, "available": True}

    def fake_read_spend(*, since, function=None):  # noqa: ARG001
        return state["usd"], state["available"]

    monkeypatch.setattr(cb, "read_spend", fake_read_spend)

    def _set(usd, *, available=True):
        state["usd"] = usd
        state["available"] = available

    return _set


@pytest.fixture(autouse=True)
def _no_ask_leak(monkeypatch):
    """Isolate the process-local ask cache between tests."""
    monkeypatch.setattr(cb, "_ASKED", set())


@pytest.fixture
def asks(monkeypatch):
    """Capture every ASK raised, bypassing the DB dedupe and the audit write."""
    raised = []

    monkeypatch.setattr(cb, "_already_asked", lambda t, r, s: (t, r, s) in cb._ASKED)

    def fake_record(**kwargs):
        raised.append(kwargs)
        return True

    import tools.agent_runtime.approval_gate as gate

    monkeypatch.setattr(gate, "record_decision", fake_record)
    return raised


# ---------------------------------------------------------------------------
# 1. Crossing a soft threshold produces an ASK — once per threshold
# ---------------------------------------------------------------------------
class TestSoftThresholdAsksOnce:
    def test_under_every_threshold_does_not_ask(self, spend, asks):
        spend(10.0)  # 10%
        verdict = cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        assert verdict.status == cb.STATUS_OK
        assert verdict.action == cb.ACTION_ALLOW
        assert asks == []

    def test_crossing_asks(self, spend, asks):
        spend(55.0)  # 55% — crosses 0.5
        verdict = cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        assert verdict.status == cb.STATUS_SOFT
        assert verdict.threshold_crossed == 0.5
        assert verdict.asked is True
        assert len(asks) == 1

    def test_asks_once_per_threshold_not_once_per_call(self, spend, asks):
        """The whole point: an ASK on every call is noise nobody reads."""
        spend(55.0)
        for _ in range(5):
            cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        assert len(asks) == 1, "soft threshold asked more than once"

    def test_a_second_threshold_asks_again(self, spend, asks):
        spend(55.0)
        cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        spend(85.0)  # now crosses 0.8
        verdict = cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        assert verdict.threshold_crossed == 0.8
        assert len(asks) == 2
        rules = [a["classification"].rule for a in asks]
        assert rules == ["cost_budget:soft:0.5", "cost_budget:soft:0.8"]

    def test_soft_threshold_does_not_change_the_chain(self, spend, asks):
        spend(55.0)
        verdict = cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        assert verdict.chain_after == CHAIN
        assert verdict.downgraded is False

    def test_ask_is_recorded_as_irreversible(self, spend, asks):
        """Money spent cannot be un-spent — the tier vocabulary is not bent."""
        from tools.agent_runtime.approval_gate import IRREVERSIBLE

        spend(55.0)
        cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        assert asks[0]["classification"].tier == IRREVERSIBLE

    def test_a_denied_ask_downgrades_early_rather_than_failing(self, spend, asks):
        cfg = {**CONFIG, "cost_budget": {**CONFIG["cost_budget"], "ask": {"approver": "deny", "on_denied": "downgrade"}}}
        spend(55.0)
        verdict = cb.evaluate("widget_generation", CHAIN, config=cfg)
        assert verdict.ask_approved is False
        assert verdict.action == cb.ACTION_DOWNGRADE
        assert verdict.chain_after[0] == "cheap-local"


# ---------------------------------------------------------------------------
# 2. The hard limit downgrades instead of failing the call
# ---------------------------------------------------------------------------
class TestHardLimitDowngrades:
    def test_hard_limit_downgrades_and_does_not_raise(self, spend, asks):
        spend(120.0)  # 120% of the limit
        verdict = cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        assert verdict.status == cb.STATUS_HARD
        assert verdict.action == cb.ACTION_DOWNGRADE
        assert verdict.downgraded is True

    def test_the_expensive_model_is_demoted_not_dropped(self, spend, asks):
        """Truncating the chain turns a budget event into an outage."""
        spend(120.0)
        verdict = cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        assert sorted(verdict.chain_after) == sorted(CHAIN), "a model was dropped"
        assert verdict.chain_after.index("dear-cloud") > verdict.chain_after.index("cheap-local")

    def test_apply_to_chain_returns_the_downgraded_chain(self, spend, asks):
        spend(120.0)
        chain, verdict = cb.apply_to_chain("widget_generation", CHAIN, config=CONFIG)
        assert chain == verdict.chain_after
        assert chain[0] == "cheap-local"

    def test_block_is_available_but_is_not_the_default(self, spend, asks):
        assert CONFIG["cost_budget"]["hard_action"] == "downgrade"
        cfg = {**CONFIG, "cost_budget": {**CONFIG["cost_budget"], "hard_action": "block"}}
        spend(120.0)
        verdict = cb.evaluate("widget_generation", CHAIN, config=cfg)
        assert verdict.action == cb.ACTION_BLOCK

    def test_unmeasurable_telemetry_is_not_a_zero_and_not_a_breach(self, spend, asks):
        """A fresh worktree must not read as 'spent nothing' or as 'over budget'."""
        spend(0.0, available=False)
        verdict = cb.evaluate("widget_generation", CHAIN, config=CONFIG)
        assert verdict.status == cb.STATUS_UNMEASURABLE
        assert verdict.action == cb.ACTION_ALLOW
        assert verdict.chain_after == CHAIN

    def test_disabled_is_a_no_op(self, spend, asks):
        cfg = {**CONFIG, "cost_budget": {**CONFIG["cost_budget"], "enabled": False}}
        spend(120.0)
        verdict = cb.evaluate("widget_generation", CHAIN, config=cfg)
        assert verdict.action == cb.ACTION_ALLOW
        assert verdict.chain_after == CHAIN

    def test_apply_to_chain_never_raises(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("budget subsystem is down")

        monkeypatch.setattr(cb, "evaluate", boom)
        chain, verdict = cb.apply_to_chain("widget_generation", CHAIN)
        assert chain == CHAIN
        assert verdict.action == cb.ACTION_ALLOW


# ---------------------------------------------------------------------------
# 3. The downgrade can reach the local tier
# ---------------------------------------------------------------------------
class TestDowngradeReachesLocal:
    def test_local_leads_the_downgraded_chain(self):
        out = cb.downgrade_chain(
            CHAIN, CONFIG["models"], CONFIG["providers"], settings=CONFIG["cost_budget"]
        )
        assert out[0] == "cheap-local"

    def test_local_beats_an_equally_priced_cloud_model(self):
        """free-cloud also declares 0.0 — prefer_local is what breaks the tie.

        Without it the downgrade lands on whichever zero-priced cloud entry the
        operator happened to list first, which is not air-gap correct.
        """
        out = cb.downgrade_chain(
            CHAIN, CONFIG["models"], CONFIG["providers"], settings=CONFIG["cost_budget"]
        )
        assert out.index("cheap-local") < out.index("free-cloud")

    def test_prefer_local_off_keeps_declared_order_among_equals(self):
        settings = {**CONFIG["cost_budget"], "downgrade": {"max_blended_per_1k": 0.0, "prefer_local": False}}
        out = cb.downgrade_chain(CHAIN, CONFIG["models"], CONFIG["providers"], settings=settings)
        assert out.index("free-cloud") < out.index("cheap-local")

    def test_an_unpriced_model_is_not_treated_as_free(self):
        out = cb.downgrade_chain(
            CHAIN, CONFIG["models"], CONFIG["providers"], settings=CONFIG["cost_budget"]
        )
        assert out[-1] == "unpriced-cloud"
        assert cb.blended_price("unpriced-cloud", CONFIG["models"]) is None

    def test_a_raised_ceiling_admits_the_mid_tier(self):
        settings = {**CONFIG["cost_budget"], "downgrade": {"max_blended_per_1k": 0.005, "prefer_local": True}}
        out = cb.downgrade_chain(CHAIN, CONFIG["models"], CONFIG["providers"], settings=settings)
        assert out.index("mid-cloud") < out.index("dear-cloud")

    def test_an_all_local_chain_is_unchanged(self):
        chain = ["cheap-local"]
        out = cb.downgrade_chain(
            chain, CONFIG["models"], CONFIG["providers"], settings=CONFIG["cost_budget"]
        )
        assert out == chain

    def test_locality_uses_the_one_shared_definition(self):
        """Not a second inline provider == 'ollama' test — that is how CUI leaks."""
        src = (REPO_ROOT / "tools" / "llm" / "cost_budget.py").read_text(encoding="utf-8")
        assert "from tools.llm.cli_bridge.activate import is_local_only_model" in src
        assert '== "ollama"' not in src


# ---------------------------------------------------------------------------
# 4. Config-declared, with no model id in Python
# ---------------------------------------------------------------------------
class TestConfigDeclared:
    @pytest.fixture(scope="class")
    def real_config(self):
        import yaml

        from tools.llm.config_path import resolve_llm_config_path

        return yaml.safe_load(resolve_llm_config_path().read_text(encoding="utf-8"))

    def test_cost_budget_block_is_declared(self, real_config):
        section = real_config.get("cost_budget")
        assert isinstance(section, dict), "cost_budget is not declared in args/llm_config.yaml"
        for key in ("enabled", "limit_usd", "soft_thresholds", "hard_action", "downgrade", "ask"):
            assert key in section, f"cost_budget.{key} is not declared"

    def test_hard_action_is_downgrade_not_block(self, real_config):
        """A fifth blocking layer would have been the bug, not the feature."""
        assert real_config["cost_budget"]["hard_action"] == "downgrade"

    def test_the_shipped_chain_downgrades_onto_a_local_model(self, real_config):
        """End to end against the REAL config: the air-gap constraint holds."""
        from tools.llm.cli_bridge.activate import is_local_only_model

        models, providers = real_config["models"], real_config["providers"]
        settings = cb.settings_for("code_generation", real_config)
        chain = real_config["routing"]["code_generation"]["chain"]
        out = cb.downgrade_chain(chain, models, providers, settings=settings)
        assert is_local_only_model(out[0], models, providers), (
            f"downgraded chain leads with {out[0]!r}, which is not local"
        )

    def test_no_model_id_is_bound_in_the_module(self):
        """Same rule tests/test_no_hardcoded_model_ids.py enforces platform-wide.

        A literal model id here would pin one vendor into the downgrade path, and
        an air-gapped deployment would silently downgrade onto a model it cannot
        reach. The chain and the pricing both come from YAML.
        """
        src = (REPO_ROOT / "tools" / "llm" / "cost_budget.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        bound = []
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg in ("model", "model_id"):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    bound.append(node.value.value)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in ("model", "model_id", "MODEL"):
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            bound.append(node.value.value)
        assert bound == [], f"model id(s) hardcoded in cost_budget.py: {bound}"

    def test_undeclared_function_is_surfaced_not_hidden(self, spend, asks):
        """An undeclared llm_function silently falls back to routing.default."""
        spend(10.0)
        assert cb.is_declared_function("widget_generation", CONFIG) is True
        assert cb.is_declared_function("never_declared", CONFIG) is False
        verdict = cb.evaluate("never_declared", CHAIN, config=CONFIG)
        assert verdict.routing_declared is False

    def test_per_function_override_merges_over_the_block(self):
        cfg = {
            **CONFIG,
            "cost_budget": {
                **CONFIG["cost_budget"],
                "per_function": {"widget_generation": {"limit_usd": 5.0, "downgrade": {"max_blended_per_1k": 0.02}}},
            },
        }
        settings = cb.settings_for("widget_generation", cfg)
        assert settings["limit_usd"] == 5.0
        assert settings["downgrade"]["max_blended_per_1k"] == 0.02
        assert settings["downgrade"]["prefer_local"] is True, "unstated key must survive the merge"
        assert cb.settings_for("other", cfg)["limit_usd"] == 100.0


# ---------------------------------------------------------------------------
# Wiring — a gate nothing calls is the defect this platform ships most
# ---------------------------------------------------------------------------
class TestRouterConsumesTheGate:
    def test_router_invoke_calls_apply_to_chain(self):
        src = (REPO_ROOT / "tools" / "llm" / "router.py").read_text(encoding="utf-8")
        assert "from tools.llm import cost_budget" in src
        assert "cost_budget.apply_to_chain(function, chain)" in src

    def test_budget_order_is_reapplied_after_rl_reranking(self):
        """RL ranks by learned Q-value with no notion of price.

        If the budget ran before rank_models and never again, RL would promote
        the demoted model straight back to the head — the same way it can undo
        force_local.
        """
        src = (REPO_ROOT / "tools" / "llm" / "router.py").read_text(encoding="utf-8")
        rank = src.index("rank_models(function, chain)")
        reapply = src.index("cost_budget.downgrade_chain(")
        assert reapply > rank, "the budget must be the LAST word on chain order"

    def test_two_tier_escalation_is_suppressed_when_downgraded(self):
        """two_tier bypasses the chain to reach the expensive planner."""
        src = (REPO_ROOT / "tools" / "llm" / "router.py").read_text(encoding="utf-8")
        assert "if _budget_verdict.downgraded" in src

    def test_period_start_survives_both_timestamp_spellings(self):
        """SQLite writes '2026-08-01 10:00:00'; isoformat writes '...T10:00:00+00:00'.

        A full-ISO boundary drops every space-separated row on day one, because
        ' ' sorts below 'T'. The boundary is a 10-char date prefix for that reason.
        """
        boundary = cb.period_start("monthly")
        assert len(boundary) == 10 and boundary.endswith("-01")
        assert "2026-08-01 10:00:00" >= boundary
        assert "2026-08-01T10:00:00+00:00" >= boundary
        assert not ("2026-07-31 23:59:59" >= boundary)
