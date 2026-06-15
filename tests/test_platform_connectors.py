#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for tools/platform_connectors — unified platform connector registry.

Tests: registry routing, adapter ABC, health probes, CLI JSON output,
graceful degradation on network failures, DB persistence path.

Run:
    pytest tests/test_platform_connectors.py -v --tb=short
"""

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.platform_connectors.base import FetchResult, HealthResult, PlatformConnector
from tools.platform_connectors.registry import PlatformConnectorRegistry


# =========================================================================
# FIXTURES / HELPERS
# =========================================================================

class _DummyAdapter(PlatformConnector):
    name = "dummy_ok"
    platform = "dummy"
    priority = 0

    def __init__(self, fail=False):
        self._fail = fail

    def fetch(self, query, **kwargs):
        if self._fail:
            return self._err(query, "dummy_error")
        result = self._ok([{"id": "1", "title": "Test item"}])
        result.query = query
        return result

    def health(self):
        return HealthResult(
            adapter_name=self.name, platform=self.platform,
            status="ok" if not self._fail else "unreachable",
            latency_ms=5.0,
        )


class _DummyFallback(PlatformConnector):
    name = "dummy_fallback"
    platform = "dummy"
    priority = 1

    def fetch(self, query, **kwargs):
        result = self._ok([{"id": "2", "title": "Fallback item"}])
        result.query = query
        return result

    def health(self):
        return HealthResult(
            adapter_name=self.name, platform=self.platform,
            status="ok", latency_ms=8.0,
        )


# =========================================================================
# BASE CLASSES
# =========================================================================

def test_fetch_result_ok():
    r = FetchResult(platform="test", adapter_name="a", query="q",
                    items=[{"x": 1}], total=1)
    assert r.ok is True
    assert r.total == 1
    d = r.to_dict()
    assert d["ok"] is True
    assert d["platform"] == "test"


def test_fetch_result_error():
    r = FetchResult(platform="test", adapter_name="a", query="q",
                    error="timeout")
    assert r.ok is False
    assert r.to_dict()["error"] == "timeout"


def test_health_result_serializes():
    h = HealthResult(adapter_name="a", platform="p", status="ok", latency_ms=10.0)
    d = h.to_dict()
    assert d["status"] == "ok"
    assert d["latency_ms"] == 10.0


def test_platform_connector_helpers():
    adapter = _DummyAdapter()
    r = adapter._ok([{"id": "x"}])
    assert r.ok
    assert r.total == 1

    r2 = adapter._err("query", "err")
    assert not r2.ok
    assert r2.error == "err"


# =========================================================================
# REGISTRY
# =========================================================================

def _fresh_registry() -> PlatformConnectorRegistry:
    reg = PlatformConnectorRegistry()
    reg._loaded = True
    return reg


def test_registry_register_and_platforms():
    reg = _fresh_registry()
    reg.register(_DummyAdapter())
    reg.register(_DummyFallback())
    assert "dummy" in reg.platforms()


def test_registry_fetch_success():
    reg = _fresh_registry()
    reg.register(_DummyAdapter(fail=False))
    result = reg.fetch("dummy", "ai query")
    assert result.ok
    assert len(result.items) == 1
    assert result.items[0]["title"] == "Test item"


def test_registry_fetch_fallback_on_error():
    """Primary adapter fails → fallback adapter succeeds."""
    reg = _fresh_registry()
    primary = _DummyAdapter(fail=True)
    primary.name = "dummy_fail"
    fallback = _DummyFallback()
    reg.register(primary)
    reg.register(fallback)

    result = reg.fetch("dummy", "test query")
    assert result.ok
    assert result.adapter_name == "dummy_fallback"


def test_registry_fetch_unknown_platform():
    reg = _fresh_registry()
    result = reg.fetch("nonexistent_platform", "test")
    assert not result.ok
    assert "no_adapter_for_platform" in result.error


def test_registry_fetch_all():
    reg = _fresh_registry()
    reg.register(_DummyAdapter())
    results = reg.fetch_all("test query")
    assert "dummy" in results
    assert results["dummy"].ok


def test_registry_doctor():
    reg = _fresh_registry()
    reg.register(_DummyAdapter())
    reg.register(_DummyFallback())
    report = reg.doctor()
    assert "dummy" in report
    assert all(isinstance(h, HealthResult) for h in report["dummy"])
    statuses = {h.status for h in report["dummy"]}
    assert "ok" in statuses


def test_registry_disabled_adapter():
    reg = _fresh_registry()
    reg._config = {"adapters": {"dummy_ok": {"enabled": False}}}
    reg.register(_DummyAdapter(fail=False))
    reg.register(_DummyFallback())
    result = reg.fetch("dummy", "test")
    # disabled primary → fallback takes over
    assert result.ok
    assert result.adapter_name == "dummy_fallback"


# =========================================================================
# DEFAULT ADAPTERS (import + instantiation only — no network calls)
# =========================================================================

def test_github_adapter_import():
    from tools.platform_connectors.adapters.github import GitHubRestAdapter
    a = GitHubRestAdapter()
    assert a.name == "github_rest"
    assert a.platform == "github"
    assert a.priority == 0


def test_hackernews_adapter_import():
    from tools.platform_connectors.adapters.hackernews import HackerNewsAdapter
    a = HackerNewsAdapter()
    assert a.name == "hackernews_algolia"
    assert a.platform == "hackernews"


def test_stackoverflow_adapter_import():
    from tools.platform_connectors.adapters.stackoverflow import StackOverflowAdapter
    a = StackOverflowAdapter()
    assert a.name == "stackoverflow_v2"
    assert a.platform == "stackoverflow"


def test_reddit_adapter_import():
    from tools.platform_connectors.adapters.reddit import RedditJsonAdapter
    a = RedditJsonAdapter()
    assert a.name == "reddit_json"
    assert a.platform == "reddit"


def test_youtube_adapters_import():
    from tools.platform_connectors.adapters.youtube import (
        YouTubeDataApiAdapter, YouTubeYtDlpAdapter,
    )
    primary = YouTubeDataApiAdapter()
    fallback = YouTubeYtDlpAdapter()
    assert primary.platform == "youtube"
    assert fallback.platform == "youtube"
    assert primary.priority < fallback.priority


# =========================================================================
# GRACEFUL DEGRADATION — no network, no deps
# =========================================================================

def test_github_no_requests():
    from tools.platform_connectors.adapters import github as gh_mod
    orig = gh_mod._HAS_REQUESTS
    try:
        gh_mod._HAS_REQUESTS = False
        from tools.platform_connectors.adapters.github import GitHubRestAdapter
        a = GitHubRestAdapter()
        result = a.fetch("test")
        assert not result.ok
        assert "requests_not_installed" in result.error
        h = a.health()
        assert h.status in ("unreachable", "auth_error")
    finally:
        gh_mod._HAS_REQUESTS = orig


def test_hackernews_no_requests():
    from tools.platform_connectors.adapters import hackernews as hn_mod
    orig = hn_mod._HAS_REQUESTS
    try:
        hn_mod._HAS_REQUESTS = False
        from tools.platform_connectors.adapters.hackernews import HackerNewsAdapter
        a = HackerNewsAdapter()
        result = a.fetch("test")
        assert not result.ok
        h = a.health()
        assert h.status == "unreachable"
    finally:
        hn_mod._HAS_REQUESTS = orig


def test_stackoverflow_no_requests():
    from tools.platform_connectors.adapters import stackoverflow as so_mod
    orig = so_mod._HAS_REQUESTS
    try:
        so_mod._HAS_REQUESTS = False
        from tools.platform_connectors.adapters.stackoverflow import StackOverflowAdapter
        a = StackOverflowAdapter()
        result = a.fetch("test")
        assert not result.ok
    finally:
        so_mod._HAS_REQUESTS = orig


def test_reddit_no_requests():
    from tools.platform_connectors.adapters import reddit as rd_mod
    orig = rd_mod._HAS_REQUESTS
    try:
        rd_mod._HAS_REQUESTS = False
        from tools.platform_connectors.adapters.reddit import RedditJsonAdapter
        a = RedditJsonAdapter()
        result = a.fetch("test")
        # Reddit fetches multiple subreddits but skips errors — returns empty ok
        assert result.total == 0
    finally:
        rd_mod._HAS_REQUESTS = orig


def test_youtube_no_api_key():
    """YouTube Data API adapter returns auth_error without key."""
    from tools.platform_connectors.adapters.youtube import YouTubeDataApiAdapter
    a = YouTubeDataApiAdapter()
    with patch.dict("os.environ", {}, clear=True):
        for key in ("YOUTUBE_API_KEY", "YT_API_KEY"):
            if key in __import__("os").environ:
                del __import__("os").environ[key]
        result = a.fetch("test")
        assert not result.ok
        assert "no_api_key" in result.error
        h = a.health()
        assert h.status == "auth_error"


def test_youtube_ytdlp_no_dep():
    from tools.platform_connectors.adapters import youtube as yt_mod
    orig = yt_mod._HAS_YTDLP
    try:
        yt_mod._HAS_YTDLP = False
        from tools.platform_connectors.adapters.youtube import YouTubeYtDlpAdapter
        a = YouTubeYtDlpAdapter()
        result = a.fetch("test")
        assert not result.ok
        assert "yt_dlp_not_installed" in result.error
        h = a.health()
        assert h.status == "unreachable"
    finally:
        yt_mod._HAS_YTDLP = orig


# =========================================================================
# CLI OUTPUT
# =========================================================================

def test_cli_list_platforms_json(capsys):
    """--list-platforms --json returns valid JSON with platforms list."""
    from tools.platform_connectors import connector_cli
    with patch.object(
        sys, "argv",
        ["connector_cli.py", "--list-platforms", "--json"]
    ):
        try:
            connector_cli.main()
        except SystemExit:
            pass
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "platforms" in data
    assert data["total"] >= 0


def test_cli_doctor_json(capsys):
    """--doctor --json returns valid JSON with summary."""
    from tools.platform_connectors import connector_cli
    with patch.object(
        sys, "argv",
        ["connector_cli.py", "--doctor", "--json"]
    ):
        try:
            connector_cli.main()
        except SystemExit:
            pass
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "summary" in data
    assert "total_adapters" in data["summary"]


def test_cli_fetch_json_no_network(capsys):
    """--fetch github --json returns JSON even with mocked network failure."""
    from tools.platform_connectors.adapters import github as gh_mod
    orig = gh_mod._HAS_REQUESTS
    try:
        gh_mod._HAS_REQUESTS = False
        # Reset registry singleton so adapters reload
        import tools.platform_connectors.registry as reg_mod
        reg_mod._registry = None
        from tools.platform_connectors import connector_cli
        with patch.object(
            sys, "argv",
            ["connector_cli.py", "--fetch", "github", "ai agents", "--json"]
        ):
            try:
                connector_cli.main()
            except SystemExit:
                pass
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "platform" in data
        assert data["platform"] == "github"
    finally:
        gh_mod._HAS_REQUESTS = orig
        import tools.platform_connectors.registry as reg_mod
        reg_mod._registry = None


# =========================================================================
# DB PERSISTENCE
# =========================================================================

def test_persist_result_no_db():
    """_persist_result silently no-ops when conn is None."""
    from tools.platform_connectors.connector_cli import _persist_result
    result = FetchResult(platform="test", adapter_name="a", query="q", items=[], total=0)
    _persist_result(None, result)  # must not raise


def test_ensure_schema_no_conn():
    """_ensure_schema silently no-ops when conn is None."""
    from tools.platform_connectors.connector_cli import _ensure_schema
    _ensure_schema(None)  # must not raise
