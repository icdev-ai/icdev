# CUI // SP-CTI
"""Stream Buffer Service — coordinates fetchers, deduplicates, and stages to disk.

Polls social-media, satellite, and news APIs on a configurable cadence, writes
timestamped JSON batch files to ``data/osint_stream_buffer/``, and tracks
state (last-seen cursors, dedup hashes) in a small SQLite table.

NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest), SI-7 (integrity).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

# Worktree root is parents[3]; project root with args/ is parents[3] for the worktree.
_worktree_root = Path(__file__).resolve().parents[3]

# Ensure project root on path so canonical imports work when run standalone
if str(_worktree_root) not in sys.path:
    sys.path.insert(0, str(_worktree_root))

from src.ingestion.fetchers.osint_stream_fetcher import OSINTStreamFetcher, OSINTStreamFetchError
from src.ingestion.fetchers.satellite_stream_fetcher import SatelliteStreamFetcher, SatelliteStreamFetchError
from src.ingestion.fetchers.news_stream_fetcher import NewsStreamFetcher, NewsStreamFetchError

log = logging.getLogger(__name__)


def _load_buffer_cfg() -> Dict[str, Any]:
    cfg_path = _worktree_root / "args" / "osint_streaming_config.yaml"
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return data.get("buffer", {})
    return {}


_BUFFER_CFG = _load_buffer_cfg()
_DEFAULT_BUFFER_DIR = _worktree_root / _BUFFER_CFG.get("directory", "data/osint_stream_buffer")
_MAX_SIGNALS_PER_BATCH: int = int(_BUFFER_CFG.get("max_signals_per_batch", 500))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class StreamBufferService:
    """Live-stream ingestion buffer.

    Args:
        buffer_dir: Directory to write timestamped JSON batch files.
        db_path: Path to SQLite state DB (cursors + dedup hashes).
        config: Optional runtime overrides for fetcher parameters.
    """

    def __init__(
        self,
        buffer_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.buffer_dir = buffer_dir or _DEFAULT_BUFFER_DIR
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or {}

        # State DB for cursors and dedup
        if db_path:
            self.db_path = db_path
        else:
            self.db_path = _repo_root / "data" / "icdev.db"
        self._init_state_table()

    # ── state management ─────────────────────────────────────────────────────────

    def _init_state_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS osint_stream_state (
                    source TEXT PRIMARY KEY,
                    last_cursor TEXT,
                    seen_hashes TEXT,          -- JSON array of recent SHA-256 hashes
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def _load_state(self, source: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT last_cursor, seen_hashes FROM osint_stream_state WHERE source = ?",
                (source,),
            ).fetchone()
        if row:
            try:
                seen: Set[str] = set(json.loads(row[1] or "[]"))
            except json.JSONDecodeError:
                seen = set()
            return {"last_cursor": row[0], "seen_hashes": seen}
        return {"last_cursor": None, "seen_hashes": set()}

    def _save_state(self, source: str, cursor: Optional[str], seen_hashes: Set[str]) -> None:
        # Keep only the most recent 10 000 hashes to cap memory / DB growth
        kept = list(seen_hashes)[-10000:]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO osint_stream_state (source, last_cursor, seen_hashes)
                VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    last_cursor = excluded.last_cursor,
                    seen_hashes = excluded.seen_hashes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (source, cursor, json.dumps(kept)),
            )
            conn.commit()

    # ── core poll methods ──────────────────────────────────────────────────────

    def poll_osint(
        self,
        query: Optional[str] = None,
        max_results: int = 100,
    ) -> Dict[str, Any]:
        """Poll social-media stream and buffer to disk.

        Returns:
            Result dict with ``source``, ``fetched``, ``buffered``, ``file``,
            ``cursor``, ``errors``.
        """
        source_key = "osint_stream"
        state = self._load_state(source_key)
        fetcher = OSINTStreamFetcher(
            base_url=self.config.get("osint_base_url", "https://api.twitter.com/2"),
            bearer_token=self.config.get("x_bearer_token"),
        )

        items: List[Dict[str, Any]] = []
        errors: List[str] = []
        try:
            items = fetcher.fetch(
                query=query or self.config.get("osint_query"),
                since_id=state["last_cursor"],
                max_results=max_results,
            )
        except OSINTStreamFetchError as exc:
            errors.append(str(exc))

        newest_id: Optional[str] = None
        if items:
            newest_id = items[0].get("_stream_meta", {}).get("newest_id")

        result = self._buffer_items(source_key, items, state["seen_hashes"], newest_id)
        result["errors"] = errors
        return result

    def poll_satellite(
        self,
        collection: str = "SENTINEL-2",
        cloud_cover_pct: int = 20,
        max_results: int = 50,
        region: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Poll satellite imagery stream and buffer to disk.

        Returns:
            Result dict with ``source``, ``fetched``, ``buffered``, ``file``,
            ``cursor``, ``errors``.
        """
        source_key = "satellite_stream"
        state = self._load_state(source_key)
        fetcher = SatelliteStreamFetcher(
            base_url=self.config.get("satellite_base_url", "https://catalogue.dataspace.copernicus.eu/odata/v1"),
        )

        items: List[Dict[str, Any]] = []
        errors: List[str] = []
        try:
            items = fetcher.fetch(
                collection=collection,
                cloud_cover_pct=cloud_cover_pct,
                since=state["last_cursor"],
                max_results=max_results,
                region=region or self.config.get("satellite_region"),
            )
        except SatelliteStreamFetchError as exc:
            errors.append(str(exc))

        # Cursor = latest SensingTime from returned items
        latest_sensing: Optional[str] = None
        if items:
            latest_sensing = max(
                (i.get("SensingTime", "") for i in items),
                default="",
            ) or None

        result = self._buffer_items(source_key, items, state["seen_hashes"], latest_sensing)
        result["errors"] = errors
        return result

    def poll_news(
        self,
        query: Optional[str] = None,
        sources: Optional[str] = None,
        language: str = "en",
        max_results: int = 100,
    ) -> Dict[str, Any]:
        """Poll open-source news stream and buffer to disk.

        Returns:
            Result dict with ``source``, ``fetched``, ``buffered``, ``file``,
            ``cursor``, ``errors``.
        """
        source_key = "news_stream"
        state = self._load_state(source_key)
        fetcher = NewsStreamFetcher(
            base_url=self.config.get("news_base_url", "https://newsapi.org/v2"),
            api_key=self.config.get("newsapi_key"),
        )

        items: List[Dict[str, Any]] = []
        errors: List[str] = []
        try:
            items = fetcher.fetch(
                query=query or self.config.get("news_query"),
                sources=sources or self.config.get("news_sources"),
                language=language,
                since=state["last_cursor"],
                max_results=max_results,
            )
        except NewsStreamFetchError as exc:
            errors.append(str(exc))

        # Cursor = latest publishedAt from returned items
        latest_pub: Optional[str] = None
        if items:
            latest_pub = max(
                (i.get("publishedAt", "") or i.get("seendate", "") for i in items),
                default="",
            ) or None

        result = self._buffer_items(source_key, items, state["seen_hashes"], latest_pub)
        result["errors"] = errors
        return result

    # ── buffer writer ──────────────────────────────────────────────────────────

    def _buffer_items(
        self,
        source_key: str,
        items: List[Dict[str, Any]],
        seen_hashes: Set[str],
        cursor: Optional[str],
    ) -> Dict[str, Any]:
        """Deduplicate and write items to a timestamped JSON batch file."""
        if not items:
            return {
                "source": source_key,
                "fetched": 0,
                "buffered": 0,
                "file": None,
                "cursor": cursor,
            }

        new_items: List[Dict[str, Any]] = []
        new_hashes: Set[str] = set()
        for item in items:
            # Deterministic hash on stable fields
            hash_input = json.dumps(item, sort_keys=True, ensure_ascii=True)
            h = _sha256(hash_input)
            if h in seen_hashes or h in new_hashes:
                continue
            new_hashes.add(h)
            new_items.append(item)
            if len(new_items) >= _MAX_SIGNALS_PER_BATCH:
                break

        if not new_items:
            return {
                "source": source_key,
                "fetched": len(items),
                "buffered": 0,
                "file": None,
                "cursor": cursor,
            }

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_file = self.buffer_dir / f"{source_key}_{ts}.json"
        payload = {
            "source": source_key,
            "signals": new_items,
            "count": len(new_items),
            "cursor": cursor,
            "buffered_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        log.info("Buffered %d %s signal(s) → %s", len(new_items), source_key, out_file.name)

        # Update state
        seen_hashes.update(new_hashes)
        self._save_state(source_key, cursor, seen_hashes)

        return {
            "source": source_key,
            "fetched": len(items),
            "buffered": len(new_items),
            "file": str(out_file),
            "cursor": cursor,
        }

    def poll_all(self) -> List[Dict[str, Any]]:
        """Poll every configured stream in sequence.

        Returns:
            List of result dicts (one per stream).
        """
        results: List[Dict[str, Any]] = []
        if self.config.get("enable_osint", True):
            results.append(self.poll_osint())
        if self.config.get("enable_satellite", True):
            results.append(self.poll_satellite())
        if self.config.get("enable_news", True):
            results.append(self.poll_news())
        return results
