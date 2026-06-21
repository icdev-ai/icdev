# CUI // SP-CTI
"""MeshProvider — abstract base class for all conflict mesh data providers.

Each provider wraps a data source (DataBridge connector, REST API, etc.)
and exposes a uniform interface: fetch() + normalize().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class MeshProvider(ABC):
    """Abstract base for all conflict mesh data providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique identifier for this provider (e.g. 'acled', 'gdelt')."""

    @abstractmethod
    def fetch(self, since_date: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch normalized conflict events from the upstream source.

        Args:
            since_date: ISO date string (YYYY-MM-DD) for incremental fetch.
            limit: Maximum number of events to return.

        Returns:
            List of canonical event dicts with keys:
              id, source, event_type, headline, event_date, geometry, metadata.
        """

    def normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a raw upstream record to the canonical event schema.

        Subclasses override this when fetch() returns raw upstream records
        rather than pre-normalized dicts.
        """
        return raw
