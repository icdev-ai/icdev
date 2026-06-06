# [CUI // SP-CTI] Phase H — Presentation Studio

Self-service, intuitive presentation builder at `/slides`, extending the VIZ layer
(Epics A–G) into a Figma-grade Studio. AI orchestrates; deterministic tools render.

## What shipped (H1–H7, all on `irad/feature`)

| Epic | Capability | Key commits |
|------|------------|-------------|
| H1 | Slide authoring: add/duplicate/delete/reorder, undo/redo, debounced autosave, speaker notes; presenter renders freeform edits | `06b4b5762` |
| H2 | Chart & table **builders** — data-entry grids + live preview (no JSON) | `dc0db5484` |
| H3 | Figma-grade editor: shapes, smart snapping/alignment guides, layers panel (hide/lock/z), icons, rich text (lists/underline), zoom/pan, multi-select + group + align/distribute | `a24987f82` `72ae4348a` `027eea8e8` `b9c7b1992` |
| H4 | AI **prompt-to-deck** ("describe your presentation"), template gallery, per-deck theme switcher | `7b9312da7` |
| H5 | Deck management: rename + tags, duplicate, delete, search | `2d5720fda` |
| H6 | Export & sharing: **PDF** (`/api/<id>/download.pdf`), read-only **share links** (`/slides/share/<token>`), presenter **timer + next-slide + slide sorter** | `8c090d3a5` `f3cdb6b29` |
| H7 | Guided onboarding: first-visit walkthrough, help panel, simple/advanced toggle | `280035143` |

## Architecture
- **Positioned element model** (`tools/viz/elements.py`, fractional 16:9) is the single
  source of truth: drives the CSS editor (`viz_editor.js`), the client presenter
  (`viz_story.js`), the PPTX builder (`pptx_builder.py`), and the PDF exporter
  (`pdf_export.py`) — everything stays WYSIWYG.
- New element types `shape`/`icon`; props `opacity`/`cornerRadius`/`stroke`/`hidden`/
  `locked`/`groupId`; rich-text `list`/`underline` in `style`.
- Deck templates: `tools/slides/templates.py`. Additive deck columns `tags`, `share_token`
  (idempotent migration in `db/init_db._migrate_viz_columns`).
- AI path: `engine.DeckRequest.prompt` → `orchestrator.plan_outline(brief=)` +
  `content_agent.generate_all(brief=)` use a general (non-ICDEV) designer prompt.

## Air-gap
Deterministic throughout: matplotlib chart/diagram PNGs, reportlab PDF, bundled SVG icon
set, no CDN. LLM features degrade gracefully and can route through the local Claude CLI
(`ICDEV_CLI_BRIDGE=true`, see [airgap-runbook](../ops/airgap-runbook.md#8a-local-claude-cli-bridge-no-cloud-key-no-ollama)).

## Tests / V&V
`tests/viz/` + `tests/slides/` (elements, builders, templates, deck-mgmt, export). Every
epic V&V'd live via a throwaway Flask server + Playwright (0 console errors); screenshots
under `playwright/screenshots/editor_h*.png` and `present_h6_aids.png`.

## Operational note
Restart the dashboard so it picks up the new blueprint/templates (a stale pre-H4 instance
was observed serving 404s on the new routes).
