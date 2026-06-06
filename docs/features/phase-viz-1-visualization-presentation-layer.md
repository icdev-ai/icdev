<!-- CUI // SP-CTI -->
# VIZ — Visualization & Presentation Layer

Closes ICDEV's "everything is text" gap. Adds a shared, air-gap-safe
visualization kernel and uses it to make `/slides` genuinely professional
(charts, tables, diagrams, KPIs) in both editable PPTX and a web-native
presenter, and to enrich PDF reports and canvas exports.

## What shipped

### Viz Kernel — `tools/viz/`
One declarative spec → multiple deterministic, offline renderers.
- `spec.py` — `ChartSpec`, `TableSpec`, `DiagramSpec`, `KpiSpec`, `TimelineSpec`
  (JSON round-trippable via `spec_from_dict`).
- `palette.py` — theme tokens; reuses `tools/slides/constants.THEME_PALETTES`.
- `render_pptx.py` — **native editable** PowerPoint charts (`add_chart`) + tables.
- `render_png.py` — matplotlib (Agg) chart/diagram PNGs for PPTX/PDF embedding.
- `render_svg.py` — pure-stdlib vector charts/diagrams (web + PDF).
- `render_html.py` — inline-SVG fragments (charts/tables/KPIs) + Mermaid (opt-in).
- `diagram.py` — networkx layout + Mermaid to/from.
- `render_diagram_export.py` — **draw.io** XML + **Excalidraw** scene JSON (editable).

### Slides — rich, data-driven decks
- `pptx_builder.py` now dispatches on slide type / viz payload: chart, table,
  diagram, KPI, agenda, quote, plus existing title/content/outro.
- `viz_mapper.py` builds **deterministic** data slides from real sources
  (kanban progress & workload, canvas status, KPI tiles) — no fabricated numbers.
- 4 additive JSON columns (`chart_json`/`table_json`/`diagram_json`/`kpis_json`)
  via idempotent migration in `db/init_db.py` (no CHECK-constraint churn).

### Web-native presenter — `/slides/<id>/present`
- Self-contained full-screen viewer (`templates/slides/present.html` + icdev mirror):
  arrow/space nav, speaker-notes (`S`), full-screen (`F`), progress bar, CUI banner.
- Charts render as inline SVG (deterministic, no JS dependency); diagrams as inline
  SVG by default (Mermaid opt-in — Mermaid mis-renders in hidden slides).
- Fixed a latent bug: added the missing `GET /slides/api/image` route (path-traversal
  guarded) that `detail.html` already referenced.

### Cross-subsystem reuse
- `tools/agentic_ai_canvas/export_pdf.py` embeds a kernel topology diagram.
- `tools/canvas/export_utils.py` gains `export_excalidraw` via the kernel.

### Multimodal AI assist (Ollama.com)
- `LLMRequest.images` + router `_apply_vision_routing` auto-select vision-capable
  models (incl. Ollama.com `minimax-m3`) when a request carries images.

### Reliability fix (pre-existing)
- `tools/budget/module_budget_tracker.py` ran unbounded DB I/O on every
  `router.invoke()`. Now bounded (4s statement/lock timeout) and **fail-open**, so a
  locked/slow budget DB can never hang an LLM call.

## Dependencies
`requirements.txt`: declared `python-pptx`, `matplotlib`, `reportlab` (previously
imported but undeclared — install-time gap closed).

## Verification
- `pytest tests/viz tests/slides/test_viz_slides.py tests/slides/test_present_routes.py
  tests/llm/test_vision_routing.py tests/budget/test_module_budget_resilience.py
  tests/viz/test_epic_d_reuse.py` — all green.
- Live browser V&V (Playwright): presenter renders title, KPIs, bar chart, table,
  diagram, content, outro; keyboard nav; **0 console errors**. Screenshots in
  `playwright/screenshots/slides_present_*.png`.

## Epic F — Interactive storytelling (Tableau-style)

The presenter evolved from static server-rendered slides into an **interactive,
data-driven app** (`tools/dashboard/static/js/viz_story.js`) driven by a JSON deck
model (`tools/viz/deck_model.py`). Charts render via the vendored `charts.js`
(hover tooltips, legend toggle, animation) — no new deps, air-gap safe.

- **Story Points:** per-slide insight annotations + a clickable story rail; insights are
  auto-derived (explicit `insight`, else first sentence of notes), and `viz_mapper`/
  `story_builder` compute them from real numbers ("DIC leads at 90%").
- **Dashboards:** `DashboardSpec` (grid of KPI/chart/table tiles). ICDEV decks get a
  Portfolio Dashboard; datasets get a filterable one.
- **Live filters (Tableau core):** uploaded CSV/JSON (`tools/viz/dataset.py`) →
  `story_builder.build_dataset_slides` produces a dashboard with `datasetChart` +
  `datasetKpi` tiles + filter dropdowns. Changing a filter re-aggregates **all charts
  AND KPIs** client-side. Verified: REGION=West drove KPIs `11/4/90 → 3/1/22` and the
  region chart from 4 bars → 1, donut re-proportioned. 0 console errors.
- **Upload UX:** New-Deck form has a "Data Story (paste CSV/JSON)" field; the engine
  auto-detects tabular content and takes the data-story fast path.
- Screenshots: `playwright/screenshots/story_dashboard.png`, `story_chart.png`,
  `story_dataset_dashboard.png`, `story_dataset_filtered_west.png`.
- Bug fixed during V&V: charts rendered into detached DOM nodes (empty); `viz_story.js`
  now defers rendering to `requestAnimationFrame` after the slide is committed.

## Epic G — Freeform editor + canvas bridge (WYSIWYG)

A positioned **element model** (`tools/viz/elements.py`) is now the single source of
truth: `elements: [{type, x, y, w, h, z, payload, style}]` with **fractional 16:9
geometry**, so the same numbers drive the web editor (CSS) and python-pptx
(`Inches(x*13.33)`). Stored in an additive `elements_json` column; auto-layout
converts any existing slide into editable elements.

- **Freeform editor** `/slides/<id>/edit` (`tools/dashboard/static/js/viz_editor.js`):
  drag, resize (corner handles), select, layer, delete; **custom text boxes with
  font size / family / color / bold / italic / align**; live chart/table/KPI/diagram
  element rendering. Saves the element model; verified add→save→reload persistence
  and 0 console errors in a real browser.
- **Image import** (`POST /slides/api/<id>/upload-image`): upload → stored asset →
  draggable/resizable `image` element → `add_picture` in PPTX.
- **WYSIWYG PPTX**: `pptx_builder` renders a freeform slide by absolute coordinates
  for every element type; **download re-renders from current elements**, so the
  PowerPoint always matches the editor (verified: edited element layout → downloaded
  PPTX has the positioned chart).
- **Canvas bridge** (`tools/slides/canvas_bridge.py`): enumerates designs across 9
  canvases (`graph_json` → native `DiagramSpec`); `/slides/<id>/add-from-canvas`
  picker (curate) + `POST /slides/api/aggregate-canvases` (auto-overview) + a generic
  `POST /slides/api/<id>/capture` (native graph/chart, or **image fallback** from a
  client-serialized card). "+ From Canvas" in the editor opens the picker.

## Deferred (documented, intentional)
- **AI hero imagery** — optional polish; air-gap data-driven charts/diagrams already
  meet the professional bar. Wire `tools/pulse/engine/image_generator.py` SVG fallback later.
- **4th `modern_light` theme** — deferred to avoid churning the `slides_decks`
  `CHECK(theme)` constraint on already-created PG tables; add with a constraint migration.
