# BI Dashboard Canvas

## CUI // SP-CTI

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

Natural-language-driven analyst/executive dashboards at `/bi_dashboard`: describe a
chart in plain English, the AI drafts a Viz Kernel spec (2D or 3D) from real queried
data, and tiles compose into savable dashboards. Spec layer round-trips through
`tools.viz.spec`; render is ECharts.

## BI Dashboard Canvas

| Tool | File | Purpose |
|------|------|---------|
| BI Dashboard Blueprint | `tools/bi_dashboard/blueprint.py` | Flask blueprint mounting the canvas at `/bi_dashboard`. Serves the NL→chart authoring UI and dashboard views; wires the spec generator, data source, store, ECharts adapter, and export into routes. |
| Dashboard Store | `tools/bi_dashboard/dashboard_store.py` | CRUD for `bi_dashboards` / `bi_generation_log`. Persists dashboards in the exact `DashboardSpec.tiles` shape (`[{"spec": <viz spec dict>, "w": <1-12>}, ...]`) so a saved dashboard reloads without re-generation. |
| Data Source | `tools/bi_dashboard/data_source.py` | Uploaded-file ingestion. Bridges a CSV/XLSX/JSON upload through `tools.viz.dataset.parse_dataset` (bounded to `MAX_ROWS`/`MAX_COLS`; macro-enabled `.xlsm` rejected outright) into the columns/dimensions/measures/rows shape the spec generator consumes. |
| ECharts Adapter | `tools/bi_dashboard/echarts_adapter.py` | Render-time-only translation of `ChartSpec` / `Chart3DSpec` into Apache ECharts `option` JSON. The DB-persisted spec format (`tools.viz.spec`) never becomes raw ECharts JSON at rest — the frontend requests the option per render. |
| Dashboard Export | `tools/bi_dashboard/export.py` | Export a whole dashboard to HTML, PPTX, or PDF (prem-rpt-01). Composes every tile's rendered chart and layout into a single shareable artifact. |
| Spec Generator | `tools/bi_dashboard/spec_generator.py` | NL → Viz Kernel spec generation. Given a user's natural-language ask and an already-queried dataset (columns/dimensions/measures/rows from `data_source`), drafts a 2D/3D chart spec grounded in the real data shape. |
