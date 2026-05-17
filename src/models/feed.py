# CUI // SP-CTI
"""Shared data model for feed items across all intelligence clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class FeedItem:
    """Normalized feed item returned by OSINT, satellite, and diplomatic clients."""

    id: str
    source: str
    title: str
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> FeedItem:
        """Build a FeedItem from a JSON dict returned by an upstream API."""
        ts_raw = raw.get("timestamp")
        ts: datetime
        if ts_raw is None:
            ts = datetime.now(timezone.utc)
        elif isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
        else:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))

        return cls(
            id=str(raw.get("id", "")),
            source=str(raw.get("source", "")),
            title=str(raw.get("title", "")),
            content=str(raw.get("content", "")),
            timestamp=ts,
            url=str(raw.get("url", "")),
            metadata=raw.get("metadata") or {},
        )
