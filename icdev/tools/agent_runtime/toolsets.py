# CUI // SP-CTI
"""Toolset bundles for the SAG runtime (sag-reg-02).

Bundles are declared as DATA in ``args/agent_toolsets.yaml`` — named, bounded sets
of tool names so a small local model is never handed the full 440+ tool surface.
This module loads those bundles, resolves them against the discovery registry
(sag-reg-01), and assembles the ``(tools, handlers)`` pair that
:func:`icdev.tools.llm.agent_loop.run_agent_loop` consumes, with mutating tools
routed through the safety gate (sag-reg-02 dispatch).

The mutating built-ins (``write_file``, ``run_command``) live in
``tools.agent_runtime.mutating_tools`` and are discovered via the ``@tool``
decorator; they are always offered to discovery so any bundle can reference them,
but the dispatch-time safety gate governs whether they actually execute.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from tools.agent_runtime import discovery
from tools.agent_runtime.dispatch import SafetyGate, build_handlers
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.toolsets")

# Modules scanned for @tool-decorated (non-MCP) tools.
_DECORATED_MODULES = ["tools.agent_runtime.mutating_tools"]


def _find_repo_root(start: Path) -> Path:
    sentinels = ("pyproject.toml", ".git", "CLAUDE.md")
    for parent in [start, *start.parents]:
        if any((parent / s).exists() for s in sentinels):
            return parent
    return start.parents[1] if len(start.parents) > 1 else start


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)
_BUNDLE_PATH = _REPO_ROOT / "args" / "agent_toolsets.yaml"


class ToolsetError(ValueError):
    """Raised when a requested bundle is unknown."""


def _bundle_path() -> Path:
    override = os.environ.get("ICDEV_AGENT_TOOLSETS")
    return Path(override) if override else _BUNDLE_PATH


def load_bundles() -> dict[str, dict[str, Any]]:
    """Load ``{bundle_name: bundle_dict}`` from YAML (empty on error)."""
    path = _bundle_path()
    if not path.exists():
        logger.warning("toolsets: %s not found", path)
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("toolsets: failed to parse %s: %s", path, exc)
        return {}
    bundles = data.get("bundles") or {}
    return bundles if isinstance(bundles, dict) else {}


def list_bundles() -> list[dict[str, Any]]:
    """Summarise bundles for ``icdev tools list`` / discovery UIs."""
    out = []
    for name, b in sorted(load_bundles().items()):
        tools = b.get("tools") or []
        out.append(
            {
                "name": name,
                "description": (b.get("description") or "").strip(),
                "mutating": bool(b.get("mutating", False)),
                "tool_count": len(tools),
                "tools": list(tools),
            }
        )
    return out


def resolve_bundles(names: list[str]) -> set[str]:
    """Resolve one or more bundle names to the union of their tool names.

    Raises:
        ToolsetError: if any bundle name is unknown.
    """
    bundles = load_bundles()
    wanted: set[str] = set()
    for name in names:
        b = bundles.get(name)
        if b is None:
            raise ToolsetError(
                f"Unknown toolset bundle {name!r}. Available: "
                f"{', '.join(sorted(bundles)) or '(none)'}"
            )
        wanted.update(b.get("tools") or [])
    return wanted


def build_registry_for_bundles(
    names: list[str],
) -> dict[str, discovery.ToolSpec]:
    """Build the discovery registry restricted to the tools in ``names``.

    Built-in and decorated tools are always discovered (they are cheap and their
    names are matched against the bundle selection); MCP and external derivation
    are limited to the requested names so the registry stays small.

    A tool on a third-party MCP server therefore has to be allowlisted twice —
    once in ``args/external_mcp_servers.yaml`` by the server's operator, and
    again by name in a bundle here. That is deliberate: enabling a server should
    not hand its tools to every role that happens to load a toolset.
    """
    wanted = resolve_bundles(names)
    registry = discovery.build_registry(
        include_mcp=True,
        include_builtin=True,
        mcp_names=wanted,
        external_names=wanted,
        decorated_modules=_DECORATED_MODULES,
    )
    # Keep only tools the bundles actually asked for (drops builtin/decorated
    # tools not referenced by the selected bundles).
    return {n: s for n, s in registry.items() if n in wanted}


def build_toolset(
    names: list[str],
    *,
    safety_gate: Optional[SafetyGate] = None,
    task_id: Optional[str] = None,
    approval_mode: Optional[str] = None,
    router: Any = None,
    approver: Any = None,
    classification: Optional[str] = None,
) -> "tuple[list[dict[str, Any]], dict[str, Any]]":
    """Assemble ``(tools, handlers)`` for the given bundle names.

    ``tools`` is the OpenAI function-schema list for ``run_agent_loop(tools=...)``;
    ``handlers`` is ``{name: handler(input, stop) -> str}`` with mutating tools
    routed through the safety gate.

    When no ``safety_gate`` is supplied, the real command-approval gate
    (:func:`tools.agent_runtime.safety.build_safety_gate`, sag-safe-01) is built
    from ``approval_mode`` / ``router`` / ``approver`` — so bundle-based toolsets
    are gated by the approval flow, not the fail-closed placeholder.

    ``classification`` declares the sensitivity of arguments sent to tools on
    third-party MCP servers; when omitted the deployment's own classification is
    used (:func:`tools.agent_runtime.dispatch.default_classification`).
    """
    if safety_gate is None:
        try:
            from tools.agent_runtime.safety import build_safety_gate

            safety_gate = build_safety_gate(
                mode=approval_mode, approver=approver, router=router
            )
        except Exception as exc:  # noqa: BLE001 — fall back to dispatch's fail-closed
            logger.warning("toolsets: safety gate unavailable (%s); failing closed", exc)
    registry = build_registry_for_bundles(names)
    tools = discovery.to_openai_tools(registry)
    handlers = build_handlers(
        registry,
        safety_gate=safety_gate,
        task_id=task_id,
        classification=classification,
    )
    return tools, handlers


def all_discovered_tools() -> list[dict[str, Any]]:
    """Return every discovered tool as ``{name, source, read_only, schema}``.

    Backs ``icdev tools list --json`` — the full surface, independent of bundles.
    """
    registry = discovery.build_registry(
        include_mcp=True,
        include_builtin=True,
        decorated_modules=_DECORATED_MODULES,
    )
    out = []
    for name, spec in sorted(registry.items()):
        out.append(
            {
                "name": name,
                "source": spec.source,
                "read_only": spec.read_only,
                "schema": spec.schema,
            }
        )
    return out
