# CUI // SP-CTI
"""Unit tests for OSINTStreamFetcher (X API v2 social-media stream)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.fetchers.osint_stream_fetcher import (
    OSINTStreamFetcher,
    OSINTStreamFetchError,
)


class TestOSINTStreamFetcher:
    """Tests for social-media stream fetcher with mocked urllib."""

    def test_fetch_success_with_mocked_response(self):
        fetcher = OSINTStreamFetcher(base_url="http://mock-x", bearer_token="test-token")

        mock_data = {
            "data": [
                {
                    "id": "t1",
                    "text": "Tweet one",
                    "author_id": "u1",
                    "created_at": "2026-05-16T10:00:00.000Z",
                },
                {
                    "id": "t2",
                    "text": "Tweet two",
                    "author_id": "u2",
                    "created_at": "2026-05-16T11:00:00.000Z",
                },
            ],
            "meta": {"newest_id": "t2"},
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            items = fetcher.fetch(query="ukraine", max_results=10)

        assert len(items) == 2
        assert items[0]["id"] == "t1"
        assert items[1]["_stream_meta"]["source"] == "x_api_v2"
        assert items[1]["_stream_meta"]["newest_id"] == "t2"

    def test_fetch_returns_empty_when_no_bearer_token(self):
        fetcher = OSINTStreamFetcher(base_url="http://mock-x", bearer_token="")
        items = fetcher.fetch()
        assert items == []

    def test_fetch_raises_on_http_error(self):
        fetcher = OSINTStreamFetcher(base_url="http://mock-x", bearer_token="test-token")

        from urllib.error import HTTPError

        mock_err = HTTPError(
            url="http://mock-x",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=mock_err):
            with pytest.raises(OSINTStreamFetchError) as exc_info:
                fetcher.fetch()

        assert "429" in str(exc_info.value)

    def test_fetch_raises_on_json_decode_error(self):
        fetcher = OSINTStreamFetcher(base_url="http://mock-x", bearer_token="test-token")

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not-json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(OSINTStreamFetchError):
                fetcher.fetch()

    def test_fetch_passes_since_id(self):
        fetcher = OSINTStreamFetcher(base_url="http://mock-x", bearer_token="test-token")

        mock_data = {"data": [], "meta": {}}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            fetcher.fetch(since_id="abc123")

        req = mock_urlopen.call_args[0][0]
        assert "since_id=abc123" in req.full_url
