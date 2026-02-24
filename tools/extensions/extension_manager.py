#!/usr/bin/env python3
# CUI // SP-CTI
"""Active extension hook system (Phase 44 — D261-D264).

Extensions are loaded from numbered Python files (Agent Zero pattern).
Two tiers: behavioral (modify data, requires allow_modification=True)
and observational (log only). Layered override: project > tenant > default.
Existing store_event() passive hooks are preserved as the observational tier.

Usage:
    from tools.extensions.extension_manager import extension_manager, ExtensionPoint

    # Register an extension programmatically
    extension_manager.register(
        ExtensionPoint.TOOL_EXECUTE_BEFORE,
        handler=my_handler,
        name="my_hook",
        priority=100,
    )

    # Dispatch an extension point
    context = {"tool_name": "ssp_generator", "args": {...}}
    modified_ctx = extension_manager.dispatch(ExtensionPoint.TOOL_EXECUTE_BEFORE, context)
"""

import importlib.util
import logging
import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("icdev.extensions")

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Extension Points
# ---------------------------------------------------------------------------

class ExtensionPoint(str, Enum):
    """Available hook points in the ICDEV lifecycle."""

    TOOL_EXECUTE_BEFORE = "tool_execute_before"
    TOOL_EXECUTE_AFTER = "tool_execute_after"
    CHAT_MESSAGE_BEFORE = "chat_message_before"
    CHAT_MESSAGE_AFTER = "chat_message_after"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    MEMORY_SAVE_BEFORE = "memory_save_before"
    MEMORY_SAVE_AFTER = "memory_save_after"
    COMPLIANCE_CHECK_BEFORE = "compliance_check_before"
    COMPLIANCE_CHECK_AFTER = "compliance_check_after"


# ---------------------------------------------------------------------------
# Extension Handler
# ---------------------------------------------------------------------------

@dataclass
class ExtensionHandler:
    """Single extension handler with priority and metadata."""

    name: str
    hook_point: ExtensionPoint
    handler: Callable[[dict], Optional[dict]]
    priority: int = 500  # Lower = runs first (0-999)
    allow_modification: bool = False
    scope: str = "default"  # default, tenant, project
    scope_id: str = ""
    description: str = ""
    enabled: bool = True
    file_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

def _load_extension_config() -> dict:
    """Load extension configuration from args/extension_config.yaml."""
    config_path = BASE_DIR / "args" / "extension_config.yaml"
    if not config_path.exists():
        return {"extensions": {"enabled": True, "hook_points": {}, "safety": {}}}
    try:
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {"extensions": {"enabled": True, "hook_points": {}, "safety": {}}}


# ---------------------------------------------------------------------------
# Extension Manager
# ---------------------------------------------------------------------------

class ExtensionManager:
    """Loads, registers, and dispatches extension hooks.

    Singleton pattern — use the module-level ``extension_manager`` instance.
    """

    def __init__(self) -> None:
        self._handlers: Dict[ExtensionPoint, List[ExtensionHandler]] = {
            ep: [] for ep in ExtensionPoint
        }
        self._lock = threading.Lock()
        self._config = _load_extension_config()
        self._loaded_files: set = set()
        # Auto-load built-in extensions on init (D324)
        self._auto_load_builtins()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        hook_point: ExtensionPoint,
        handler: Callable[[dict], Optional[dict]],
        name: str = "",
        priority: int = 500,
        allow_modification: bool = False,
        scope: str = "default",
        scope_id: str = "",
        description: str = "",
        file_path: Optional[str] = None,
    ) -> ExtensionHandler:
        """Register an extension handler programmatically.

        Returns the created ExtensionHandler.
        """
        ext = ExtensionHandler(
            name=name or handler.__name__,
            hook_point=hook_point,
            handler=handler,
            priority=priority,
            allow_modification=allow_modification,
            scope=scope,
            scope_id=scope_id,
            description=description,
            file_path=file_path,
        )
        with self._lock:
            self._handlers[hook_point].append(ext)
            # Sort by priority (lower first)
            self._handlers[hook_point].sort(key=lambda h: h.priority)
        logger.info("Registered extension: %s at %s (priority=%d)", ext.name, hook_point.value, priority)
        return ext

    def unregister(self, hook_point: ExtensionPoint, name: str) -> bool:
        """Unregister an extension by name. Returns True if found."""
        with self._lock:
            handlers = self._handlers[hook_point]
            before = len(handlers)
            self._handlers[hook_point] = [h for h in handlers if h.name != name]
            return len(self._handlers[hook_point]) < before

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(self, hook_point: ExtensionPoint, context: dict) -> dict:
        """Run all handlers for a hook point in priority order.

        Behavioral handlers (allow_modification=True) may return a modified
        context dict. Observational handlers' return values are ignored.

        Exceptions in handlers are caught and logged — they never propagate.

        Returns the (possibly modified) context dict.
        """
        ext_config = self._config.get("extensions", {})
        if not ext_config.get("enabled", True):
            return context

        safety = ext_config.get("safety", {})
        max_total_ms = safety.get("max_total_handler_time_ms", 30000)
        catch_exceptions = safety.get("catch_handler_exceptions", True)

        with self._lock:
            handlers = list(self._handlers.get(hook_point, []))

        total_start = time.time()
        result = dict(context)  # shallow copy

        for ext in handlers:
            if not ext.enabled:
                continue

            # Check total time budget
            elapsed_ms = (time.time() - total_start) * 1000
            if elapsed_ms > max_total_ms:
                logger.warning(
                    "Extension dispatch timeout for %s after %.0fms (limit=%dms)",
                    hook_point.value, elapsed_ms, max_total_ms,
                )
                break

            handler_start = time.time()
            try:
                ret = ext.handler(result)
                duration_ms = (time.time() - handler_start) * 1000

                # Behavioral hooks can modify context
                if ext.allow_modification and isinstance(ret, dict):
                    result = ret

                logger.debug(
                    "Extension %s.%s completed in %.1fms (modified=%s)",
                    hook_point.value, ext.name, duration_ms,
                    ext.allow_modification and isinstance(ret, dict),
                )

            except Exception as exc:
                duration_ms = (time.time() - handler_start) * 1000
                logger.error(
                    "Extension %s.%s raised %s in %.1fms: %s",
                    hook_point.value, ext.name, type(exc).__name__, duration_ms, exc,
                )
                if not catch_exceptions:
                    raise

        return result

    def dispatch_async(self, hook_point: ExtensionPoint, context: dict) -> None:
        """Fire-and-forget dispatch for observational hooks.

        Runs handlers in a background thread. Does not return results.
        """
        thread = threading.Thread(
            target=self.dispatch,
            args=(hook_point, context),
            daemon=True,
        )
        thread.start()

    # ------------------------------------------------------------------
    # File-based extension loading
    # ------------------------------------------------------------------

    def load_extensions_from_directory(
        self,
        directory: Path,
        scope: str = "default",
        scope_id: str = "",
    ) -> int:
        """Load extensions from numbered Python files in a directory.

        Directory structure: extensions/{hook_point}/{priority}_{name}.py
        Each file must export: handle(context: dict) -> dict | None
        Optional exports: PRIORITY, NAME, ALLOW_MODIFICATION, DESCRIPTION

        Returns number of extensions loaded.
        """
        loaded = 0
        if not directory.is_dir():
            return loaded

        for hook_dir in directory.iterdir():
            if not hook_dir.is_dir():
                continue

            # Map directory name to ExtensionPoint
            try:
                hook_point = ExtensionPoint(hook_dir.name)
            except ValueError:
                continue

            for py_file in sorted(hook_dir.glob("*.py")):
                if py_file.name.startswith("_") and py_file.name != "__init__.py":
                    continue
                if str(py_file) in self._loaded_files:
                    continue

                try:
                    ext = self._load_extension_file(py_file, hook_point, scope, scope_id)
                    if ext:
                        loaded += 1
                        self._loaded_files.add(str(py_file))
                except Exception as exc:
                    logger.error("Failed to load extension %s: %s", py_file, exc)

        return loaded

    def _load_extension_file(
        self,
        file_path: Path,
        hook_point: ExtensionPoint,
        scope: str,
        scope_id: str,
    ) -> Optional[ExtensionHandler]:
        """Load a single extension file."""
        spec = importlib.util.spec_from_file_location(
            f"ext_{hook_point.value}_{file_path.stem}", str(file_path)
        )
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        handler_fn = getattr(module, "handle", None)
        if handler_fn is None or not callable(handler_fn):
            logger.warning("Extension %s missing handle() function", file_path)
            return None

        # Extract optional metadata
        priority = getattr(module, "PRIORITY", 500)
        name = getattr(module, "NAME", file_path.stem)
        allow_mod = getattr(module, "ALLOW_MODIFICATION", False)
        description = getattr(module, "DESCRIPTION", "")

        # Parse priority from filename prefix if present (e.g., 010_audit_log.py)
        parts = file_path.stem.split("_", 1)
        if parts[0].isdigit():
            priority = int(parts[0])
            if not hasattr(module, "NAME"):
                name = parts[1] if len(parts) > 1 else file_path.stem

        return self.register(
            hook_point=hook_point,
            handler=handler_fn,
            name=name,
            priority=priority,
            allow_modification=allow_mod,
            scope=scope,
            scope_id=scope_id,
            description=description,
            file_path=str(file_path),
        )

    # ------------------------------------------------------------------
    # Auto-load built-in extensions (D324)
    # ------------------------------------------------------------------

    def _auto_load_builtins(self) -> int:
        """Scan tools/extensions/builtins/*.py and register EXTENSION_HOOKS.

        Each file is expected to export an ``EXTENSION_HOOKS`` dict mapping
        hook point names to handler metadata::

            EXTENSION_HOOKS = {
                "chat_message_after": {
                    "handler": handle,
                    "name": "my_hook",
                    "priority": 10,
                    "allow_modification": True,
                    "description": "...",
                },
            }

        Returns the number of handlers registered.
        """
        builtins_dir = BASE_DIR / "tools" / "extensions" / "builtins"
        if not builtins_dir.is_dir():
            return 0

        loaded = 0
        for py_file in sorted(builtins_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            if str(py_file) in self._loaded_files:
                continue

            try:
                module = self._load_file(py_file)
                if module is None:
                    continue

                hooks = getattr(module, "EXTENSION_HOOKS", None)
                if not isinstance(hooks, dict):
                    continue

                for hook_name, meta in hooks.items():
                    try:
                        hook_point = ExtensionPoint(hook_name)
                    except ValueError:
                        logger.warning(
                            "Unknown hook point '%s' in %s", hook_name, py_file)
                        continue

                    handler_fn = meta.get("handler")
                    if handler_fn is None or not callable(handler_fn):
                        continue

                    self.register(
                        hook_point=hook_point,
                        handler=handler_fn,
                        name=meta.get("name", py_file.stem),
                        priority=meta.get("priority", 500),
                        allow_modification=meta.get("allow_modification", False),
                        scope="builtin",
                        description=meta.get("description", ""),
                        file_path=str(py_file),
                    )
                    loaded += 1

                self._loaded_files.add(str(py_file))
            except Exception as exc:
                logger.error("Failed to load builtin %s: %s", py_file, exc)

        return loaded

    def _load_file(self, file_path: Path):
        """Load a Python file via importlib and return the module."""
        spec = importlib.util.spec_from_file_location(
            f"ext_builtin_{file_path.stem}", str(file_path)
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_handlers(self, hook_point: Optional[ExtensionPoint] = None) -> List[dict]:
        """List registered handlers, optionally filtered by hook point."""
        with self._lock:
            handlers = []
            points = [hook_point] if hook_point else list(ExtensionPoint)
            for hp in points:
                for h in self._handlers.get(hp, []):
                    handlers.append({
                        "name": h.name,
                        "hook_point": h.hook_point.value,
                        "priority": h.priority,
                        "allow_modification": h.allow_modification,
                        "scope": h.scope,
                        "scope_id": h.scope_id,
                        "enabled": h.enabled,
                        "description": h.description,
                        "file_path": h.file_path,
                    })
            return handlers

    def handler_count(self, hook_point: Optional[ExtensionPoint] = None) -> int:
        """Count registered handlers."""
        with self._lock:
            if hook_point:
                return len(self._handlers.get(hook_point, []))
            return sum(len(v) for v in self._handlers.values())


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
extension_manager = ExtensionManager()
