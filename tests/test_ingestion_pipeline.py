# CUI // SP-CTI
"""Integration tests for the ICDEV™ ingestion pipeline service.

Validates that IngestionPipelineService.run_source() correctly orchestrates
the IL5 fetcher → adapter → display flow end-to-end.

NIST 800-53: AU-2, AU-12, SC-28, SI-7.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.pipeline.IngestionPipelineService import (
    IngestionPipelineService,
    PipelineConfigError,
    PipelineRuntimeError,
)


class TestIngestionPipelineService:
    """End-to-end pipeline tests with mocked external dependencies."""

    def test_run_source_requires_source_type(self):
        with pytest.raises(PipelineConfigError):
            IngestionPipelineService.run_source({})

    def test_run_source_rejects_unsupported_type(self):
        result = IngestionPipelineService.run_source({"source_type": "unknown"})
        assert result["status"] == "failed"
        assert any("unsupported source_type" in e for e in result["errors"])

    def test_il5_pipeline_completed_with_mocked_feed(self, tmp_path):
        """Acceptance: IL5 source config successfully ingests and displays end-to-end."""
        db_path = tmp_path / "test.db"

        mock_records = [
            {
                "source_id": "pub-001",
                "content": "classified IL5 payload",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "metadata": {"feed": "test"},
            },
            {
                "source_id": "pub-002",
                "content": "second payload",
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        mock_events = [
            {
                "id": "evt-001",
                "source_id": "pub-001",
                "classification": "CUI",
                "impact_level": "IL5",
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "display_latency_s": 2.5,
                "sla_met": 1,
                "metadata": json.dumps({"feed": "test"}),
            },
            {
                "id": "evt-002",
                "source_id": "pub-002",
                "classification": "CUI",
                "impact_level": "IL5",
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "display_latency_s": 3.0,
                "sla_met": 1,
                "metadata": json.dumps({}),
            },
        ]

        display_json = json.dumps(
            {
                "component": "table",
                "classification": "CUI",
                "impact_level": "IL5",
                "sla_threshold_s": 30,
                "summary": {"total": 2, "sla_met": 2, "sla_violated": 0, "sla_unknown": 0, "compliance_pct": 100.0},
                "table": {"columns": ["id", "source_id"], "rows": mock_events},
            }
        )

        with patch("src.ingestion.fetchers.il5_fetcher.IL5Fetcher") as MockFetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = mock_records
            MockFetcher.return_value = mock_fetcher

            with patch("src.ingestion.adapters.il5_adapter.IL5Adapter") as MockAdapter:
                mock_adapter = MagicMock()
                mock_adapter.ingest.return_value = ["evt-001", "evt-002"]
                mock_adapter.get_events.return_value = mock_events
                mock_adapter.render_ui.return_value = display_json
                MockAdapter.return_value = mock_adapter

                result = IngestionPipelineService.run_source(
                    {"source_type": "il5"},
                    db_path=db_path,
                )

        assert result["source_type"] == "il5"
        assert result["status"] == "completed"
        assert result["fetched"] == 2
        assert result["ingested"] == 2
        assert result["event_ids"] == ["evt-001", "evt-002"]
        assert result["display_payload"] == display_json
        assert result["errors"] == []
        assert result["sla_met"] is True
        assert result["elapsed_ms"] >= 0
        assert "started_at" in result
        assert "completed_at" in result

        # Verify the adapter was called with the correct db_path
        MockAdapter.assert_called_with(db_path=db_path)
        mock_adapter.ingest.assert_called_once_with(mock_records)
        mock_adapter.get_events.assert_called_once()
        mock_adapter.render_ui.assert_called_once()

    def test_il5_pipeline_failed_when_ingest_fails(self, tmp_path):
        """If ingest fails after a successful fetch and nothing ingested, status is failed."""
        db_path = tmp_path / "test.db"
        mock_records = [{"source_id": "bad", "content": "x"}]

        with patch("src.ingestion.fetchers.il5_fetcher.IL5Fetcher") as MockFetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = mock_records
            MockFetcher.return_value = mock_fetcher

            with patch("src.ingestion.adapters.il5_adapter.IL5Adapter") as MockAdapter:
                mock_adapter = MagicMock()
                mock_adapter.ingest.side_effect = RuntimeError("DB locked")
                MockAdapter.return_value = mock_adapter

                result = IngestionPipelineService.run_source(
                    {"source_type": "il5"},
                    db_path=db_path,
                )

        assert result["status"] == "failed"
        assert result["fetched"] == 1
        assert result["ingested"] == 0
        assert any("DB locked" in e for e in result["errors"])

    def test_il5_pipeline_failed_when_fetch_fails(self, tmp_path):
        """If fetch fails, status is failed and display is None."""
        db_path = tmp_path / "test.db"

        with patch("src.ingestion.fetchers.il5_fetcher.IL5Fetcher") as MockFetcher:
            from src.ingestion.fetchers.il5_fetcher import IL5FetchError

            mock_fetcher = MagicMock()
            mock_fetcher.fetch.side_effect = IL5FetchError("feed down")
            MockFetcher.return_value = mock_fetcher

            result = IngestionPipelineService.run_source(
                {"source_type": "il5"},
                db_path=db_path,
            )

        assert result["status"] == "failed"
        assert result["fetched"] == 0
        assert result["ingested"] == 0
        assert result["display_payload"] is None
        assert any("feed down" in e for e in result["errors"])

    def test_il5_pipeline_with_custom_feed_url(self, tmp_path):
        """Custom feed_url from config is passed to IL5Fetcher."""
        db_path = tmp_path / "test.db"
        custom_url = "http://example.com/api/il5/feed"

        with patch("src.ingestion.fetchers.il5_fetcher.IL5Fetcher") as MockFetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = []
            MockFetcher.return_value = mock_fetcher

            IngestionPipelineService.run_source(
                {"source_type": "il5", "feed_url": custom_url},
                db_path=db_path,
            )

            MockFetcher.assert_called_once_with(feed_url=custom_url)

    def test_osint_stream_pipeline_completed(self, tmp_path):
        """Acceptance: osint_stream config successfully buffers signals to disk."""
        db_path = tmp_path / "test.db"
        buffer_dir = tmp_path / "stream_buffer"

        with patch(
            "src.ingestion.pipeline.IngestionPipelineService.StreamBufferService"
        ) as MockBuf:
            mock_buf = MagicMock()
            mock_buf.poll_osint.return_value = {
                "source": "osint_stream",
                "fetched": 3,
                "buffered": 3,
                "file": str(buffer_dir / "osint_stream_test.json"),
                "cursor": "c1",
            }
            MockBuf.return_value = mock_buf

            result = IngestionPipelineService.run_source(
                {"source_type": "osint_stream", "query": "ukraine", "limit": 50},
                db_path=db_path,
            )

        assert result["source_type"] == "osint_stream"
        assert result["status"] == "completed"
        assert result["fetched"] == 3
        assert result["ingested"] == 3
        assert result["display_payload"] == str(buffer_dir / "osint_stream_test.json")
        assert result["errors"] == []

    def test_satellite_stream_pipeline_completed(self, tmp_path):
        """Acceptance: satellite_stream config successfully buffers scenes to disk."""
        db_path = tmp_path / "test.db"
        buffer_dir = tmp_path / "stream_buffer"

        with patch(
            "src.ingestion.pipeline.IngestionPipelineService.StreamBufferService"
        ) as MockBuf:
            mock_buf = MagicMock()
            mock_buf.poll_satellite.return_value = {
                "source": "satellite_stream",
                "fetched": 5,
                "buffered": 5,
                "file": str(buffer_dir / "satellite_stream_test.json"),
                "cursor": "2026-05-16T10:00:00Z",
            }
            MockBuf.return_value = mock_buf

            result = IngestionPipelineService.run_source(
                {"source_type": "satellite_stream", "collection": "SENTINEL-2", "limit": 25},
                db_path=db_path,
            )

        assert result["source_type"] == "satellite_stream"
        assert result["status"] == "completed"
        assert result["fetched"] == 5
        assert result["ingested"] == 5
        assert result["display_payload"] == str(buffer_dir / "satellite_stream_test.json")

    def test_news_stream_pipeline_completed(self, tmp_path):
        """Acceptance: news_stream config successfully buffers articles to disk."""
        db_path = tmp_path / "test.db"
        buffer_dir = tmp_path / "stream_buffer"

        with patch(
            "src.ingestion.pipeline.IngestionPipelineService.StreamBufferService"
        ) as MockBuf:
            mock_buf = MagicMock()
            mock_buf.poll_news.return_value = {
                "source": "news_stream",
                "fetched": 10,
                "buffered": 10,
                "file": str(buffer_dir / "news_stream_test.json"),
                "cursor": "2026-05-16T12:00:00Z",
            }
            MockBuf.return_value = mock_buf

            result = IngestionPipelineService.run_source(
                {"source_type": "news_stream", "query": "cyber", "limit": 100},
                db_path=db_path,
            )

        assert result["source_type"] == "news_stream"
        assert result["status"] == "completed"
        assert result["fetched"] == 10
        assert result["ingested"] == 10
        assert result["display_payload"] == str(buffer_dir / "news_stream_test.json")

    def test_osint_stream_pipeline_failed_on_fetch_error(self, tmp_path):
        """If stream fetch fails, status is failed and display_payload is None."""
        db_path = tmp_path / "test.db"

        with patch(
            "src.ingestion.pipeline.IngestionPipelineService.StreamBufferService"
        ) as MockBuf:
            mock_buf = MagicMock()
            from src.ingestion.fetchers.osint_stream_fetcher import OSINTStreamFetchError
            mock_buf.poll_osint.side_effect = OSINTStreamFetchError("rate limited")
            MockBuf.return_value = mock_buf

            result = IngestionPipelineService.run_source(
                {"source_type": "osint_stream"},
                db_path=db_path,
            )

        assert result["status"] == "failed"
        assert result["fetched"] == 0
        assert result["ingested"] == 0
        assert result["display_payload"] is None
        assert any("rate limited" in e for e in result["errors"])
