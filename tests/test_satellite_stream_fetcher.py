# CUI // SP-CTI
"""Unit tests for SatelliteStreamFetcher (Copernicus OData stream)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.fetchers.satellite_stream_fetcher import (
    SatelliteStreamFetcher,
    SatelliteStreamFetchError,
)


class TestSatelliteStreamFetcher:
    """Tests for satellite imagery stream fetcher with mocked urllib."""

    def test_fetch_success_with_mocked_response(self):
        fetcher = SatelliteStreamFetcher(base_url="http://mock-copernicus")

        mock_data = {
            "value": [
                {
                    "Id": "s1",
                    "Name": "S2A_T32TQR_20260516T103021",
                    "SensingTime": "2026-05-16T10:30:21Z",
                    "CloudCoverPercentage": 5.2,
                },
                {
                    "Id": "s2",
                    "Name": "S2B_T32TQR_20260516T103021",
                    "SensingTime": "2026-05-16T11:30:21Z",
                    "CloudCoverPercentage": 12.0,
                },
            ]
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            items = fetcher.fetch(collection="SENTINEL-2", cloud_cover_pct=20, max_results=10)

        assert len(items) == 2
        assert items[0]["Id"] == "s1"
        assert items[0]["_stream_meta"]["source"] == "copernicus_odata"

    def test_fetch_raises_on_http_error(self):
        fetcher = SatelliteStreamFetcher(base_url="http://mock-copernicus")

        from urllib.error import HTTPError

        mock_err = HTTPError(
            url="http://mock-copernicus",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=mock_err):
            with pytest.raises(SatelliteStreamFetchError) as exc_info:
                fetcher.fetch()

        assert "500" in str(exc_info.value)

    def test_fetch_raises_on_json_decode_error(self):
        fetcher = SatelliteStreamFetcher(base_url="http://mock-copernicus")

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not-json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(SatelliteStreamFetchError):
                fetcher.fetch()

    def test_fetch_uses_default_since_when_omitted(self):
        fetcher = SatelliteStreamFetcher(base_url="http://mock-copernicus")

        mock_data = {"value": []}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            fetcher.fetch()

        req = mock_urlopen.call_args[0][0]
        import urllib.parse
        decoded = urllib.parse.unquote(req.full_url)
        assert "SensingTime+gt" in decoded or "SensingTime gt" in decoded
