from unittest.mock import MagicMock, patch

import pytest
import requests

from src.clients.satellite_client import SatelliteClient, SatelliteError
from src.models.feed import FeedItem


def test_fetch_recent_imagery_success():
    client = SatelliteClient(base_url="http://mock-sat", api_key="key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "items": [
            {
                "id": "s1",
                "source": "sat",
                "title": "Scene 1",
                "content": "desc",
                "timestamp": "2026-05-16T09:00:00Z",
                "metadata": {"cloud_cover": 10},
            },
        ]
    }
    mock_resp.raise_for_status.return_value = None

    with patch("src.clients.satellite_client.requests.request", return_value=mock_resp):
        items = client.fetch_recent_imagery(region="pacific", limit=1)

    assert len(items) == 1
    assert isinstance(items[0], FeedItem)
    assert items[0].id == "s1"
    assert items[0].metadata == {"cloud_cover": 10}


def test_fetch_recent_imagery_http_error():
    client = SatelliteClient(base_url="http://mock-sat")
    with patch(
        "src.clients.satellite_client.requests.request", side_effect=requests.ConnectionError("fail")
    ):
        with pytest.raises(SatelliteError):
            client.fetch_recent_imagery()


def test_fetch_scene_success():
    client = SatelliteClient(base_url="http://mock-sat")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "id": "scene-99",
        "source": "sat",
        "title": "Scene 99",
        "content": "high-res",
        "timestamp": "2026-05-16T07:00:00Z",
        "url": "http://mock-sat/scene-99.tif",
    }
    mock_resp.raise_for_status.return_value = None

    with patch("src.clients.satellite_client.requests.request", return_value=mock_resp):
        item = client.fetch_scene("scene-99")

    assert isinstance(item, FeedItem)
    assert item.id == "scene-99"
    assert item.url == "http://mock-sat/scene-99.tif"


def test_fetch_recent_imagery_unexpected_format():
    client = SatelliteClient(base_url="http://mock-sat")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"total": 0}
    mock_resp.raise_for_status.return_value = None

    with patch("src.clients.satellite_client.requests.request", return_value=mock_resp):
        with pytest.raises(SatelliteError):
            client.fetch_recent_imagery()
