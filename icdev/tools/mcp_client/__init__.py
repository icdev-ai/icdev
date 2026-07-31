# CUI // SP-CTI
"""Outbound MCP client — call tools on third-party MCP servers.

The inverse of ``tools/mcp/``, which makes ICDEV an MCP *server*. Registry and
gate live in ``registry.py``; transports in ``client.py``; the defence against
attacker-controlled tool descriptions in ``sanitize.py``.
"""
from icdev.tools.mcp_client.registry import (  # noqa: F401
    NAMESPACE_PREFIX,
    ExternalTool,
    ExternalToolRegistry,
    get_external_registry,
    reset_external_registry,
)

__all__ = [
    "NAMESPACE_PREFIX",
    "ExternalTool",
    "ExternalToolRegistry",
    "get_external_registry",
    "reset_external_registry",
]
