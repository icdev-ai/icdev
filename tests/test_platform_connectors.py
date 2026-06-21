"""Tests for unified platform connector registry (adapt-conn-07)."""
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_importable():
    from tools.platform_connectors.registry import get_adapter, list_adapters, register
    assert callable(get_adapter)
    assert callable(list_adapters)
    assert callable(register)


def test_list_adapters_includes_defaults():
    from tools.platform_connectors.registry import list_adapters
    adapters = list_adapters()
    assert "github" in adapters
    assert "reddit" in adapters
    assert "hacker_news" in adapters


def test_get_adapter_returns_adapter():
    from tools.platform_connectors.registry import get_adapter
    a = get_adapter("github")
    assert a.name == "github"
    assert callable(a.fetch)
    assert callable(a.health_check)


def test_get_adapter_unknown_raises_keyerror():
    from tools.platform_connectors.registry import get_adapter
    with pytest.raises(KeyError):
        get_adapter("nonexistent_platform_xyz")


def test_register_custom_adapter():
    from tools.platform_connectors.registry import register, get_adapter

    class FakeAdapter:
        name = "test_fake_platform"
        def fetch(self, query, limit=10, **kw): return []
        def health_check(self): return {"status": "ok"}

    register(FakeAdapter())
    assert get_adapter("test_fake_platform").name == "test_fake_platform"


# ---------------------------------------------------------------------------
# _safe_get
# ---------------------------------------------------------------------------

def test_safe_get_returns_data_on_200():
    from tools.platform_connectors.registry import _safe_get

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"key": "value"}

    with patch("requests.get", return_value=mock_resp):
        data, err = _safe_get("https://example.com")

    assert err is None
    assert data == {"key": "value"}


def test_safe_get_rate_limited():
    from tools.platform_connectors.registry import _safe_get

    mock_resp = MagicMock()
    mock_resp.status_code = 429

    with patch("requests.get", return_value=mock_resp):
        data, err = _safe_get("https://example.com")

    assert data is None
    assert err == "rate_limited"


def test_safe_get_forbidden():
    from tools.platform_connectors.registry import _safe_get

    mock_resp = MagicMock()
    mock_resp.status_code = 403

    with patch("requests.get", return_value=mock_resp):
        data, err = _safe_get("https://example.com")

    assert err == "forbidden"


def test_safe_get_requests_unavailable():
    from tools.platform_connectors.registry import _safe_get
    import sys

    with patch.dict(sys.modules, {"requests": None}):
        data, err = _safe_get("https://example.com")

    assert data is None
    assert err == "requests_unavailable"


# ---------------------------------------------------------------------------
# GitHub adapter
# ---------------------------------------------------------------------------

def test_github_adapter_importable():
    from tools.platform_connectors.github import GitHubAdapter
    a = GitHubAdapter()
    assert a.name == "github"


def test_github_fetch_returns_normalized():
    from tools.platform_connectors.github import GitHubAdapter

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [{
        "full_name": "microsoft/markitdown",
        "html_url": "https://github.com/microsoft/markitdown",
        "owner": {"login": "microsoft"},
        "stargazers_count": 5000,
        "description": "Multi-format doc converter",
        "topics": ["markdown"],
        "forks_count": 100,
        "language": "Python",
    }]}

    with patch("requests.get", return_value=mock_resp):
        results = GitHubAdapter().fetch("markitdown", limit=5)

    assert len(results) == 1
    r = results[0]
    assert r["title"] == "microsoft/markitdown"
    assert r["score"] == 5000
    assert r["metadata"]["platform"] == "github"


def test_github_fetch_graceful_on_rate_limit():
    from tools.platform_connectors.github import GitHubAdapter

    mock_resp = MagicMock()
    mock_resp.status_code = 429

    with patch("requests.get", return_value=mock_resp):
        results = GitHubAdapter().fetch("test")

    assert results == []


def test_github_health_check_ok():
    from tools.platform_connectors.github import GitHubAdapter

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"rate": {"remaining": 59}}

    with patch("requests.get", return_value=mock_resp):
        result = GitHubAdapter().health_check()

    assert result["status"] == "ok"
    assert "latency_ms" in result
    assert result["rate_remaining"] == 59


# ---------------------------------------------------------------------------
# Reddit adapter
# ---------------------------------------------------------------------------

def test_reddit_adapter_importable():
    from tools.platform_connectors.reddit import RedditAdapter
    assert RedditAdapter().name == "reddit"


def test_reddit_fetch_returns_normalized():
    from tools.platform_connectors.reddit import RedditAdapter

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {"children": [{"data": {
            "title": "LLM context compression tricks",
            "permalink": "/r/ML/comments/abc/",
            "author": "user1",
            "score": 150,
            "selftext": "Body text",
            "subreddit_name_prefixed": "r/MachineLearning",
            "num_comments": 42,
            "upvote_ratio": 0.95,
        }}]}
    }

    with patch("requests.get", return_value=mock_resp):
        results = RedditAdapter().fetch("context compression", limit=5)

    assert len(results) == 1
    r = results[0]
    assert "LLM context" in r["title"]
    assert r["score"] == 150
    assert r["metadata"]["platform"] == "reddit"


# ---------------------------------------------------------------------------
# HN adapter
# ---------------------------------------------------------------------------

def test_hn_adapter_importable():
    from tools.platform_connectors.hacker_news import HackerNewsAdapter
    assert HackerNewsAdapter().name == "hacker_news"


def test_hn_fetch_returns_normalized():
    from tools.platform_connectors.hacker_news import HackerNewsAdapter

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"hits": [{
        "title": "Ask HN: Best LLM context compression?",
        "url": "https://example.com",
        "author": "hn_user",
        "points": 200,
        "num_comments": 75,
        "objectID": "12345",
        "created_at": "2026-06-01T00:00:00Z",
        "story_text": "",
    }]}

    with patch("requests.get", return_value=mock_resp):
        results = HackerNewsAdapter().fetch("LLM compression", limit=5)

    assert len(results) == 1
    r = results[0]
    assert "LLM" in r["title"]
    assert r["score"] == 200
    assert r["metadata"]["platform"] == "hacker_news"


def test_hn_health_check_ok():
    from tools.platform_connectors.hacker_news import HackerNewsAdapter

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"hits": []}

    with patch("requests.get", return_value=mock_resp):
        result = HackerNewsAdapter().health_check()

    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

def test_all_adapters_implement_protocol():
    from tools.platform_connectors.registry import list_adapters, get_adapter, PlatformAdapter
    for name in list_adapters():
        if name == "test_fake_platform":
            continue
        adapter = get_adapter(name)
        assert isinstance(adapter, PlatformAdapter), f"{name} must implement PlatformAdapter"
        assert hasattr(adapter, "fetch")
        assert hasattr(adapter, "health_check")
