"""Tests for social trend scanner (adapt-l30-04)."""
import json
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Import + module shape
# ---------------------------------------------------------------------------

def test_module_importable():
    from tools.research.source_scanners.social_trend_scanner import scan_social_trends
    assert callable(scan_social_trends)


def test_public_api_symbols():
    from tools.research.source_scanners.social_trend_scanner import (
        _dedup,
        _content_hash,
        _normalize_entity,
    )
    assert callable(_dedup)
    assert callable(_content_hash)
    assert callable(_normalize_entity)


def test_registered_in_source_scanners():
    from tools.research.source_scanner import SOURCE_SCANNERS
    assert "social_trends" in SOURCE_SCANNERS, "social_trends must be in SOURCE_SCANNERS"


# ---------------------------------------------------------------------------
# Entity disambiguation
# ---------------------------------------------------------------------------

def test_normalize_entity_subreddit():
    from tools.research.source_scanners.social_trend_scanner import _normalize_entity
    assert _normalize_entity("r/MachineLearning") == "subreddit:MachineLearning"


def test_normalize_entity_github():
    from tools.research.source_scanners.social_trend_scanner import _normalize_entity
    assert _normalize_entity("github.com/microsoft/markitdown") == "repo:microsoft/markitdown"
    assert _normalize_entity("https://github.com/foo/bar") == "repo:foo/bar"


def test_normalize_entity_raw():
    from tools.research.source_scanners.social_trend_scanner import _normalize_entity
    assert _normalize_entity("HackerNews") == "entity:HackerNews"


# ---------------------------------------------------------------------------
# Cross-source deduplication
# ---------------------------------------------------------------------------

def test_dedup_removes_duplicate_hashes():
    from tools.research.source_scanners.social_trend_scanner import _dedup, _content_hash
    ch = _content_hash("reddit", "AI agents are great")
    signals = [
        {"content_hash": ch, "title": "AI agents are great", "source": "reddit"},
        {"content_hash": ch, "title": "AI agents are great", "source": "hn"},
        {"content_hash": "abc123", "title": "Different post", "source": "reddit"},
    ]
    result = _dedup(signals)
    assert len(result) == 2


def test_dedup_preserves_order():
    from tools.research.source_scanners.social_trend_scanner import _dedup
    signals = [{"content_hash": "a", "title": "first"}, {"content_hash": "b", "title": "second"}]
    result = _dedup(signals)
    assert result[0]["title"] == "first"


def test_content_hash_stable():
    from tools.research.source_scanners.social_trend_scanner import _content_hash
    h1 = _content_hash("reddit", "same text")
    h2 = _content_hash("reddit", "same text")
    assert h1 == h2
    assert len(h1) == 16


def test_content_hash_differs_by_source():
    from tools.research.source_scanners.social_trend_scanner import _content_hash
    assert _content_hash("reddit", "text") != _content_hash("hn", "text")


# ---------------------------------------------------------------------------
# Graceful degradation: requests unavailable
# ---------------------------------------------------------------------------

def test_graceful_when_requests_absent():
    from tools.research.source_scanners import social_trend_scanner
    original = social_trend_scanner._HAS_REQUESTS
    social_trend_scanner._HAS_REQUESTS = False
    try:
        result = social_trend_scanner.scan_social_trends(config={}, session_config={
            "keywords": ["AI agents"],
            "max_per_source": 5,
        })
        assert isinstance(result, list)
        # All internal sources return [] when requests unavailable → dedup gives []
        assert len(result) == 0
    finally:
        social_trend_scanner._HAS_REQUESTS = original


# ---------------------------------------------------------------------------
# Reddit mock: success path
# ---------------------------------------------------------------------------

def _make_reddit_response(posts):
    return {
        "data": {
            "children": [{"data": p} for p in posts]
        }
    }


def test_reddit_returns_normalized_signals():
    from tools.research.source_scanners import social_trend_scanner

    mock_post = {
        "title": "Context compression for AI agents",
        "url": "https://example.com",
        "selftext": "This is the body.",
        "subreddit_name_prefixed": "r/MachineLearning",
        "author": "user123",
        "score": 42,
        "permalink": "/r/MachineLearning/comments/abc/",
    }

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = _make_reddit_response([mock_post])

    social_trend_scanner._HAS_REQUESTS = True
    with patch("requests.get", return_value=fake_resp):
        signals = social_trend_scanner._scan_reddit(
            keywords=["context compression"], subreddits=[], max_results=10
        )

    assert len(signals) == 1
    s = signals[0]
    assert s["title"] == "Context compression for AI agents"
    assert s["upvotes"] == 42
    assert s["source_type"] == "social_trends"
    meta = json.loads(s["metadata"])
    assert meta["platform"] == "reddit"
    assert meta["entity"].startswith("subreddit:")


# ---------------------------------------------------------------------------
# HN mock: success path
# ---------------------------------------------------------------------------

def test_hn_returns_normalized_signals():
    from tools.research.source_scanners import social_trend_scanner

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "hits": [{
            "title": "Show HN: New LLM compression library",
            "url": "https://github.com/foo/bar",
            "author": "hacker_user",
            "points": 150,
            "num_comments": 30,
            "objectID": "99999",
        }]
    }

    social_trend_scanner._HAS_REQUESTS = True
    with patch("requests.get", return_value=fake_resp):
        signals = social_trend_scanner._scan_hn(keywords=["LLM compression"], max_results=10)

    assert len(signals) == 1
    s = signals[0]
    assert "Show HN" in s["title"]
    assert s["upvotes"] == 150
    assert s["citations"] == 30
    meta = json.loads(s["metadata"])
    assert meta["platform"] == "hacker_news"


# ---------------------------------------------------------------------------
# GitHub mock: success path
# ---------------------------------------------------------------------------

def test_github_returns_normalized_signals():
    from tools.research.source_scanners import social_trend_scanner

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "items": [{
            "name": "markitdown",
            "description": "Multi-format doc converter",
            "html_url": "https://github.com/microsoft/markitdown",
            "owner": {"login": "microsoft"},
            "stargazers_count": 5000,
            "topics": ["markdown", "docx"],
            "full_name": "microsoft/markitdown",
        }]
    }

    social_trend_scanner._HAS_REQUESTS = True
    with patch("requests.get", return_value=fake_resp):
        signals = social_trend_scanner._scan_github_topics(keywords=["markdown"], max_results=10)

    assert len(signals) == 1
    s = signals[0]
    assert "microsoft" in s["title"]
    assert s["upvotes"] == 5000
    meta = json.loads(s["metadata"])
    assert meta["platform"] == "github"
    assert "repo:" in meta["entity"]


# ---------------------------------------------------------------------------
# Rate-limit handling
# ---------------------------------------------------------------------------

def test_rate_limit_returns_empty_not_exception():
    from tools.research.source_scanners import social_trend_scanner

    fake_resp = MagicMock()
    fake_resp.status_code = 429

    social_trend_scanner._HAS_REQUESTS = True
    with patch("requests.get", return_value=fake_resp):
        signals = social_trend_scanner.scan_social_trends(
            config={}, session_config={"keywords": ["test"]}
        )
    assert isinstance(signals, list)


# ---------------------------------------------------------------------------
# Full pipeline integration (mocked HTTP)
# ---------------------------------------------------------------------------

def test_scan_social_trends_full_pipeline():
    from tools.research.source_scanners import social_trend_scanner

    fake_resp_reddit = MagicMock()
    fake_resp_reddit.status_code = 200
    fake_resp_reddit.json.return_value = _make_reddit_response([{
        "title": "AI agents rising",
        "url": "https://reddit.com/r/ML",
        "selftext": "",
        "subreddit_name_prefixed": "r/ML",
        "author": "tester",
        "score": 10,
        "permalink": "/r/ML/1/",
    }])

    fake_resp_hn = MagicMock()
    fake_resp_hn.status_code = 200
    fake_resp_hn.json.return_value = {"hits": [{
        "title": "LLM compression tips",
        "url": "https://example.com",
        "author": "hn_user",
        "points": 20,
        "num_comments": 5,
        "objectID": "12345",
    }]}

    fake_resp_gh = MagicMock()
    fake_resp_gh.status_code = 200
    fake_resp_gh.json.return_value = {"items": [{
        "name": "llm-compress",
        "description": "Compress LLM context",
        "html_url": "https://github.com/foo/llm-compress",
        "owner": {"login": "foo"},
        "stargazers_count": 100,
        "topics": ["llm"],
        "full_name": "foo/llm-compress",
    }]}

    social_trend_scanner._HAS_REQUESTS = True
    with patch("requests.get", side_effect=[
        fake_resp_reddit,   # reddit keyword 1
        fake_resp_hn,       # hn keyword 1
        fake_resp_gh,       # github keyword 1
    ]):
        result = social_trend_scanner.scan_social_trends(
            config={},
            session_config={"keywords": ["AI agents"], "max_per_source": 5},
        )

    assert len(result) == 3
    platforms = {json.loads(s["metadata"])["platform"] for s in result}
    assert platforms == {"reddit", "hacker_news", "github"}
