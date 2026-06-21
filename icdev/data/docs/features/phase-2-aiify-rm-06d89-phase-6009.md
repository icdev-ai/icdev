<!-- CUI // SP-CTI -->

# AI-ify Determination — `aiify-rm-06d89-phase-6009`

- **Opportunity ID:** 6009
- **Scan ID:** 43
- **Roadmap:** `rm-06d89040cf`
- **Phase:** Phase 2 — Core Modernization
- **Pattern:** `metadata_extraction` → `llm_generation`
- **External module_path:** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\documents\data_models.py` (paperless-ngx clone)
- **Model recommendation:** `claude-sonnet-4-6`
- **Date:** 2026-06-05

## Determination: DUPLICATE of opportunity 6086 — closed, no new code

The `module_path` is a temporary `aiify_git_zwu66zfu` shallow clone of the external
paperless-ngx repository that the AI-ify engine clones, scans, then deletes
(`engine.py` `_clone_git_url` → `shutil.rmtree`). The clone has already been reaped
(`src/documents/data_models.py` no longer exists on disk), so the external file is
unmodifiable and is not part of the ICDEV™ codebase.

Per the established disposition for paperless `src/documents/*`
`metadata_extraction`→`llm_generation` opportunities, the AI-ification is landed in the
**analogous ICDEV internal subsystem** — the Document Intelligence Canvas (DIC),
`tools/document_intelligence/ingest_orchestrator.py` — via the `_ai_metadata_extraction`
helper shipped under opportunity **6086** (commit `6a388264e`). Opportunity 6009 is the
same pattern/paradigm against a different filename in the same paperless subtree
(`data_models.py`), a sibling of already-closed 6086 / 6092 / 5993 / 5994.

## Verification at HEAD (`433a115c4`, branch `irad/feature`)

- `_ai_metadata_extraction` defined at `ingest_orchestrator.py:646`
- Wired into the ingest flow at `ingest_orchestrator.py:1041-1043`
- `extract_metadata` flag default `True` at `ingest_orchestrator.py:953`
- Closed enum `_METADATA_DOC_TYPES` at `ingest_orchestrator.py:614`
- Confidence gate `_METADATA_MIN_CONFIDENCE = 0.70` at `ingest_orchestrator.py:625` / `:712`
- Tests: `tests/test_dic_ingest_metadata.py` — **17/17 pass**

LLM proposes structured `document_type` (closed enum → `"other"`), normalized `tags`, and
a real ISO `date`, gated by a 0.70 confidence floor; surfaced as a **HITL proposal** on
`IngestOutcome.metadata` (never silently persisted). The model is selected from `.env`
via `LLMRouter` (no hardcoded model ID, per CLAUDE.md).

## Disposition

Card moved to **done** with `bypass_verification: true` and a `bypass_reason` naming
opportunity 6086 / commit `6a388264e` as the implementing change. No new production code —
the requested capability already exists and is green.
