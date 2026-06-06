#!/usr/bin/env python3
# CUI // SP-CTI
"""Central NDJSON logger for all ICDEV™ components (LOG-01).

Every tool module must obtain a logger via ``get_logger(component_name)``
rather than calling ``logging.getLogger()`` directly.  This ensures:

  * Structured NDJSON output readable by Genesis log_triage reflex
  * Dual rotation: time-based (daily) AND size-based (10 MB)
  * Per-component level overrides via args/logging_config.yaml
  * Consistent trace_id / session_id fields for AI triage

Usage:
    from tools.logging.icdev_logger import get_logger
    log = get_logger("my_component")
    log.info("message", extra={"extra": {"key": "value"}})
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

# Lazy YAML import (stdlib fallback)
try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_CACHE: Dict[str, logging.Logger] = {}
_CONFIG_CACHE: Optional[Dict[str, Any]] = None

BASE_DIR = Path(__file__).resolve().parent.parent.parent


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
    timed = TimedRotatingFileHandler(
        timed_path,
        when=rotation.get("when", "midnight"),
        backupCount=int(rotation.get("retention_days", 30)),
        encoding="utf-8",
    )
    timed.setFormatter(fmt)
    handlers.append(timed)

    # Size-based rotation (10 MB, 5 backups)
    sized_path = log_dir / f"{component}_size.ndjson"
    sized = RotatingFileHandler(
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
