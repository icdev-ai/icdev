# CUI // SP-CTI
"""Unit tests for tools/bi_dashboard/spec_generator.py."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tools.bi_dashboard.spec_generator import (
    _extract_json,
    _heuristic_structure,
    _structure_to_spec,
    _validate_structure,
    generate_spec,
)
from tools.viz.spec import Chart3DSpec, ChartSpec

_DATASET = {
    "columns": ["region", "sales"],
    "dimensions": ["region"],
    "measures": ["sales"],
    "rows": [["East", 100], ["West", 200], ["East", 50]],
}

_3D_DATASET = {
    "columns": ["risk", "impact", "cost"],
    "dimensions": [],
    "measures": ["risk", "impact", "cost"],
    "rows": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
}


def _mock_router(content: str):
    router = MagicMock()
    router.invoke.return_value = SimpleNamespace(content=content)
    return router


# ── _extract_json ─────────────────────────────────────────────────────────────

def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced_code_block():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_embedded_in_prose():
    assert _extract_json('Sure, here you go: {"a": 1} thanks!') == {"a": 1}


def test_extract_json_garbage_returns_none():
    assert _extract_json("not json at all") is None


# ── _validate_structure ───────────────────────────────────────────────────────

def test_validate_rejects_unknown_column():
    err = _validate_structure(
        {"kind": "chart", "chart_type": "bar", "dimension": "nope", "measures": ["sales"]}, _DATASET)
    assert err and "nope" in err


def test_validate_rejects_bad_kind():
    err = _validate_structure({"kind": "bogus"}, _DATASET)
    assert err and "kind" in err


def test_validate_rejects_bad_chart_type_for_kind():
    err = _validate_structure(
        {"kind": "chart", "chart_type": "bar3d", "dimension": "region", "measures": ["sales"]}, _DATASET)
    assert err


def test_validate_accepts_good_2d_structure():
    err = _validate_structure(
        {"kind": "chart", "chart_type": "bar", "dimension": "region", "measures": ["sales"]}, _DATASET)
    assert err is None


def test_validate_accepts_good_3d_structure():
    err = _validate_structure(
        {"kind": "chart3d", "chart_type": "scatter3d",
         "x_field": "risk", "y_field": "impact", "z_field": "cost"}, _3D_DATASET)
    assert err is None


def test_validate_rejects_3d_field_not_in_columns():
    err = _validate_structure(
        {"kind": "chart3d", "chart_type": "scatter3d",
         "x_field": "risk", "y_field": "impact", "z_field": "nonexistent"}, _3D_DATASET)
    assert err


# ── _heuristic_structure ──────────────────────────────────────────────────────

def test_heuristic_picks_bar_for_categorical_dimension():
    h = _heuristic_structure(_DATASET)
    assert h["kind"] == "chart" and h["chart_type"] == "bar" and h["dimension"] == "region"


def test_heuristic_picks_line_for_time_like_dimension():
    ds = {"columns": ["month", "revenue"], "dimensions": ["month"], "measures": ["revenue"], "rows": []}
    h = _heuristic_structure(ds)
    assert h["chart_type"] == "line"


def test_heuristic_picks_scatter3d_for_three_measures():
    h = _heuristic_structure(_3D_DATASET)
    assert h["kind"] == "chart3d" and h["chart_type"] == "scatter3d"
    assert h["x_field"] == "risk" and h["y_field"] == "impact" and h["z_field"] == "cost"


# ── _structure_to_spec: numbers come from real data, never invented ──────────

def test_structure_to_spec_aggregates_real_rows():
    structure = {"kind": "chart", "chart_type": "bar", "dimension": "region",
                 "measures": ["sales"], "title": "Sales by Region", "unit": "$"}
    spec = _structure_to_spec(structure, _DATASET)
    assert isinstance(spec, ChartSpec)
    assert spec.categories == ["East", "West"]
    assert spec.series[0].values == [150.0, 200.0]  # 100+50, 200 — real sums, not invented


def test_structure_to_spec_builds_chart3d_points():
    structure = {"kind": "chart3d", "chart_type": "scatter3d",
                 "x_field": "risk", "y_field": "impact", "z_field": "cost", "title": "R", "unit": ""}
    spec = _structure_to_spec(structure, _3D_DATASET)
    assert isinstance(spec, Chart3DSpec)
    assert spec.points == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]


def test_structure_to_spec_gauge_sums_single_measure():
    structure = {"kind": "chart", "chart_type": "gauge", "dimension": "",
                 "measures": ["sales"], "title": "Total Sales", "unit": "$"}
    spec = _structure_to_spec(structure, _DATASET)
    assert spec.series[0].values == [350.0]  # 100+200+50


# ── generate_spec: end-to-end with a mocked router ────────────────────────────

def test_generate_spec_uses_llm_structure_when_valid():
    llm_json = '{"kind":"chart","chart_type":"bar","dimension":"region","measures":["sales"],"title":"Sales","unit":""}'
    with patch("tools.llm.get_router", return_value=_mock_router(llm_json)):
        spec, method, structure = generate_spec("show sales by region", _DATASET)
    assert method == "llm"
    assert structure["dimension"] == "region"
    assert spec.categories == ["East", "West"]
    assert spec.series[0].values == [150.0, 200.0]


def test_generate_spec_retries_once_then_succeeds():
    router = MagicMock()
    router.invoke.side_effect = [
        SimpleNamespace(content='{"kind":"chart","chart_type":"bar","dimension":"bogus","measures":["sales"]}'),
        SimpleNamespace(content='{"kind":"chart","chart_type":"bar","dimension":"region","measures":["sales"]}'),
    ]
    with patch("tools.llm.get_router", return_value=router):
        spec, method, structure = generate_spec("show sales by region", _DATASET)
    assert method == "llm_retry"
    assert router.invoke.call_count == 2
    assert structure["dimension"] == "region"


def test_generate_spec_falls_back_to_heuristic_after_two_bad_attempts():
    router = MagicMock()
    router.invoke.return_value = SimpleNamespace(content="not valid json at all")
    with patch("tools.llm.get_router", return_value=router):
        spec, method, structure = generate_spec("show sales by region", _DATASET)
    assert method == "heuristic"
    assert router.invoke.call_count == 2
    assert isinstance(spec, ChartSpec)
    assert spec.categories == ["East", "West"]  # heuristic still uses real aggregated data


def test_generate_spec_falls_back_to_heuristic_when_llm_unavailable():
    with patch("tools.llm.get_router", side_effect=RuntimeError("no provider")):
        spec, method, structure = generate_spec("show sales by region", _DATASET)
    assert method == "heuristic"
    assert isinstance(spec, ChartSpec)


def test_generate_spec_passes_prior_structure_for_refinement():
    llm_json = '{"kind":"chart","chart_type":"donut","dimension":"region","measures":["sales"],"title":"Sales","unit":""}'
    router = _mock_router(llm_json)
    prior = {"kind": "chart", "chart_type": "bar", "dimension": "region", "measures": ["sales"]}
    with patch("tools.llm.get_router", return_value=router):
        spec, method, structure = generate_spec("make it a donut", _DATASET, prior_structure=prior)
    assert structure["chart_type"] == "donut"
    prompt_sent = router.invoke.call_args[0][1].messages[0]["content"]
    assert "Previous chart structure" in prompt_sent
