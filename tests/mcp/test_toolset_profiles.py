# CUI // SP-CTI
"""Tests for curated MCP toolset profiles (SAG sag-mcp-01).

Verifies profile loading/resolution, that every profile references only real
TOOL_REGISTRY tools, the CUI-egress fail-closed gate, and that the unified
server registers a bounded surface when a profile is active.
"""
from __future__ import annotations

import pytest

from tools.mcp import toolset_profiles as tp


def test_profiles_load():
    profiles = tp.load_profiles()
    assert profiles, "expected at least one profile"
    for name in ("minimal", "compliance", "security", "research", "kanban"):
        assert name in profiles


def test_every_profile_tool_is_real():
    """No profile may reference a tool absent from TOOL_REGISTRY."""
    from tools.mcp.tool_registry import TOOL_REGISTRY

    valid = set(TOOL_REGISTRY)
    bad: dict[str, list[str]] = {}
    for name, prof in tp.load_profiles().items():
        missing = [t for t in (prof.get("tools") or []) if t not in valid]
        if missing:
            bad[name] = missing
    assert not bad, f"profiles reference unknown tools: {bad}"


def test_resolve_toolset_filters_to_registry():
    resolved = tp.resolve_toolset("minimal", registry_names={"health_check"})
    # get_status is dropped because it is not in the supplied registry set.
    assert resolved == {"health_check"}


def test_resolve_unknown_profile_raises():
    with pytest.raises(tp.ToolsetProfileError):
        tp.resolve_toolset("does-not-exist")


def test_list_profiles_shape():
    rows = tp.list_profiles()
    names = {r["name"] for r in rows}
    assert "compliance" in names
    comp = next(r for r in rows if r["name"] == "compliance")
    assert comp["cui_egress"] == "local_only"
    assert comp["tool_count"] >= 5


def test_cloud_safe_profile_never_gated(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROVIDER", "anthropic")  # cloud
    # research is cloud_safe → must not raise regardless of provider.
    tp.enforce_cui_egress("research")


def test_local_only_profile_blocked_on_cloud(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROVIDER", "anthropic")  # cloud
    monkeypatch.delenv("ICDEV_MCP_ALLOW_CLOUD_CUI", raising=False)
    with pytest.raises(tp.ToolsetProfileError):
        tp.enforce_cui_egress("compliance")


def test_local_only_profile_allowed_on_local(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")  # local
    tp.enforce_cui_egress("compliance")  # must not raise


def test_local_only_override(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ICDEV_MCP_ALLOW_CLOUD_CUI", "1")
    tp.enforce_cui_egress("compliance")  # override → no raise


def test_server_registers_bounded_surface(monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_PROVIDER", "ollama")  # allow local_only
    from tools.mcp.unified_server import create_server
    from tools.mcp.tool_registry import TOOL_REGISTRY

    full = create_server()
    bounded = create_server(toolset="security")
    full_n = len(full._tools)
    bounded_n = len(bounded._tools)
    assert bounded_n < full_n
    # Only the profile's tools (that exist in the registry) are registered.
    sec_tools = set(tp.load_profiles()["security"]["tools"]) & set(TOOL_REGISTRY)
    assert set(bounded._tools) == sec_tools
    assert len(TOOL_REGISTRY) > 100
