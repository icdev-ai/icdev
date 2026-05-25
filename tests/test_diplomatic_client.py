from unittest.mock import MagicMock, patch

import pytest
import requests

from src.clients.diplomatic_client import DiplomaticClient, DiplomaticError
from src.models.feed import FeedItem


def test_fetch_summaries_success():
    client = DiplomaticClient(base_url="http://mock-dip", api_key="key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "items": [
            {
                "id": "d1",
                "source": "diplomatic",
                "title": "Cable A",
                "content": "Summary A",
                "timestamp": "2026-05-16T08:00:00Z",
                "metadata": {"classification": "UNCLASS"},
            },
            {
                "id": "d2",
                "source": "diplomatic",
                "title": "Cable B",
                "content": "Summary B",
                "timestamp": "2026-05-16T09:00:00Z",
            },
        ]
    }
    mock_resp.raise_for_status.return_value = None

    with patch("src.clients.diplomatic_client.requests.request", return_value=mock_resp):
        items = client.fetch_summaries(limit=2)

    assert len(items) == 2
    assert isinstance(items[0], FeedItem)
    assert items[0].id == "d1"
    assert items[0].metadata == {"classification": "UNCLASS"}
    assert items[1].title == "Cable B"


def test_fetch_summaries_http_error():
    client = DiplomaticClient(base_url="http://mock-dip")
    with patch(
        "src.clients.diplomatic_client.requests.request", side_effect=requests.Timeout("fail")
    ):
        with pytest.raises(DiplomaticError):
            client.fetch_summaries()


def test_fetch_summary_success():
    client = DiplomaticClient(base_url="http://mock-dip")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "id": "dip-99",
        "source": "diplomatic",
        "title": "Cable 99",
        "content": "high-level summary",
        "timestamp": "2026-05-16T07:00:00Z",
        "url": "http://mock-dip/dip-99.pdf",
    }
    mock_resp.raise_for_status.return_value = None

    with patch("src.clients.diplomatic_client.requests.request", return_value=mock_resp):
        item = client.fetch_summary("dip-99")

    assert isinstance(item, FeedItem)
    assert item.id == "dip-99"
    assert item.url == "http://mock-dip/dip-99.pdf"


def test_fetch_summaries_unexpected_format():
    client = DiplomaticClient(base_url="http://mock-dip")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"total": 0}
    mock_resp.raise_for_status.return_value = None

    with patch("src.clients.diplomatic_client.requests.request", return_value=mock_resp):
        with pytest.raises(DiplomaticError):
            client.fetch_summaries()
