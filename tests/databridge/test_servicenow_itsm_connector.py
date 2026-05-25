# CUI // SP-CTI
"""Unit tests for ServiceNow ITSM connector."""

from __future__ import annotations

import base64
import unittest
from unittest.mock import MagicMock, patch

from tools.databridge.connectors.servicenow_itsm_connector import (
    ServiceNowITSMConnector,
    _CHANGE_REQUEST_STATE_MAP,
)


class TestServiceNowITSMConnector(unittest.TestCase):
    """Test the ITSM connector authentication, REST calls, and normalization."""

    def setUp(self) -> None:
        self.conn = ServiceNowITSMConnector()

    def test_build_auth_headers_basic(self) -> None:
        headers = self.conn._build_auth_headers(
            {"username": "admin", "password": "secret"}
        )
        assert headers["Accept"] == "application/json"
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"].startswith("Basic ")
        decoded = base64.b64decode(headers["Authorization"].split(" ", 1)[1]).decode()
        assert decoded == "admin:secret"

    def test_build_auth_headers_bearer(self) -> None:
        headers = self.conn._build_auth_headers(
            {"bearer_token": "my-oauth-token"}
        )
        assert headers["Authorization"] == "Bearer my-oauth-token"
        assert headers["Accept"] == "application/json"

    def test_build_auth_headers_no_hardcoded_secrets(self) -> None:
        # Ensure the connector does not embed any literal credentials
        import inspect
        source = inspect.getsource(self.conn._build_auth_headers)
        assert "admin" not in source or "get(" in source
        assert "secret" not in source
        assert "password123" not in source

    def test_endpoints_include_change_request(self) -> None:
        assert "change_request" in self.conn._endpoints
        assert "/api/now/table/change_request" in self.conn._endpoints["change_request"]

    @patch(
        "tools.databridge.connectors.servicenow_itsm_connector.ServiceNowITSMConnector._http_get"
    )
    def test_health_check_healthy(self, mock_get: MagicMock) -> None:
        mock_get.return_value = {"result": [{"name": "glide.war"}]}
        self.conn._base_url = "https://demo.service-now.com"
        self.conn._auth_headers = {}
        result = self.conn.health_check()
        assert result["status"] == "healthy"
        assert result["connector"] == "servicenow_itsm"
        assert result["rows_returned"] == 1

    @patch(
        "tools.databridge.connectors.servicenow_itsm_connector.ServiceNowITSMConnector._http_get"
    )
    def test_health_check_unhealthy(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("Connection refused")
        self.conn._base_url = "https://demo.service-now.com"
        self.conn._auth_headers = {}
        result = self.conn.health_check()
        assert result["status"] == "unhealthy"
        assert "Connection refused" in result["error"]

    @patch(
        "tools.databridge.connectors.servicenow_itsm_connector.ServiceNowITSMConnector._http_get"
    )
    def test_fetch_table_pagination(self, mock_get: MagicMock) -> None:
        # First page returns full limit, second page returns fewer
        mock_get.side_effect = [
            {"result": [{"sys_id": "1"}] * 1000},
            {"result": [{"sys_id": "1"}] * 500},
        ]
        self.conn._base_url = "https://demo.service-now.com"
        self.conn._auth_headers = {}
        rows = self.conn.fetch_table("change_request", limit=1000)
        assert len(rows) == 1500
        assert mock_get.call_count == 2

    @patch(
        "tools.databridge.connectors.servicenow_itsm_connector.ServiceNowITSMConnector._http_get"
    )
    def test_get_change_request(self, mock_get: MagicMock) -> None:
        mock_get.return_value = {"result": {"sys_id": "abc123", "number": "CHG0001"}}
        self.conn._base_url = "https://demo.service-now.com"
        self.conn._auth_headers = {}
        row = self.conn.get_change_request("abc123")
        assert row is not None
        assert row["sys_id"] == "abc123"

    @patch(
        "tools.databridge.connectors.servicenow_itsm_connector.ServiceNowITSMConnector._http_post"
    )
    def test_create_change_request(self, mock_post: MagicMock) -> None:
        mock_post.return_value = {"result": {"sys_id": "new123"}}
        self.conn._base_url = "https://demo.service-now.com"
        self.conn._auth_headers = {}
        result = self.conn.create_change_request({"short_description": "Test"})
        assert result is not None
        assert result["result"]["sys_id"] == "new123"

    @patch(
        "tools.databridge.connectors.servicenow_itsm_connector.ServiceNowITSMConnector._http_put"
    )
    def test_update_change_request(self, mock_put: MagicMock) -> None:
        mock_put.return_value = {"result": {"sys_id": "upd123"}}
        self.conn._base_url = "https://demo.service-now.com"
        self.conn._auth_headers = {}
        result = self.conn.update_change_request("upd123", {"state": "3"})
        assert result is not None
        assert result["result"]["sys_id"] == "upd123"

    def test_normalize_change_request(self) -> None:
        raw = {
            "sys_id": {"value": "abc", "display_value": "abc"},
            "number": "CHG0001",
            "short_description": "Server patch",
            "state": {"value": "-4", "display_value": "Assess"},
            "risk": "3 - Moderate",
            "priority": "2 - High",
            "requested_by": {"display_value": "Alice"},
            "assignment_group": {"display_value": "Infrastructure"},
            "assigned_to": {"display_value": "Bob"},
            "start_date": "2026-05-01 08:00:00",
            "end_date": "2026-05-01 10:00:00",
            "work_notes": "Patching in progress",
            "approval": "requested",
        }
        normalized = self.conn.normalize_change_request(raw)
        assert normalized["sys_id"] == "abc"
        assert normalized["number"] == "CHG0001"
        assert normalized["short_description"] == "Server patch"
        assert normalized["state"] == "assess"
        assert normalized["classification"] == "CUI"
        assert normalized["planned_start_date"] == "2026-05-01 08:00:00"

    def test_extract_rows_list(self) -> None:
        assert self.conn._extract_rows({"result": [{"a": 1}]}) == [{"a": 1}]

    def test_extract_rows_dict(self) -> None:
        assert self.conn._extract_rows({"result": {"a": 1}}) == [{"a": 1}]

    def test_extract_rows_raw_list(self) -> None:
        assert self.conn._extract_rows([{"a": 1}]) == [{"a": 1}]

    def test_extract_rows_empty(self) -> None:
        assert self.conn._extract_rows({}) == []

    def test_state_map_coverage(self) -> None:
        assert _CHANGE_REQUEST_STATE_MAP["-5"] == "new"
        assert _CHANGE_REQUEST_STATE_MAP["3"] == "closed"
        assert _CHANGE_REQUEST_STATE_MAP["4"] == "canceled"


if __name__ == "__main__":
    unittest.main()
