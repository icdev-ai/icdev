<!-- CUI // SP-CTI -->

# Phase 2 — AI-ify Determination: `aiify-rm-06d89-phase-6008`

- **Opportunity ID:** 6008
- **Scan ID:** 43
- **Roadmap ID:** `rm-06d89040cf`
- **Phase:** Phase 2 — Core Modernization
- **Pattern:** `metadata_extraction` → `llm_generation`
- **External module:** `C:\Users\schuo\AppData\Local\Temp\claude\aiify_git_zwu66zfu\src\documents\data_models.py`
- **Model recommendation:** `claude-sonnet-4-6`
- **Disposition:** Closed as **duplicate of opportunity 6086** (`bypass_verification: true`)

## Determination

The opportunity's `module_path` points at a **shallow git clone** of an external
open-source repo (paperless-ngx) that the AI-ify engine clones into a
`tempfile.mkdtemp(prefix="aiify_git_")` directory, scans, and then deletes
(`engine.py:_clone_git_url` / `shutil.rmtree`). `src/documents/data_models.py` is a
new filename in the same paperless `src/documents/*` subtree already covered by the
`metadata_extraction → llm_generation` family (6086, 6092, 5993, 5994). The external
file is not part of the ICDEV™ codebase and is unmodifiable.

Per the established disposition for this family, the AI-ification lands in the
**analogous ICDEV internal subsystem** — the Document Intelligence Canvas (DIC),
`tools/document_intelligence/ingest_orchestrator.py` — rather than the deleted
external file. That work shipped as **opportunity 6086** (commit `6a388264e`,
merged to `irad/feature` via PR squash).

## Verification at HEAD (`433a115c4`, branch `irad/feature`)

- `_ai_metadata_extraction(text, filename)` present — `ingest_orchestrator.py:646`
- Wired into the ingest path — `ingest_orchestrator.py:1041-1043`
- `extract_metadata` flag default `True` — `ingest_orchestrator.py:953`
- Closed doc-type enum `_METADATA_DOC_TYPES` — `ingest_orchestrator.py:614`
- Confidence gate `_METADATA_MIN_CONFIDENCE = 0.70` — `ingest_orchestrator.py:625`, `:712`
- Tests: **17/17 pass** — `tests/test_dic_ingest_metadata.py`

The metadata proposal is surfaced as a **HITL proposal** on `IngestOutcome.metadata`
(never silently persisted), with `document_type` constrained to the closed enum,
`tags` lower-cased/de-duped/length+count capped, and a real ISO `date`, all gated
by the `_METADATA_MIN_CONFIDENCE = 0.70` threshold. The model ID is selected from
`.env` via the LLM Router (no hardcoded model ID per CLAUDE.md).

## Conclusion

No new code required — the internal analog already exists and is green. Card moved
to **done** with `bypass_verification: true` and a `bypass_reason` naming commit
`6a388264e` and this determination document.
