<!-- CUI // SP-CTI -->
# Phase 2 — AI-ify Opportunity 116 (Determination: Duplicate)

- **Kanban task:** `aiify-rm-a3344-phase-116`
- **Roadmap:** `rm-a334408112` (scan_id 1)
- **Opportunity:** 116
- **Pattern → paradigm:** `document_ingestion_pipeline` → `llm_generation`
- **External module:** `paperless/models.py` (paperless-ngx shallow clone `aiify_git_5cc2wcba`, since reaped/GONE — external, unmodifiable)

## Determination

**Duplicate of `aiify-opp-6098`** — the DIC `ingest_orchestrator.py` LLM document-summary enrichment.

The external scan flagged paperless-ngx `models.py` with `function_name: "<unknown>"`, meaning it could not pinpoint a specific ingestion routine. The generic `document_ingestion_pipeline -> llm_generation` pattern it emits is already fully realized in the analogous ICDEV subsystem (DIC = `tools/document_intelligence/`).

Commit `65f1c8f11` (`feat(aiify-opp-6098)`) added `_ai_document_summary()` to `tools/document_intelligence/ingest_orchestrator.py`. It:
- Grounds the LLM on the document's own leading text (capped at `_SUMMARY_INPUT_CHARS = 6000`);
- Produces a strict JSON `{"title": ..., "summary": ...}` via `LLMRouter().invoke("summarization", ...)`;
- Degrades silently to `None` on any failure so ingestion is never blocked;
- Is invoked during the main `ingest_file()` flow at line 1497.

This is the faithful AI-ification of the `document_ingestion_pipeline -> llm_generation` pattern for the DIC subsystem.

## Verification (branch kanban/aiify-rm-a3344-phase-116)

- External clone `aiify_git_5cc2wcba/.../paperless/models.py`: **GONE** (reaped by engine; clone dir empty).
- `65f1c8f11` is an **ancestor of HEAD** (`git merge-base --is-ancestor 65f1c8f11 HEAD` → exit 0).
- `_ai_document_summary` present in:
  - `tools/document_intelligence/ingest_orchestrator.py`
  - `icdev/tools/document_intelligence/ingest_orchestrator.py` (mirror)
- `tests/test_dic_ingest_ai_summary.py`: **passes** (bounded input, JSON parsing, fenced-code tolerance, graceful degradation).

No new code required — closing as a duplicate with `bypass_verification`.
