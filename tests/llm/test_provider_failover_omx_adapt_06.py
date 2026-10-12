# CUI // SP-CTI
"""omx-adapt-06 -- Claude (subscription) <-> Ollama Cloud failover, both seams.

OPERATOR DECISION 2026-10-11: "If Claude exhausts its tokens, we have Ollama
Cloud and vice versa." One classifier reads provider error BODIES (Pi exits 0
on a provider 400/404 -- measured in omx-spike-01 and omx-spike-02), records
the exhaustion in the router's D-AUTO-DEGRADE state, and both the LLM router
and the kanban dispatcher act on that one record:

* classifier fixtures for each provider's error shape, including Pi's exit-0
  body and the Anthropic 400 "extra usage";
* the router skips an exhausted family, uses the other, and returns after the
  resume time;
* a kanban dispatch that failed on an exhausted provider is re-queued for the
  OTHER provider without a failure count; both exhausted holds the task.
"""
from __future__ import annotations

import importlib
import json
import time
from types import SimpleNamespace

import pytest
import yaml

from tools.llm import provider_exhaustion as pe
from tools.llm.cli_bridge.activate import _cli_bridge_override

POLICY = {
    "enabled": True,
    "hold_seconds": {"exhausted": 3600, "rate_limited": 300},
    "providers": {
        "claude": {
            "router_providers": ["claude-cli", "cli", "anthropic"],
            "harness_model_prefixes": ["icdev-claude/", "anthropic/"],
            "failover_to": "ollama_cloud",
            "router_failover_models": ["kimi-cloud", "deepseek-cloud"],
        },
        "ollama_cloud": {
            "router_providers": ["ollama_cloud"],
            "harness_model_prefixes": ["ollama-cloud/"],
            "failover_to": "claude",
            "router_failover_models": ["claude-cli"],
        },
    },
    "harnesses": {
        "claude_cli": {"family": "claude", "failover_adapter": {"ollama_cloud": "opencode_cli"}},
        "opencode_cli": {
            "model_env": "ICDEV_OPENCODE_MODEL",
            "models": {"claude": "", "ollama_cloud": ""},
            "failover_adapter": {"claude": "claude_cli"},
        },
        "pi_cli": {
            "model_env": "ICDEV_PI_MODEL",
            "models": {"claude": "", "ollama_cloud": ""},
            "failover_adapter": {"claude": "claude_cli"},
        },
    },
}

NOW = 1_800_000_000.0

# ── provider error bodies ───────────────────────────────────────────────────
CLAUDE_CLI_LIMIT = (
    '{"type":"result","subtype":"success","is_error":true,'
    '"result":"Claude AI usage limit reached|1800003600"}'
)
CLAUDE_HIT_YOUR_LIMIT = "You've hit your limit · resets 3pm (America/New_York)"
# omx-spike-02, measured: Pi on its native Anthropic OAuth, EXIT 0.
PI_ANTHROPIC_EXTRA_USAGE = (
    'message_end assistant stopReason= error | error= 400 {"type":"error","error":'
    '{"type":"invalid_request_error","message":"Third-party apps now draw from your '
    'extra usage, not your plan limits. Add more at claude.ai/settings/usage and keep going."}}'
)
# Pi on ollama-cloud: the JSON stream's last assistant message, EXIT 0.
PI_OLLAMA_WEEKLY = json.dumps({
    "type": "message_end",
    "message": {"role": "assistant", "stopReason": "error", "content": [],
                "errorMessage": '429 {"error":"you have reached your weekly usage limit, '
                                'try again in 2 hours"}'},
})
OPENCODE_OLLAMA_429 = json.dumps({
    "type": "error",
    "error": {"name": "APIError", "data": {"message": "Too Many Requests", "statusCode": 429}},
})


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "STATE_PATH", tmp_path / "llm_degraded_tier2.json")
    monkeypatch.setattr(pe, "load_policy",
                        lambda config=None: json.loads(json.dumps(POLICY)))
    monkeypatch.setattr(pe, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.delenv("ICDEV_OPENCODE_MODEL", raising=False)
    monkeypatch.delenv("ICDEV_PI_MODEL", raising=False)
    yield tmp_path


def _later(monkeypatch, seconds: float) -> None:
    monkeypatch.setattr(pe, "time", SimpleNamespace(time=lambda: NOW + seconds))


# ── 1. classifier fixtures ──────────────────────────────────────────────────
class TestClassifier:
    def test_claude_cli_usage_limit_with_epoch(self):
        exh = pe.classify(CLAUDE_CLI_LIMIT, now=NOW)
        assert (exh.provider, exh.kind, exh.resume_at) == ("claude", "exhausted", 1800003600.0)

    def test_claude_hit_your_limit_resets_wall_clock_in_its_zone(self):
        exh = pe.classify(CLAUDE_HIT_YOUR_LIMIT, now=NOW)
        assert exh.provider == "claude" and exh.kind == "exhausted"
        assert NOW < exh.resume_at <= NOW + 86400

    def test_pi_exit0_anthropic_400_extra_usage_is_claude_exhausted(self):
        """The body, not the exit code, says Claude is out (omx-spike-02)."""
        exh = pe.classify(PI_ANTHROPIC_EXTRA_USAGE, provider="claude", now=NOW)
        assert (exh.provider, exh.kind) == ("claude", "exhausted")

    def test_credit_balance_too_low(self):
        assert pe.classify("Credit balance is too low", now=NOW).provider == "claude"

    def test_pi_exit0_ollama_weekly_limit_with_stated_resume(self):
        exh = pe.classify(PI_OLLAMA_WEEKLY, provider="ollama_cloud", now=NOW)
        assert (exh.provider, exh.kind) == ("ollama_cloud", "exhausted")
        assert exh.resume_at == NOW + 2 * 3600

    def test_opencode_429_event_is_rate_limited(self):
        exh = pe.classify(OPENCODE_OLLAMA_429, provider="ollama_cloud", now=NOW)
        assert (exh.provider, exh.kind, exh.resume_at) == ("ollama_cloud", "rate_limited", None)

    def test_ollama_body_naming_ollama_needs_no_hint(self):
        exh = pe.classify("429: usage limit reached -- upgrade at https://ollama.com/upgrade")
        assert exh.provider == "ollama_cloud"

    def test_generic_body_without_a_hint_is_not_attributed(self):
        assert pe.classify('{"error":"you have reached your weekly usage limit"}') is None

    @pytest.mark.parametrize("text", [
        "Added rate limit handling to the gateway and tests for quota maths.",
        'File "x.py", line 429, in invoke',
        "All 12 tests passed.",
        "",
    ])
    def test_prose_and_tracebacks_are_not_exhaustion(self, text):
        assert pe.classify(text, provider="claude", now=NOW) is None


# ── 2. shared state ─────────────────────────────────────────────────────────
class TestState:
    def test_record_lands_in_the_d_auto_degrade_file(self, isolated_state):
        pe.record(pe.classify(CLAUDE_CLI_LIMIT, now=NOW))
        state = json.loads((isolated_state / "llm_degraded_tier2.json").read_text("utf-8"))
        assert "provider:claude" in state["degraded"]
        assert state["probed_at"]["provider:claude"] == 1800003600.0
        assert state["provider_detail"]["claude"]["resume_source"] == "stated"
        assert pe.exhausted_until("claude") == 1800003600.0

    def test_no_stated_time_uses_the_declared_hold(self):
        pe.record(pe.Exhaustion("ollama_cloud", "rate_limited"))
        assert pe.exhausted_until("ollama_cloud") == NOW + 300

    def test_a_rate_limit_never_shortens_a_quota_exhaustion(self):
        pe.record(pe.Exhaustion("claude", "exhausted", NOW + 7200))
        pe.record(pe.Exhaustion("claude", "rate_limited"))
        assert pe.exhausted_until("claude") == NOW + 7200

    def test_resumes_after_the_resume_time(self, monkeypatch):
        pe.record(pe.Exhaustion("claude", "exhausted", NOW + 60))
        _later(monkeypatch, 61)
        assert pe.exhausted_until("claude") is None
        assert pe.snapshot()["claude"]["exhausted"] is False

    def test_router_persist_keeps_provider_entries(self, tmp_path, monkeypatch):
        """The router rewrites this file from its own model set; it must not erase
        an exhaustion another process recorded."""
        from tools.llm.router import LLMRouter

        pe.record(pe.Exhaustion("claude", "exhausted", NOW + 600))
        monkeypatch.setattr(LLMRouter, "_degraded_tier2_models", {"some-model"})
        monkeypatch.setattr(LLMRouter, "_degraded_tier2_probed_at", {"some-model": 1.0})
        LLMRouter._persist_degraded_tier2_state(object.__new__(LLMRouter))
        state = json.loads(pe.state_path().read_text("utf-8"))
        assert {"provider:claude", "some-model"} <= set(state["degraded"])
        assert pe.exhausted_until("claude") == NOW + 600


# ── 3. router seam ──────────────────────────────────────────────────────────
PROVIDERS = {
    "ollama": {"type": "ollama", "base_url": "http://localhost:11434"},
    "ollama_cloud": {"type": "ollama", "base_url": "https://ollama.com",
                     "api_key_env": "OLLAMA_API_KEY"},
    "cli": {"type": "cli", "cli_binary": "claude"},
}
MODELS = {
    "qwen3-local": {"provider": "ollama", "model_id": "q:latest"},
    "kimi-cloud": {"provider": "ollama_cloud", "model_id": "k:cloud"},
    "deepseek-cloud": {"provider": "ollama_cloud", "model_id": "d:cloud"},
    "claude-cli": {"provider": "cli", "model_id": "claude-cli"},
}


@pytest.fixture
def router(tmp_path, monkeypatch):
    from tools.llm.router import LLMRouter

    monkeypatch.setenv("ICDEV_CLI_BRIDGE", "false")
    before = _cli_bridge_override.get()
    cfg = {"providers": PROVIDERS, "models": MODELS, "settings": {},
           "routing": {"code_generation": {"chain": ["claude-cli", "qwen3-local"]},
                       "summarize": {"chain": ["kimi-cloud", "qwen3-local"]}},
           "provider_failover": POLICY}
    path = tmp_path / "llm_config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    try:
        yield LLMRouter(config_path=str(path))
    finally:
        _cli_bridge_override.set(before)


class TestRouter:
    def test_claude_exhausted_routes_to_ollama_cloud_until_resume(self, router, monkeypatch):
        assert router._get_chain_for_function("code_generation") == ["claude-cli", "qwen3-local"]
        router._observe_provider_error({"provider": "cli"},
                                       RuntimeError(CLAUDE_CLI_LIMIT))
        assert router._get_chain_for_function("code_generation") == [
            "kimi-cloud", "deepseek-cloud", "qwen3-local"]
        _later(monkeypatch, 3601)  # past the stated resume (|1800003600)
        assert router._get_chain_for_function("code_generation") == ["claude-cli", "qwen3-local"]

    def test_ollama_cloud_exhausted_routes_to_claude(self, router):
        router._observe_provider_error({"provider": "ollama_cloud"},
                                       RuntimeError(PI_OLLAMA_WEEKLY))
        assert router._get_chain_for_function("summarize") == ["claude-cli", "qwen3-local"]

    def test_both_exhausted_keeps_what_is_left(self, router):
        pe.record(pe.Exhaustion("claude", "exhausted", NOW + 600))
        pe.record(pe.Exhaustion("ollama_cloud", "exhausted", NOW + 600))
        assert router._get_chain_for_function("code_generation") == ["qwen3-local"]

    def test_two_tier_treats_an_exhausted_family_as_degraded(self, router):
        pe.record(pe.Exhaustion("claude", "exhausted", NOW + 600))
        assert router._is_tier2_degraded("claude-cli")
        assert not router._should_probe_degraded("claude-cli")
        assert not router._is_tier2_degraded("kimi-cloud")

    def test_a_non_exhaustion_error_records_nothing(self, router):
        router._observe_provider_error({"provider": "cli"}, RuntimeError("syntax error"))
        assert pe.exhausted_until("claude") is None


# ── 4. kanban dispatch seam ─────────────────────────────────────────────────
k = importlib.import_module("tools.genesis.reflexes.kanban")


class _Adapter:
    def __init__(self, name):
        self.name = name

    def available(self):
        return True


@pytest.fixture
def board(monkeypatch):
    calls = {"moves": [], "resume": [], "failures": [], "retries": []}
    monkeypatch.setattr(k, "_move_task", lambda tid, status, **kw: calls["moves"].append(
        (tid, status, kw.get("reason", ""))))
    monkeypatch.setattr(k, "_save_resume_at", lambda tid, dt: calls["resume"].append(
        (tid, dt.timestamp())))
    monkeypatch.setattr(k, "_record_failure_and_maybe_flag",
                        lambda tid, reason: calls["failures"].append(tid))
    monkeypatch.setattr(k, "_increment_retry_count",
                        lambda tid: calls["retries"].append(tid) or 1)
    monkeypatch.setattr(k, "_adapter_usable", lambda name: True)
    monkeypatch.setattr(k, "_get_executor_type", lambda tid: None)
    monkeypatch.setattr(k, "_guard_parity_screen", lambda a, chain, tid, out: a)
    from tools.agents import registry
    monkeypatch.setattr(registry, "get_adapter", lambda name: _Adapter(name))
    k._dispatch_provider.clear()
    k._provider_failover_model.clear()
    k._provider_held.clear()
    yield calls
    k._dispatch_provider.clear()
    k._provider_failover_model.clear()
    k._provider_held.clear()


class TestKanbanCompletion:
    def test_claude_exhausted_fails_over_without_a_failure_count(self, board, monkeypatch):
        monkeypatch.setenv("ICDEV_OPENCODE_MODEL", "ollama-cloud/test-model")
        k._dispatch_provider["t-1"] = ("claude_cli", None, "claude")
        assert k._handle_provider_exhaustion("t-1", 1, "work...\n" + CLAUDE_CLI_LIMIT)
        (tid, status, reason), = board["moves"]
        assert status == "token_exhausted" and reason.startswith(k.PROVIDER_FAILOVER_REASON)
        assert "claude" in reason and "ollama_cloud" in reason and "opencode_cli" in reason
        assert board["resume"] == [("t-1", pytest.approx(time.time(), abs=60))]
        assert board["failures"] == [] and board["retries"] == []
        assert pe.exhausted_until("claude") == 1800003600.0

    def test_pi_exit0_ollama_exhausted_fails_over_to_claude(self, board, monkeypatch):
        """ret == 0, and the BODY still says the provider refused (Pi)."""
        k._dispatch_provider["t-2"] = ("pi_cli", "ollama-cloud/test-model", "ollama_cloud")
        assert k._handle_provider_exhaustion("t-2", 0, PI_OLLAMA_WEEKLY)
        (_tid, status, reason), = board["moves"]
        assert status == "token_exhausted" and "claude_cli" in reason
        assert board["failures"] == [] and board["retries"] == []

    def test_both_exhausted_holds_until_the_earlier_resume(self, board):
        pe.record(pe.Exhaustion("claude", "exhausted", NOW + 5 * 3600))
        k._dispatch_provider["t-3"] = ("pi_cli", "ollama-cloud/test-model", "ollama_cloud")
        assert k._handle_provider_exhaustion("t-3", 0, PI_OLLAMA_WEEKLY)
        (_tid, status, reason), = board["moves"]
        assert status == "token_exhausted" and "held (not failed)" in reason
        assert board["resume"] == [("t-3", NOW + 2 * 3600)]  # ollama's, the earlier
        assert "t-3" in k._provider_held
        assert board["failures"] == [] and board["retries"] == []

    def test_a_successful_run_that_mentions_limits_is_not_failed_over(self, board):
        k._dispatch_provider["t-4"] = ("claude_cli", None, "claude")
        out = "Implemented the 'Claude AI usage limit reached' classifier; tests pass."
        assert not k._handle_provider_exhaustion("t-4", 0, out)
        assert board["moves"] == [] and pe.exhausted_until("claude") is None

    def test_a_plain_failure_falls_through_to_the_existing_paths(self, board):
        k._dispatch_provider["t-5"] = ("claude_cli", None, "claude")
        assert not k._handle_provider_exhaustion("t-5", 1, "AssertionError: 3 != 4")
        assert board["moves"] == []


class TestKanbanDispatch:
    def test_redispatch_runs_claude_task_on_ollama_cloud_harness(self, board, monkeypatch):
        monkeypatch.setenv("ICDEV_OPENCODE_MODEL", "ollama-cloud/test-model")
        pe.record(pe.Exhaustion("claude", "exhausted", NOW + 600))
        outcomes = []
        adapter, switched, held = k._provider_failover_dispatch(
            _Adapter("claude_cli"), {"id": "t-6"}, ["claude_cli"], outcomes)
        assert (adapter.name, switched, held) == ("opencode_cli", True, False)
        assert k._dispatch_provider["t-6"] == ("opencode_cli", "ollama-cloud/test-model",
                                               "ollama_cloud")
        session = k._agent_session({"id": "t-6"}, "do it", ".")
        assert session.metadata["model_id"] == "ollama-cloud/test-model"

    def test_same_harness_switches_model_when_one_is_declared(self, board, monkeypatch):
        monkeypatch.setenv("ICDEV_PI_MODEL", "ollama-cloud/test-model")
        policy = json.loads(json.dumps(POLICY))
        policy["harnesses"]["pi_cli"]["models"]["claude"] = "icdev-claude/sonnet"
        monkeypatch.setattr(pe, "load_policy", lambda config=None: policy)
        pe.record(pe.Exhaustion("ollama_cloud", "exhausted", NOW + 600))
        adapter, switched, held = k._provider_failover_dispatch(
            _Adapter("pi_cli"), {"id": "t-7"}, ["pi_cli"], [])
        assert (adapter.name, switched, held) == ("pi_cli", False, False)
        assert k._provider_failover_model["t-7"] == "icdev-claude/sonnet"

    def test_both_exhausted_at_dispatch_holds(self, board):
        pe.record(pe.Exhaustion("claude", "exhausted", NOW + 900))
        pe.record(pe.Exhaustion("ollama_cloud", "exhausted", NOW + 300))
        adapter, switched, held = k._provider_failover_dispatch(
            _Adapter("claude_cli"), {"id": "t-8"}, ["claude_cli"], [])
        assert held and not switched
        assert board["resume"] == [("t-8", NOW + 300)]
        assert board["failures"] == []

    def test_nothing_exhausted_runs_unchanged(self, board):
        adapter, switched, held = k._provider_failover_dispatch(
            _Adapter("claude_cli"), {"id": "t-9"}, ["claude_cli"], [])
        assert (adapter.name, switched, held) == ("claude_cli", False, False)
        assert board["moves"] == []


def test_capability_matrix_and_panel_surface_the_state(monkeypatch):
    pe.record(pe.Exhaustion("claude", "exhausted", NOW + 600))
    import tools.agents.capability_matrix as capability_matrix
    import tools.cli.omarchy_panel as omarchy_panel

    state = capability_matrix._provider_exhaustion_state()
    assert state["claude"]["exhausted"] and state["claude"]["failover_to"] == "ollama_cloud"
    assert state["ollama_cloud"]["exhausted"] is False
    text = capability_matrix._render_text({"adapters": {}, "summary": {"cells": 0},
                                           "provider_exhaustion": state})
    assert "provider claude: EXHAUSTED (exhausted)" in text
    assert "provider ollama_cloud: available -> fails over to claude" in text
    assert omarchy_panel.provider_exhaustion(pe.snapshot)["claude"]["exhausted"]
