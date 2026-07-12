# CUI // SP-CTI
"""ICDEV demand-signal connector — read-only feed over rfi_capability_gaps."""
from __future__ import annotations

import importlib

import pytest

from tools.databridge.connector import ConnectorRequest
from tools.databridge.connectors.icdev_demand_connector import ICDEVDemandConnector
from tools.databridge.registry import list_registered

SIGNALS = [
    {"content_hash": "h1", "capability_need": "Zero trust network segmentation",
     "keywords": ["zero trust", "network"], "domain": "network", "frequency": 3,
     "priority": 0.9, "is_high_demand": 1, "status": "open"},
    {"content_hash": "h2", "capability_need": "Automated STIG remediation",
     "keywords": ["stig", "security"], "domain": "security", "frequency": 1,
     "priority": 0.4, "is_high_demand": 0, "status": "resolved"},
]


@pytest.fixture
def connector(monkeypatch):
    c = ICDEVDemandConnector()
    monkeypatch.setattr(c, "_read_signals", lambda limit: SIGNALS[:limit])
    assert c.connect({}) is True
    return c


def test_registered():
    assert list_registered().get("icdev_demand") == "ICDEVDemandConnector"


def test_read_only_capabilities(connector):
    assert connector.capabilities.supports_read is True
    assert connector.capabilities.supports_write is False
    with pytest.raises(NotImplementedError):
        connector.write(ConnectorRequest(table_name="demand_signals"), {"x": 1})


def test_read_signals(connector):
    resp = connector.read(ConnectorRequest(table_name="demand_signals"))
    assert resp.status == "ok" and resp.row_count == 2
    assert resp.data[0]["capability_need"].startswith("Zero trust")
    assert resp.metadata["source"] == "rfi_capability_gaps"


def test_status_filter_and_limit(connector):
    resp = connector.read(ConnectorRequest(table_name="demand_signals",
                                           filters={"status": "open"}))
    assert resp.row_count == 1 and resp.data[0]["content_hash"] == "h1"
    resp = connector.read(ConnectorRequest(table_name="demand_signals", limit=1))
    assert resp.row_count == 1


def test_unknown_table(connector):
    resp = connector.read(ConnectorRequest(table_name="nope"))
    assert resp.status == "error"


def test_feeds_allowlist_and_scope():
    feeds = importlib.import_module("tools.dashboard.api.databridge_feeds")
    assert "icdev_demand" in feeds._CONNECTOR_ALLOWLIST
    keys = importlib.import_module("tools.cortex.service_keys")
    assert "databridge:icdev_demand:read" in keys.ALL_SCOPES
