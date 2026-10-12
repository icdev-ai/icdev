"""omx-guard-07-d3: the runner refuses to spawn an adapter on a metered Claude route.

Under ``anthropic_billing: subscription_only`` only ``native_subscription``,
``cli_bridge`` and ``none`` (no Claude route at all) may be dispatched; a
declared ``api_key`` route is refused before anything is spawned.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest import mock

import pytest

from tools.agents import dispatch_policy
from tools.agents.dispatch_policy import DispatchRefused
from tools.genesis.reflexes import kanban
from tools.llm import anthropic_billing


@pytest.fixture
def subscription_only(monkeypatch):
    monkeypatch.setattr(anthropic_billing, "billing_mode",
                        lambda path=None: anthropic_billing.SUBSCRIPTION_ONLY)


def _adapter(route, name="metered_cli"):
    return SimpleNamespace(name=name, claude_route=route,
                           spawn=mock.Mock(name="spawn"), invoke=mock.Mock(name="invoke"))


def test_runner_spawn_refuses_api_key_route_and_logs_metered(subscription_only, caplog, tmp_path):
    adapter = _adapter("api_key")
    caplog.set_level(logging.ERROR)
    # get_logger() loggers may not propagate to root; listen on the module's own.
    dispatch_policy.logger.addHandler(caplog.handler)
    try:
        with pytest.raises(DispatchRefused, match="metered"):
            kanban._dispatch_via_agent_adapter(
                adapter, {"id": "t-1"}, "p.md", "do it", str(tmp_path), tmp_path / "t.log")
    finally:
        dispatch_policy.logger.removeHandler(caplog.handler)
    assert "metered" in caplog.text
    adapter.spawn.assert_not_called()
    adapter.invoke.assert_not_called()


def test_repair_spawn_refuses_api_key_route(subscription_only, tmp_path):
    adapter = _adapter("api_key")
    with pytest.raises(DispatchRefused):
        kanban._spawn_repair(adapter, object(), "t-1", tmp_path / "r.log")
    adapter.spawn.assert_not_called()


@pytest.mark.parametrize("route", ["native_subscription", "cli_bridge", "none"])
def test_subscription_routes_pass(subscription_only, route):
    dispatch_policy.enforce(_adapter(route))


def test_unrecognised_route_is_refused(subscription_only):
    with pytest.raises(DispatchRefused):
        dispatch_policy.enforce(_adapter("bogus"))


def test_allow_metered_lets_api_key_through():
    dispatch_policy.enforce(_adapter("api_key"), mode=anthropic_billing.ALLOW_METERED)


def test_declared_routes_of_registered_adapters_all_pass(subscription_only):
    from tools.agents import registry

    for name in registry.list_adapters():
        assert dispatch_policy.refusal_reason(registry.get_adapter(name)) == "", name
