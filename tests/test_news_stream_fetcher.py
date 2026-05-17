# CUI // SP-CTI
"""Unit tests for NewsStreamFetcher (NewsAPI + GDELT fallback)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.fetchers.news_stream_fetcher import (
    NewsStreamFetcher,
    NewsStreamFetchError,
)


class TestNewsStreamFetcher:
    """Tests for open-source news stream fetcher with mocked urllib."""

    def test_fetch_newsapi_success(self):
        fetcher = NewsStreamFetcher(base_url="http://mock-newsapi", api_key="test-key")

        mock_data = {
            "status": "ok",
            "articles": [
                {
                    "title": "Article One",
                    "description": "Desc one",
                    "url": "http://example.com/1",
                    "publishedAt": "2026-05-16T10:00:00Z",
                    "source": {"name": "BBC"},
                },
                {
                    "title": "Article Two",
                    "description": "Desc two",
                    "url": "http://example.com/2",
                    "publishedAt": "2026-05-16T11:00:00Z",
                    "source": {"name": "Reuters"},
                },
            ],
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            items = fetcher.fetch(query="cyber", max_results=10)

        assert len(items) == 2
        assert items[0]["title"] == "Article One"
        assert items[0]["_stream_meta"]["source"] == "newsapi"

    def test_fetch_gdelt_fallback_when_newsapi_fails(self):
        fetcher = NewsStreamFetcher(base_url="http://mock-newsapi", api_key="test-key")

        newsapi_err = urllib.error.HTTPError(
            url="http://mock-newsapi",
            code=500,
            msg="Error",
            hdrs={},
            fp=None,
        )

        gdelt_data = {
            "articles": [
                {
                    "title": "GDELT Article",
                    "url": "http://example.com/g1",
                    "seendate": "20260516100000",
                }
            ]
        }
        gdelt_resp = MagicMock()
        gdelt_resp.read.return_value = json.dumps(gdelt_data).encode("utf-8")
        gdelt_resp.__enter__ = MagicMock(return_value=gdelt_resp)
        gdelt_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", side_effect=[newsapi_err, gdelt_resp]):
            items = fetcher.fetch(query="cyber", max_results=10)

        assert len(items) == 1
        assert items[0]["_stream_meta"]["source"] == "gdelt"

    def test_fetch_gdelt_direct_when_no_api_key(self):
        fetcher = NewsStreamFetcher(base_url="http://mock-newsapi", api_key="")

        gdelt_data = {
            "articles": [
                {
                    "title": "GDELT Direct",
                    "url": "http://example.com/g2",
                    "seendate": "20260516120000",
                }
            ]
        }
        gdelt_resp = MagicMock()
        gdelt_resp.read.return_value = json.dumps(gdelt_data).encode("utf-8")
        gdelt_resp.__enter__ = MagicMock(return_value=gdelt_resp)
        gdelt_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=gdelt_resp):
            items = fetcher.fetch(max_results=10)

        assert len(items) == 1
        assert items[0]["_stream_meta"]["source"] == "gdelt"

    def test_fetch_raises_on_bad_newsapi_status(self):
        fetcher = NewsStreamFetcher(base_url="http://mock-newsapi", api_key="test-key")

        mock_data = {"status": "error", "message": "Invalid API key"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        gdelt_resp = MagicMock()
        gdelt_resp.read.return_value = json.dumps({"articles": []}).encode("utf-8")
        gdelt_resp.__enter__ = MagicMock(return_value=gdelt_resp)
        gdelt_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", side_effect=[mock_resp, gdelt_resp]):
            items = fetcher.fetch()

        # Should fallback to GDELT and return empty
        assert items == []

    def test_fetch_raises_on_unexpected_format(self):
        fetcher = NewsStreamFetcher(base_url="http://mock-newsapi", api_key="test-key")

        mock_data = {"status": "ok", "total": 0}  # missing articles
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        gdelt_resp = MagicMock()
        gdelt_resp.read.return_value = json.dumps({"articles": []}).encode("utf-8")
        gdelt_resp.__enter__ = MagicMock(return_value=gdelt_resp)
        gdelt_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", side_effect=[mock_resp, gdelt_resp]):
            items = fetcher.fetch()

        assert items == []


import urllib.error
