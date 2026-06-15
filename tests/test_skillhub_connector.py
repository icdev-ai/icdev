#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for SkillHub DataBridge Connector."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.databridge.connector import ConnectorRequest, ConnectorType  # noqa: E402
from tools.databridge.connectors.skillhub_connector import SkillHubConnector  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def connector():
    """Connected SkillHub connector."""
    c = SkillHubConnector()
    c.connect({})
    yield c
    c.disconnect()


@pytest.fixture
def mock_api_response():
    """Factory for mocking urllib responses."""

    def _make_response(data, status=200):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data).encode("utf-8")
        mock_resp.status = status
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    return _make_response


SAMPLE_SKILL_DETAIL = {
    "skill": {
        "slug": "self-improving-agent",
        "displayName": "self-improving-agent",
        "summary": "Captures learnings, errors, and corrections",
        "tags": {"latest": "3.0.5"},
        "stats": {
            "comments": 50,
            "downloads": 273365,
            "installsAllTime": 4495,
            "installsCurrent": 4327,
            "stars": 2493,
            "versions": 17,
        },
        "createdAt": 1767632598365,
        "updatedAt": 1773761532273,
    },
    "latestVersion": {
        "version": "3.0.5",
        "createdAt": 1773760428300,
        "changelog": "re-upload after skillhub update",
        "license": "MIT-0",
    },
    "owner": {
        "handle": "pskoett",
        "displayName": "pskoett",
        "image": "https://avatars.githubusercontent.com/u/177305814",
    },
}

SAMPLE_SEARCH_RESULTS = {
    "results": [
        {"slug": "self-improving-agent", "displayName": "self-improving-agent", "score": 0.95},
        {"slug": "code-reviewer", "displayName": "code-reviewer", "score": 0.72},
    ]
}


# ---------------------------------------------------------------------------
# Unit Tests (mocked API)
# ---------------------------------------------------------------------------
class TestConnectorProperties:
    """Test connector metadata and interface."""

    def test_connector_name(self, connector):
        assert connector.connector_name == "skillhub"

    def test_connector_type(self, connector):
        assert connector.connector_type == ConnectorType.SAAS_API

    def test_capabilities(self, connector):
        caps = connector.capabilities
        assert caps.supports_read is True
        assert caps.supports_write is False
        assert "json" in caps.supported_formats

    def test_connect_disconnect(self):
        c = SkillHubConnector()
        assert c.connect({}) is True
        assert c._connected is True
        c.disconnect()
        assert c._connected is False

    def test_custom_api_base(self):
        c = SkillHubConnector()
        c.connect({"api_base": "https://custom.api.example.com/v1"})
        assert c._api_base == "https://custom.api.example.com/v1"
        c.disconnect()


class TestSearchSkills:
    """Test vector search."""

    @patch("tools.databridge.connectors.skillhub_connector.urlopen")
    def test_search_returns_results(self, mock_urlopen, connector, mock_api_response):
        mock_urlopen.return_value = mock_api_response(SAMPLE_SEARCH_RESULTS)
        results = connector.search_skills("self-improvement")
        assert len(results) == 2
        assert results[0]["slug"] == "self-improving-agent"

    @patch("tools.databridge.connectors.skillhub_connector.urlopen")
    def test_search_via_read(self, mock_urlopen, connector, mock_api_response):
        mock_urlopen.return_value = mock_api_response(SAMPLE_SEARCH_RESULTS)
        resp = connector.read(ConnectorRequest(table_name="search", query="improvement"))
        assert resp.status == "ok"
        assert resp.row_count == 2


class TestGetSkill:
    """Test skill detail retrieval."""

    @patch("tools.databridge.connectors.skillhub_connector.urlopen")
    def test_get_skill_detail(self, mock_urlopen, connector, mock_api_response):
        mock_urlopen.return_value = mock_api_response(SAMPLE_SKILL_DETAIL)
        detail = connector.get_skill("self-improving-agent")
        assert detail["skill"]["slug"] == "self-improving-agent"
        assert detail["latestVersion"]["license"] == "MIT-0"
        assert detail["skill"]["stats"]["stars"] == 2493

    @patch("tools.databridge.connectors.skillhub_connector.urlopen")
    def test_get_via_read(self, mock_urlopen, connector, mock_api_response):
        mock_urlopen.return_value = mock_api_response(SAMPLE_SKILL_DETAIL)
        resp = connector.read(ConnectorRequest(table_name="detail", query="self-improving-agent"))
        assert resp.status == "ok"
        assert resp.row_count == 1


class TestListSkills:
    """Test skill listing."""

    @patch("tools.databridge.connectors.skillhub_connector.urlopen")
    def test_list_empty(self, mock_urlopen, connector, mock_api_response):
        mock_urlopen.return_value = mock_api_response({"items": [], "nextCursor": None})
        result = connector.list_skills()
        assert result["items"] == []
        assert result["nextCursor"] is None


class TestHealthCheck:
    """Test health check."""

    @patch("tools.databridge.connectors.skillhub_connector.urlopen")
    def test_healthy(self, mock_urlopen, connector, mock_api_response):
        mock_urlopen.return_value = mock_api_response(SAMPLE_SKILL_DETAIL)
        health = connector.health_check()
        assert health["status"] == "healthy"
        assert health["sample_skill"] == "self-improving-agent"

    @patch("tools.databridge.connectors.skillhub_connector.urlopen")
    def test_unhealthy(self, mock_urlopen, connector):
        mock_urlopen.side_effect = Exception("Connection refused")
        health = connector.health_check()
        assert health["status"] in ("unhealthy", "degraded")


class TestReadInvalidTable:
    """Test error handling."""

    def test_unknown_table(self, connector):
        resp = connector.read(ConnectorRequest(table_name="invalid"))
        assert resp.status == "error"
        assert "Unknown table_name" in resp.errors[0]


class TestDownload:
    """Test skill download."""

    @patch("tools.databridge.connectors.skillhub_connector.urlopen")
    def test_download_path_traversal_blocked(self, mock_urlopen, connector, tmp_path):
        """Path traversal in zip entries is blocked."""
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../../etc/passwd", "malicious")
        buf.seek(0)

        mock_resp = MagicMock()
        mock_resp.read.return_value = buf.read()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = connector.download_skill("evil-skill", str(tmp_path))
        assert result["success"] is False
        assert "Path traversal" in result["error"]


class TestRateLimit:
    """Test rate limiting."""

    def test_request_counter_resets(self, connector):
        connector._request_count = 0
        connector._window_start = 0  # Far in the past
        connector._rate_limit_check()
        # Window should have reset
        assert connector._request_count == 0


# ---------------------------------------------------------------------------
# Live Integration Test (skipped by default, run with --run-live)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    "--run-live" not in sys.argv,
    reason="Live API test — run with --run-live flag",
)
class TestLiveAPI:
    """Live integration tests against skillhub.ai (require network)."""

    def test_live_get_skill(self, connector):
        detail = connector.get_skill("self-improving-agent")
        assert detail["skill"]["slug"] == "self-improving-agent"
        assert detail["latestVersion"]["license"] == "MIT-0"

    def test_live_health(self, connector):
        health = connector.health_check()
        assert health["status"] == "healthy"
