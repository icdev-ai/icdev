<!-- CUI -->
# AI-ify Determination — aiify-rm-06d89-phase-5993

**Disposition:** Closed as **duplicate of aiify-opp-6086** (`_ai_metadata_extraction`).

| Field | Value |
|-------|-------|
| Kanban ID | `aiify-rm-06d89-phase-5993` |
| Roadmap | `rm-06d89040cf` (Phase 2 — Core Modernization) |
| Scan ID | 43 |
| Opportunity ID | 5993 |
| Pattern → Paradigm | `metadata_extraction` → `llm_generation` |
| Model recommendation | claude-sonnet-4-6 |
| External module_path | `…/aiify_git_zwu66zfu/src/documents/bulk_edit.py` (paperless-ngx clone, **reaped**) |

## Rationale

The `module_path` points at a temporary `aiify_git_*` shallow clone of the external
paperless-ngx repository, which the AI-ify engine clones, scans, then deletes
(`engine.py` `shutil.rmtree`). The file is gone and was never part of ICDEV — it is
unmodifiable. Per the established disposition for this family, the AI-ification lands
in the **analogous ICDEV subsystem**, the Document Intelligence Canvas (DIC).

This is a `metadata_extraction` → `llm_generation` sibling of **aiify-opp-6086**
(originally `src/documents/views.py`; siblings have included `workflows/mutations.py`
— 6092). Filename differs (`bulk_edit.py`) but pattern + paradigm are identical, so the
internal analog is the same: `_ai_metadata_extraction` in
`tools/document_intelligence/ingest_orchestrator.py`.

## Verification at HEAD (irad/feature, `cd21b8dc3`)

- `_ai_metadata_extraction` — def L646, wired into ingest flow L1041-1043
- `extract_metadata` flag default `True` — L953
- `_METADATA_DOC_TYPES` closed enum — L614
- `_METADATA_MIN_CONFIDENCE = 0.70` — L625, gate L712
- Tests: `tests/test_dic_ingest_metadata.py` — **17/17 pass**

Note: original 6086 commit `6a388264e` is no longer a direct ancestor (squashed/merged
via PR) but the implementation and tests are present and green at HEAD — same situation
recorded for sibling 6092.

## Outcome

No new code required. Card moved to **done** with `bypass_verification: true` and a
`bypass_reason` naming this determination. LLM model selection remains Router-driven
(`.env`), not hardcoded, per CLAUDE.md.
<!-- CUI -->
