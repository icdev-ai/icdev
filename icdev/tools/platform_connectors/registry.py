# CUI // SP-CTI
"""Platform adapter registry (adapt-conn-01).

Adapters register by name; callers query by name via get_adapter().
All adapters must implement PlatformAdapter.fetch() and health_check().

Usage:
    from tools.platform_connectors.registry import get_adapter, list_adapters
    adapter = get_adapter("github")
    results = adapter.fetch("context compression", limit=10)
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
from typing import Any, Protocol, runtime_checkable

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = 10
_DEFAULT_UA = "ICDev-Platform-Connector/1.0 (air-gap-safe)"


def _safe_get(url: str, params: dict | None = None, headers: dict | None = None,
              timeout: int = _DEFAULT_TIMEOUT) -> tuple[Any, str | None]:
    """Shared HTTP GET helper with rate-limit + error handling.

    Returns (data_dict_or_list, error_code_or_None).
    """
    try:
        import requests
    except ImportError:
        return None, "requests_unavailable"
    hdrs = {"User-Agent": _DEFAULT_UA}
    if headers:
        hdrs.update(headers)
    try:
        resp = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        if resp.status_code == 429:
            return None, "rate_limited"
        if resp.status_code == 403:
            return None, "forbidden"
        if resp.status_code != 200:
            return None, f"http_{resp.status_code}"
        try:
            return resp.json(), None
        except Exception:
            return {"_raw": resp.text}, None
    except Exception as exc:
        return None, f"error:{exc}"


# Public alias so web_scanner.py / creative/source_scanner.py can import without
# reaching into a private name (adapt-conn-05/06).
safe_get = _safe_get


@runtime_checkable
class PlatformAdapter(Protocol):
    """Protocol every connector must satisfy."""
    name: str

    def fetch(self, query: str, limit: int = 30, **kwargs) -> list[dict]:
        """Fetch items matching query. Returns list of normalized dicts."""
        ...

    def health_check(self) -> dict:
        """Return {status: ok|error, latency_ms: float|None, error: str|None}."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, "PlatformAdapter"] = {}


def register(adapter: "PlatformAdapter") -> None:
    _REGISTRY[adapter.name] = adapter
    logger.debug("platform_connectors: registered %r", adapter.name)


def get_adapter(name: str) -> "PlatformAdapter":
    """Return a registered adapter by name, or raise KeyError."""
    if name not in _REGISTRY:
        # Lazy-load the standard adapters on first miss
        _ensure_defaults_loaded()
    if name not in _REGISTRY:
        raise KeyError(f"No platform adapter registered for {name!r}. Available: {list(_REGISTRY)}")
    return _REGISTRY[name]


def list_adapters() -> list[str]:
    _ensure_defaults_loaded()
    return sorted(_REGISTRY.keys())


def _ensure_defaults_loaded() -> None:
    if _REGISTRY:
        return
    try:
        from tools.platform_connectors.github import GitHubAdapter
        from tools.platform_connectors.reddit import RedditAdapter
        from tools.platform_connectors.hacker_news import HackerNewsAdapter
        register(GitHubAdapter())
        register(RedditAdapter())
        register(HackerNewsAdapter())
    except Exception as exc:
        logger.warning("platform_connectors: failed to load defaults: %s", exc)
