# CUI // SP-CTI
"""Declarative visualization specs for the ICDEV™ Viz Kernel.

These dataclasses are the single contract between *producers* (slides
viz_mapper, PDF report generators, canvas exporters) and *renderers*
(render_pptx / render_png / render_svg / render_html).

Design rules:
  - Plain data only. No rendering logic, no I/O, no LLM calls.
  - JSON round-trippable via ``to_dict`` / ``from_dict`` so specs can be
    persisted in DB columns (chart_json, table_json, …) and rebuilt.
  - Numbers come from real sources; the kernel never fabricates them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Chart types the kernel knows how to draw in every target ─────────────────
CHART_TYPES: list[str] = ["bar", "column", "line", "area", "pie", "donut", "gauge"]
DIAGRAM_LAYOUTS: list[str] = ["spring", "layered", "grid"]


@dataclass
class Series:
    """One data series within a chart (e.g. a line, or a group of bars)."""
    name: str
    values: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "values": list(self.values)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Series":
        return cls(name=str(d.get("name", "")), values=[float(v) for v in d.get("values", [])])


@dataclass
class ChartSpec:
    """A chart driven by real category/series data.

    ``categories`` are the x-axis labels (or pie slice labels). ``series`` is a
    list of :class:`Series`. For pie/donut/gauge only the first series is used.
    """
    title: str = ""
    chart_type: str = "column"        # one of CHART_TYPES
    categories: list[str] = field(default_factory=list)
    series: list[Series] = field(default_factory=list)
    unit: str = ""                    # e.g. "%", "$", "tasks"
    max_value: float | None = None    # for gauge; auto if None

    kind = "chart"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "chart",
            "title": self.title,
            "chart_type": self.chart_type,
            "categories": list(self.categories),
            "series": [s.to_dict() for s in self.series],
            "unit": self.unit,
            "max_value": self.max_value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChartSpec":
        return cls(
            title=str(d.get("title", "")),
            chart_type=str(d.get("chart_type", "column")),
            categories=[str(c) for c in d.get("categories", [])],
            series=[Series.from_dict(s) for s in d.get("series", [])],
            unit=str(d.get("unit", "")),
            max_value=(float(d["max_value"]) if d.get("max_value") is not None else None),
        )


@dataclass
class TableSpec:
    """A data table / comparison matrix."""
    title: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    kind = "table"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "table",
            "title": self.title,
            "headers": list(self.headers),
            "rows": [[str(c) for c in r] for r in self.rows],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TableSpec":
        return cls(
            title=str(d.get("title", "")),
            headers=[str(h) for h in d.get("headers", [])],
            rows=[[str(c) for c in r] for r in d.get("rows", [])],
        )


@dataclass
class DiagramSpec:
    """A node/edge diagram (architecture, flow, network, org).

    ``nodes``: list of {"id", "label", "type"}. ``edges``: list of
    {"source", "target", "label"}. Compatible with the canvas graph_json
    shape so ``tools/canvas/export_utils`` and ``MermaidParser`` interop.
    """
    title: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    layout: str = "spring"            # one of DIAGRAM_LAYOUTS

    kind = "diagram"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "diagram",
            "title": self.title,
            "nodes": [dict(n) for n in self.nodes],
            "edges": [dict(e) for e in self.edges],
            "layout": self.layout,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DiagramSpec":
        return cls(
            title=str(d.get("title", "")),
            nodes=[dict(n) for n in d.get("nodes", [])],
            edges=[dict(e) for e in d.get("edges", [])],
            layout=str(d.get("layout", "spring")),
        )


@dataclass
class KpiTile:
    """A single KPI tile in a metrics slide."""
    label: str
    value: str
    delta: str = ""                   # e.g. "+12%", "-3"
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "value": self.value, "delta": self.delta, "unit": self.unit}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KpiTile":
        return cls(
            label=str(d.get("label", "")),
            value=str(d.get("value", "")),
            delta=str(d.get("delta", "")),
            unit=str(d.get("unit", "")),
        )


@dataclass
class KpiSpec:
    """A row of KPI tiles (the 'data'/metrics slide)."""
    title: str = ""
    tiles: list[KpiTile] = field(default_factory=list)

    kind = "kpis"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "kpis", "title": self.title, "tiles": [t.to_dict() for t in self.tiles]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KpiSpec":
        return cls(
            title=str(d.get("title", "")),
            tiles=[KpiTile.from_dict(t) for t in d.get("tiles", [])],
        )


@dataclass
class Milestone:
    """One milestone on a timeline."""
    label: str
    when: str                         # free-form date/period label
    status: str = "planned"           # planned | in_progress | done

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "when": self.when, "status": self.status}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Milestone":
        return cls(
            label=str(d.get("label", "")),
            when=str(d.get("when", "")),
            status=str(d.get("status", "planned")),
        )


@dataclass
class TimelineSpec:
    """A horizontal roadmap / timeline."""
    title: str = ""
    milestones: list[Milestone] = field(default_factory=list)

    kind = "timeline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "timeline",
            "title": self.title,
            "milestones": [m.to_dict() for m in self.milestones],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TimelineSpec":
        return cls(
            title=str(d.get("title", "")),
            milestones=[Milestone.from_dict(m) for m in d.get("milestones", [])],
        )


@dataclass
class DashboardSpec:
    """A Tableau-style dashboard: a grid of viz tiles over an optional dataset.

    ``tiles`` is a list of {"spec": <viz spec dict>, "w": <1-12 col span>}.
    Each tile's spec is any serialized viz spec (chart/kpi/table — carries its
    own ``kind``). When ``dataset`` is provided (columns/rows/dimensions/
    measures), the web runtime can filter + re-aggregate all tiles client-side.
    """
    title: str = ""
    tiles: list[dict[str, Any]] = field(default_factory=list)
    dataset: dict[str, Any] | None = None
    filters: list[str] = field(default_factory=list)  # dimension names to expose as filters

    kind = "dashboard"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "dashboard",
            "title": self.title,
            "tiles": [dict(t) for t in self.tiles],
            "dataset": self.dataset,
            "filters": list(self.filters),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DashboardSpec":
        return cls(
            title=str(d.get("title", "")),
            tiles=[dict(t) for t in d.get("tiles", [])],
            dataset=d.get("dataset"),
            filters=[str(f) for f in d.get("filters", [])],
        )


# Union alias for type hints / dispatch
VizSpec = Any  # ChartSpec | TableSpec | DiagramSpec | KpiSpec | TimelineSpec | DashboardSpec

_KIND_MAP = {
    "chart": ChartSpec,
    "table": TableSpec,
    "diagram": DiagramSpec,
    "kpis": KpiSpec,
    "timeline": TimelineSpec,
    "dashboard": DashboardSpec,
}


def spec_from_dict(d: dict[str, Any]) -> VizSpec:
    """Rebuild a concrete spec from its serialized dict (uses the ``kind`` tag)."""
    if not isinstance(d, dict):
        raise TypeError("spec_from_dict expects a dict")
    kind = d.get("kind")
    cls = _KIND_MAP.get(kind)
    if cls is None:
        raise ValueError(f"unknown viz spec kind: {kind!r}")
    return cls.from_dict(d)
