# CUI // SP-CTI
"""MCP tool handlers for the Compass reverse bridge.

Deliberately kept in a dedicated file rather than appended to
`tools/mcp/gap_handlers.py` -- these two handlers are the only MCP-facing
callers of `tools/integrations/compass_client.py`, so co-locating them with
the client keeps the whole Compass-bridge surface in one place. Registered
in `tools/mcp/tool_registry.py` as `compass_lcat_lookup` and
`compass_staffing_summary`.
"""
from __future__ import annotations

from tools.integrations.compass_client import lcat_lookup, staffing_summary


def handle_compass_lcat_lookup(args: dict) -> dict:
    """Look up a BLS SOC labor category for a task description or resume
    text via a running Compass backend's live LCAT taxonomy. Returns an
    error dict (not raised) if `text` is missing/empty or Compass is
    unreachable/not configured -- see `compass_client.lcat_lookup`'s
    degrade-to-None contract."""
    text = (args.get("text") or "").strip()
    if not text:
        return {"error": "text is required"}

    result = lcat_lookup(text)
    if result is None:
        return {"error": "Compass unreachable or not configured (see args/compass_integration.yaml)"}
    return result


def handle_compass_staffing_summary(args: dict) -> dict:
    """Fetch Compass's current staffing matrix (personnel vs. resume-
    matched LCAT compliance) via a running Compass backend. Returns an
    error dict (not raised) if Compass is unreachable/not configured."""
    result = staffing_summary()
    if result is None:
        return {"error": "Compass unreachable or not configured (see args/compass_integration.yaml)"}
    return result

# CUI // SP-CTI
