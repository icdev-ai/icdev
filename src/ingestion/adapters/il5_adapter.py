# CUI // SP-CTI
"""ICDEV™ IL5 Adapter — transforms and persists raw IL5 records.

Accepts raw record dicts from the IL5 fetcher and persists them via the
core IL5 ingestion module. Also supports UI formatting via the display
service so the pipeline can return a display-ready payload.

NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest), SI-7 (integrity).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.il5.ingestion import ingest_il5_event, get_il5_events
from tools.il5.il5_display_service import render_il5_ui

log = logging.getLogger(__name__)


class IL5AdapterError(Exception):
    """Raised when an IL5 record cannot be ingested."""


class IL5Adapter:
    """Adapter that persists raw IL5 records and optionally returns UI payload.

    Args:
        db_path: Override the default database path (used in tests).
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path

    def ingest(self, records: List[Dict[str, Any]]) -> List[str]:
        """Persist a batch of raw IL5 records.

        Each record is hashed and stored via ``ingest_il5_event``. Failures
        on individual items are isolated so one bad record does not abort
        the batch.

        Args:
            records: Raw record dicts from ``IL5Fetcher.fetch()``.

        Returns:
            List of successfully ingested event IDs.
        """
        event_ids: List[str] = []
        for item in records:
            try:
                source_id = str(item.get("source_id") or item.get("id") or "unknown")
                content = item.get("content") or json.dumps(item)
                published_raw = item.get("published_at") or item.get("source_published_at")
                source_published_at: Optional[datetime] = None
                if published_raw:
                    try:
                        source_published_at = datetime.fromisoformat(
                            str(published_raw).replace("Z", "+00:00")
                        )
                    except ValueError:
                        log.debug("Could not parse published_at %r", published_raw)

                metadata = item.get("metadata") or {}
                event_id = ingest_il5_event(
                    source_id,
                    content,
                    source_published_at=source_published_at,
                    metadata=metadata,
                    db_path=self.db_path,
                )
                event_ids.append(event_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("IL5 adapter skipped item: %s", exc)
        return event_ids

    def get_events(
        self,
        since: Optional[str] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Retrieve IL5 events for display.

        Args:
            since: ISO 8601 cursor.
            limit: Max rows to return.

        Returns:
            List of event dicts ordered newest-first.
        """
        return get_il5_events(since=since, limit=limit, db_path=self.db_path)

    def render_ui(
        self,
        events: List[Dict[str, Any]],
        *,
        component: str = "table",
    ) -> str:
        """Format events into a JSON UI payload string.

        Args:
            events: Event list from ``get_events()``.
            component: ``"table"`` or ``"chart"``.

        Returns:
            JSON-serialized UI response string.
        """
        return render_il5_ui(events, component=component)
