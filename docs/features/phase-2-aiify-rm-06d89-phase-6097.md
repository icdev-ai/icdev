# Phase 2 — Core Modernization: Ingest-Workload Anomaly Detection (aiify-opp-6097)

**Opportunity:** 6097 · **Pattern:** `hardcoded_threshold → anomaly_detection`
**Source (external, ephemeral):** `paperless-ngx src/paperless/celery.py` — the Celery
task-queue / worker config whose hardcoded numeric thresholds (task time/size
limits) fence off runaway tasks.
**Landed in:** `tools/document_intelligence/ingest_orchestrator.py` (DIC) — per the
established aiify pattern, external-repo opportunities are AI-ified in the
analogous ICDEV subsystem.

## Why DIC

A Celery worker config caps task time/size so one pathological payload cannot
hammer the worker pool. In DIC every ingested file becomes a background job
(extract → OCR → embed → KG bridge) whose cost is driven by the file. The
document-level analog of that worker guard is detecting a pathological **ingest
cost profile** up front, so a human is warned before a file silently overloads
the pipeline. This is distinct from the existing DIC content detectors
(date 6048, duplicate-block 5984, freshness 6042, OCR-confidence 6105): those
inspect document *content*; this inspects the *processing workload shape*.

## What it does

`assess_ingest_workload(byte_size, text_len, page_count)` returns a HITL proposal
(or `None` when unremarkable) surfaced under
`IngestOutcome.metadata["workload_anomaly"]`. Three deterministic rules, each
lifting the "hardcoded_threshold" into one tunable named constant:

| Rule | Fires when | Real-world cause |
|------|-----------|------------------|
| `sparse_extraction` | file ≥ `_WORKLOAD_MIN_FILE_BYTES` (50 KiB) yet < `_WORKLOAD_MIN_CHARS_PER_KB` (2.0) chars/KiB | image-only / scanned / corrupt PDF that hammers OCR for no content |
| `sparse_pages` | page count known and < `_WORKLOAD_MIN_CHARS_PER_PAGE` (50) chars/page | scanned imagery OCR did not recover |
| `payload_explosion` | file ≥ `_WORKLOAD_EXPLOSION_MIN_BYTES` (1 KiB) and > `_WORKLOAD_MAX_CHARS_PER_KB` (4096) chars/KiB | decompression/expansion blow-up (archive/zip-bomb-like) |

## Design (mirrors siblings 6048 / 5984)

- **Deterministic detector is always authoritative** — pure ratio math + named
  thresholds, offline, no network. A zero byte size never divides; negative
  inputs are clamped.
- **Small files excluded** by a size floor — a short legitimate note is small
  AND text-light without being a runaway job.
- **Evaluated even when no text was extracted** — unlike the content detectors,
  an empty extraction on a large file is *precisely* the anomaly, so this rule
  is not gated on `text.strip()`.
- **Optional LLM layer grades severity only** (`_ai_workload_severity`) — never
  the detection; degrades silently to the heuristic baseline on any failure.
- **HITL proposal only** — written under `metadata["workload_anomaly"]` solely
  when something is anomalous; never silently acted on.

## Wiring

`ingest_file(..., detect_workload_anomaly: bool = True)` runs the check after the
duplicate-block pass, using `p.stat().st_size`, `len(text)`, and
`extraction.page_count`.

## Tests

`tests/test_dic_ingest_workload_anomaly.py` — 21 tests: all three rules, the
size-floor exclusion, zero/negative-input safety, severity bands, the
LLM-severity grading (normalized / fenced / garbled / failure / no-flags-skip),
JSON-clean proposals, and that the LLM can override severity but never the
deterministic detection. `21 passed`; sibling date/duplicate suites unchanged
(`51 passed`).
