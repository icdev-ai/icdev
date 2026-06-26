# Slides Deck Generator

> Agentic PPTX generation from ICDEV™ live data **or any topic**. AI-researched, occasion-aware, themed, citable, and exportable to PPTX/PDF/HTML.

## Tools

| Tool | Path | Description |
|------|------|-------------|
| `DeckEngine` | `tools/slides/engine.py` | Main orchestration DAG: gather/research → plan → generate → graphics → build → export |
| `Orchestrator` | `tools/slides/orchestrator.py` | LLM-based outline planner; deck-type-specific prompts + general-occasion prompt |
| `ContentAgent` | `tools/slides/content_agent.py` | Parallel per-slide content generation (bullets, notes, citations, visual context) |
| `GraphicsGenerator` | `tools/slides/graphics_generator.py` | Theme+tone-aware image pipeline: dispatches to the ICDEV-native `AssetGenerator` (SDXL/matplotlib/SVG) |
| `AssetGenerator` | `tools/viz/asset_generator.py` | ICDEV-native unified media dispatcher for slides, pulse, and viz graphics; air-gap aware, cacheable, GPU/SVG fallbacks |
| `PptxBuilder` | `tools/slides/pptx_builder.py` | python-pptx assembly with 8 themes + citation footer |
| `ExportPDF` | `tools/slides/export_pdf.py` | fpdf2 PDF export (themed, air-gap safe) |
| `ExportHTML` | `tools/slides/export_html.py` | Self-contained responsive HTML export |
| `ResearchConnector` | `tools/slides/research_connector.py` | Topic research: web search in connected mode, LLM+KG fallback in air-gap mode |
| `InputParser` | `tools/slides/input_parser.py` | text/PDF/DOCX ingestion |
| Source: capabilities | `tools/slides/sources/icdev_capabilities.py` | ICDEV feature catalog |
| Source: canvases | `tools/slides/sources/canvases.py` | Active canvas list from feature flags |
| Source: child_apps | `tools/slides/sources/child_apps.py` | Showcase/child application catalog |
| Source: kanban | `tools/slides/sources/kanban.py` | Project epics + task burndown |
| Source: genesis | `tools/slides/sources/genesis.py` | Genesis reflex run summaries |
| Blueprint | `tools/slides/blueprint.py` | Flask canvas at `/slides/` (10+ routes: wizard, detail, generate, revise, regenerate, PPTX/PDF/HTML download, IQE) |
| IQE Adapter | `tools/iqe/adapters/slides.py` | slides.decks + slides.slides collections |
| Genesis Reflex | `tools/genesis/reflexes/slides.py` | Weekly Friday 17:00 auto-deck |
| DB Init | `tools/slides/db/init_db.py` | PG-first canvas DB with idempotent migration runner |
| Curated: Innovation Lab | `tools/slides/curated_decks/innovation_lab_business_case.py` | Executive investment brief — 12-slide narrative deck with Excel-derived differentiators and editable ROI placeholders |

## Deck Types

- `executive_overview` — ICDEV™ platform pitch from live sources
- `compliance_briefing` — regulatory / crosswalk summary
- `sales_play` — customer-facing value narrative
- `technical_deep_dive` — architecture / engineering detail
- `training_workshop` — onboarding / enablement
- `general_presentation` — **any open-ended topic/occasion**; skips ICDEV internal sources and uses the research connector

## Wizard

Dashboard wizard at `/slides/new` is three steps:

1. **Topic & Occasion** — title, occasion, target audience, deck type, max slides
2. **Tone & Style** — tone selector (professional, visionary, casual, playful, authoritative, warm, dramatic, minimal, fun_fiesta, adventurous_outdoor), theme palette
3. **Layout & Output** — export formats (PPTX + optional PDF/HTML), citation style, ICDEV sources (hidden for general decks), graphics toggle, extra context

## Configuration

- `args/slides_config.yaml` — pipeline, research, graphics, themes, tones, sources, genesis reflex
- `.env` — `ICDEV_SLIDES_ENABLED=true`, `SLIDES_IMAGE_PROVIDER`, `SLIDES_IMAGE_MODEL`
- `args/llm_config.yaml` — 5 routing functions: `slides_outline_planning`, `slides_content_generation`, `slides_content_revision`, `slides_visual_prompt`, `slides_topic_research`

## LLM Models

- `minimax-m3` — Ollama cloud, 1M context, native multimodality; PRIMARY for outline planning + visual prompts + topic research
- `qwen3-local` — local Qwen3 9B; PRIMARY for content generation
- Fallback chains defined in `args/llm_config.yaml`

## Image Generation

| Provider | Backend | Notes |
|----------|---------|-------|
| `ollama_cloud` | POST /v1/images/generations | Requires SLIDES_IMAGE_MODEL (e.g. sdxl:latest) |
| `dalle` | OpenAI DALL-E 3 | Requires OPENAI_API_KEY |
| `gemini` | Gemini Imagen 3 | Requires GOOGLE_API_KEY |
| `matplotlib` | Programmatic | Default; always available; air-gap safe |

## Research Modes

| Mode | Detection | Source |
|------|-----------|--------|
| Connected | `tools.airgap.detector.is_airgap()` returns False | DuckDuckGo HTML search + inline source URLs |
| Air-gapped | `is_airgap()` returns True | Local LLM knowledge + optional Knowledge Graph search; no external HTTP |

## CLI

```bash
# Run demo deck (kanban + canvases, matplotlib graphics)
python -c "from tools.slides.engine import DeckEngine; r = DeckEngine().run_demo(); print(r.pptx_path)"

# Generate a general-occasion deck
python -c "
from tools.slides.engine import DeckEngine, DeckRequest
r = DeckEngine().run(DeckRequest(
    title='AI in Agriculture',
    deck_type='general_presentation',
    tone='visionary',
    occasion='keynote',
    target_audience='industry analysts',
    output_formats=['pptx','pdf','html']
))
print(r.pptx_path, r.pdf_path, r.html_path)
"

# Genesis reflex manual trigger
python tools/genesis/daemon.py --reflex slides --once --json

# Dashboard
# http://localhost:5050/slides
```
