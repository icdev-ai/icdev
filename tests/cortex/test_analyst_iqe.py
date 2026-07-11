# CUI // SP-CTI
"""Tests for the Cortex Analyst IQE-primary path (tools/cortex/analyst.py).

Collections are registered with fake adapter functions via
``register_collection`` — every question below hits nl_to_iqe's deterministic
pattern extractors, so no LLM/Ollama call is ever made.
"""
from __future__ import annotations

import pytest

from tools.cortex.analyst import CortexAnalystError, ask
from tools.cortex.schemas import Citation, CortexContext, CortexResult
from tools.iqe import executor as _executor
from tools.iqe.executor import register_collection

_SATELLITES = [
    {"id": 1, "name": "alpha", "status": "active", "weight": 12},
    {"id": 2, "name": "bravo", "status": "retired", "weight": 5},
    {"id": 3, "name": "charlie", "status": "active", "weight": 30},
]

_STATIONS = [
    {"id": 10, "label": "north", "type": "uplink"},
    {"id": 11, "label": "south", "type": "downlink"},
]

_ANOMALIES = [
    {"id": 100, "severity": 4, "timestamp": "2026-02-01"},
    {"id": 101, "severity": 2, "timestamp": "2026-03-15"},
]


class StubConn:
    """Records the security context; adapters below never touch the conn."""

    def __init__(self):
        self.security_context = None

    def set_security_context(self, ctx):
        self.security_context = ctx


@pytest.fixture(autouse=True)
def _fake_collections():
    """Register 3 fake collections; restore the shared registry afterwards."""
    saved = dict(_executor._default._registry)
    register_collection("satellites", lambda conn: list(_SATELLITES))
    register_collection("stations", lambda conn: list(_STATIONS))
    register_collection("anomalies", lambda conn: list(_ANOMALIES))
    yield
    _executor._default._registry.clear()
    _executor._default._registry.update(saved)


def test_ask_returns_rows_and_citations():
    result = ask("show all satellites", conn=StubConn())
    assert isinstance(result, CortexResult)
    assert result.data["row_count"] == 3
    assert result.data["rows"] == _SATELLITES
    assert result.data["iqe"].startswith("foreach")
    assert result.provider == "iqe"
    assert result.grounded is True
    assert "3 rows" in result.text
    assert len(result.citations) == 1
    cit = result.citations[0]
    assert isinstance(cit, Citation)
    assert cit.source_type == "analyst"
    assert cit.source_table == "satellites"
    # ctx-analyst-03: the snippet is the row-summary evidence text; the
    # executed IQE stays in data["iqe"].
    assert cit.snippet.startswith("3 rows")


def test_ask_applies_where_filter():
    result = ask("satellites with weight greater than 10", conn=StubConn())
    ids = sorted(r["id"] for r in result.data["rows"])
    assert ids == [1, 3]
    assert result.citations[0].source_table == "satellites"


def test_ask_with_explicit_collections():
    result = ask("list all stations", collections=["stations"], conn=StubConn())
    assert result.data["row_count"] == 2
    assert result.data["collections"] == ["stations"]


def test_ask_second_collection_resolves_by_name():
    result = ask("how many anomalies", conn=StubConn())
    assert result.data["row_count"] == 2
    assert result.citations[0].source_table == "anomalies"


def test_unmatched_question_raises_typed_error():
    # mode="iqe": in auto mode an unmatched question falls back to the NLQ
    # engine instead of raising (ctx-analyst-02).
    with pytest.raises(CortexAnalystError) as exc_info:
        ask("show all unicorns", mode="iqe", conn=StubConn())
    err = exc_info.value
    assert err.question == "show all unicorns"
    assert err.governance.outcomes.get("collection_resolution") == "fail"


def test_unregistered_collection_raises_typed_error():
    with pytest.raises(CortexAnalystError) as exc_info:
        ask("show all unicorns", collections=["unicorns"], conn=StubConn())
    err = exc_info.value
    assert err.collection == "unicorns"
    assert err.governance.outcomes.get("collection_authorization") == "fail"


def test_unknown_canvas_raises_typed_error():
    with pytest.raises(CortexAnalystError):
        ask("show all satellites", canvas="no-such-canvas", conn=StubConn())


def test_context_threads_into_connection():
    conn = StubConn()
    ctx = CortexContext(tenant_id="t-42", user_id="u-1", classification="SECRET")
    ask("show all satellites", ctx=ctx, conn=conn)
    sec = conn.security_context
    assert sec is not None
    assert sec.tenant_id == "t-42"
    assert sec.user_id == "u-1"
    assert sec.classification == "SECRET"


def test_context_classification_flows_to_citations():
    ctx = CortexContext(tenant_id="t-42", classification="SECRET")
    result = ask("show all satellites", ctx=ctx, conn=StubConn())
    assert result.citations[0].classification == "SECRET"


def test_invalid_mode_raises():
    with pytest.raises(CortexAnalystError):
        ask("show all satellites", mode="quantum", conn=StubConn())


def test_empty_question_raises():
    with pytest.raises(CortexAnalystError):
        ask("   ", conn=StubConn())


def test_governance_report_all_gates_pass():
    result = ask("show all satellites", conn=StubConn())
    g = result.governance
    assert g.gates_run == [
        "collection_resolution",
        "iqe_translation",
        "collection_authorization",
        "iqe_execution",
    ]
    assert set(g.outcomes.values()) == {"pass"}
    assert g.blocked is False


def test_result_round_trips_through_dict():
    result = ask("show all satellites", conn=StubConn())
    restored = CortexResult.from_dict(result.to_dict())
    assert restored.data["row_count"] == 3
    assert restored.citations[0].source_table == "satellites"
    assert restored.provider == "iqe"


def test_ask_exposed_from_package_and_api():
    from tools.cortex import CortexAnalystError as pkg_err, ask as pkg_ask
    from tools.cortex.api import CortexAnalystError as api_err, ask as api_ask

    # ctx-govern-04: the package and api now expose the governance-wrapped
    # facade (not the raw analyst ask). Both carry the marker and wrap a raw
    # analyst ``ask`` (identity across the tools/icdev shim is not asserted —
    # from-imports resolve to distinct module objects across that boundary).
    assert getattr(pkg_ask, "__cortex_governed__", False) is True
    assert getattr(api_ask, "__cortex_governed__", False) is True
    assert pkg_ask.__wrapped__.__name__ == "ask"
    assert api_ask.__wrapped__.__name__ == "ask"
    assert pkg_err is CortexAnalystError and api_err is CortexAnalystError
