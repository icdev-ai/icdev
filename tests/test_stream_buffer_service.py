# CUI // SP-CTI
"""Unit tests for StreamBufferService — deduplication, state, and disk writes."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch


from src.ingestion.services.stream_buffer_service import StreamBufferService


class TestStreamBufferService:
    """Tests for live-stream buffer with mocked fetchers and temp directories."""

    def test_poll_osint_buffers_to_disk(self, tmp_path):
        db_path = tmp_path / "state.db"
        buffer_dir = tmp_path / "buffer"

        svc = StreamBufferService(buffer_dir=buffer_dir, db_path=db_path, config={})

        mock_items = [
            {"id": "t1", "text": "Tweet 1", "_stream_meta": {"newest_id": "t1"}},
            {"id": "t2", "text": "Tweet 2", "_stream_meta": {"newest_id": "t2"}},
        ]

        with patch(
            "src.ingestion.services.stream_buffer_service.OSINTStreamFetcher"
        ) as MockFetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = mock_items
            MockFetcher.return_value = mock_fetcher

            result = svc.poll_osint(max_results=10)

        assert result["source"] == "osint_stream"
        assert result["fetched"] == 2
        assert result["buffered"] == 2
        assert result["file"] is not None
        assert Path(result["file"]).exists()

        with open(result["file"], "r", encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["count"] == 2
        assert payload["source"] == "osint_stream"

    def test_poll_osint_deduplicates(self, tmp_path):
        db_path = tmp_path / "state.db"
        buffer_dir = tmp_path / "buffer"

        svc = StreamBufferService(buffer_dir=buffer_dir, db_path=db_path, config={})

        mock_items = [
            {"id": "t1", "text": "Tweet 1", "_stream_meta": {"newest_id": "t1"}},
        ]

        with patch(
            "src.ingestion.services.stream_buffer_service.OSINTStreamFetcher"
        ) as MockFetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = mock_items
            MockFetcher.return_value = mock_fetcher

            # First poll — should buffer
            result1 = svc.poll_osint(max_results=10)
            assert result1["buffered"] == 1

            # Second poll with identical item — should deduplicate
            result2 = svc.poll_osint(max_results=10)
            assert result2["buffered"] == 0

    def test_poll_satellite_buffers_to_disk(self, tmp_path):
        db_path = tmp_path / "state.db"
        buffer_dir = tmp_path / "buffer"

        svc = StreamBufferService(buffer_dir=buffer_dir, db_path=db_path, config={})

        mock_items = [
            {
                "Id": "s1",
                "Name": "Scene1",
                "SensingTime": "2026-05-16T10:00:00Z",
                "_stream_meta": {},
            },
        ]

        with patch(
            "src.ingestion.services.stream_buffer_service.SatelliteStreamFetcher"
        ) as MockFetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = mock_items
            MockFetcher.return_value = mock_fetcher

            result = svc.poll_satellite(max_results=10)

        assert result["source"] == "satellite_stream"
        assert result["fetched"] == 1
        assert result["buffered"] == 1
        assert result["file"] is not None

    def test_poll_news_buffers_to_disk(self, tmp_path):
        db_path = tmp_path / "state.db"
        buffer_dir = tmp_path / "buffer"

        svc = StreamBufferService(buffer_dir=buffer_dir, db_path=db_path, config={})

        mock_items = [
            {
                "title": "News 1",
                "url": "http://example.com/1",
                "publishedAt": "2026-05-16T10:00:00Z",
                "_stream_meta": {},
            },
        ]

        with patch(
            "src.ingestion.services.stream_buffer_service.NewsStreamFetcher"
        ) as MockFetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = mock_items
            MockFetcher.return_value = mock_fetcher

            result = svc.poll_news(max_results=10)

        assert result["source"] == "news_stream"
        assert result["fetched"] == 1
        assert result["buffered"] == 1
        assert result["file"] is not None

    def test_poll_all_runs_all_enabled_streams(self, tmp_path):
        db_path = tmp_path / "state.db"
        buffer_dir = tmp_path / "buffer"

        svc = StreamBufferService(
            buffer_dir=buffer_dir,
            db_path=db_path,
            config={"enable_osint": True, "enable_satellite": True, "enable_news": True},
        )

        with patch(
            "src.ingestion.services.stream_buffer_service.OSINTStreamFetcher"
        ) as MockOSF:
            mock_osf = MagicMock()
            mock_osf.fetch.return_value = [{"id": "t1", "_stream_meta": {}}]
            MockOSF.return_value = mock_osf

            with patch(
                "src.ingestion.services.stream_buffer_service.SatelliteStreamFetcher"
            ) as MockSat:
                mock_sat = MagicMock()
                mock_sat.fetch.return_value = [{"Id": "s1", "_stream_meta": {}}]
                MockSat.return_value = mock_sat

                with patch(
                    "src.ingestion.services.stream_buffer_service.NewsStreamFetcher"
                ) as MockNews:
                    mock_news = MagicMock()
                    mock_news.fetch.return_value = [{"title": "N1", "_stream_meta": {}}]
                    MockNews.return_value = mock_news

                    results = svc.poll_all()

        assert len(results) == 3
        sources = [r["source"] for r in results]
        assert "osint_stream" in sources
        assert "satellite_stream" in sources
        assert "news_stream" in sources

    def test_state_table_created_on_init(self, tmp_path):
        db_path = tmp_path / "state.db"
        buffer_dir = tmp_path / "buffer"

        StreamBufferService(buffer_dir=buffer_dir, db_path=db_path, config={})

        with sqlite3.connect(db_path) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()

        assert any("osint_stream_state" in t for t in tables)

    def test_cursor_persisted_between_polls(self, tmp_path):
        db_path = tmp_path / "state.db"
        buffer_dir = tmp_path / "buffer"

        svc = StreamBufferService(buffer_dir=buffer_dir, db_path=db_path, config={})

        mock_items = [
            {"id": "t1", "text": "Tweet 1", "_stream_meta": {"newest_id": "cursor-1"}},
        ]

        with patch(
            "src.ingestion.services.stream_buffer_service.OSINTStreamFetcher"
        ) as MockFetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = mock_items
            MockFetcher.return_value = mock_fetcher

            result = svc.poll_osint(max_results=10)
            assert result["cursor"] == "cursor-1"

            # Verify cursor is persisted
            state = svc._load_state("osint_stream")
            assert state["last_cursor"] == "cursor-1"

    def test_empty_fetch_returns_no_file(self, tmp_path):
        db_path = tmp_path / "state.db"
        buffer_dir = tmp_path / "buffer"

        svc = StreamBufferService(buffer_dir=buffer_dir, db_path=db_path, config={})

        with patch(
            "src.ingestion.services.stream_buffer_service.OSINTStreamFetcher"
        ) as MockFetcher:
            mock_fetcher = MagicMock()
            mock_fetcher.fetch.return_value = []
            MockFetcher.return_value = mock_fetcher

            result = svc.poll_osint(max_results=10)

        assert result["fetched"] == 0
        assert result["buffered"] == 0
        assert result["file"] is None
