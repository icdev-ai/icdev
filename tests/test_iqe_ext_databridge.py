# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/ext_databridge.py — 8 cases.

All DataBridge connector calls are mocked so these tests run without
any external services or credentials configured.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.iqe.executor import Executor
from tools.iqe.parser import parse


def test_connectors_registry_returns_rows() -> None:
    from tools.iqe.adapters.ext_databridge import connectors_registry_adapter

    mock_registry = {
        "servicenow_itsm": "ServiceNowITSMConnector",
        "splunk": "SplunkConnector",
    }
    with patch("tools.databridge.registry.list_registered", return_value=mock_registry):
        rows = connectors_registry_adapter(conn=None)

    assert len(rows) == 2
    names = {r["connector_name"] for r in rows}
    assert "servicenow_itsm" in names
    assert "splunk" in names
    assert all(r["status"] == "registered" for r in rows)


def test_connectors_registry_graceful_on_error() -> None:
    from tools.iqe.adapters.ext_databridge import connectors_registry_adapter

    with patch("tools.databridge.registry.list_registered", side_effect=RuntimeError("db down")):
        rows = connectors_registry_adapter(conn=None)

    assert rows == []


def test_safe_fetch_returns_empty_when_connector_missing() -> None:
    from tools.iqe.adapters.ext_databridge import _safe_fetch

    with patch("tools.databridge.registry.get_connector_instance", return_value=None):
        result = _safe_fetch("nonexistent_connector", "some_table")

    assert result == []


def test_safe_fetch_returns_rows_on_success() -> None:
    from tools.iqe.adapters.ext_databridge import _safe_fetch
    from tools.databridge.connector import ConnectorResponse

    mock_connector = MagicMock()
    mock_connector.read.return_value = ConnectorResponse(
        status="ok",
        data=[{"id": "INC001", "priority": "1"}, {"id": "INC002", "priority": "2"}],
        row_count=2,
    )

    with patch("tools.databridge.registry.get_connector_instance", return_value=mock_connector):
        result = _safe_fetch("servicenow_itsm", "incident")

    assert len(result) == 2
    assert result[0]["id"] == "INC001"


def test_safe_fetch_returns_empty_on_error_status() -> None:
    from tools.iqe.adapters.ext_databridge import _safe_fetch
    from tools.databridge.connector import ConnectorResponse

    mock_connector = MagicMock()
    mock_connector.read.return_value = ConnectorResponse(
        status="error",
        data=None,
        errors=["auth failure"],
    )

    with patch("tools.databridge.registry.get_connector_instance", return_value=mock_connector):
        result = _safe_fetch("servicenow_itsm", "incident")

    assert result == []


def test_safe_fetch_graceful_on_connector_exception() -> None:
    from tools.iqe.adapters.ext_databridge import _safe_fetch

    mock_connector = MagicMock()
    mock_connector.read.side_effect = ConnectionError("timeout")

    with patch("tools.databridge.registry.get_connector_instance", return_value=mock_connector):
        result = _safe_fetch("splunk", "events")

    assert result == []


def test_iqe_roundtrip_servicenow_incidents_filter() -> None:
    from tools.iqe.adapters.ext_databridge import servicenow_incidents_adapter
    from tools.databridge.connector import ConnectorResponse

    mock_data = [
        {"number": "INC001", "priority": "1", "state": "new"},
        {"number": "INC002", "priority": "2", "state": "in_progress"},
        {"number": "INC003", "priority": "1", "state": "resolved"},
    ]
    mock_connector = MagicMock()
    mock_connector.read.return_value = ConnectorResponse(status="ok", data=mock_data, row_count=3)

    ex = Executor()
    with patch("tools.databridge.registry.get_connector_instance", return_value=mock_connector):
        ex.register_collection("ext.servicenow.incidents", servicenow_incidents_adapter)
        ast = parse(
            "foreach i in ext.servicenow.incidents "
            "where i.priority == \"1\" "
            "select i.number, i.state"
        )
        rows = ex.run(ast, conn=None)

    assert len(rows) == 2
    numbers = {r["number"] for r in rows}
    assert "INC001" in numbers
    assert "INC003" in numbers
    assert "INC002" not in numbers


def test_seed_queries_parse_clean() -> None:
    from pathlib import Path
    from tools.iqe.parser import parse as iqe_parse, IQESyntaxError

    seed_dir = Path(__file__).resolve().parents[1] / "context" / "iqe" / "queries" / "ext"
    iqe_files = sorted(seed_dir.glob("*.iqe"))
    assert len(iqe_files) >= 8, f"Expected ≥8 seed files, found {len(iqe_files)}"

    for path in iqe_files:
        source = path.read_text(encoding="utf-8")
        query_lines = [
            line for line in source.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        query = "\n".join(query_lines).strip()
        if not query:
            continue
        try:
            iqe_parse(query)
        except IQESyntaxError as exc:
            raise AssertionError(f"Seed query {path.name} failed to parse: {exc}") from exc
