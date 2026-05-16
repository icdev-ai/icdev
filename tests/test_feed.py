from datetime import datetime, timezone

from src.models.feed import FeedItem


def test_feed_item_from_json_string_timestamp():
    raw = {
        "id": "feed-1",
        "source": "test-source",
        "title": "Test Title",
        "content": "Test content",
        "timestamp": "2026-05-16T12:00:00Z",
        "url": "http://example.com/1",
        "metadata": {"confidence": 80},
    }
    item = FeedItem.from_json(raw)
    assert item.id == "feed-1"
    assert item.source == "test-source"
    assert item.title == "Test Title"
    assert item.content == "Test content"
    assert item.url == "http://example.com/1"
    assert item.metadata == {"confidence": 80}
    assert item.timestamp == datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_feed_item_from_json_epoch_timestamp():
    raw = {
        "id": "feed-2",
        "source": "epoch-source",
        "title": "Epoch Title",
        "content": "Epoch content",
        "timestamp": 1715860800,
    }
    item = FeedItem.from_json(raw)
    assert item.timestamp == datetime.fromtimestamp(1715860800, tz=timezone.utc)


def test_feed_item_from_json_missing_timestamp():
    raw = {"id": "feed-3", "source": "src", "title": "T", "content": "C"}
    item = FeedItem.from_json(raw)
    assert item.id == "feed-3"
    assert isinstance(item.timestamp, datetime)
