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

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.il5.ingestion import (
    IL5_CLASSIFICATION,
    IL5_IMPACT_LEVEL,
    ingest_il5_event,
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

    Returns a summary dict: {fetched, ingested, skipped, errors}.
    Failures on individual items are isolated — one bad record does not
    abort the rest of the batch.
    """
    fetched = ingested = skipped = 0
    errors: List[str] = []

    try:
        req = urllib.request.Request(
            feed_url,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        items: List[Dict[str, Any]] = json.loads(raw)
    except urllib.error.URLError as exc:
        log.warning("IL5 feed unreachable (%s): %s", feed_url, exc)
        return {"fetched": 0, "ingested": 0, "skipped": 0, "errors": [str(exc)]}
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("IL5 feed returned unparseable response: %s", exc)
        return {"fetched": 0, "ingested": 0, "skipped": 0, "errors": [str(exc)]}

    fetched = len(items)

    for item in items:
        try:
            source_id: str = str(item.get("source_id") or item.get("id") or "unknown")
            content: str = item.get("content") or json.dumps(item)
            published_raw: Optional[str] = item.get("published_at") or item.get(
                "source_published_at"
            )
            source_published_at: Optional[datetime] = None
            if published_raw:
                try:
                    source_published_at = datetime.fromisoformat(
                        published_raw.replace("Z", "+00:00")
                    )
                except ValueError:
                    log.debug("Could not parse published_at %r", published_raw)

            metadata: Dict[str, Any] = {
                k: v
                for k, v in item.items()
                if k not in {"content", "source_id", "id", "published_at", "source_published_at"}
            }
            metadata.update(
                {
                    "classification": IL5_CLASSIFICATION,
                    "impact_level": IL5_IMPACT_LEVEL,
                    "feed_url": feed_url,
                }
            )

            ingest_il5_event(
                source_id,
                content,
                source_published_at=source_published_at,
                metadata=metadata,
                db_path=db_path,
            )
            ingested += 1
        except Exception as exc:  # noqa: BLE001 — per-item isolation
            errors.append(f"{item.get('id', '?')}: {exc}")
            skipped += 1
            log.debug("Skipped IL5 feed item: %s", exc)

    return {
        "fetched": fetched,
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
