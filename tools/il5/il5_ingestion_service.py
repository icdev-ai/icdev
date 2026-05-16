# CUI // SP-CTI
"""ICDEV™ IL5 Ingestion Service — fetch and parse IL5 publication feed records.

Polls the configured IL5 source publication feed via HTTP, parses raw records,
and delegates persistence to the core ingestion module. This layer is
intentionally free of display logic: callers receive raw records and decide
how to present them.

Architecture note: HTTP polling at 3-second intervals (D103) keeps detection
latency well under the 30-second SLA defined in tools.il5.ingestion.

NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest), SI-7 (integrity).

Usage::

    from tools.il5.il5_ingestion_service import fetch_il5_data
    records = fetch_il5_data()
    for rec in records:
        print(rec["id"], rec["source_id"])
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.il5.ingestion import (
    get_il5_events,
)

log = logging.getLogger(__name__)

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FEED_URL = "http://localhost:5050/api/il5/feed"
_DEFAULT_TIMEOUT_S = 5
_DEFAULT_LIMIT = 50


def fetch_il5_data(
    *,
    feed_url: str = _DEFAULT_FEED_URL,
    since: Optional[str] = None,
    limit: int = _DEFAULT_LIMIT,
    timeout: int = _DEFAULT_TIMEOUT_S,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Fetch raw IL5 records from the source publication feed.

    Polls the feed endpoint for new records, persists any unseen items via
    ``ingest_il5_event``, then returns all matching records from the local
    store. No formatting or display logic is applied.

    Args:
        feed_url: URL of the IL5 publication feed. Defaults to the local
            ICDEV™ feed endpoint.
        since: ISO 8601 cursor; fetch only records published after this time.
        limit: Maximum number of records to return.
        timeout: HTTP request timeout in seconds.
        db_path: Override the default database path (used in tests).

    Returns:
        List of raw IL5 record dicts ordered newest-first, each containing
        at minimum: id, source_id, content_hash, classification, impact_level,
        ingested_at, source_published_at, display_latency_s, sla_met, metadata.
    """
    _poll_feed(feed_url=feed_url, timeout=timeout, db_path=db_path)
    return get_il5_events(since=since, limit=limit, db_path=db_path)


def _poll_feed(
    *,
    feed_url: str,
    timeout: int,
    db_path: Optional[Path],
) -> Dict[str, Any]:
    """Fetch items from the publication feed and persist new ones.

    Delegates the full fetch → adapter → display flow to
    ``IngestionPipelineService`` so IL5 ingestion runs through the
    canonical pipeline rather than bypassing it.

    Returns a summary dict: {fetched, ingested, skipped, errors}.
    """
    from src.ingestion.pipeline.IngestionPipelineService import IngestionPipelineService

    result = IngestionPipelineService.trigger_il5(
        feed_url=feed_url,
        limit=_DEFAULT_LIMIT,
        db_path=db_path,
    )
    return {
        "fetched": result.get("fetched", 0),
        "ingested": result.get("ingested", 0),
        "skipped": 0,
        "errors": result.get("errors", []),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
