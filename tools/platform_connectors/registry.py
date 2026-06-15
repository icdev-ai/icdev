#!/usr/bin/env python3
# CUI // SP-CTI
"""Unified Platform Connector Registry — multi-backend routing with fallbacks.

Usage:
    registry = get_registry()
    result = registry.fetch("github", "machine learning ops", max_results=10)
    health = registry.doctor()

Routing:
    - Adapters per platform are sorted by priority (lower = first)
    - First adapter returning ok=True wins; others are tried as fallbacks
    - Errors from all adapters are collected and returned in metadata

Design references: D66 (ABC), D146 (circuit breaker), D-RES-3 (function registry).
"""

import os
from pathlib import Path
from typing import Any

from tools.platform_connectors.base import FetchResult, HealthResult, PlatformConnector

BASE_DIR = Path(__file__).resolve().parent.parent.parent

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _load_config() -> dict:
    config_path = BASE_DIR / "args" / "platform_connectors_config.yaml"
    if not _HAS_YAML or not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}


class PlatformConnectorRegistry:
    """Registry of all platform adapters with multi-backend routing.

    Adapters are grouped by platform name. On fetch(), the registry
    tries each adapter in priority order; the first successful result wins.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, list[PlatformConnector]] = {}
        self._config: dict = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._config = _load_config()
        self._register_defaults()
        self._loaded = True

    def _register_defaults(self) -> None:
        """Register all built-in platform adapters."""
        from tools.platform_connectors.adapters.github import GitHubRestAdapter
        from tools.platform_connectors.adapters.hackernews import HackerNewsAdapter
        from tools.platform_connectors.adapters.stackoverflow import StackOverflowAdapter
        from tools.platform_connectors.adapters.reddit import RedditJsonAdapter
        from tools.platform_connectors.adapters.youtube import (
            YouTubeDataApiAdapter,
            YouTubeYtDlpAdapter,
        )

        for adapter in [
            GitHubRestAdapter(),
            HackerNewsAdapter(),
            StackOverflowAdapter(),
            RedditJsonAdapter(),
            YouTubeDataApiAdapter(),
            YouTubeYtDlpAdapter(),
        ]:
            self._register(adapter)

    def _register(self, adapter: PlatformConnector) -> None:
        platform = adapter.platform
        if platform not in self._adapters:
            self._adapters[platform] = []
        self._adapters[platform].append(adapter)
        self._adapters[platform].sort(key=lambda a: a.priority)

    def register(self, adapter: PlatformConnector) -> None:
        """Register a custom adapter. Call after get_registry() to extend."""
        self._ensure_loaded()
        self._register(adapter)

    def platforms(self) -> list[str]:
        """Return names of all registered platforms."""
        self._ensure_loaded()
        return sorted(self._adapters.keys())

    def fetch(
        self,
        platform: str,
        query: str,
        **kwargs: Any,
    ) -> FetchResult:
        """Fetch from a platform, trying adapters in priority order.

        Args:
            platform: Platform name, e.g. "github", "hackernews".
            query: Search query or topic string.
            **kwargs: Passed to adapter.fetch() — max_results, since_days, etc.

        Returns:
            FetchResult from the first adapter that succeeds.
            On total failure, returns the last adapter's error result.
        """
        self._ensure_loaded()
        adapters = self._adapters.get(platform, [])
        if not adapters:
            return FetchResult(
                platform=platform,
                adapter_name="none",
                query=query,
                error=f"no_adapter_for_platform:{platform}",
            )

        last_result: FetchResult | None = None
        attempt_errors: list[str] = []

        for adapter in adapters:
            if not self._is_enabled(adapter):
                continue
            result = adapter.fetch(query, **kwargs)
            result.query = query
            if result.ok:
                return result
            attempt_errors.append(f"{adapter.name}:{result.error}")
            last_result = result

        if last_result is None:
            return FetchResult(
                platform=platform,
                adapter_name="none",
                query=query,
                error="all_adapters_disabled",
            )
        last_result.metadata["attempt_errors"] = attempt_errors
        return last_result

    def fetch_all(self, query: str, **kwargs: Any) -> dict[str, FetchResult]:
        """Fetch from all registered platforms concurrently (simple loop, no threads).

        Returns dict keyed by platform name.
        """
        self._ensure_loaded()
        results: dict[str, FetchResult] = {}
        for platform in self.platforms():
            results[platform] = self.fetch(platform, query, **kwargs)
        return results

    def doctor(self) -> dict[str, list[HealthResult]]:
        """Run health probes on all registered adapters.

        Returns dict keyed by platform, each value is a list of HealthResult
        (one per adapter backend registered for that platform).
        """
        self._ensure_loaded()
        report: dict[str, list[HealthResult]] = {}
        for platform, adapters in self._adapters.items():
            report[platform] = [a.health() for a in adapters]
        return report

    def _is_enabled(self, adapter: PlatformConnector) -> bool:
        """Check per-adapter enabled flag from config (defaults True)."""
        adapters_cfg = self._config.get("adapters", {})
        adapter_cfg = adapters_cfg.get(adapter.name, {})
        return adapter_cfg.get("enabled", True)


_registry: PlatformConnectorRegistry | None = None


def get_registry() -> PlatformConnectorRegistry:
    """Return the singleton PlatformConnectorRegistry, initializing on first call."""
    global _registry
    if _registry is None:
        _registry = PlatformConnectorRegistry()
    return _registry
