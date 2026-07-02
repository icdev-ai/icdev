# CUI // SP-CTI
"""ICDEV™ Visualization Kernel (`tools.viz`).

A single declarative spec → multiple deterministic render targets:

  - render_pptx : native python-pptx charts + tables (editable, air-gap)
  - render_png  : matplotlib (Agg) charts/diagrams → PNG (for PPTX/PDF embedding)
  - render_svg  : pure-stdlib SVG (for web + vector PDF)
  - render_html : web fragments wired to charts.js / mermaid.js / sigma.js

Every renderer works fully offline. No spec invents data — callers populate
specs from real source data; the kernel only draws what it is given.

Shared across slides, PDF reports, and canvas exports.
"""
from __future__ import annotations

from tools.viz.spec import (
    ChartSpec,
    Chart3DSpec,
    DashboardSpec,
    DiagramSpec,
    KpiSpec,
    KpiTile,
    Milestone,
    Series,
    TableSpec,
    TimelineSpec,
    VizSpec,
)

__all__ = [
    "ChartSpec",
    "Chart3DSpec",
    "DashboardSpec",
    "DiagramSpec",
    "KpiSpec",
    "KpiTile",
    "Milestone",
    "Series",
    "TableSpec",
    "TimelineSpec",
    "VizSpec",
]
