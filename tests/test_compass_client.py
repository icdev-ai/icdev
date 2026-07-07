# CUI // SP-CTI
"""tools/integrations/compass_client.py -- the never-raise, degrade-to-None
contract for lcat_lookup()/staffing_summary(), and that the reverse MCP
handlers (tools/integrations/compass_mcp_handlers.py) surface those None
results as error dicts rather than raising.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.integrations import compass_client, compass_mcp_handlers


def _resp(status_code=200, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body if json_body is not None else {}
    return r


def _cfg(enabled=True, compass_url="http://127.0.0.1:8010", timeout_seconds=15.0):
    return {"enabled": enabled, "compass_url": compass_url, "timeout_seconds": timeout_seconds}


def test_lcat_lookup_returns_none_for_empty_text(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg())
    assert compass_client.lcat_lookup("") is None
    assert compass_client.lcat_lookup("   ") is None


def test_lcat_lookup_returns_none_when_integration_disabled(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg(enabled=False))
    assert compass_client.lcat_lookup("Cloud engineer, 5 yrs AWS") is None


def test_lcat_lookup_returns_none_when_url_missing(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg(compass_url=""))
    assert compass_client.lcat_lookup("Cloud engineer, 5 yrs AWS") is None


def test_lcat_lookup_returns_none_on_non_200(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg())
    monkeypatch.setattr(compass_client, "http_request", MagicMock(return_value=_resp(status_code=500)))
    assert compass_client.lcat_lookup("Cloud engineer, 5 yrs AWS") is None


def test_lcat_lookup_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg())
    monkeypatch.setattr(compass_client, "http_request", MagicMock(side_effect=ConnectionError("refused")))
    assert compass_client.lcat_lookup("Cloud engineer, 5 yrs AWS") is None


def test_lcat_lookup_returns_none_on_non_dict_json(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg())
    monkeypatch.setattr(compass_client, "http_request", MagicMock(return_value=_resp(json_body=["not", "a", "dict"])))
    assert compass_client.lcat_lookup("Cloud engineer, 5 yrs AWS") is None


def test_lcat_lookup_returns_parsed_dict_on_success(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg())
    body = {"soc_code": "15-1252", "title": "Software Developer", "confidence": 0.91, "match_score": 0.87}
    mock_request = MagicMock(return_value=_resp(json_body=body))
    monkeypatch.setattr(compass_client, "http_request", mock_request)

    result = compass_client.lcat_lookup("Backend engineer, Python/FastAPI")

    assert result == body
    args, kwargs = mock_request.call_args
    assert args[0] == "POST"
    assert args[1] == "http://127.0.0.1:8010/api/staffing/lcat-lookup"
    assert kwargs["json"] == {"text": "Backend engineer, Python/FastAPI"}


def test_staffing_summary_returns_none_when_integration_disabled(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg(enabled=False))
    assert compass_client.staffing_summary() is None


def test_staffing_summary_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg())
    monkeypatch.setattr(compass_client, "http_request", MagicMock(side_effect=TimeoutError("timed out")))
    assert compass_client.staffing_summary() is None


def test_staffing_summary_returns_parsed_dict_on_success(monkeypatch):
    monkeypatch.setattr(compass_client, "_load_config", lambda: _cfg())
    body = {"rows": [], "person_count": 12, "mismatch_count": 1, "unresolved_count": 0}
    mock_request = MagicMock(return_value=_resp(json_body=body))
    monkeypatch.setattr(compass_client, "http_request", mock_request)

    result = compass_client.staffing_summary()

    assert result == body
    args, kwargs = mock_request.call_args
    assert args[0] == "GET"
    assert args[1] == "http://127.0.0.1:8010/api/staffing/matrix"


def test_load_config_returns_empty_dict_when_file_missing(monkeypatch):
    monkeypatch.setattr(compass_client.Path, "exists", lambda self: False)
    assert compass_client._load_config() == {}


def test_handle_compass_lcat_lookup_returns_error_dict_for_missing_text():
    result = compass_mcp_handlers.handle_compass_lcat_lookup({})
    assert "error" in result


def test_handle_compass_lcat_lookup_returns_error_dict_when_client_returns_none(monkeypatch):
    monkeypatch.setattr(compass_mcp_handlers, "lcat_lookup", lambda text: None)
    result = compass_mcp_handlers.handle_compass_lcat_lookup({"text": "Cloud engineer"})
    assert "error" in result


def test_handle_compass_lcat_lookup_passes_through_result(monkeypatch):
    monkeypatch.setattr(compass_mcp_handlers, "lcat_lookup", lambda text: {"soc_code": "15-1252"})
    result = compass_mcp_handlers.handle_compass_lcat_lookup({"text": "Cloud engineer"})
    assert result == {"soc_code": "15-1252"}


def test_handle_compass_staffing_summary_returns_error_dict_when_client_returns_none(monkeypatch):
    monkeypatch.setattr(compass_mcp_handlers, "staffing_summary", lambda: None)
    result = compass_mcp_handlers.handle_compass_staffing_summary({})
    assert "error" in result


def test_handle_compass_staffing_summary_passes_through_result(monkeypatch):
    monkeypatch.setattr(compass_mcp_handlers, "staffing_summary", lambda: {"person_count": 3})
    result = compass_mcp_handlers.handle_compass_staffing_summary({})
    assert result == {"person_count": 3}


def test_tool_registry_has_both_compass_tools_wired_to_real_handlers():
    from tools.mcp.tool_registry import TOOL_REGISTRY
    import importlib

    for tool_name in ("compass_lcat_lookup", "compass_staffing_summary"):
        entry = TOOL_REGISTRY[tool_name]
        mod = importlib.import_module(entry["module"])
        assert hasattr(mod, entry["handler"])
