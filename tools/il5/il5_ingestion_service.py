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

    Uses ``urllib.request`` (stdlib) to poll the feed endpoint, parses
    the JSON response, and delegates persistence to
    ``ingest_il5_event`` so IL5 ingestion is self-contained.

    Returns a summary dict: {fetched, ingested, skipped, errors}.
    """
    import json as _json
    import urllib.request as _request
    import urllib.error as _error

    from tools.il5.ingestion import ingest_il5_event

    fetched = 0
    ingested = 0
    errors: List[str] = []

    try:
        req = _request.Request(
            feed_url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        with _request.urlopen(req, timeout=timeout) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except _error.HTTPError as exc:
        errors.append(f"HTTP {exc.code}: {exc.reason}")
        return {
            "fetched": 0,
            "ingested": 0,
            "skipped": 0,
            "errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        errors.append(str(exc))
        return {
            "fetched": 0,
            "ingested": 0,
            "skipped": 0,
            "errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    records: List[Dict[str, Any]] = payload if isinstance(payload, list) else []
    fetched = len(records)

    for rec in records:
        source_id = rec.get("source_id") or rec.get("id")
        content = rec.get("content") or rec.get("payload") or ""
        if not source_id or not content:
            continue
        published_raw = rec.get("source_published_at") or rec.get("published_at")
        source_published_at = None
        if published_raw:
            try:
                source_published_at = datetime.fromisoformat(
                    str(published_raw).replace("Z", "+00:00")
                )
            except ValueError:
                pass
        try:
            ingest_il5_event(
                str(source_id),
                str(content),
                source_published_at=source_published_at,
                metadata=rec.get("metadata"),
                db_path=db_path,
            )
            ingested += 1
        except Exception as exc:
            errors.append(str(exc))

    return {
        "fetched": fetched,
        "ingested": ingested,
        "skipped": fetched - ingested,
        "errors": errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
