# CUI // SP-CTI
"""Tests for tools/data_canvas/clients/openmetadata.py — 4 cases, mocked HTTP."""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.data_canvas.clients.openmetadata import OpenMetadataClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

TABLE_FIXTURE = {
    "id": "aaaa-1111",
    "name": "users",
    "fullyQualifiedName": "ddc_service.mydb.ddc.users",
    "tableType": "Regular",
}

LINEAGE_FIXTURE = {
    "entity": {"id": "aaaa-1111", "type": "table"},
    "nodes": [{"id": "aaaa-1111", "type": "table"}, {"id": "bbbb-2222", "type": "table"}],
    "upstreamEdges": [],
    "downstreamEdges": [
        {"fromEntity": {"id": "aaaa-1111"}, "toEntity": {"id": "bbbb-2222"}}
    ],
}


def _make_response(payload: dict) -> mock.MagicMock:
    """Return a mock urllib response that reads *payload* as JSON."""
    raw = json.dumps(payload).encode("utf-8")
    resp = mock.MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


# ── Test cases ────────────────────────────────────────────────────────────────


class TestListTables:
    def test_returns_tables_on_success(self):
        """list_tables() parses the data[] array from a single-page response."""
        payload = {"data": [TABLE_FIXTURE], "paging": {}}
        client = OpenMetadataClient(host="http://om.test", token="tok")
        with mock.patch("urllib.request.urlopen", return_value=_make_response(payload)):
            tables = client.list_tables()
        assert len(tables) == 1
        assert tables[0]["id"] == "aaaa-1111"
        assert tables[0]["name"] == "users"

    def test_returns_empty_list_when_unreachable(self):
        """list_tables() returns [] gracefully when the host is unreachable."""
        client = OpenMetadataClient(host="http://unreachable.test")
        url_err = urllib.error.URLError("Connection refused")
        with mock.patch("urllib.request.urlopen", side_effect=url_err):
            tables = client.list_tables()
        assert tables == []


class TestGetLineage:
    def test_returns_lineage_on_success(self):
        """get_lineage() returns the parsed lineage graph for a known entity ID."""
        client = OpenMetadataClient(host="http://om.test", token="tok")
        with mock.patch("urllib.request.urlopen", return_value=_make_response(LINEAGE_FIXTURE)):
            result = client.get_lineage("aaaa-1111")
        assert result["entity"]["id"] == "aaaa-1111"
        assert len(result["downstreamEdges"]) == 1

    def test_returns_empty_dict_when_unreachable(self):
        """get_lineage() returns {} gracefully when the host is unreachable."""
        client = OpenMetadataClient(host="http://unreachable.test")
        url_err = urllib.error.URLError("Connection refused")
        with mock.patch("urllib.request.urlopen", side_effect=url_err):
            result = client.get_lineage("aaaa-1111")
        assert result == {}
