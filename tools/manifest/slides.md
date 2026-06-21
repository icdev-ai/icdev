# Slides Deck Generator

> Agentic PPTX generation from ICDEV™ live data — canvases, kanban, genesis, capabilities.

## Tools

| Tool | Path | Description |
|------|------|-------------|
| `DeckEngine` | `tools/slides/engine.py` | Main orchestration DAG: gather → plan → generate → graphics → build |
| `Orchestrator` | `tools/slides/orchestrator.py` | LLM-based slide outline planner (3-strategy JSON fallback) |
| `ContentAgent` | `tools/slides/content_agent.py` | Parallel per-slide content generation (bullets, notes, visual context) |
| `GraphicsGenerator` | `tools/slides/graphics_generator.py` | Two-stage image pipeline: LLM prompt → Ollama/DALL-E/Gemini/Pillow |
| `PptxBuilder` | `tools/slides/pptx_builder.py` | python-pptx assembly with 3 themes |
| `InputParser` | `tools/slides/input_parser.py` | text/PDF/DOCX ingestion |
| Source: capabilities | `tools/slides/sources/icdev_capabilities.py` | ICDEV feature catalog |
| Source: canvases | `tools/slides/sources/canvases.py` | Active canvas list from feature flags |
| Source: child_apps | `tools/slides/sources/child_apps.py` | Showcase/child application catalog |
| Source: kanban | `tools/slides/sources/kanban.py` | Project epics + task burndown |
| Source: genesis | `tools/slides/sources/genesis.py` | Genesis reflex run summaries |
| Blueprint | `tools/slides/blueprint.py` | Flask canvas at `/slides/` (7 routes) |
| IQE Adapter | `tools/iqe/adapters/slides.py` | slides.decks + slides.slides collections |
| Genesis Reflex | `tools/genesis/reflexes/slides.py` | Weekly Friday 17:00 auto-deck |
| DB Init | `tools/slides/db/init_db.py` | PG-first canvas DB (slides_decks, slides_slides, slides_audit) |
| Curated: Innovation Lab | `tools/slides/curated_decks/innovation_lab_business_case.py` | Executive investment brief — 12-slide narrative deck with Excel-derived differentiators and editable ROI placeholders |

## Configuration

- `args/slides_config.yaml` — pipeline, graphics, themes, sources, genesis reflex
- `.env` — `ICDEV_SLIDES_ENABLED=true`, `SLIDES_IMAGE_PROVIDER`, `SLIDES_IMAGE_MODEL`
- `args/llm_config.yaml` — 4 routing functions: `slides_outline_planning`, `slides_content_generation`, `slides_content_revision`, `slides_visual_prompt`

## LLM Models

- `minimax-m3` — Ollama cloud, 1M context, native multimodality; PRIMARY for outline planning + visual prompts
- `qwen3-local` — local Qwen3 9B; PRIMARY for content generation
- Fallback chains defined in `args/llm_config.yaml`

## Image Generation

| Provider | Backend | Notes |
|----------|---------|-------|
| `ollama_cloud` | POST /v1/images/generations | Requires SLIDES_IMAGE_MODEL (e.g. sdxl:latest) |
| `dalle` | OpenAI DALL-E 3 | Requires OPENAI_API_KEY |
| `gemini` | Gemini Imagen 3 | Requires GOOGLE_API_KEY |
| `matplotlib` | Programmatic | Default; always available; air-gap safe |

## CLI

```bash
# Run demo deck (kanban + canvases, matplotlib graphics)
python -c "from tools.slides.engine import DeckEngine; r = DeckEngine().run_demo(); print(r.pptx_path)"

# Genesis reflex manual trigger
python tools/genesis/daemon.py --reflex slides --once --json

# Dashboard
# http://localhost:5050/slides
```
