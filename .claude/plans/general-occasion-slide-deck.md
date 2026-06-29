# General-Purpose, Occasion-Aware Slide Deck Generator

## Goal
Extend the existing ICDEV™ Slide Deck Generator (`/slides` canvas) so it can build **general-purpose, occasion-aware presentations** for any open-ended topic or event, with AI-driven research, tone/style selection, and multi-format export.

## In Scope
- New deck type `general_presentation` inside `/slides`.
- Open-ended topic + occasion tag + target audience + tone/style.
- 3-step UI wizard: **Topic/Occasion → Tone/Style → Layout/Output**.
- Conditional research: web search when connected; internal RAG/KG + local LLM fallback when air-gapped.
- AI-generated content with inline citations/source links.
- Tone-matched themes and AI-generated imagery.
- Export to **PPTX**, **PDF**, and **HTML**.
- Synchronous generation, single-slide regeneration with tone/theme swap.
- General decks ignore ICDEV internal sources (`icdev_capabilities`, `canvases`, `kanban`, `genesis`).

## Out of Scope
- Async/background generation queues.
- New canvas or new component registry entry (reuse existing `slides` canvas).
- External image-generation APIs not already supported (DALL-E/Gemini/Ollama stay as-is).

## Assumptions / Interpretations
1. **Theme ≠ Tone**: `theme` is the color palette; `tone` drives writing style and visual style hints. A single deck has one theme and one tone.
2. **Occasion is open text** with a small set of suggested presets (e.g., "Birthday", "Quarterly Review", "Wedding Toast", "Sales Pitch").
3. **Air-gap decision** uses `tools.airgap.detector.is_airgap()`. In air-gap mode the system will **not** call web search and will instead use local LLM + optional RAG/KG.
4. **Citation style** is user-selectable (APA / MLA / Chicago / inline links). Citations are stored per slide and rendered in PPTX/PDF/HTML.
5. **PDF** will be built with `fpdf2` (pure Python, air-gap friendly) following the pattern in `tools/network/pdf_export.py`.
6. **HTML** will be a self-contained export with embedded CSS, following the pattern in `tools/pulse/engine/exporter.py`.
7. Research will return a short synthesized summary plus a list of `{title, url, snippet}` sources; the LLM content agent will cite from this list.

## Files to Change

### 1. `tools/slides/constants.py`
- Add `general_presentation` to `DECK_TYPES`.
- Add `TONES` list: `professional`, `fun`, `creative`, `adventurous`, `minimal`, `bold`.
- Add `CITATION_STYLES` list: `apa`, `mla`, `chicago`, `inline_links`.
- Add new tone-driven themes with palettes:
  - `fun_fiesta` (warm coral / teal / cream)
  - `creative_aurora` (deep purple / magenta / mint)
  - `adventurous_outdoor` (forest green / clay / sky)
  - `minimal_mono` (white / charcoal / accent)
  - `bold_neon` (black / electric lime / hot pink)
- Add `TONE_STYLE_HINTS` mapping tone → writing + visual instructions.
- Add `DEFAULT_TONE = "professional"`, `DEFAULT_CITATION_STYLE = "inline_links"`.
- Add `OUTPUT_FORMATS` list: `pptx`, `pdf`, `html`.
- Update all `CHECK_*` constraints from Python constants.

### 2. `args/slides_config.yaml`
- Add `research` section:
  - `connected_provider`: `web_search` (uses `tools.http.client` / LLM router)
  - `airgap_fallback`: `rag_kg_local_llm`
  - `max_results`: 10
  - `citation_style`: `inline_links`
  - `cache_ttl_minutes`: 60
- Add `export_formats` list: `[pptx, pdf, html]` and `default_enabled`: all three.
- Add new LLM routing function name: `slides_topic_research`.
- Add `tone_style_hints` mirroring `constants.TONE_STYLE_HINTS` for config overrides.

### 3. `tools/slides/db/init_db.py` + migration
- Add columns to `slides_decks`:
  - `occasion TEXT`
  - `tone TEXT`
  - `target_audience TEXT`
  - `citation_style TEXT`
  - `output_formats JSONB` / `TEXT` (SQLite)
  - `pdf_path TEXT`
  - `html_path TEXT`
- Add column to `slides_slides`:
  - `citations JSONB` / `TEXT`
- Create `tools/slides/db/migrations/001_general_presentation.sql` with idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for PG and SQLite.
- Update `init_db()` to apply pending migrations after schema creation.
- Update `tests/conftest.py` `MINIMAL_ICDEV_SCHEMA` to match.

### 4. `tools/slides/research_connector.py` (new)
- `research_topic(topic, occasion, audience, airgap)` returns:
  ```python
  {"summary": str, "sources": [{"title", "url", "snippet"}], "citation_style": str}
  ```
- Connected mode: issue a web search via `tools.http.client` or an LLM router call to `slides_topic_research`; parse response into sources + summary.
- Air-gap mode: skip web; use local LLM (`LLMRouter.invoke(slides_topic_research, ...)`) with no external calls, and optionally query local RAG/KG if `tools.rag` / `tools.kg` are available.
- Citation formatting helper: `format_citations(sources, style)`.

### 5. `tools/slides/engine.py`
- Extend `DeckRequest` with:
  - `occasion`, `tone`, `target_audience`, `citation_style`, `output_formats`.
- In `run()`:
  - If `deck_type == "general_presentation"`, call `research_connector.research_topic()` in `_gather` instead of ICDEV source map.
  - Still allow `upload_text`/`upload_file_path` as supplemental context.
  - Pass tone/occasion/audience/citation_style into `orchestrator.plan_outline()` and `content_agent.generate_all()`.
  - After PPTX build, build PDF and HTML when requested and add their paths to `DeckResult`.
- Extend `DeckResult` with `pdf_path` and `html_path`.
- Update `_create_deck_record` and `_update_deck_record` to persist new columns and per-slide citations.

### 6. `tools/slides/orchestrator.py`
- Add a second system prompt for general decks:
  - Non-ICDEV, occasion-aware narrative arc: hook → context → key points → takeaway → call-to-action.
  - Injects tone, occasion, and audience instructions.
- `plan_outline()` accepts `tone`, `occasion`, `target_audience`, `citation_style` and selects the correct prompt.
- Keep existing ICDEV prompt for legacy deck types.

### 7. `tools/slides/content_agent.py`
- `_generate_one()` accepts `tone` and `citation_style`.
- Inject tone instructions and citation rules into the system prompt.
- Parse `citations` from LLM output and fall back to sources from `raw_content["research"]`.
- Include `citations` in returned slide dict.
- `revise_slide()` accepts optional `tone` override so regenerating a slide can change tone.

### 8. `tools/slides/graphics_generator.py`
- Make style hint dynamic per theme/tone using `THEME_PALETTES` and `TONE_STYLE_HINTS`.
- Update `generate()` signature to accept `theme` and `tone`.
- Update matplotlib fallback to use the requested theme palette instead of hardcoded navy/gold.

### 9. `tools/slides/pptx_builder.py`
- Add new palettes to `THEME_PALETTES` (loaded from constants).
- Add `_build_citation_footer()` for slides with citations.
- Support `quote` slide type better (centered, large text).
- `build()` unchanged signature; export modules handle PDF/HTML.

### 10. `tools/slides/export_pdf.py` (new)
- `build_pdf(slides, theme, title, output_dir)` returns PDF path.
- Uses `fpdf2` with palette colors.
- One page per slide: title, bullets, optional image, speaker notes at bottom, citations footer.
- Classification banner via `classification_manager.py` (CUI default).

### 11. `tools/slides/export_html.py` (new)
- `build_html(slides, theme, title, deck_meta, output_dir)` returns HTML path.
- Self-contained HTML with embedded CSS, slide sections, images, notes toggle, citation links.
- Responsive layout.

### 12. `tools/slides/blueprint.py`
- `/api/generate` accepts new fields and routes to `DeckRequest`.
- Add routes:
  - `GET /api/<id>/download/pdf`
  - `GET /api/<id>/download/html`
  - `POST /api/<id>/regenerate-slide` — regenerates one slide with optional `tone`/`theme`/`feedback`.
- `/new` passes tone/theme/occasion/citation-style/output-format lists to template.
- `/detail` query includes new columns and citations.

### 13. `tools/dashboard/templates/slides/new.html`
- Convert to 3-step wizard:
  - **Step 1**: Title, Topic, Occasion (preset + custom), Target Audience, Max Slides.
  - **Step 2**: Tone, Theme, Citation Style, Graphics toggle.
  - **Step 3**: Output formats (PPTX/PDF/HTML checkboxes), Review & Generate.
- JavaScript advances steps and POSTs the final payload.

### 14. `tools/dashboard/templates/slides/detail.html`
- Show tone/occasion/audience badges.
- Add PDF and HTML download buttons alongside PPTX.
- Render per-slide citations (linked when URL available).
- Add "Regenerate slide" with tone/theme selector in each slide card.

### 15. `tools/dashboard/templates/slides/index.html`
- Show `general_presentation` type badge.
- Show tone badge.
- Add download-format dropdown or separate buttons per deck.

### 16. `tools/iqe/adapters/slides.py`
- Update `decks_adapter` SELECT to include `occasion`, `tone`, `output_formats`.
- Update `slides_adapter` SELECT to include `citations`.

### 17. `tests/conftest.py`
- Update `slides_decks` and `slides_slides` table definitions to match new schema.

### 18. `tests/slides/test_slides_engine.py`
- Add tests:
  - `general_presentation` research connector returns summary/sources.
  - Airgap mode skips web search.
  - Tone changes content style hint.
  - Citations parsed and stored.
  - PDF and HTML export produce files.
  - Single-slide regenerate endpoint updates DB and preserves other slides.

### 19. `tools/manifest/slides.md`
- Document new general-occasion deck type, research connector, tone/theme model, and export formats.

### 20. `args/component_registry.yaml`
- No entry changes required; the existing `slides` canvas entry covers this feature. Verify `completeness.template` still points to the correct wizard page.

## DB Migration Plan
Create `tools/slides/db/migrations/001_general_presentation.sql`:
```sql
-- SQLite + PostgreSQL idempotent migration
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS occasion TEXT;
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS tone TEXT;
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS target_audience TEXT;
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS citation_style TEXT;
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS output_formats JSONB DEFAULT '[]';
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS pdf_path TEXT;
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS html_path TEXT;

ALTER TABLE slides_slides ADD COLUMN IF NOT EXISTS citations JSONB DEFAULT '[]';
```
Update `init_db()` to run pending migrations by comparing a `schema_version` (SQLite `PRAGMA user_version` / PG `slides_meta` table) against migration file names.

## Implementation Phases
1. **Foundation** — constants, config, schema, migration, `research_connector.py`.
2. **Pipeline** — engine, orchestrator, content agent, graphics generator, citation handling.
3. **Exports** — PDF + HTML modules and PPTX citation footer updates.
4. **UI & API** — wizard template, detail/index updates, blueprint routes, single-slide regenerate.
5. **Tests & Compliance** — pytest, ruff, bandit, coherence checker, Playwright V&V.

## Acceptance Criteria
- [ ] `/slides/new` renders the 3-step wizard.
- [ ] `general_presentation` decks can be generated from an open-ended topic.
- [ ] Non-airgap mode performs web search; airgap mode degrades gracefully to local LLM/RAG/KG.
- [ ] PPTX, PDF, and HTML exports are downloadable.
- [ ] Slides contain inline citations/source links.
- [ ] Single-slide regeneration works with tone/theme override.
- [ ] `pytest tests/slides/ -v` passes.
- [ ] `ruff check tools/slides` passes.
- [ ] `python tools/workflow/coherence_checker.py --all --fix --gate` passes.
- [ ] Playwright V&V confirms wizard navigation and download buttons.

## Risks & Mitigations
| Risk | Mitigation |
|------|------------|
| Air-gap detection false positive/negative | Respect `ICDEV_AIRGAP` env override; log detection result. |
| Web search API unavailable | Fallback to LLM-only summary with empty citations. |
| PDF fonts / Unicode glyphs | Use `fpdf2` core fonts with `encode('latin-1', 'replace')` fallback. |
| Theme palette contrast issues | Generate palettes with WCAG-friendly dark/light pairs; test with matplotlib fallback. |
| New DB columns break existing tests | Update `tests/conftest.py` minimal schema before running tests. |

## Success Criteria
A user can open `/slides/new`, type “Sustainable Aviation Tech Trends,” pick occasion “Keynote,” audience “Industry Analysts,” tone “Bold,” and generate a 10-slide deck with cited sources, downloadable as PPTX/PDF/HTML, then regenerate slide 4 with tone “Minimal” without rebuilding the whole deck.
