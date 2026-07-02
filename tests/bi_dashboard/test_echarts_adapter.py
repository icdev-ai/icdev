# CUI // SP-CTI
"""Unit tests for ChartSpec/Chart3DSpec -> ECharts option translation."""
from __future__ import annotations

from tools.viz.spec import ChartSpec, Chart3DSpec, Series
from tools.bi_dashboard.echarts_adapter import to_2d_option, to_3d_option


def test_column_chart_uses_category_xaxis():
    spec = ChartSpec(title="Revenue", chart_type="column", categories=["A", "B"],
                     series=[Series("rev", [1, 2])], unit="$")
    opt = to_2d_option(spec)
    assert opt["xAxis"]["type"] == "category"
    assert opt["yAxis"]["type"] == "value"
    assert opt["series"][0]["type"] == "bar"
    assert opt["series"][0]["data"] == [1, 2]


def test_bar_chart_is_horizontal():
    spec = ChartSpec(title="Revenue", chart_type="bar", categories=["A", "B"],
                     series=[Series("rev", [1, 2])])
    opt = to_2d_option(spec)
    assert opt["xAxis"]["type"] == "value"
    assert opt["yAxis"]["type"] == "category"


def test_line_and_area_series_type():
    line = to_2d_option(ChartSpec(chart_type="line", categories=["A"], series=[Series("s", [1])]))
    assert line["series"][0]["type"] == "line"
    assert "areaStyle" not in line["series"][0]

    area = to_2d_option(ChartSpec(chart_type="area", categories=["A"], series=[Series("s", [1])]))
    assert area["series"][0]["type"] == "line"
    assert area["series"][0]["areaStyle"] == {}


def test_donut_has_inner_radius_pie_does_not():
    donut = to_2d_option(ChartSpec(chart_type="donut", categories=["A", "B"],
                                   series=[Series("c", [3, 7])]))
    assert donut["series"][0]["type"] == "pie"
    assert donut["series"][0]["radius"] == ["50%", "70%"]

    pie = to_2d_option(ChartSpec(chart_type="pie", categories=["A", "B"],
                                 series=[Series("c", [3, 7])]))
    assert pie["series"][0]["radius"] == "70%"


def test_gauge_uses_max_value_and_single_datapoint():
    spec = ChartSpec(title="Health", chart_type="gauge", series=[Series("v", [87])],
                     unit="%", max_value=100)
    opt = to_2d_option(spec)
    assert opt["series"][0]["type"] == "gauge"
    assert opt["series"][0]["max"] == 100
    assert opt["series"][0]["data"] == [{"value": 87, "name": "Health"}]


def test_bar3d_uses_category_axes_and_index_points():
    spec = Chart3DSpec(title="Sales", chart_type="bar3d",
                       x_categories=["East", "West"], y_categories=["Q1", "Q2"],
                       points=[[0, 0, 100], [0, 1, 120], [1, 0, 80], [1, 1, 90]],
                       z_label="Sales")
    opt = to_3d_option(spec)
    assert opt["series"][0]["type"] == "bar3D"
    assert opt["xAxis3D"]["type"] == "category" and opt["xAxis3D"]["data"] == ["East", "West"]
    assert opt["yAxis3D"]["data"] == ["Q1", "Q2"]
    assert opt["zAxis3D"]["name"] == "Sales"
    assert opt["series"][0]["data"] == [[0, 0, 100], [0, 1, 120], [1, 0, 80], [1, 1, 90]]


def test_scatter3d_uses_numeric_axes():
    spec = Chart3DSpec(title="Risk", chart_type="scatter3d",
                       points=[[1.0, 2.0, 3.0], [4.0, 1.0, 2.5]],
                       x_label="Risk", y_label="Impact", z_label="Cost")
    opt = to_3d_option(spec)
    assert opt["series"][0]["type"] == "scatter3D"
    assert opt["xAxis3D"]["type"] == "value" and opt["xAxis3D"]["name"] == "Risk"
    assert opt["series"][0]["data"] == [[1.0, 2.0, 3.0], [4.0, 1.0, 2.5]]


def test_surface3d_requests_realistic_shading():
    spec = Chart3DSpec(title="Terrain", chart_type="surface3d",
                       points=[[0, 0, 1], [0, 1, 2], [1, 0, 1.5]])
    opt = to_3d_option(spec)
    assert opt["series"][0]["type"] == "surface"
    assert opt["series"][0]["shading"] == "realistic"
