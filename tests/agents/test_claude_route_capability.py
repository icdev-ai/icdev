# CUI // SP-CTI
"""omx-adapt-04 -- `claude_route` is declared per adapter and PROBED; an overclaim fails.

The claim lives in args/agent_capabilities.yaml as native_subscription |
cli_bridge | none. The probe measures the route (a native claude.ai login, or a
harness config ICDEV renders against the loopback bridge) and plants a metered
Anthropic key in the spawn env: a key that survives ``build_env()`` makes any
route an overclaim, because the backend could then bill the API.
"""
from __future__ import annotations

import pytest

from tools.agents import capability_matrix as cm
from tools.agents import registry

EXPECTED = {
    "claude_cli": "native_subscription",
    "opencode_cli": "cli_bridge",
    "pi_cli": "cli_bridge",
    "codex_cli": "none",
    "copilot_cli": "none",
    "local_llm_router": "none",
    "local_agent": "none",
}


def _cell(adapter, config=None):
    return cm.probe_adapter(adapter, config, only=["claude_route"])["capabilities"]["claude_route"]


@pytest.mark.parametrize("adapter,route", sorted(EXPECTED.items()))
def test_every_adapter_declares_its_route(adapter, route):
    assert cm.declared_route(adapter) == route


@pytest.mark.parametrize("adapter,route", sorted(EXPECTED.items()))
def test_declared_route_is_what_the_probe_measures(adapter, route):
    cell = _cell(adapter)
    assert cell["detail"]["measured_route"] == route
    assert cell["verdict"] == (cm.CONFIRMED if route != "none" else cm.AGREED_ABSENT), cell


def test_the_gate_finds_no_claude_route_overclaim():
    assert cm.main(["--capability", "claude_route", "--gate"]) == 0


def test_none_is_not_read_as_a_claim():
    """bool("none") is True -- a route-shaped claim must not silently become a yes."""
    assert cm.declared_for("codex_cli")["values"]["claude_route"] is False
    assert cm.declared_for("pi_cli")["values"]["claude_route"] is True


def test_a_route_the_adapter_cannot_take_is_an_overclaim():
    config = cm.load_declarations()
    config["declared"]["codex_cli"]["claude_route"] = "cli_bridge"
    cell = _cell("codex_cli", config)
    assert cell["verdict"] == cm.OVERCLAIMED, cell


def test_claiming_native_for_a_bridge_harness_is_an_overclaim():
    config = cm.load_declarations()
    config["declared"]["pi_cli"]["claude_route"] = "native_subscription"
    assert _cell("pi_cli", config)["verdict"] == cm.OVERCLAIMED


def test_a_leaked_metered_key_makes_the_route_an_overclaim(monkeypatch):
    adapter = registry.get_adapter("opencode_cli")
    monkeypatch.setattr(type(adapter), "build_env",
                        lambda self, session: dict((session.metadata or {}).get("env") or {}))
    cell = _cell("opencode_cli")
    assert cell["verdict"] == cm.OVERCLAIMED
    assert cell["detail"]["metered_key_reaches_backend"] is True


@pytest.mark.parametrize("adapter", ["claude_cli", "opencode_cli", "pi_cli"])
def test_adapter_spawn_env_never_carries_a_metered_key(adapter, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient")
    a = registry.get_adapter(adapter)
    env = a.build_env(cm._session(metadata={"env": {"ANTHROPIC_API_KEY": "sk-ant-passed",
                                                    "ICDEV_KEEP": "1"}}))
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["ICDEV_KEEP"] == "1"
