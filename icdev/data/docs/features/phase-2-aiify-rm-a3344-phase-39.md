<!-- CUI // SP-CTI -->
# Phase 2 — AI-ify Opportunity 39 (Determination: Duplicate)

- **Kanban task:** `aiify-rm-a3344-phase-39`
- **Roadmap:** `rm-a334408112` (scan_id 1)
- **Opportunity:** 39
- **Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
- **External module:** `paperless/src/documents/management/commands/document_consumer.py` (paperless-ngx shallow clone `aiify_git_5cc2wcba`, since reaped/GONE — external, unmodifiable)

## Determination

**Duplicate of `aiify-rm-a3344-phase-20`** — the DIC `ingest_orchestrator.py` collection-level consumption pipeline health anomaly detection (`detect_collection_anomalies`).

The external scan flagged paperless-ngx `management/commands/document_consumer.py` with `function_name: "<unknown>"`. This management command is the top-level document consumption pipeline orchestrator in paperless-ngx; its hardcoded thresholds govern:

- Minimum OCR character count before marking ingestion successful
- Max consecutive-failure retry limit before quarantining a document
- Queue-depth warning ceiling when the consume directory backlog grows too large

Commit `df5410cfc` (`feat(aiify-rm-a3344-phase-20)`) already addressed this in the analogous ICDEV subsystem (DIC = `tools/document_intelligence/`). That commit added `detect_collection_anomalies()` to `tools/document_intelligence/ingest_orchestrator.py` (lines 3286–3465), explicitly citing this file in the block comment:

> `# flagged paperless-ngx src/documents/management/commands/document_consumer.py`

The implementation:
- Queries `dic_documents` for recent ingestion history (configurable via `DIC_CONSUMER_HEALTH_LOOKBACK`, default 100)
- Applies IQR-based outlier detection on `page_count` and `byte_size` distributions (fence via `DIC_CONSUMER_HEALTH_IQR_FENCE`, default 1.5)
- Replaces the hardcoded backlog ceiling with `DIC_CONSUMER_MAX_QUEUE_DOCS` (default 500)
- Returns a `ConsumerHealthReport` with `verdict` in `{healthy, degraded, critical}` — never blocks ingestion (HITL-only)

## Verification (branch kanban/aiify-rm-a3344-phase-39)

- External clone `aiify_git_5cc2wcba/.../management/commands/document_consumer.py`: **GONE** (reaped by engine).
- `df5410cfc` is an **ancestor of HEAD** (`git merge-base --is-ancestor df5410cfc HEAD` → exit 0).
- `detect_collection_anomalies` present in `tools/document_intelligence/ingest_orchestrator.py` at line 3352.
- Block comment at line 3289 explicitly references `management/commands/document_consumer.py`.

No new code required — closing as a duplicate with `bypass_verification`.
