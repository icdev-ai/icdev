#!/usr/bin/env python3
# CUI // SP-CTI
"""Central NDJSON logger for all ICDEV™ components (LOG-01).

Every tool module must obtain a logger via ``get_logger(component_name)``
rather than calling ``logging.getLogger()`` directly.  This ensures:

  * Structured NDJSON output readable by Genesis log_triage reflex
  * Dual rotation: time-based (daily) AND size-based (10 MB)
  * Per-component level overrides via args/logging_config.yaml
  * Consistent trace_id / session_id fields for AI triage

Windows multi-process rotation (hcx-ace-10)
-------------------------------------------
Several ICDEV processes (dashboard, Genesis daemon, kanban runner, ACE
controllers) open the SAME per-component log file concurrently.  On Windows a
file that is open in one process cannot be renamed by another, so when a
``TimedRotatingFileHandler`` / ``RotatingFileHandler`` tries to roll the file
over it fails with ``PermissionError: [WinError 32]`` (ERROR_SHARING_VIOLATION).
The stock handlers propagate that error into ``handleError`` on every single
record — log records are lost and stderr floods with ``--- Logging error ---``.

Chosen fix (option **a**): the handlers are subclassed
(``_SafeTimedRotatingFileHandler`` / ``_SafeRotatingFileHandler``) so that a
rotation blocked by a sharing violation is **swallowed once** and the process
simply keeps appending to the current file.  A single one-line notice is written
to stderr per handler instance (never repeated — no flood), and for the timed
handler the next-rollover clock is advanced so the doomed rename is not
re-attempted on every subsequent record.  This keeps behaviour on POSIX (where
renaming an open file is legal) completely unchanged: ``super().doRollover()``
succeeds there and the swallow path is never taken.  Per-process filenames
(option b) were rejected because they fragment each component's log across N
files, breaking the log_triage reflex's single-stream assumption.

Script-run components (kax-obs-01)
---------------------------------
``get_logger(__name__)`` is the house convention, but ``__name__`` is
``"__main__"`` when a module is executed as a script rather than imported.
Every daemon started by ``tools/genesis/launcher.py`` is executed that way, so
before this fix they all wrote to one shared ``.logs/__main__.ndjson`` (10 MB,
21 884 pr_watcher lines) while their own per-component file stayed empty.
``_canonical_component`` maps the sentinel back to the dotted module path.

Usage:
    from tools.logging.icdev_logger import get_logger
    log = get_logger("my_component")
    log.info("message", extra={"extra": {"key": "value"}})
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional
from icdev.core.paths import repo_root

# Lazy YAML import (stdlib fallback)
try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_CACHE: Dict[str, logging.Logger] = {}
_CONFIG_CACHE: Optional[Dict[str, Any]] = None

BASE_DIR = repo_root(__file__)


class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON (NDJSON)."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "component": record.name,
                "message": record.getMessage(),
                "trace_id": getattr(record, "trace_id", None),
                "session_id": getattr(record, "session_id", None),
                "extra": getattr(record, "extra", {}),
            },
            default=str,
        )


def _is_sharing_violation(exc: BaseException) -> bool:
    """True when *exc* is a Windows 'file in use by another process' error.

    ERROR_SHARING_VIOLATION is WinError 32; ERROR_ACCESS_DENIED is 5.  Any
    ``PermissionError`` raised while renaming an open log file falls in this
    bucket on Windows.  On POSIX this returns False for the (legal) rename path,
    so unrelated OSErrors still propagate.
    """
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) in (5, 32)


class _WindowsSafeRotationMixin:
    """Mixin that tolerates a rotation blocked by a cross-process file lock.

    When ``doRollover`` fails because another process holds the base file open
    (Windows sharing violation), we swallow the error, make sure the stream is
    reopened on the current file so records keep landing, and advance the timed
    handler's next-rollover clock so we don't re-attempt the doomed rename on
    every record.  A single stderr notice is emitted per handler instance.
    """

    _rotation_skip_warned = False

    def _warn_rotation_skipped_once(self) -> None:
        if self._rotation_skip_warned:
            return
        self._rotation_skip_warned = True
        try:
            sys.stderr.write(
                f"[icdev_logger] rotation of {getattr(self, 'baseFilename', '?')} "
                "skipped: file held open by another process (Windows sharing "
                "violation); continuing to append to the current file. This "
                "notice is shown once per handler.\n"
            )
        except Exception:
            pass

    def rotate(self, source: str, dest: str) -> None:  # type: ignore[override]
        # Primary swallow point: base->rotated rename goes through here for both
        # RotatingFileHandler and TimedRotatingFileHandler.
        try:
            super().rotate(source, dest)  # type: ignore[misc]
        except OSError as exc:
            if not _is_sharing_violation(exc):
                raise
            self._warn_rotation_skipped_once()

    def doRollover(self) -> None:  # type: ignore[override]
        # Safety net: intermediate os.rename/os.remove calls in
        # RotatingFileHandler.doRollover bypass self.rotate() and can also hit a
        # sharing violation.  Swallow those too and recover the stream.
        try:
            super().doRollover()  # type: ignore[misc]
        except OSError as exc:
            if not _is_sharing_violation(exc):
                raise
            self._warn_rotation_skipped_once()
            # Reopen the current base file so subsequent records are not lost.
            if getattr(self, "stream", None) is None and not getattr(self, "delay", False):
                self.stream = self._open()  # type: ignore[attr-defined]
            # Advance the timed handler's clock so we don't retry every record.
            if hasattr(self, "computeRollover") and hasattr(self, "rolloverAt"):
                self.rolloverAt = self.computeRollover(int(time.time()))  # type: ignore[attr-defined]


class _SafeTimedRotatingFileHandler(_WindowsSafeRotationMixin, TimedRotatingFileHandler):
    """TimedRotatingFileHandler that tolerates Windows cross-process rotation."""


class _SafeRotatingFileHandler(_WindowsSafeRotationMixin, RotatingFileHandler):
    """RotatingFileHandler that tolerates Windows cross-process rotation."""


def _canonical_component(component: str) -> str:
    """Resolve the sentinel ``"__main__"`` to the script's real dotted path.

    ``get_logger(__name__)`` at module scope is the house convention, but
    ``__name__`` is ``"__main__"`` whenever that module is *executed* rather
    than imported — which is exactly how every long-running ICDEV daemon is
    started (``python tools/ci/pr_watcher.py --daemon``, launcher.py).  Left
    unresolved, all of them share one ``.logs/__main__.ndjson`` bucket while
    their own ``.logs/<dotted.name>.ndjson`` stays empty, so per-component log
    silence carries no information (kax-obs-01).

    Rebuild the package path the module would have had on import by walking up
    from the script while ``__init__.py`` exists.  ``python tools/ci/x.py`` and
    ``python -m tools.ci.x`` therefore agree on ``tools.ci.x``.  A script that
    is not inside a package falls back to its filename stem; only a ``__main__``
    with no ``__file__`` at all (``python -c``, REPL) keeps the sentinel.
    """
    if component != "__main__":
        return component
    path = getattr(sys.modules.get("__main__"), "__file__", None)
    if not path:
        return component
    try:
        script = Path(path).resolve()
    except OSError:
        return component
    parts = [script.stem]
    directory = script.parent
    # Bounded walk: package nesting is shallow, and a stray __init__.py near a
    # filesystem root must not spin.
    for _ in range(20):
        if not (directory / "__init__.py").exists():
            break
        parts.append(directory.name)
        parent = directory.parent
        if parent == directory:
            break
        directory = parent
    return ".".join(reversed(parts))


def _load_config() -> Dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    cfg_path = BASE_DIR / "args" / "logging_config.yaml"
    if _HAS_YAML and cfg_path.exists():
        try:
            _CONFIG_CACHE = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            return _CONFIG_CACHE
        except Exception:
            pass
    _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def _build_handlers(component: str, cfg: Dict[str, Any]) -> list:
    log_dir = Path(cfg.get("log_dir", ".logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = Path(os.environ.get("TEMP", ".")) / "icdev_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

    rotation = cfg.get("rotation", {})
    fmt = _JsonFormatter()
    handlers = []

    # Time-based rotation (daily at midnight, 30-day retention)
    timed_path = log_dir / f"{component}.ndjson"
    timed = _SafeTimedRotatingFileHandler(
        timed_path,
        when=rotation.get("when", "midnight"),
        backupCount=int(rotation.get("retention_days", 30)),
        encoding="utf-8",
    )
    timed.setFormatter(fmt)
    handlers.append(timed)

    # Size-based rotation (10 MB, 5 backups)
    sized_path = log_dir / f"{component}_size.ndjson"
    sized = _SafeRotatingFileHandler(
        sized_path,
        maxBytes=int(rotation.get("max_bytes", 10_485_760)),
        backupCount=5,
        encoding="utf-8",
    )
    sized.setFormatter(fmt)
    handlers.append(sized)

    return handlers


def get_logger(component: str) -> logging.Logger:
    """Return a cached, fully configured NDJSON logger for *component*.

    Thread-safe for read after first write (GIL protects dict assignment).
    """
    component = _canonical_component(component)
    if component in _CACHE:
        return _CACHE[component]

    cfg = _load_config()
    overrides = cfg.get("component_overrides", {}).get(component, {})
    level_name: str = overrides.get("level") or cfg.get("global_level", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)

    logger = logging.getLogger(component)
    logger.setLevel(level)
    logger.propagate = False  # avoid double-logging to root handler

    for handler in _build_handlers(component, cfg):
        logger.addHandler(handler)

    _CACHE[component] = logger
    return logger


def invalidate_cache() -> None:
    """Force config + logger re-init (for tests that swap args/logging_config.yaml)."""
    global _CONFIG_CACHE
    _CONFIG_CACHE = None
    _CACHE.clear()


if __name__ == "__main__":
    log = get_logger("icdev_logger_smoke")
    log.info("ICDEV logger self-test OK")
    cfg = _load_config()
    log_dir = Path(cfg.get("log_dir", ".logs"))
    smoke_file = log_dir / "icdev_logger_smoke.ndjson"
    print(f"Log written to: {smoke_file}")
    if smoke_file.exists():
        print(smoke_file.read_text(encoding="utf-8"))
