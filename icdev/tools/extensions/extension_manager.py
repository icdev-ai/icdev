#!/usr/bin/env python3
# CUI // SP-CTI
"""Active extension hook system (Phase 44 — D261-D264).

Extensions are loaded from numbered Python files — a filesystem ordering
convention used by Unix /etc/rc.d/, cron.d/, systemd drop-ins, git hooks,
and many other systems. ICDEV uses `NNN_name.py` (3-digit leading zero).
Agent Zero uses a related `_NN_name.py` convention in its `extensions/python/
<hook_point>/` layout. The naming convention is shared; the implementation
(enum-keyed dispatcher in ICDEV vs decorator + base class in Agent Zero) is
independent. See OPT-73 audit report.

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

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import importlib.util
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = get_logger("icdev.extensions")

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Extension Points
# ---------------------------------------------------------------------------


class ExtensionPoint(str, Enum):
    """Available hook points in the ICDEV™ lifecycle."""

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


#: Fallbacks for a hook point ``args/extension_config.yaml`` declares no block
#: for. Every one of them preserves the behaviour dispatch() had before the
#: per-point keys were read, so an absent block can never be the thing that
#: turns a working handler off or strips its ability to modify.
_DEFAULT_MAX_HANDLERS = 20
_DEFAULT_TOTAL_BUDGET_MS = 30000


def _positive_int(value: Any) -> Optional[int]:
    """``value`` as a positive int, or None if it is not one.

    A malformed entry (a string, a negative, a null) resolves to None and the
    caller falls back to its default. A bound nobody can parse must not become
    a bound of zero — that would silently disable the point it was meant to
    protect, which is the failure direction this whole change is about.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    ivalue = int(value)
    return ivalue if ivalue > 0 else None


# ---------------------------------------------------------------------------
# Telemetry (hcx-live-02)
# ---------------------------------------------------------------------------
# Nothing counted a dispatch, so `tools/awareness/capability_consumption.py`
# could not distinguish "this hook point is consumed" from "this hook point has
# never been called" — and a handler that raised was logged and forgotten,
# because `catch_handler_exceptions` defaults true. One row per dispatch on the
# existing `runtime_invocations` table answers both: the row IS the dispatch
# count, and its `status`/`error_class`/`error_message` are the failure.
#
# No new table: `runtime_invocations` already exists for exactly this shape and
# already enforces the rules that matter here (never raises, stores argument KEY
# NAMES only, degrades before its migration has run).
#
# COST, measured rather than assumed. One dispatch costs ~8ms wall-clock for the
# open/close pair (SQLite, warm; ~2.4ms of that is two `get_connection()` round
# trips and the rest is the commit). That is the same price `record()` has been
# charging the MCP surface per tool call since migration 341, so this doubles an
# accepted cost rather than introducing a new class of one — but since
# hcx-live-01 it lands on EVERY tool call in the SAG runtime, which is worth
# knowing before you register a handler here. Two named, auditable switches turn
# it off: `ICDEV_OBS_INVOCATIONS=0` for all invocation telemetry, or
# `hook_points.<point>.enabled: false` for one point (which skips the dispatch
# entirely, telemetry included — ~0.002ms). Never a code edit that drops the
# row while leaving the point live: that is how the seam became unmeasurable in
# the first place.


def _record_dispatch(hook_point: "ExtensionPoint", context: dict):
    """Open a telemetry span for one dispatch. Returns a context manager.

    Kept at module level rather than inline so a test can replace it, and so the
    import failure path has one home. The caller treats ANY failure here as
    "no telemetry", never as a dispatch failure.
    """
    from tools.observability.invocation_recorder import SURFACE_EXTENSION, record

    return record(SURFACE_EXTENSION, hook_point.value, arg_keys=context)


class _NullSpan:
    """Stand-in when telemetry is unavailable. Absorbs the same annotations."""

    status = ""
    error_class = None
    error_message = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


# ---------------------------------------------------------------------------
# Extension Manager
# ---------------------------------------------------------------------------


class ExtensionManager:
    """Loads, registers, and dispatches extension hooks.

    Singleton pattern — use the module-level ``extension_manager`` instance.
    """

    #: Counter names kept per hook point by :meth:`stats`.
    _STAT_KEYS = (
        "dispatches", "suppressed", "handlers_run", "handler_failures",
        "handlers_dropped", "timeouts", "modifications_suppressed",
    )

    def __init__(self, config: Optional[dict] = None, load_builtins: bool = True) -> None:
        """Build a manager.

        Args:
            config: pre-loaded configuration, in the shape of
                ``args/extension_config.yaml``. Defaults to reading that file.
            load_builtins: scan ``tools/extensions/builtins/``. Tests of the
                dispatch contract pass False — the nine chat builtins import
                RAG, Bayesian learning and the genesis status reader, and none
                of that should decide whether a kill-switch test can run.
        """
        self._handlers: Dict[ExtensionPoint, List[ExtensionHandler]] = {ep: [] for ep in ExtensionPoint}
        self._lock = threading.Lock()
        self._config = config if config is not None else _load_extension_config()
        self._loaded_files: set = set()
        self._stats: Dict[str, Dict[str, int]] = {}
        self._stats_lock = threading.Lock()
        self._last_error: Dict[str, str] = {}
        if load_builtins:
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

    def point_config(self, hook_point: ExtensionPoint) -> Dict[str, Any]:
        """Resolve the four per-point keys ``args/extension_config.yaml`` declares.

        ``hook_points.<point>`` shipped with ``enabled``, ``allow_modification``,
        ``max_handlers`` and ``timeout_ms`` for all ten points and :meth:`dispatch`
        read none of them — inert configuration one layer below the inert hook
        points themselves. Every value resolves here, and every fallback is the
        behaviour dispatch had before they were read:

        ``enabled``
            The per-point kill switch. Before this the only working switch was
            the global ``extensions.enabled``, which also stops the nine chat
            handlers that are in use.
        ``allow_modification``
            A **ceiling** on what a handler may declare, not a second copy of
            the per-handler flag. ``tool_execute_after`` ships ``false`` with
            the comment "Post-hooks observe only by default"; a handler
            registering ``allow_modification=True`` there could rewrite the
            context anyway. The ceiling only ever lowers: it cannot grant
            modification to a handler that did not ask for it.
        ``max_handlers``
            How many handlers ONE dispatch runs, in priority order. Falls back
            to ``safety.max_handlers_per_point`` (also previously unread).
        ``timeout_ms``
            The per-point wall-clock budget. Combined with the global
            ``safety.max_total_handler_time_ms`` by ``min()`` — the global value
            is a ceiling, so a per-point key can tighten it but never raise it.
        """
        ext_config = self._config.get("extensions") or {}
        safety = ext_config.get("safety") or {}
        block = (ext_config.get("hook_points") or {}).get(hook_point.value)
        if not isinstance(block, dict):
            block = {}

        max_handlers = (
            _positive_int(block.get("max_handlers"))
            or _positive_int(safety.get("max_handlers_per_point"))
            or _DEFAULT_MAX_HANDLERS
        )
        global_budget = (
            _positive_int(safety.get("max_total_handler_time_ms"))
            or _DEFAULT_TOTAL_BUDGET_MS
        )
        point_budget = _positive_int(block.get("timeout_ms")) or global_budget

        return {
            "enabled": block.get("enabled", True) is not False,
            "allow_modification": block.get("allow_modification", True) is not False,
            "max_handlers": max_handlers,
            "timeout_ms": min(point_budget, global_budget),
        }

    def dispatch(self, hook_point: ExtensionPoint, context: dict) -> dict:
        """Run all handlers for a hook point in priority order.

        Behavioral handlers (allow_modification=True) may return a modified
        context dict, subject to the point's ``allow_modification`` ceiling.
        Observational handlers' return values are ignored.

        Exceptions in handlers are caught and logged — they never propagate.
        That fail-soft default is deliberate and unchanged: this sits on the
        chat path and the tool path, and a broken extension must not break the
        caller. What is NOT unchanged is that the failure used to be invisible.
        Every dispatch now writes one ``runtime_invocations`` row on the
        ``extension`` surface, and a raising handler makes that row an ``error``
        naming the handler. A dispatch with ZERO handlers is recorded too, on
        purpose: "dispatched, nothing listening" and "never dispatched" are
        different defects with different fixes, and eight of the ten points are
        currently one of the two.

        A point disabled by config records nothing at all — the kill switch is
        total, including its telemetry cost, since an operator standing a
        hot-path hook down should not keep paying two SQL statements per call
        for it. The suppression is counted in :meth:`stats` instead.

        Returns the (possibly modified) context dict.
        """
        ext_config = self._config.get("extensions", {})
        if not ext_config.get("enabled", True):
            return context

        point = self.point_config(hook_point)
        if not point["enabled"]:
            logger.debug(
                "Extension point %s is disabled in args/extension_config.yaml",
                hook_point.value,
            )
            self._bump(hook_point, "suppressed")
            return context

        safety = ext_config.get("safety", {})
        catch_exceptions = safety.get("catch_handler_exceptions", True)
        max_total_ms = point["timeout_ms"]

        with self._lock:
            handlers = [h for h in self._handlers.get(hook_point, []) if h.enabled]

        dropped = handlers[point["max_handlers"]:]
        handlers = handlers[: point["max_handlers"]]
        if dropped:
            logger.warning(
                "Extension point %s has %d handler(s) over its max_handlers=%d "
                "bound; not running: %s",
                hook_point.value,
                len(dropped),
                point["max_handlers"],
                ", ".join(h.name for h in dropped),
            )
            self._bump(hook_point, "handlers_dropped", len(dropped))

        total_start = time.time()
        result = dict(context)  # shallow copy
        failures: List[str] = []
        first_error: Optional[BaseException] = None

        span = _NullSpan()
        try:
            span = _record_dispatch(hook_point, context)
        except Exception as exc:  # noqa: BLE001 — telemetry never breaks dispatch
            logger.debug("extension dispatch telemetry unavailable: %s", exc)

        # `record()` is a generator context manager, so the annotatable handle is
        # what it YIELDS — setting .status on the manager object itself would
        # write to a throwaway and the row would close as 'ok' whatever happened.
        with span as inv:
            self._bump(hook_point, "dispatches")
            for ext in handlers:
                # Check the point's time budget
                elapsed_ms = (time.time() - total_start) * 1000
                if elapsed_ms > max_total_ms:
                    logger.warning(
                        "Extension dispatch timeout for %s after %.0fms (limit=%dms)",
                        hook_point.value,
                        elapsed_ms,
                        max_total_ms,
                    )
                    self._bump(hook_point, "timeouts")
                    break

                handler_start = time.time()
                try:
                    ret = ext.handler(result)
                    duration_ms = (time.time() - handler_start) * 1000
                    self._bump(hook_point, "handlers_run")

                    # Behavioral hooks can modify context — if the point permits
                    # it. A handler that declared modification at a point that
                    # forbids it is a policy breach worth a line each time, not
                    # a silent drop.
                    returned_dict = isinstance(ret, dict)
                    modified = False
                    if ext.allow_modification and returned_dict:
                        if point["allow_modification"]:
                            result = ret
                            modified = True
                        else:
                            self._bump(hook_point, "modifications_suppressed")
                            logger.warning(
                                "Extension %s.%s returned a modified context but "
                                "hook_points.%s.allow_modification is false — discarded",
                                hook_point.value,
                                ext.name,
                                hook_point.value,
                            )

                    logger.debug(
                        "Extension %s.%s completed in %.1fms (modified=%s)",
                        hook_point.value,
                        ext.name,
                        duration_ms,
                        modified,
                    )

                except Exception as exc:
                    duration_ms = (time.time() - handler_start) * 1000
                    logger.error(
                        "Extension %s.%s raised %s in %.1fms: %s",
                        hook_point.value,
                        ext.name,
                        type(exc).__name__,
                        duration_ms,
                        exc,
                    )
                    self._bump(hook_point, "handler_failures")
                    failures.append(f"{ext.name}: {type(exc).__name__}: {exc}")
                    self._last_error[hook_point.value] = failures[-1]
                    if first_error is None:
                        first_error = exc
                    if not catch_exceptions:
                        # The opt-out. record() annotates the span from the
                        # exception on its way past, so the failure is still
                        # recorded — it just is not swallowed.
                        raise

            if failures:
                # A dispatch where any handler failed IS a failed dispatch. This
                # is what puts a broken handler in the `errors` column of
                # `invocation_recorder.summary()` and on the monitoring panel,
                # instead of only in a log line nobody reads.
                inv.status = "error"
                inv.error_class = type(first_error).__name__ if first_error else "Exception"
                inv.error_message = "; ".join(failures)

        return result

    # ------------------------------------------------------------------
    # In-process counters
    # ------------------------------------------------------------------

    def _bump(self, hook_point: ExtensionPoint, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            bucket = self._stats.setdefault(
                hook_point.value, {k: 0 for k in self._STAT_KEYS}
            )
            bucket[key] = bucket.get(key, 0) + amount

    def stats(self, hook_point: Optional[ExtensionPoint] = None) -> Dict[str, Any]:
        """Counters for this process: dispatches, failures, suppressions.

        The database row is the durable record; this is the cheap one, and it is
        what a caller with no database (a test, an air-gapped run, a process
        started before the migration) can still read. ``last_error`` carries the
        most recent handler failure verbatim so "which handler broke" does not
        require a query.
        """
        with self._stats_lock:
            if hook_point is not None:
                bucket = dict(
                    self._stats.get(hook_point.value, {k: 0 for k in self._STAT_KEYS})
                )
                bucket["last_error"] = self._last_error.get(hook_point.value)
                return bucket
            return {
                name: {**counts, "last_error": self._last_error.get(name)}
                for name, counts in self._stats.items()
            }

    def reset_stats(self) -> None:
        """Clear the in-process counters."""
        with self._stats_lock:
            self._stats.clear()
            self._last_error.clear()

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
        spec = importlib.util.spec_from_file_location(f"ext_{hook_point.value}_{file_path.stem}", str(file_path))
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
                        logger.warning("Unknown hook point '%s' in %s", hook_name, py_file)
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
        spec = importlib.util.spec_from_file_location(f"ext_builtin_{file_path.stem}", str(file_path))
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
                    handlers.append(
                        {
                            "name": h.name,
                            "hook_point": h.hook_point.value,
                            "priority": h.priority,
                            "allow_modification": h.allow_modification,
                            "scope": h.scope,
                            "scope_id": h.scope_id,
                            "enabled": h.enabled,
                            "description": h.description,
                            "file_path": h.file_path,
                        }
                    )
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
