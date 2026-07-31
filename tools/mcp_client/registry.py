# CUI // SP-CTI
"""Registry of external MCP servers, and the gate in front of them.

Surfaces third-party MCP tools to ICDEV agents under a namespaced id, and is
the single place every outbound call passes through.

Four controls, none of which require trusting the remote server:

1. **Namespacing.** Remote tools are exposed as ``ext__<server>__<tool>``. A
   remote server cannot advertise a tool called ``sandbox_execute`` and have an
   agent reach ICDEV's real one; the names cannot collide because ICDEV tool
   ids never start with ``ext__``.
2. **Allowlist.** Only tools named in a server's ``tools:`` list are exposed.
   A server advertising a hundred tools contributes exactly the listed ones.
3. **Classification ceiling.** Arguments are checked against the server's
   ceiling before the call. A server at UNCLASSIFIED never receives CUI.
4. **Air-gap interlock.** Every server is disabled in air-gap mode, whatever
   the config says.

Descriptions are sanitised on the way in (``sanitize.py``) because they enter an
agent's prompt as attacker-controlled text.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import yaml

from icdev.tools.logging.icdev_logger import get_logger
from icdev.tools.mcp_client import client as transport_mod
from icdev.tools.mcp_client.sanitize import (
    is_suspicious,
    sanitize_description,
    sanitize_tool_name,
)

logger = get_logger(__name__)

#: Prefix that makes a remote tool structurally distinguishable from an ICDEV
#: one. Nothing in TOOL_REGISTRY starts with this.
NAMESPACE_PREFIX = "ext__"

#: Ordered least- to most-sensitive. A server may receive content at or below
#: its ceiling.
_CLASSIFICATION_ORDER = ["UNCLASSIFIED", "CUI", "SECRET", "TOP SECRET"]


@dataclass
class ExternalTool:
    """One allowlisted tool on one external server."""

    server: str
    remote_name: str
    namespaced_name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.namespaced_name,
            "server": self.server,
            "remote_name": self.remote_name,
            "description": self.description,
            "input_schema": self.input_schema,
            "external": True,
        }


def _config_path():
    from icdev._paths import get_data_path

    return get_data_path("args") / "external_mcp_servers.yaml"


def load_config() -> dict:
    """Load the registry config, degrading to disabled."""
    try:
        raw = yaml.safe_load(_config_path().read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — absent config means "no servers"
        logger.debug("mcp_client: no external server config (%s)", exc)
        return {"enabled": False, "servers": [], "defaults": {}}
    raw.setdefault("enabled", False)
    raw.setdefault("servers", [])
    raw.setdefault("defaults", {})
    return raw


def _classification_rank(value: str) -> int:
    text = str(value or "UNCLASSIFIED").strip().upper()
    return _CLASSIFICATION_ORDER.index(text) if text in _CLASSIFICATION_ORDER else 0


def enabled_servers() -> list[dict]:
    """Server specs that are enabled, with defaults merged in.

    Returns empty in air-gap mode regardless of config: an external MCP server
    is off-box by definition, so this is not a policy decision the config gets
    to make.
    """
    if transport_mod._airgap_blocks():
        logger.info("mcp_client: air-gap active — no external MCP servers")
        return []

    config = load_config()
    if not config.get("enabled"):
        return []

    defaults = config.get("defaults") or {}
    out: list[dict] = []
    for spec in config.get("servers") or []:
        if not isinstance(spec, dict) or not spec.get("enabled"):
            continue
        if not spec.get("name"):
            logger.warning("mcp_client: skipping unnamed server entry")
            continue
        merged = {**defaults, **spec}
        out.append(merged)
    return out


class ExternalToolRegistry:
    """Discovers, namespaces and dispatches external MCP tools."""

    def __init__(self) -> None:
        self._transports: dict[str, Any] = {}
        self._tools: dict[str, ExternalTool] = {}
        self._specs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._discovered = False

    # -- discovery ---------------------------------------------------------

    def discover(self, *, force: bool = False) -> list[ExternalTool]:
        """Connect to each enabled server and collect its allowlisted tools."""
        with self._lock:
            if self._discovered and not force:
                return list(self._tools.values())

            self._tools.clear()
            self._specs.clear()

            for spec in enabled_servers():
                name = str(spec["name"])
                allow = {sanitize_tool_name(t) for t in (spec.get("tools") or [])}
                if not allow:
                    logger.info("mcp_client[%s]: no tools allowlisted — contributing none", name)
                    continue

                transport = self._transports.get(name)
                if transport is None:
                    transport = transport_mod.build_transport(spec)
                    if transport is None:
                        continue
                    self._transports[name] = transport

                self._specs[name] = spec
                for remote in transport.list_tools():
                    if not isinstance(remote, dict):
                        continue
                    raw_name = remote.get("name") or ""
                    safe_name = sanitize_tool_name(raw_name)
                    if safe_name not in allow:
                        continue

                    raw_description = remote.get("description") or ""
                    if is_suspicious(raw_description):
                        logger.warning(
                            "mcp_client[%s]: tool %r description contained "
                            "injection-shaped content; sanitised", name, safe_name,
                        )

                    namespaced = f"{NAMESPACE_PREFIX}{sanitize_tool_name(name)}__{safe_name}"
                    self._tools[namespaced] = ExternalTool(
                        server=name,
                        remote_name=str(raw_name),
                        namespaced_name=namespaced,
                        description=sanitize_description(raw_description, server=name),
                        input_schema=remote.get("inputSchema") or {},
                    )

            self._discovered = True
            return list(self._tools.values())

    def list_tools(self) -> list[dict]:
        return [t.to_dict() for t in self.discover()]

    def get(self, namespaced_name: str) -> ExternalTool | None:
        self.discover()
        return self._tools.get(namespaced_name)

    # -- dispatch ----------------------------------------------------------

    def call(
        self,
        namespaced_name: str,
        arguments: dict | None = None,
        *,
        classification: str = "UNCLASSIFIED",
    ) -> dict:
        """Call an external tool. Always returns a dict; never raises.

        ``classification`` is the sensitivity of *arguments*. A server whose
        ceiling is below it is refused before any connection is made.
        """
        tool = self.get(namespaced_name)
        if tool is None:
            return {"ok": False, "error": f"unknown external tool {namespaced_name!r}"}

        spec = self._specs.get(tool.server) or {}
        ceiling = spec.get("classification_ceiling", "UNCLASSIFIED")
        if _classification_rank(classification) > _classification_rank(ceiling):
            logger.warning(
                "mcp_client[%s]: refused %s content to a %s server",
                tool.server, classification, ceiling,
            )
            return {
                "ok": False,
                "error": (
                    f"classification {classification} exceeds the ceiling "
                    f"({ceiling}) for server {tool.server!r}"
                ),
            }

        transport = self._transports.get(tool.server)
        if transport is None:
            return {"ok": False, "error": f"server {tool.server!r} is not connected"}

        result = transport.call_tool(tool.remote_name, dict(arguments or {}))
        if result is None:
            return {"ok": False, "error": f"call to {namespaced_name!r} failed"}
        return {"ok": True, "server": tool.server, "tool": tool.remote_name, "result": result}

    def close(self) -> None:
        with self._lock:
            for transport in self._transports.values():
                try:
                    transport.close()
                except Exception:  # noqa: BLE001, S110
                    pass
            self._transports.clear()
            self._tools.clear()
            self._discovered = False


_registry: ExternalToolRegistry | None = None
_registry_lock = threading.Lock()


def get_external_registry() -> ExternalToolRegistry:
    """Process-wide registry."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ExternalToolRegistry()
    return _registry


def reset_external_registry() -> None:
    """Drop the cached registry and close transports (tests)."""
    global _registry
    with _registry_lock:
        if _registry is not None:
            _registry.close()
        _registry = None
