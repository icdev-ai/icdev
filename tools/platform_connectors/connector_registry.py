# CUI // SP-CTI
"""Multi-backend registry over the ``adapters/`` platform connectors.

Why this module exists
----------------------
``tools/platform_connectors/`` was internally forked into two incompatible
designs that both call themselves the registry:

* ``registry.py`` — a ``Protocol``-based design whose adapters return
  ``list[dict]`` from ``fetch(query, limit)``. Used by ``web_scanner`` and
  ``creative/source_scanner``.
* ``base.py`` + ``adapters/`` — an ABC-based design (``PlatformConnector``)
  whose adapters return a ``FetchResult`` from ``fetch(query, **kwargs)`` and a
  ``HealthResult`` from ``health()``, with ``name``/``platform``/``priority`` so
  several backends can serve one platform in fallback order.

``connector_cli.py`` was written against the second design but imported
``get_registry`` from the first, which never defined it. That single missing
name raised ``ImportError`` at module import, so all three MCP tools built on
it — ``platform_connector_fetch``, ``platform_connector_fetch_all`` and
``platform_connector_doctor`` — failed on every call. They have never executed.

This module supplies the missing piece against the ABC design (the one the CLI
actually speaks), rather than rewriting the CLI to the other one. Both designs
keep working; nothing that imports ``registry.py`` is touched.

Multi-backend routing: adapters sharing a ``platform`` are tried in ascending
``priority`` and the first success wins, which is the behaviour the adapters'
own docstrings describe.
"""
from __future__ import annotations

import threading
from typing import Any

from tools.logging.icdev_logger import get_logger
from tools.platform_connectors.base import FetchResult, HealthResult, PlatformConnector

logger = get_logger(__name__)


class ConnectorRegistry:
    """Groups ``PlatformConnector`` adapters by platform, in priority order."""

    def __init__(self) -> None:
        self._adapters: dict[str, list[PlatformConnector]] = {}

    # -- registration ------------------------------------------------------

    def register(self, adapter: PlatformConnector) -> None:
        platform = getattr(adapter, "platform", "") or "unknown"
        bucket = self._adapters.setdefault(platform, [])
        if any(a.name == adapter.name for a in bucket):
            return
        bucket.append(adapter)
        bucket.sort(key=lambda a: getattr(a, "priority", 100))
        logger.debug(
            "platform_connectors: registered %s (platform=%s, priority=%s)",
            adapter.name, platform, getattr(adapter, "priority", 100),
        )

    def platforms(self) -> list[str]:
        return sorted(self._adapters)

    def adapters_for(self, platform: str) -> list[PlatformConnector]:
        return list(self._adapters.get(platform, []))

    # -- fetching ----------------------------------------------------------

    def fetch(self, platform: str, query: str, **kwargs: Any) -> FetchResult:
        """Try each backend for *platform* in priority order; first success wins.

        Returns a failed ``FetchResult`` rather than raising when the platform is
        unknown or every backend fails — the CLI and the MCP tools treat a
        result object as their contract, and an exception there would surface as
        a tool crash instead of a reportable failure.
        """
        backends = self.adapters_for(platform)
        if not backends:
            return FetchResult(
                platform=platform, adapter_name="", query=query, items=[],
                error=f"no adapter registered for platform {platform!r}; "
                      f"available: {self.platforms()}",
            )

        last_error = ""
        for adapter in backends:
            try:
                result = adapter.fetch(query, **kwargs)
            except Exception as exc:  # noqa: BLE001 — try the next backend
                last_error = f"{adapter.name}: {exc}"
                logger.warning("platform_connectors: %s raised: %s", adapter.name, exc)
                continue
            if getattr(result, "ok", False):
                return result
            last_error = getattr(result, "error", "") or f"{adapter.name}: empty result"

        return FetchResult(
            platform=platform, adapter_name="", query=query, items=[],
            error=last_error or "all backends failed",
        )

    def fetch_all(self, query: str, **kwargs: Any) -> list[FetchResult]:
        """Fetch *query* from every registered platform."""
        return [self.fetch(platform, query, **kwargs) for platform in self.platforms()]

    # -- health ------------------------------------------------------------

    def doctor(self) -> dict[str, list[HealthResult]]:
        """Probe every adapter. A raising adapter is reported, never fatal."""
        report: dict[str, list[HealthResult]] = {}
        for platform, backends in self._adapters.items():
            results: list[HealthResult] = []
            for adapter in backends:
                try:
                    results.append(adapter.health())
                except Exception as exc:  # noqa: BLE001
                    results.append(HealthResult(
                        adapter_name=adapter.name, platform=platform,
                        status="unreachable", message=str(exc),
                    ))
            report[platform] = results
        return report


_registry: ConnectorRegistry | None = None
_lock = threading.Lock()


#: Adapter modules shipped under ``adapters/``. Classes are discovered by
#: subclass scan rather than by name: youtube.py alone defines two backends
#: (data-api and yt-dlp), and naming them here would silently drop one and
#: quietly defeat the multi-backend fallback the design exists for.
_ADAPTER_MODULES: tuple[str, ...] = (
    "github", "hackernews", "reddit", "stackoverflow", "youtube",
)


def _load_default_adapters(registry: ConnectorRegistry) -> None:
    """Register every concrete adapter in ``adapters/``.

    One unimportable adapter (a missing optional dependency, say) must not take
    the whole registry down with it — that failure shape is exactly what this
    module was written to fix.
    """
    from importlib import import_module

    for module_name in _ADAPTER_MODULES:
        try:
            module = import_module(f"tools.platform_connectors.adapters.{module_name}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("platform_connectors: adapter %s unavailable: %s", module_name, exc)
            continue

        found = 0
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, PlatformConnector)
                and obj is not PlatformConnector
                and not getattr(obj, "__abstractmethods__", None)
                and obj.__module__ == module.__name__  # skip re-exports
            ):
                try:
                    registry.register(obj())
                    found += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "platform_connectors: could not instantiate %s: %s",
                        obj.__name__, exc,
                    )
        if not found:
            logger.warning("platform_connectors: no connector class in %s", module_name)


def get_registry() -> ConnectorRegistry:
    """Return the process-wide registry, loading default adapters on first use."""
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                registry = ConnectorRegistry()
                _load_default_adapters(registry)
                _registry = registry
    return _registry


def reset_registry() -> None:
    """Drop the cached registry (tests)."""
    global _registry
    with _lock:
        _registry = None
