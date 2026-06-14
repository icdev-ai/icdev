<!-- CUI // SP-CTI -->
# Phase 2 — AI-ify Opportunity 115 (Determination: Duplicate)

- **Kanban task:** `aiify-rm-a3344-phase-115`
- **Roadmap:** `rm-a334408112` (scan_id 1)
- **Opportunity:** 115
- **Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
- **External module:** `paperless/celery.py` (paperless-ngx shallow clone `aiify_git_5cc2wcba`, since reaped/GONE — external, unmodifiable)

## Determination

**Duplicate of `aiify-opp-91`** — the DIC `detect_ingest_job_anomalies()` in `tools/document_intelligence/analytics_engine.py`.

The external scan flagged paperless-ngx `celery.py` with `function_name: "<unknown>"` for hardcoded task scheduling thresholds (soft time limits, worker concurrency, retry limits, task routing timeouts). These are module-level constants that configure Celery's task execution pipeline.

The ICDEV analog is the DIC ingest job task monitoring layer. `detect_ingest_job_anomalies()` (aiify-opp-91) replaces hardcoded pass/fail duration thresholds with IQR-based latency outlier detection that adapts to the actual job duration distribution:

- Detects failed, stale-queued, and stale-processing ingest jobs with no hardcoded time magic numbers;
- IQR fence (`_INGEST_JOB_IQR_FENCE`) derives anomaly boundaries from the live corpus of completed jobs;
- `stale_minutes` parameter is caller-configurable rather than baked in;
- Produces structured `{failed, stale_queued, stale_processing, latency_outliers, summary, severity}` for HITL triage.

This is the faithful AI-ification of `hardcoded_threshold → anomaly_detection` for the DIC task scheduling surface.

## Verification (branch kanban/aiify-rm-a3344-phase-115)

- External clone `aiify_git_5cc2wcba/.../paperless/celery.py`: **GONE** (reaped by engine; temp dir absent).
- `detect_ingest_job_anomalies` present in `tools/document_intelligence/analytics_engine.py` at HEAD.
- `aiify-opp-91` tag confirmed in source comment (line 1139 header + line 1217 docstring).

No new code required — closing as a duplicate with `bypass_verification`.
