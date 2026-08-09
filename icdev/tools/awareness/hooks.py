# CUI // SP-CTI
"""Phase 1e — Post-tool hook subscriber for auto-reindex on every Edit.

Subscribes to the Phase 44 ``TOOL_EXECUTE_AFTER`` extension point so
that every Edit/Write/NotebookEdit on a tracked file synchronously
refreshes the corresponding kg_nodes entry via
``component_indexer.upsert_file``.

Design:
  * Registration is idempotent — importing this module multiple times
    only registers the handler once (guarded via a module-level flag).
  * Handler filters by tool_name + file extension. Only edits to
    Python/Markdown/HTML/Jinja/YAML/JSON files within the project
    tree trigger a reindex.
  * Per-hook target: <=30ms. The upsert path is a single-file AST
    parse + one SQL UPSERT, well under that budget.
  * Exceptions are caught inside the handler so a broken parser
    never blocks tool execution. The extension_manager also catches
    handler exceptions as a second safety net.
  * Honors the Phase 1c enablement helper — disabled components
    still get indexed (so the UI can render them dimmed) but the
    enablement tag is refreshed on every edit.

Registration:
  The .claude/hooks/post_tool_use.py loader imports this module once
  at process startup, which triggers ``_register_once()``. No other
  call sites should need to touch the extension manager directly.

Public API:
  on_tool_execute_after(context) -> dict
  register() -> bool
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional


BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
LOG = get_logger("awareness.hooks")

# Tool names that carry a file_path we care about
_TRACKED_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})

# File extensions that the component_indexer knows how to parse.
# Anything outside this set is skipped at the filter step — we never
# dispatch to the indexer for binary blobs, screenshots, etc.
_TRACKED_EXTENSIONS = frozenset(
    {".py", ".md", ".html", ".jinja2", ".j2", ".yaml", ".yml", ".json"}
)

# Idempotency flag for register() — multiple imports of this module
# only add the handler once.
_REGISTERED = False


def _extract_file_path(context: Dict[str, Any]) -> Optional[Path]:
    """Pull the edited file path out of the hook context.

    The Phase 44 hook context shape was historically inconsistent —
    some call sites pass ``tool_input`` as a dict, others pass flat
    keys like ``file_path``. This helper handles both.
    """
    if not isinstance(context, dict):
        return None

    # Most common: context contains the full tool_input dict
    tool_input = context.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("file_path", "notebook_path", "path"):
            val = tool_input.get(key)
            if val:
                return Path(val)

    # Flat-key fallback
    for key in ("file_path", "notebook_path", "path"):
        val = context.get(key)
        if val:
            return Path(val)

    return None


def _is_tracked_path(path: Path) -> bool:
    """Decide whether a file path is inside the project and has a
    tracked extension."""
    if path.suffix.lower() not in _TRACKED_EXTENSIONS:
        return False
    try:
        path_abs = path.resolve()
    except OSError:
        return False
    try:
        path_abs.relative_to(BASE_DIR.resolve())
    except ValueError:
        return False
    return True


def on_tool_execute_after(context: Dict[str, Any]) -> Dict[str, Any]:
    """TOOL_EXECUTE_AFTER handler — refresh kg_nodes for edited files.

    Returns the context dict unmodified (observational handler).
    Never raises; swallows every error so tool execution is never
    blocked.
    """
    try:
        if not isinstance(context, dict):
            return context

        tool_name = context.get("tool_name", "")
        if tool_name not in _TRACKED_TOOLS:
            return context

        path = _extract_file_path(context)
        if path is None:
            return context

        if not _is_tracked_path(path):
            return context

        # Lazy import so module load is cheap when the hook never fires
        from tools.awareness.component_indexer import upsert_file

        result = upsert_file(path)
        if result.get("persisted"):
            LOG.debug(
                "awareness hook refreshed %s -> node=%s entity_type=%s enabled=%s",
                path,
                result.get("node_id"),
                result.get("entity_type"),
                result.get("enabled"),
            )
    except Exception as exc:
        # Never propagate — log and swallow
        LOG.warning("awareness hook failed: %s: %s", type(exc).__name__, exc)

    return context


def register() -> bool:
    """Register ``on_tool_execute_after`` with the extension manager.

    Idempotent — calling multiple times has no effect after the first
    successful registration. Returns True on success, False if the
    extension framework is unavailable (e.g., in a test environment
    without Phase 44 support).
    """
    global _REGISTERED
    if _REGISTERED:
        return True

    try:
        from tools.extensions.extension_manager import (
            ExtensionPoint,
            extension_manager,
        )
    except ImportError:
        LOG.debug("extension_manager unavailable — awareness hook not registered")
        return False

    try:
        extension_manager.register(
            hook_point=ExtensionPoint.TOOL_EXECUTE_AFTER,
            handler=on_tool_execute_after,
            name="awareness_component_indexer",
            priority=900,  # low priority — run after most other hooks
            allow_modification=False,
            scope="default",
            description=(
                "Phase 1e — refresh kg-icdev-self-awareness nodes on "
                "every Edit/Write/NotebookEdit event. Target <=30ms."
            ),
        )
    except Exception as exc:
        LOG.warning("awareness hook registration failed: %s", exc)
        return False

    _REGISTERED = True
    LOG.info("awareness hook registered (TOOL_EXECUTE_AFTER)")
    return True


def _reset_for_test() -> None:
    """Test helper — clear the idempotency flag so tests can re-register."""
    global _REGISTERED
    _REGISTERED = False


# Auto-register on import so the .claude hook loader only needs to
# `import tools.awareness.hooks` once at process startup.
register()
