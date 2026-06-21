# CUI // SP-CTI
"""Base HTTP client — loads timeout and retry thresholds from args/http_client.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple, Union

import yaml

_ARGS_DIR = Path(__file__).resolve().parents[2] / "args"

_DEFAULTS: dict = {
    "timeout": {"connect": 10, "read": 30},
    "max_retries": 3,
    "backoff_factor": 0.5,
    "max_redirects": 10,
}


def _load_config() -> dict:
    config_path = _ARGS_DIR / "http_client.yaml"
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return _DEFAULTS


_CFG = _load_config()


class BaseHTTPClient:
    """Shared base for REST API clients.

    Reads connect/read timeout from args/http_client.yaml unless the caller
    passes an explicit *timeout* override.
    """

    def __init__(
        self,
        timeout: Union[Tuple[int, int], int, None] = None,
    ) -> None:
        if timeout is None:
            t = _CFG.get("timeout", _DEFAULTS["timeout"])
            connect = int(t.get("connect", 10))
            read = int(t.get("read", 30))
            self.timeout: Union[Tuple[int, int], int] = (connect, read)
        else:
            self.timeout = timeout

        self.max_retries: int = int(_CFG.get("max_retries", _DEFAULTS["max_retries"]))
        self.max_redirects: int = int(_CFG.get("max_redirects", _DEFAULTS["max_redirects"]))
