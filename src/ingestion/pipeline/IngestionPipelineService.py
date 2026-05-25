# CUI // SP-CTI
"""ICDEV™ Main Data Ingestion Pipeline Service.

Orchestrates fetcher → adapter → display for all supported ingestion
sources. When configured with an IL5 source, the pipeline triggers the
IL5 fetcher and adapter so new publications flow end-to-end into the
dashboard within the 30-second SLA.

NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest), SI-7 (integrity).

Usage::

    from src.ingestion.pipeline.IngestionPipelineService import IngestionPipelineService
    result = IngestionPipelineService.run_source({"source_type": "il5"})
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.il5.sla_handler import IL5PipelineTimer, check_il5_timeout

from src.ingestion.services.stream_buffer_service import StreamBufferService

log = logging.getLogger(__name__)


class PipelineConfigError(Exception):
    """Raised when the pipeline configuration is invalid."""


class PipelineRuntimeError(Exception):
    """Raised when a pipeline stage fails irrecoverably."""


class IngestionPipelineService:
    """Unified ingestion pipeline for ICDEV data sources.

    Args:
        config: Pipeline configuration dict. Required key:
            ``source_type`` — e.g. ``"il5"``.
        db_path: Optional DB path override (used in tests).
    """

    def __init__(
        self,
        config: Dict[str, Any],
        db_path: Optional[Path] = None,
    ) -> None:
        if not config or "source_type" not in config:
            raise PipelineConfigError("config must contain 'source_type'")
        self.config = config
        self.db_path = db_path
        self._results: List[Dict[str, Any]] = []

    @classmethod
    def run_source(
        cls,
        config: Dict[str, Any],
        db_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Convenience factory — instantiate and run in one call.

        Args:
            config: Pipeline configuration dict.
            db_path: Optional DB path override.

        Returns:
            Pipeline result dict with ``source_type``, ``status``, ``fetched``,
            ``ingested``, ``display_payload``, and timing info.
        """
        svc = cls(config, db_path=db_path)
        return svc.run()

    @classmethod
    def trigger_il5(
        cls,
        *,
        feed_url: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 50,
        db_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Trigger the IL5 ingestion pipeline for new publications.

        This is the canonical entry point used by the live ingestion service
        so that every poll runs through the full fetch → adapter → display
        flow end-to-end.

        Args:
            feed_url: Override the IL5 publication feed URL.
            since: ISO 8601 cursor for incremental fetch.
            limit: Max records to fetch.
            db_path: Optional DB path override.

        Returns:
            Pipeline result dict.
        """
        config: Dict[str, Any] = {"source_type": "il5", "limit": limit}
        if feed_url:
            config["feed_url"] = feed_url
        if since:
            config["since"] = since
        return cls.run_source(config, db_path=db_path)

    def run(self) -> Dict[str, Any]:
        """Execute the full ingestion pipeline for the configured source.

        Returns:
            Result dict with keys:
                - source_type (str)
                - status (str): "completed" | "partial" | "failed"
                - fetched (int)
                - ingested (int)
                - event_ids (list)
                - display_payload (str): JSON UI payload when display=True
                - started_at (str): ISO 8601 timestamp
                - completed_at (str): ISO 8601 timestamp
                - elapsed_ms (int)
                - sla_met (bool | None)
                - errors (list)
        """
        started_at = datetime.now(timezone.utc)
        source_type = self.config["source_type"]
        log.info("[pipeline] starting source=%s", source_type)

        try:
            if source_type == "il5":
                result = self._run_il5(started_at)
            elif source_type == "osint_stream":
                result = self._run_osint_stream(started_at)
            elif source_type == "satellite_stream":
                result = self._run_satellite_stream(started_at)
            elif source_type == "news_stream":
                result = self._run_news_stream(started_at)
            else:
                raise PipelineConfigError(f"unsupported source_type: {source_type}")
        except Exception as exc:
            log.error("[pipeline] source=%s failed: %s", source_type, exc)
            completed_at = datetime.now(timezone.utc)
            return {
                "source_type": source_type,
                "status": "failed",
                "fetched": 0,
                "ingested": 0,
                "event_ids": [],
                "display_payload": None,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "elapsed_ms": int((completed_at - started_at).total_seconds() * 1000),
                "sla_met": None,
                "errors": [str(exc)],
            }

        self._results.append(result)
        return result

    def _run_il5(self, started_at: datetime) -> Dict[str, Any]:
        """Execute the IL5 ingestion stage.

        Flow:
        1. IL5Fetcher fetches raw records from the publication feed.
        2. IL5Adapter persists each record via ``ingest_il5_event``.
        3. IL5Adapter retrieves persisted events and renders a UI payload.
        4. SLA is checked against the 30-second deadline.
        """
        from ..fetchers.il5_fetcher import IL5Fetcher, IL5FetchError
        from ..adapters.il5_adapter import IL5Adapter

        feed_url = self.config.get("feed_url", "http://localhost:5050/api/il5/feed")
        since = self.config.get("since")
        limit = self.config.get("limit", 50)
        component = self.config.get("component", "table")

        errors: List[str] = []
        fetched_count = 0
        ingested_count = 0
        event_ids: List[str] = []
        display_payload: Optional[str] = None

        with IL5PipelineTimer(label="il5_pipeline") as timer:
            # 1. Fetch
            try:
                fetcher = IL5Fetcher(feed_url=feed_url)
                records = fetcher.fetch(since=since, limit=limit)
                fetched_count = len(records)
                timer.check()
            except IL5FetchError as exc:
                errors.append(f"fetch: {exc}")
                records = []

            # 2. Ingest
            if records:
                try:
                    adapter = IL5Adapter(db_path=self.db_path)
                    event_ids = adapter.ingest(records)
                    ingested_count = len(event_ids)
                    timer.check()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"ingest: {exc}")

            # 3. Display payload
            if ingested_count > 0 or fetched_count > 0:
                try:
                    adapter = IL5Adapter(db_path=self.db_path)
                    events = adapter.get_events(since=since, limit=limit)
                    display_payload = adapter.render_ui(events, component=component)
                    timer.check()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"display: {exc}")

        completed_at = datetime.now(timezone.utc)
        elapsed_ms = int((completed_at - started_at).total_seconds() * 1000)

        # SLA check
        sla_met: Optional[bool] = None
        try:
            check_il5_timeout(started_at, label="il5_pipeline")
            sla_met = True
        except TimeoutError:
            sla_met = False

        status = "completed"
        if errors:
            status = "partial" if ingested_count > 0 else "failed"

        return {
            "source_type": "il5",
            "status": status,
            "fetched": fetched_count,
            "ingested": ingested_count,
            "event_ids": event_ids,
            "display_payload": display_payload,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "elapsed_ms": elapsed_ms,
            "sla_met": sla_met,
            "errors": errors,
        }

    def _run_osint_stream(self, started_at: datetime) -> Dict[str, Any]:
        """Execute the social-media stream ingestion stage.

        Flow:
        1. StreamBufferService polls X API v2 for recent tweets.
        2. Deduplicated signals are written to ``data/osint_stream_buffer/``.
        3. Result dict returned with buffered count and file path.
        """
        from ..fetchers.osint_stream_fetcher import OSINTStreamFetchError

        buffer_dir = self.config.get("buffer_dir")
        db_path = self.db_path
        query = self.config.get("query")
        max_results = self.config.get("limit", 100)
        config: Dict[str, Any] = {
            "enable_osint": True,
            "enable_satellite": False,
            "enable_news": False,
            "osint_query": query,
        }

        errors: List[str] = []
        try:
            svc = StreamBufferService(
                buffer_dir=Path(buffer_dir) if buffer_dir else None,
                db_path=db_path,
                config=config,
            )
            result = svc.poll_osint(query=query, max_results=max_results)
        except OSINTStreamFetchError as exc:
            errors.append(f"fetch: {exc}")
            result = {
                "source": "osint_stream",
                "fetched": 0,
                "buffered": 0,
                "file": None,
                "cursor": None,
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"buffer: {exc}")
            result = {
                "source": "osint_stream",
                "fetched": 0,
                "buffered": 0,
                "file": None,
                "cursor": None,
            }

        completed_at = datetime.now(timezone.utc)
        elapsed_ms = int((completed_at - started_at).total_seconds() * 1000)

        status = "completed"
        if errors:
            status = "partial" if result.get("buffered", 0) > 0 else "failed"

        return {
            "source_type": "osint_stream",
            "status": status,
            "fetched": result.get("fetched", 0),
            "ingested": result.get("buffered", 0),
            "event_ids": [],
            "display_payload": result.get("file"),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "elapsed_ms": elapsed_ms,
            "sla_met": None,
            "errors": errors,
        }

    def _run_satellite_stream(self, started_at: datetime) -> Dict[str, Any]:
        """Execute the satellite imagery stream ingestion stage.

        Flow:
        1. StreamBufferService polls Copernicus OData for recent scenes.
        2. Deduplicated scenes are written to ``data/osint_stream_buffer/``.
        3. Result dict returned with buffered count and file path.
        """
        from ..fetchers.satellite_stream_fetcher import SatelliteStreamFetchError

        buffer_dir = self.config.get("buffer_dir")
        db_path = self.db_path
        collection = self.config.get("collection", "SENTINEL-2")
        cloud_cover = self.config.get("cloud_cover_pct", 20)
        max_results = self.config.get("limit", 50)
        region = self.config.get("region")
        config: Dict[str, Any] = {
            "enable_osint": False,
            "enable_satellite": True,
            "enable_news": False,
            "satellite_region": region,
        }

        errors: List[str] = []
        try:
            svc = StreamBufferService(
                buffer_dir=Path(buffer_dir) if buffer_dir else None,
                db_path=db_path,
                config=config,
            )
            result = svc.poll_satellite(
                collection=collection,
                cloud_cover_pct=cloud_cover,
                max_results=max_results,
                region=region,
            )
        except SatelliteStreamFetchError as exc:
            errors.append(f"fetch: {exc}")
            result = {
                "source": "satellite_stream",
                "fetched": 0,
                "buffered": 0,
                "file": None,
                "cursor": None,
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"buffer: {exc}")
            result = {
                "source": "satellite_stream",
                "fetched": 0,
                "buffered": 0,
                "file": None,
                "cursor": None,
            }

        completed_at = datetime.now(timezone.utc)
        elapsed_ms = int((completed_at - started_at).total_seconds() * 1000)

        status = "completed"
        if errors:
            status = "partial" if result.get("buffered", 0) > 0 else "failed"

        return {
            "source_type": "satellite_stream",
            "status": status,
            "fetched": result.get("fetched", 0),
            "ingested": result.get("buffered", 0),
            "event_ids": [],
            "display_payload": result.get("file"),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "elapsed_ms": elapsed_ms,
            "sla_met": None,
            "errors": errors,
        }

    def _run_news_stream(self, started_at: datetime) -> Dict[str, Any]:
        """Execute the open-source news stream ingestion stage.

        Flow:
        1. StreamBufferService polls NewsAPI / GDELT for recent articles.
        2. Deduplicated articles are written to ``data/osint_stream_buffer/``.
        3. Result dict returned with buffered count and file path.
        """
        from ..fetchers.news_stream_fetcher import NewsStreamFetchError

        buffer_dir = self.config.get("buffer_dir")
        db_path = self.db_path
        query = self.config.get("query")
        sources = self.config.get("sources")
        language = self.config.get("language", "en")
        max_results = self.config.get("limit", 100)
        config: Dict[str, Any] = {
            "enable_osint": False,
            "enable_satellite": False,
            "enable_news": True,
            "news_query": query,
            "news_sources": sources,
        }

        errors: List[str] = []
        try:
            svc = StreamBufferService(
                buffer_dir=Path(buffer_dir) if buffer_dir else None,
                db_path=db_path,
                config=config,
            )
            result = svc.poll_news(
                query=query,
                sources=sources,
                language=language,
                max_results=max_results,
            )
        except NewsStreamFetchError as exc:
            errors.append(f"fetch: {exc}")
            result = {
                "source": "news_stream",
                "fetched": 0,
                "buffered": 0,
                "file": None,
                "cursor": None,
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"buffer: {exc}")
            result = {
                "source": "news_stream",
                "fetched": 0,
                "buffered": 0,
                "file": None,
                "cursor": None,
            }

        completed_at = datetime.now(timezone.utc)
        elapsed_ms = int((completed_at - started_at).total_seconds() * 1000)

        status = "completed"
        if errors:
            status = "partial" if result.get("buffered", 0) > 0 else "failed"

        return {
            "source_type": "news_stream",
            "status": status,
            "fetched": result.get("fetched", 0),
            "ingested": result.get("buffered", 0),
            "event_ids": [],
            "display_payload": result.get("file"),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "elapsed_ms": elapsed_ms,
            "sla_met": None,
            "errors": errors,
        }
