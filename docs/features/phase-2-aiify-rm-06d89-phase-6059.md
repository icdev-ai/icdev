# CUI // SP-CTI

# Phase 2 — AI-ify: Extraction-Quality Anomaly Detection for `paperless-ngx` `src/documents/serialisers.py`

**Task:** `aiify-rm-06d89-phase-6059`
**Opportunity:** 6059 (scan 43, roadmap `rm-06d89040cf`)
**Pattern:** `hardcoded_threshold` → **`anomaly_detection`**
**Phase:** P2 — Core Modernization
**Recommended model:** `claude-haiku-4-5-20251001`
**Scores:** composite 0.547 · value 0.523 · feasibility 0.7475 · risk 0.75

> **Scope note — advisory, external repo.** Scan 43 targets the third-party
> open-source project **paperless-ngx**, cloned into an ephemeral temp directory
> (`aiify_git_zwu66zfu`) that no longer exists. ICDEV does **not** own or vendor
> this code, so the deliverable is an **augmentation design recommendation** plus
> an implementation in the analogous ICDEV subsystem (DIC). No paperless-ngx
> runtime is modified. This mirrors the sibling opportunities 6048 (date-parsing
> anomaly, `ingest_orchestrator.py`) and 6052 (search-relevance anomaly,
> `search_engine.py`).

## Opportunity

The scanner flagged `src/documents/serialisers.py` — the DRF **document-record
serialisation/validation layer** of a document-management backend. A serialiser
of this kind keeps a single **hardcoded threshold** (`hardcoded_threshold`):
accept any record whose text/field length is above some constant *N*, reject the
rest. That fixed number is blind to two silent-failure modes it structurally
cannot see:

1. **Silent extraction failure** — a record that came back *structurally valid*
   (a real `page_count`, no library error) yet yielded **near-empty text**: a
   scanned PDF whose OCR produced nothing, a DOCX whose body never decoded. The
   record looks fine to a length check that happens to pass on metadata.
2. **Yield cliff** — a batch where most records extracted richly and a few fell
   off a statistical edge into a near-empty noise tail.

A single inline length cutoff cannot express either: the first needs the notion
of *text-per-page yield*, the second needs the *batch distribution*.

## Analogous ICDEV subsystem

The Document Intelligence Canvas (DIC) `extractors.py` layer is the direct
analog — it builds the `Extraction(text, provider, content_type, page_count,
title, …)` record that *is* the serialised document, exactly what paperless's
`serialisers.py` shapes and validates on the way in. It previously had **no**
quality anomaly detection (date-parsing lives in `ingest_orchestrator.py`,
search-relevance in `search_engine.py`, freshness in `freshness_engine.py`).

## What shipped

All additions are in `tools/document_intelligence/extractors.py`, modeled
one-to-one on the 6052 search-anomaly block.

### 1. Named per-page text-yield bands (lift the hardcoded threshold)

```python
_YIELD_RICH = 200.0      # chars/page → "rich"
_YIELD_SPARSE = 40.0     # chars/page → "sparse"; below → "empty"
```

`_classify_yield(chars_per_page)` reproduces the rich/sparse/empty banding. The
implicit "min text length" cutoff now lives in one named place, normalised
**per page** so a long multi-page report is not mistaken for a low-yield record.

### 2. Batch-relative statistical outlier pass (the anomaly_detection)

`_compute_extraction_anomalies(extractions)` is a pure, air-gap-safe heuristic:

- Computes mean/stdev of per-page yield across the batch; flags **low outliers**
  whose yield `< mean − 1.5·stdev` **and** that are not themselves rich (abs
  ceiling = `_YIELD_RICH`) — the near-empty tail past a yield cliff.
- Sets `has_empty` when any record falls in the `empty` band (a silent total
  extraction failure — the worst case).
- Guards tiny batches (`< _ANOMALY_MIN_DOCS = 4`): only banding is evaluated, no
  statistical outliers reported.
- `_extraction_fields` reads both `Extraction` objects and dict records and
  floors `page_count` at 1 so the per-page divisor is always safe.

### 3. Deterministic severity baseline (always available)

`_heuristic_extraction_anomaly_severity(sparse_count, total, has_empty)` — pure
function of the sparse/empty fraction, escalated to `high` whenever `has_empty`.
This is authoritative and never depends on the LLM.

### 4. Optional best-effort LLM severity grade

`_ai_extraction_anomaly_severity(summary, anomalies)` routes through the new
`dic_extraction_anomaly_severity` LLM function (registered in
`args/llm_config.yaml`, chain `claude-haiku → qwen3-local → gpt-4o-mini →
llama-local`, effort `low`). It is grounded on the real distribution plus a
**bounded** sample (`_ANOMALY_SAMPLE = 5`) of outliers, keeps **injection
scanning ON** (record titles derive from arbitrary ingested files), and degrades
silently to `None` on no-data, blank/malformed/out-of-range output, or any LLM
failure. Callers treat `None` as "use the deterministic heuristic."

### 5. Orchestration + convenience

- `detect_extraction_anomalies(extractions, use_llm=True)` — returns the report
  (`anomaly_count`, `mean`, `stdev`, `threshold`, `has_empty`, `sparse_count`,
  `anomalies[]`, deterministic `severity`, optional `ai_grade`).
- `extract_batch_with_quality(paths, use_llm=True)` — extracts a batch and
  annotates it, identical extraction to per-file `extract_file`. Quality
  assessment **never changes what was extracted**.

## Security & compliance

- Statistical detection is pure-Python, no I/O, no network — air-gap safe.
- LLM grade is best-effort only; never a hard dependency.
- Injection scanning stays ON for the untrusted record titles; `classification`
  is set to `CUI` on the request.
- No new DB tables, routes, or templates — pure augmentation of an existing
  ingestion-layer module.

## Tests

`tests/test_dic_extraction_anomaly.py` — **33 tests, all passing**:

- band constants + `_classify_yield` rich/sparse/empty;
- `_extraction_fields` over objects and dicts, page_count floor, bad-input
  tolerance;
- `_compute_extraction_anomalies`: min-docs guard, yield-cliff outlier
  detection, never-flag-rich, `has_empty`, empty-batch safety, per-page
  normalisation protecting long docs, ascending sort;
- `_heuristic_extraction_anomaly_severity` baseline incl. escalation on
  `has_empty`;
- `_ai_extraction_anomaly_severity` dedicated-key routing, skip-when-nothing,
  runs-on-empty, injection-scan-on, grounding, bounded sample, fenced JSON,
  out-of-range/malformed/blank → `None`;
- `detect_extraction_anomalies` orchestration with and without the LLM.

Existing `test_dic_search_anomaly.py` (34) and the DIC extractor suite remain
green; `ruff check` clean on both changed files.

## Files changed

| File | Change |
|------|--------|
| `tools/document_intelligence/extractors.py` | Extraction-quality anomaly detector (bands + statistical outlier pass + heuristic & optional LLM severity + `extract_batch_with_quality`). |
| `args/llm_config.yaml` | Register `dic_extraction_anomaly_severity` LLM function. |
| `tools/manifest/document-intelligence-canvas.md` | Document the new capability on the `extractors.py` row. |
| `tests/test_dic_extraction_anomaly.py` | 33 tests (new). |
| `docs/features/phase-2-aiify-rm-06d89-phase-6059.md` | This document. |
