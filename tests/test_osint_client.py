from unittest.mock import MagicMock, patch

import pytest
import requests

from src.clients.osint_client import OSINTClient, OSINTError
from src.models.feed import FeedItem


def test_fetch_feeds_success():
    client = OSINTClient(base_url="http://mock-osint", api_key="key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "items": [
            {"id": "1", "source": "osint", "title": "T1", "content": "C1", "timestamp": "2026-05-16T10:00:00Z"},
            {"id": "2", "source": "osint", "title": "T2", "content": "C2", "timestamp": "2026-05-16T11:00:00Z"},
        ]
    }
    mock_resp.raise_for_status.return_value = None

    with patch("src.clients.osint_client.requests.request", return_value=mock_resp):
        items = client.fetch_feeds(limit=2)

    assert len(items) == 2
    assert isinstance(items[0], FeedItem)
    assert items[0].id == "1"
    assert items[1].title == "T2"


def test_fetch_feeds_http_error():
    client = OSINTClient(base_url="http://mock-osint")
    with patch("src.clients.osint_client.requests.request", side_effect=requests.HTTPError("fail")):
        with pytest.raises(OSINTError):
            client.fetch_feeds()


def test_fetch_indicator_success():
    client = OSINTClient(base_url="http://mock-osint")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "id": "ind-99",
        "source": "osint",
        "title": "Indicator 99",
        "content": "malware hash",
        "timestamp": "2026-05-16T08:00:00Z",
    }
    mock_resp.raise_for_status.return_value = None

    with patch("src.clients.osint_client.requests.request", return_value=mock_resp):
        item = client.fetch_indicator("ind-99")

    assert isinstance(item, FeedItem)
    assert item.id == "ind-99"
    assert item.title == "Indicator 99"


def test_fetch_feeds_unexpected_format():
    client = OSINTClient(base_url="http://mock-osint")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"total": 0}
    mock_resp.raise_for_status.return_value = None

    with patch("src.clients.osint_client.requests.request", return_value=mock_resp):
        with pytest.raises(OSINTError):
            client.fetch_feeds()
