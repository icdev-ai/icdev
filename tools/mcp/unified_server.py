#!/usr/bin/env python3
# CUI // SP-CTI
"""Unified MCP Gateway Server — single entry point for all ICDEV tools.

Aggregates all 18 domain servers plus ~55 new tool wrappers into one
MCP server process.  Uses lazy module loading: tool handlers are only
imported when first called, so startup is fast regardless of tool count.

Usage:
    python tools/mcp/unified_server.py

.mcp.json entry:
    "icdev-unified": {
        "command": "python",
        "args": ["tools/mcp/unified_server.py"],
        "env": { "ICDEV_DB_PATH": "data/icdev.db", "ICDEV_PROJECT_ROOT": "." }
    }

Architecture Decision D301:
    Declarative tool registry with lazy loading.  Existing 18 servers
    remain independently runnable (backward compat).  Registry maps
    tool name -> (module, handler, schema).  Handlers imported via
    importlib.import_module() on first call, cached thereafter.
    All tools inherit D284 auto-instrumentation from base_server.py.
"""

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.mcp.base_server import MCPServer  # noqa: E402

logger = logging.getLogger("mcp.unified")


class UnifiedMCPServer(MCPServer):
    """Unified MCP server with lazy-loaded tool handlers from declarative registry."""

    def __init__(self):
        super().__init__(name="icdev-unified", version="1.0.0")
        self._handler_cache: Dict[str, Callable] = {}
        self._register_all()

    # ------------------------------------------------------------------
    # Lazy loading
    # ------------------------------------------------------------------

    def _resolve_handler(self, tool_name: str, entry: dict) -> Callable:
        """Lazy-load and cache a tool handler.

        On import failure, returns a stub that reports the error gracefully
        (consistent with existing _import_tool() pattern across 18 servers).
        """
        if tool_name in self._handler_cache:
            return self._handler_cache[tool_name]

        module_path = entry["module"]
        handler_name = entry["handler"]

        try:
            mod = importlib.import_module(module_path)
            handler = getattr(mod, handler_name)
        except (ImportError, AttributeError, ModuleNotFoundError) as exc:
            logger.warning("Cannot import %s.%s: %s", module_path, handler_name, exc)

            def _stub(args: dict, _err=str(exc), _mod=module_path, _fn=handler_name) -> dict:
                return {
                    "error": f"Module not available: {_mod}.{_fn}",
                    "details": _err,
                    "status": "pending",
                }

            handler = _stub

        self._handler_cache[tool_name] = handler
        return handler

    # ------------------------------------------------------------------
    # Registry loading
    # ------------------------------------------------------------------

    def _register_all(self) -> None:
        """Register all tools and resources from the declarative registry."""
        from tools.mcp.tool_registry import TOOL_REGISTRY, RESOURCE_REGISTRY

        # Register tools with lazy dispatch closures
        for tool_name, entry in TOOL_REGISTRY.items():
            self._register_lazy_tool(tool_name, entry)

        logger.info("Registered %d tools from unified registry", len(TOOL_REGISTRY))

        # Register resources with lazy dispatch
        for uri, entry in RESOURCE_REGISTRY.items():
            self._register_lazy_resource(uri, entry)

        logger.info("Registered %d resources from unified registry", len(RESOURCE_REGISTRY))

    def _register_lazy_tool(self, tool_name: str, entry: dict) -> None:
        """Register a single tool with a lazy-loading handler closure."""

        def _make_handler(name: str, ent: dict) -> Callable:
            def lazy_handler(args: dict) -> Any:
                handler = self._resolve_handler(name, ent)
                return handler(args)

            return lazy_handler

        self.register_tool(
            name=tool_name,
            description=entry["description"],
            input_schema=entry["input_schema"],
            handler=_make_handler(tool_name, entry),
        )

    def _register_lazy_resource(self, uri: str, entry: dict) -> None:
        """Register a single resource with a lazy-loading handler closure."""

        def _make_resource_handler(mod_path: str, handler_name: str) -> Callable:
            def lazy_resource(u: str) -> Any:
                try:
                    mod = importlib.import_module(mod_path)
                    fn = getattr(mod, handler_name)
                    return fn(u)
                except (ImportError, AttributeError) as exc:
                    return {"error": f"Resource handler not available: {mod_path}.{handler_name}: {exc}"}

            return lazy_resource

        self.register_resource(
            uri=uri,
            name=entry["name"],
            description=entry["description"],
            handler=_make_resource_handler(entry["module"], entry["handler"]),
            mime_type=entry.get("mime_type", "application/json"),
        )


def create_server() -> UnifiedMCPServer:
    """Factory function for the unified MCP gateway server."""
    return UnifiedMCPServer()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    server = create_server()
    server.run()
