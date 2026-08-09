#!/usr/bin/env python3
# Path setup must come first so ICDev's tools/ wins over any shadowing packages (e.g. FathomDesk)
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent.parent))

# CUI // SP-CTI
"""Unified MCP Gateway Server — single entry point for all ICDEV™ tools.

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
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Dict

# Invocation telemetry. Imported defensively: this server is the entry point for
# every MCP client, and it must still start on a tree where the observability
# package is unavailable (a partial checkout, an older wheel). Falling back to a
# no-op context manager keeps the dispatch path identical in that case.
try:
    from tools.observability.invocation_recorder import SURFACE_MCP as _SURFACE_MCP
    from tools.observability.invocation_recorder import record as _record_invocation
except Exception:  # noqa: BLE001
    _SURFACE_MCP = "mcp"

    def _record_invocation(*_a, **_kw):  # type: ignore[misc]
        return nullcontext()

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.mcp.base_server import MCPServer  # noqa: E402

logger = get_logger("mcp.unified")


class UnifiedMCPServer(MCPServer):
    """Unified MCP server with lazy-loaded tool handlers from declarative registry."""

    def __init__(self, toolset: str | None = None):
        super().__init__(name="icdev-unified", version="1.0.0")
        self._handler_cache: Dict[str, Callable] = {}
        # Optional curated toolset profile (sag-mcp-01): restrict the exposed
        # surface to a bounded set for small local models / external agents.
        self._toolset = toolset or os.environ.get("ICDEV_MCP_TOOLSET") or None
        self._allowed_tools: set[str] | None = None
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
        """Register all tools and resources from the declarative registry.

        When a curated toolset profile is active (``--toolset`` / the
        ``ICDEV_MCP_TOOLSET`` env var), only the profile's tools are registered
        so small local models / external agents see a bounded surface. The
        profile's CUI-egress policy is enforced before any tool is exposed.
        """
        from tools.mcp.tool_registry import TOOL_REGISTRY, RESOURCE_REGISTRY

        if self._toolset:
            from tools.mcp.toolset_profiles import (
                enforce_cui_egress,
                resolve_toolset,
            )

            # Fail-closed CUI egress gate for local_only profiles on cloud LLMs.
            enforce_cui_egress(self._toolset)
            self._allowed_tools = resolve_toolset(
                self._toolset, registry_names=set(TOOL_REGISTRY)
            )
            logger.info(
                "toolset profile %r active: exposing %d of %d tools",
                self._toolset,
                len(self._allowed_tools),
                len(TOOL_REGISTRY),
            )

        # Register tools with lazy dispatch closures
        registered = 0
        for tool_name, entry in TOOL_REGISTRY.items():
            if self._allowed_tools is not None and tool_name not in self._allowed_tools:
                continue
            self._register_lazy_tool(tool_name, entry)
            registered += 1

        logger.info("Registered %d tools from unified registry", registered)

        # Register resources vs tool-overflow entries from RESOURCE_REGISTRY.
        # True resources have a "name" field; tool-like entries (with "input_schema")
        # were placed in RESOURCE_REGISTRY by mistake and are registered as tools.
        resource_count = 0
        tool_overflow_count = 0
        for uri, entry in RESOURCE_REGISTRY.items():
            if "name" in entry:
                self._register_lazy_resource(uri, entry)
                resource_count += 1
            elif "input_schema" in entry:
                if self._allowed_tools is not None and uri not in self._allowed_tools:
                    continue
                self._register_lazy_tool(uri, entry)
                tool_overflow_count += 1

        logger.info("Registered %d resources from unified registry", resource_count)
        if tool_overflow_count:
            logger.info(
                "Registered %d tool-overflow entries from RESOURCE_REGISTRY as tools",
                tool_overflow_count,
            )

    def _register_lazy_tool(self, tool_name: str, entry: dict) -> None:
        """Register a single tool with a lazy-loading handler closure."""

        def _make_handler(name: str, ent: dict) -> Callable:
            def lazy_handler(args: dict) -> Any:
                handler = self._resolve_handler(name, ent)
                # Every one of the 512 registered tools passes through this
                # closure, so this is the one place that can observe all of
                # them. Before this, MCP had zero recorded invocations:
                # measured 2026-08-02, `mcp_%`/`tool_%` events in audit_trail
                # numbered 0 and no invocation table existed at all.
                #
                # Only argument KEY NAMES are recorded — never values. See
                # tools/observability/invocation_recorder.py.
                with _record_invocation(_SURFACE_MCP, name, arg_keys=args):
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


def create_server(toolset: str | None = None) -> UnifiedMCPServer:
    """Factory function for the unified MCP gateway server.

    Args:
        toolset: Optional curated toolset profile name (sag-mcp-01). Falls back
            to the ``ICDEV_MCP_TOOLSET`` env var when ``None``.
    """
    return UnifiedMCPServer(toolset=toolset)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Unified MCP Gateway Server")
    parser.add_argument("--db-path", dest="db_path", metavar="<path>",
                        help="Override DB path (sets ICDEV_DB_PATH env var)")
    parser.add_argument("--status", action="store_true",
                        help="Show server status and exit")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON output")
    parser.add_argument("--toolset", dest="toolset", metavar="<profile>",
                        help="Expose only a curated toolset profile "
                             "(see args/mcp_toolset_profiles.yaml)")
    parser.add_argument("--list-toolsets", action="store_true",
                        help="List available curated toolset profiles and exit")
    args = parser.parse_args()

    if args.db_path:
        os.environ["ICDEV_DB_PATH"] = args.db_path

    if args.list_toolsets:
        from tools.mcp.toolset_profiles import list_profiles

        profiles = list_profiles()
        if args.json:
            print(json.dumps({"profiles": profiles}))
        else:
            for p in profiles:
                print(f"{p['name']:<12} [{p['cui_egress']}] "
                      f"{p['tool_count']} tools — {p['description']}")
        sys.exit(0)

    if args.status:
        status = {"server": "icdev-unified", "version": "1.0.0", "status": "ready"}
        if args.json:
            print(json.dumps(status))
        else:
            print("[OK] icdev-unified v1.0.0 — ready")
        sys.exit(0)

    server = create_server(toolset=args.toolset)
    server.run()
