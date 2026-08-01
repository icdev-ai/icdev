# CUI // SP-CTI
"""IRIS DataBridge connector — stub-mode contract tests (ctx-expose-05).

IRIS has no published API; these tests pin the fixture shapes downstream
consumers (compass staffing/writing pillars, dashboard feeds) build against.
"""
from __future__ import annotations

import pytest

from tools.databridge.connector import ConnectorRequest
from tools.databridge.connectors.iris_connector import IRISConnector
from tools.databridge.registry import list_registered


@pytest.fixture
def connector():
    c = IRISConnector()
    assert c.connect({"stub_mode": True}) is True
    return c


def test_registered_in_databridge_registry():
    assert list_registered().get("iris") == "IRISConnector"


def test_capabilities(connector):
    caps = connector.capabilities
    assert caps.supports_read and caps.supports_write
    assert connector.connector_name == "iris"


def test_list_tables(connector):
    assert set(connector.list_tables()) == {
        "staffing_alignment", "performance_reviews", "dashboard_feed", "health",
    }


def test_stub_health(connector):
    health = connector.health_check()
    assert health["status"] == "healthy"
    assert health["mode"] == "stub"


@pytest.mark.parametrize("table,required_keys", [
    ("staffing_alignment", {"person_id", "current_lcat", "recommended_lcat",
                            "alignment_score", "rationale"}),
    ("performance_reviews", {"review_id", "subject_id", "period", "status",
                             "workflow_stage"}),
    ("dashboard_feed", {"widget_id", "series", "as_of"}),
])
def test_stub_read_shapes(connector, table, required_keys):
    resp = connector.read(ConnectorRequest(table_name=table))
    assert resp.status == "ok"
    assert resp.row_count == len(resp.data) > 0
    assert resp.metadata["stub"] is True
    for row in resp.data:
        assert required_keys <= set(row)


def test_stub_read_respects_limit(connector):
    resp = connector.read(ConnectorRequest(table_name="staffing_alignment", limit=1))
    assert resp.row_count == 1


def test_read_unknown_table_errors(connector):
    resp = connector.read(ConnectorRequest(table_name="nope"))
    assert resp.status == "error"
    assert "Unknown IRIS table" in resp.errors[0]


def test_write_performance_reviews_ok(connector):
    resp = connector.write(
        ConnectorRequest(table_name="performance_reviews", sync_direction="write"),
        {"subject_id": "p-1", "period": "2026-Q2", "draft": "text"},
    )
    assert resp.status == "ok"
    assert resp.data["accepted"] is True
    assert resp.metadata["stub"] is True


def test_write_read_only_table_rejected(connector):
    resp = connector.write(
        ConnectorRequest(table_name="staffing_alignment", sync_direction="write"),
        {"x": 1},
    )
    assert resp.status == "error"
    assert "read-only" in resp.errors[0]


def test_infer_schema_from_stub(connector):
    schema = connector.infer_schema("staffing_alignment")
    names = {f.name for f in schema.fields}
    assert "person_id" in names and "alignment_score" in names


def test_live_mode_requires_base_url():
    c = IRISConnector()
    assert c.connect({"stub_mode": False}) is False
