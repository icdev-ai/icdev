# CUI // SP-CTI
"""OHC — OpsAdapter abstract base class.

All open source and CSP adapters extend this class. Pattern mirrors
tools/llm/provider.py (LLMProvider ABC).

Adapters MUST:
- Return available=False cleanly when their SDK is not installed
- Never raise ImportError to the caller
- Never make network calls in __init__ (lazy connection)
- Support --json output via to_dict() on all responses
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdapterHealth:
    """Health check result from an adapter."""
    available: bool
    adapter_name: str
    adapter_type: str       # oss | csp
    domain: str             # llmops | mlops | aiops
    version: str = ""
    latency_ms: int = 0
    error: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "adapter_name": self.adapter_name,
            "adapter_type": self.adapter_type,
            "domain": self.domain,
            "version": self.version,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "details": self.details,
        }


@dataclass
class AdapterResource:
    """A resource returned by list_resources()."""
    id: str
    name: str
    resource_type: str
    status: str = "unknown"
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "resource_type": self.resource_type,
            "status": self.status,
            "metadata": self.metadata,
        }


@dataclass
class AdapterMetrics:
    """Metrics bundle from get_metrics()."""
    adapter_name: str
    metric_name: str
    values: list[dict]       # [{timestamp, value, labels}]
    unit: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "adapter_name": self.adapter_name,
            "metric_name": self.metric_name,
            "values": self.values,
            "unit": self.unit,
            "error": self.error,
        }


class OpsAdapter(ABC):
    """Abstract base for all OHC adapters (OSS + CSP)."""

    ADAPTER_NAME: str = ""
    ADAPTER_TYPE: str = ""   # oss | csp
    DOMAIN: str = ""         # llmops | mlops | aiops

    # ── Required Interface ────────────────────────────────────────────────────

    @abstractmethod
    def available(self) -> bool:
        """Return True if the adapter SDK/CLI is installed and reachable."""

    @abstractmethod
    def health_check(self) -> AdapterHealth:
        """Probe the tool and return a health report."""

    @abstractmethod
    def list_resources(self, resource_type: str = "", **kwargs: Any) -> list[AdapterResource]:
        """List available resources (experiments, models, metrics, etc.)."""

    @abstractmethod
    def get_metrics(self, metric_name: str, **kwargs: Any) -> AdapterMetrics:
        """Fetch time-series or aggregate metrics from the tool."""

    # ── Optional Interface ────────────────────────────────────────────────────

    def push_event(self, event_type: str, payload: dict) -> dict:
        """Push an event to the tool (e.g., log a run, create incident).
        Default: no-op returning a stub.
        """
        return {"status": "not_implemented", "adapter": self.ADAPTER_NAME}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _stub_health(self, error: str) -> AdapterHealth:
        """Return a standardized unavailable health response."""
        return AdapterHealth(
            available=False,
            adapter_name=self.ADAPTER_NAME,
            adapter_type=self.ADAPTER_TYPE,
            domain=self.DOMAIN,
            error=error,
        )

    def _timed_probe(self, fn) -> tuple[Any, int]:
        """Run fn() and return (result, elapsed_ms)."""
        t0 = time.monotonic()
        result = fn()
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return result, elapsed_ms
