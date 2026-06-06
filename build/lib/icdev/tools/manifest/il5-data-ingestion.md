# IL5 Data Ingestion & SLA Enforcement

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## IL5 Data Ingestion & SLA Enforcement (CUI)

Handles Impact Level 5 (IL5/CUI) event ingestion, display formatting, and 30-second end-to-end SLA enforcement.  
NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest), SI-7 (integrity), SI-12 (info mgmt).

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| IL5 Ingestion Core | tools/il5/ingestion.py | Core CRUD layer for IL5/CUI events. Persists events with classification markings, content hash (SHA-256, not verbatim payload), and computes display latency vs. `source_published_at` to evaluate the 30-second SLA. Auto-creates `il5_ingestion_log` table. Uses `get_connection()`. | `ingest_il5_event(source_id, content, source_published_at?, metadata?, db_path?)` → event UUID; `get_il5_events(since?, limit?, db_path?)` → list; `check_sla_compliance(event_id)` → dict; `get_sla_summary()` → dict | UUID string / list of event dicts / SLA compliance dict |
| IL5 Display Service | tools/il5/il5_display_service.py | Formats raw IL5 event dicts (from ingestion core) into UI-ready payloads: tabular rows with SLA labels (MET/VIOLATED/UNKNOWN) and Chart.js-compatible bar-chart data. Pure display logic; no DB I/O. | `format_events_for_display(events, include_chart?)` → dict | `{rows: [...], chart_data?: {...}, classification, impact_level, sla_threshold_s}` |
| IL5 Ingestion Service | tools/il5/il5_ingestion_service.py | HTTP-polling feed fetcher (3-second interval, D103). Polls configured IL5 source feed, validates payload structure via `parse_il5_payload()`, and delegates persistence to the core ingestion module. Raises `IL5ValidationError` on bad records. | `fetch_il5_data(feed_url?, timeout_s?, limit?, db_path?)` → list; `parse_il5_payload(payload)` → canonical record dict | List of ingested event dicts |
| IL5 SLA Handler | tools/il5/sla_handler.py | 30-second end-to-end pipeline guard. Three usage patterns: checkpoint function `check_il5_timeout(start_time)` (raises `TimeoutError` on breach), context manager `IL5PipelineTimer()`, and decorator `@il5_timeout`. All log breaches (AU-2/AU-12). | `check_il5_timeout(start, timeout_s?)` / `with IL5PipelineTimer()` / `@il5_timeout` | Raises `TimeoutError` on SLA breach |
| IL5 Deploy Staging | tools/il5/deploy_staging.py | Staging validation script. Runs 3 checks: module import health, SLA constant verification (`SLA_SECONDS == 30`), and end-to-end latency test (ingest synthetic event, verify display latency < 30 s). Exit 0 on pass, 1 on fail. | `python tools/il5/deploy_staging.py [--json]` | Pass/fail report (stdout or JSON) |
