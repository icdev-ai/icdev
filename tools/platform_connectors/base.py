#!/usr/bin/env python3
# CUI // SP-CTI
"""Base ABC for all platform connectors (D66 provider abstraction pattern).

Every platform adapter inherits from PlatformConnector and implements:
  fetch(query, **kwargs) -> list[dict]  — retrieve items matching query
  health()              -> dict         — probe reachability and auth

The registry router picks adapters in priority order, moving to the next
fallback on any non-success response (network error, auth failure, rate limit).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class FetchResult:
    """Normalized result from any platform adapter."""
    platform: str
    adapter_name: str
    query: str
    items: list = field(default_factory=list)
    total: int = 0
    error: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "adapter_name": self.adapter_name,
            "query": self.query,
            "items": self.items,
            "total": self.total,
            "error": self.error,
            "fetched_at": self.fetched_at,
            "metadata": self.metadata,
            "ok": self.ok,
        }


@dataclass
class HealthResult:
    """Health probe result for a single adapter backend."""
    adapter_name: str
    platform: str
    status: str  # "ok" | "degraded" | "unreachable" | "auth_error"
    latency_ms: float | None = None
    message: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "adapter_name": self.adapter_name,
            "platform": self.platform,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "checked_at": self.checked_at,
        }


class PlatformConnector(ABC):
    """Abstract base for all platform connectors.

    Subclasses set:
        name     — unique slug, e.g. "github_rest"
        platform — logical group, e.g. "github"
        priority — lower = preferred (0 = highest priority)
    """

    name: str = "unnamed"
    platform: str = "unknown"
    priority: int = 0

    @abstractmethod
    def fetch(self, query: str, **kwargs: Any) -> FetchResult:
        """Fetch items matching query from this platform.

        Args:
            query: Free-text search query or topic.
            **kwargs: Adapter-specific options (max_results, since_days, etc.)

        Returns:
            FetchResult with items list and error field.
        """

    @abstractmethod
    def health(self) -> HealthResult:
        """Probe reachability and authentication.

        Returns:
            HealthResult with status and latency_ms.
        """

    def _ok(self, items: list, **meta: Any) -> FetchResult:
        return FetchResult(
            platform=self.platform,
            adapter_name=self.name,
            query="",
            items=items,
            total=len(items),
            metadata=meta,
        )

    def _err(self, query: str, error: str) -> FetchResult:
        return FetchResult(
            platform=self.platform,
            adapter_name=self.name,
            query=query,
            error=error,
        )
