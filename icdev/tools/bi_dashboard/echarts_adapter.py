# CUI // SP-CTI
"""ChartSpec/Chart3DSpec -> Apache ECharts ``option`` JSON.

Render-time-only translation: the DB-persisted spec format (tools.viz.spec)
never becomes raw ECharts JSON — the frontend calls
``echarts.init(dom).setOption(to_2d_option(spec))`` (or the 3D counterpart,
which additionally requires the echarts-gl extension) at render time only.

No data is invented here — every number comes straight from the spec that was
built from a real query result (tools.viz.dataset.aggregate / IQE rows).
"""
from __future__ import annotations

from typing import Any

from tools.viz.palette import get_palette
from tools.viz.spec import ChartSpec, Chart3DSpec

_2D_TYPE_MAP = {
    "bar": "bar",       # horizontal (category axis on Y)
    "column": "bar",    # vertical (category axis on X)
    "line": "line",
    "area": "line",     # + areaStyle
    "pie": "pie",
    "donut": "pie",     # + inner radius
    "gauge": "gauge",
}


def to_2d_option(spec: ChartSpec, theme: str = "midnight_executive") -> dict[str, Any]:
    """Build an ECharts ``option`` object for a 2D :class:`ChartSpec`."""
    pal = get_palette(theme)
    ctype = spec.chart_type
    echarts_type = _2D_TYPE_MAP.get(ctype, "bar")
    colors = [pal.series_hex(i) for i in range(max(len(spec.series), 1))]

    option: dict[str, Any] = {
        "title": {"text": spec.title, "textStyle": {"color": pal.hex("text")}},
        "tooltip": {"trigger": "item" if echarts_type in ("pie", "gauge") else "axis"},
        "color": colors,
        "backgroundColor": "transparent",
    }

    if echarts_type == "gauge":
        value = spec.series[0].values[0] if spec.series and spec.series[0].values else 0
        option["series"] = [{
            "type": "gauge",
            "name": spec.title,
            "max": spec.max_value if spec.max_value is not None else 100,
            "detail": {"formatter": "{value}" + spec.unit},
            "data": [{"value": value, "name": spec.title}],
        }]
        return option

    if echarts_type == "pie":
        s = spec.series[0] if spec.series else None
        data = [{"name": c, "value": (s.values[i] if s and i < len(s.values) else 0)}
                for i, c in enumerate(spec.categories)]
        radius = ["50%", "70%"] if ctype == "donut" else "70%"
        option["legend"] = {"data": spec.categories, "textStyle": {"color": pal.hex("text")}}
        option["series"] = [{"type": "pie", "radius": radius, "name": spec.title, "data": data}]
        return option

    option["legend"] = {"data": [s.name for s in spec.series], "textStyle": {"color": pal.hex("text")}}
    category_axis = {"type": "category", "data": spec.categories, "name": ""}
    value_axis = {"type": "value", "name": spec.unit}
    if ctype == "bar":
        option["xAxis"], option["yAxis"] = value_axis, category_axis
    else:
        option["xAxis"], option["yAxis"] = category_axis, value_axis

    series = []
    for s in spec.series:
        item: dict[str, Any] = {"name": s.name, "type": echarts_type, "data": list(s.values)}
        if ctype == "area":
            item["areaStyle"] = {}
        series.append(item)
    option["series"] = series
    return option


_3D_TYPE_MAP = {
    "bar3d": "bar3D",
    "scatter3d": "scatter3D",
    "surface3d": "surface",
}


def to_3d_option(spec: Chart3DSpec, theme: str = "midnight_executive") -> dict[str, Any]:
    """Build an ECharts-GL ``option`` object for a :class:`Chart3DSpec`."""
    pal = get_palette(theme)
    echarts_type = _3D_TYPE_MAP.get(spec.chart_type, "scatter3D")

    option: dict[str, Any] = {
        "title": {"text": spec.title, "textStyle": {"color": pal.hex("text")}},
        "tooltip": {},
        "color": [pal.series_hex(0)],
        "backgroundColor": "transparent",
        "series": [{"type": echarts_type, "data": [list(p) for p in spec.points]}],
    }

    if spec.chart_type == "bar3d":
        option["xAxis3D"] = {"type": "category", "data": spec.x_categories, "name": spec.x_label}
        option["yAxis3D"] = {"type": "category", "data": spec.y_categories, "name": spec.y_label}
        option["zAxis3D"] = {"type": "value", "name": spec.z_label or spec.unit}
        option["grid3D"] = {}
    else:
        option["xAxis3D"] = {"type": "value", "name": spec.x_label}
        option["yAxis3D"] = {"type": "value", "name": spec.y_label}
        option["zAxis3D"] = {"type": "value", "name": spec.z_label or spec.unit}
        option["grid3D"] = {}
        if spec.chart_type == "surface3d":
            option["series"][0]["shading"] = "realistic"

    return option
