# CUI // SP-CTI
"""Shared helpers for the Studio executor tests.

Since exa-policy-07 a tool carries its OWN authorization declaration in
``tools/mcp/tool_registry.py``, and a tool with no ``read_only`` declaration
resolves restrictively (IL5, admin only) so that a tool nobody classified is
never reachable by everybody. Test stubs injected into ``TOOL_REGISTRY`` land in
that bucket, which would refuse them before the mechanics under test ever run —
so a stub has to be classified too, exactly like a real tool.
"""
from __future__ import annotations


def declare_stubs_read_only(monkeypatch, *names: str) -> None:
    """Declare ``names`` read-only in the MCP registry for one test.

    ``READ_ONLY_DECLARATIONS`` is a ``MappingProxyType`` so it cannot be
    ``setitem``-patched; the whole mapping is replaced (monkeypatch restores it
    at teardown) and the resolver's cache is cleared on the way in and out.
    """
    from types import MappingProxyType

    from tools.mcp import tool_registry

    monkeypatch.setattr(
        tool_registry,
        "READ_ONLY_DECLARATIONS",
        MappingProxyType({**tool_registry.READ_ONLY_DECLARATIONS,
                          **{name: True for name in names}}),
    )
    tool_registry.reset_authorization_cache()


def reset_authorization_cache() -> None:
    """Drop resolved stub answers so they cannot outlive the patch."""
    from tools.mcp import tool_registry

    tool_registry.reset_authorization_cache()
