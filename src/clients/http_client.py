# CUI // SP-CTI
"""Base HTTP client — loads configurable thresholds from args/http_client.yaml."""

from __future__ import annotations

import pathlib
from typing import Any

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "args" / "http_client.yaml"

_DEFAULTS: dict[str, Any] = {
    "timeout": {"connect": 10, "read": 30},
    "max_retries": 3,
    "backoff_factor": 0.5,
    "max_redirects": 10,
}


def _load_config() -> dict[str, Any]:
    if _YAML_AVAILABLE and _CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            return {**_DEFAULTS, **data}
        except Exception:
            pass
    return dict(_DEFAULTS)


class BaseHTTPClient:
    """Mixin that injects configurable timeout and retry thresholds."""

    def __init__(self, timeout: tuple[int, int] | int | None = None) -> None:
        config = _load_config()
        if timeout is None:
            t = config.get("timeout", _DEFAULTS["timeout"])
            if isinstance(t, dict):
                timeout = (int(t.get("connect", 10)), int(t.get("read", 30)))
            else:
                timeout = int(t)
        self.timeout: tuple[int, int] | int = timeout
        self.max_retries: int = int(config.get("max_retries", _DEFAULTS["max_retries"]))
        self.max_redirects: int = int(config.get("max_redirects", _DEFAULTS["max_redirects"]))
