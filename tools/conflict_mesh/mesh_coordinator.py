# CUI // SP-CTI
"""MeshCoordinator — federated data mesh orchestrator.

Pulls conflict event data from all registered MeshProviders, deduplicates
by event id, and returns a unified sorted event stream.

Usage:
    from tools.conflict_mesh.providers.acled_provider import ACLEDProvider
    from tools.conflict_mesh.providers.gdelt_provider import GDELTProvider
    from tools.conflict_mesh.providers.reliefweb_provider import ReliefWebProvider
    from tools.conflict_mesh.mesh_coordinator import MeshCoordinator

    coord = MeshCoordinator([ACLEDProvider(), GDELTProvider(), ReliefWebProvider()])
    events = coord.fetch_all(since_date="2024-01-01", limit_per_provider=100)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools.conflict_mesh.providers.base import MeshProvider
from tools.logging.icdev_logger import get_logger

log = get_logger("conflict_mesh.coordinator")


class MeshCoordinator:
    """Orchestrates federated data pull across multiple conflict data providers."""

    def __init__(self, providers: List[MeshProvider]) -> None:
        self._providers = providers

    def fetch_all(
        self,
        since_date: Optional[str] = None,
        limit_per_provider: int = 100,
    ) -> List[Dict[str, Any]]:
        """Pull from all providers, deduplicate by event id, return sorted stream.

        Events from a failing provider are logged and skipped; the coordinator
        continues with remaining providers so one bad source never blocks others.

        Args:
            since_date: ISO date (YYYY-MM-DD) for incremental fetch.
            limit_per_provider: Max events to fetch from each provider.

        Returns:
            Deduplicated list of canonical event dicts, newest first.
        """
        seen_ids: set = set()
        all_events: List[Dict[str, Any]] = []

        for provider in self._providers:
            try:
                events = provider.fetch(since_date=since_date, limit=limit_per_provider)
                for evt in events:
                    evt_id = evt.get("id")
                    if evt_id and evt_id in seen_ids:
                        continue
                    if evt_id:
                        seen_ids.add(evt_id)
                    all_events.append(evt)
                log.debug(
                    "Provider %s returned %d events", provider.provider_name, len(events)
                )
            except Exception as exc:
                log.warning(
                    "Provider %s failed: %s — skipping", provider.provider_name, exc
                )

        # Sort newest first (None dates sort last)
        all_events.sort(
            key=lambda e: e.get("event_date") or "",
            reverse=True,
        )
        return all_events
