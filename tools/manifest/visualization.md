# Visualization Kernel (`tools/viz/`)

Shared, air-gap-safe visualization layer. One declarative spec →
multiple deterministic render targets (PPTX, PNG, SVG, HTML) + editable
diagram exports (draw.io, Excalidraw). Reused by slides, PDF reports, and
canvas exports. No spec fabricates data — callers populate specs from real
sources.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Viz Specs | tools/viz/spec.py | Declarative `ChartSpec`/`TableSpec`/`DiagramSpec`/`KpiSpec`/`TimelineSpec` + `spec_from_dict()`; JSON round-trippable for DB persistence | dataclasses | dict |
| Palette | tools/viz/palette.py | Theme-aware colour tokens (reuses `THEME_PALETTES`); hex/rgb01/rgb255/RGBColor + categorical series cycle | theme str | `Palette` |
| PPTX Renderer | tools/viz/render_pptx.py | Native **editable** python-pptx charts (`add_chart`) + tables (`add_table`), themed | (slide, spec, l, t, w, h, theme) | mutates slide |
| PNG Renderer | tools/viz/render_png.py | matplotlib (Agg) `chart_to_png` / `diagram_to_png` → themed PNG for PPTX/PDF embedding | (spec, theme, out_path) | PNG path |
| SVG Renderer | tools/viz/render_svg.py | Pure-stdlib `chart_to_svg` / `diagram_to_svg` → vector for web + PDF | (spec, theme) | SVG str |
| HTML Renderer | tools/viz/render_html.py | `chart_to_html`/`table_to_html`/`kpis_to_html`/`diagram_to_html` (inline SVG + Mermaid) for web deck | (spec, theme) | HTML str |
| Diagram Helper | tools/viz/diagram.py | networkx layout, `to_mermaid`, `from_mermaid` (via `MermaidParser`) | DiagramSpec / str | positions / Mermaid / spec |
| Diagram Export | tools/viz/render_diagram_export.py | `to_drawio` (reuses `export_utils.export_drawio`) + `to_excalidraw` (deterministic scene JSON) | DiagramSpec | `.drawio` XML / `.excalidraw` JSON |
| Slides Viz Mapper | tools/slides/viz_mapper.py | Deterministic real-source-data → viz slide dicts (kanban burndown/progress, canvas status, KPIs) + Story-Point insights + dashboard slide. No LLM, no fabricated numbers. | gathered `raw` dict | list[slide dict] |

## Interactive Storytelling (Tableau-style, Epic F)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Dashboard Spec | tools/viz/spec.py `DashboardSpec` | Grid of viz tiles over an optional embedded dataset; `filters` expose dimensions for live client-side re-aggregation | dataclass | dict |
| Deck Model | tools/viz/deck_model.py | DB slides → JSON deck model the interactive presenter consumes (`build_deck_model`); pre-renders diagrams to SVG, derives Story-Point insights | (deck, slides, theme) | dict |
| Dataset Ingest | tools/viz/dataset.py | Parse CSV/JSON → typed table (dimensions vs measures inferred); `aggregate(rows, cols, dim, measure, agg)` group-by helper (mirrors client) | CSV/JSON text or path | dict / (cats, vals) |
| Story Builder | tools/viz/story_builder.py | Dataset → interactive dashboard (filterable datasetChart + datasetKpi tiles) + auto Story-Point slides with computed insights; no LLM | parsed dataset dict | list[slide dict] |
| Story Runtime | tools/dashboard/static/js/viz_story.js | Client presenter: renders `__DECK` via charts.js (interactive tooltips/legend/animation), Tableau dashboards w/ live filters + KPI/chart re-aggregation, Story rail + insight annotations, keyboard nav | window.__DECK JSON | DOM |

Presenter route `GET /slides/<id>/present` (blueprint) builds the deck model and embeds it; an uploaded CSV/JSON in the New-Deck flow triggers the data-story fast path (`engine._run_dataset_story`).

**Multimodal AI assist (tools/llm):** `LLMRequest.images` + router `_apply_vision_routing`
auto-select vision-capable models (incl. Ollama.com `minimax-m3`) when a request carries
images. See `tools/llm/provider.py`, `tools/llm/router.py`, `args/llm_config.yaml` (`vision`).
