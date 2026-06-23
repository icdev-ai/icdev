# ACE — Autonomous Collaborative Engine (Co-Worker Canvas)
## CUI // SP-CTI

The ACE Co-Worker Engine is the ICDEV™ canvas for assembling, dispatching, and
monitoring teams of autonomous digital coworkers. It is a core building block of
the NOVA initiative; see `tools/manifest/autonomous-coworker.md` for the
ECHO/SOUL/TRUST/SELA integration.

## Canvas Blueprint

| Tool | File | Purpose |
|------|------|---------|
| ACE Blueprint | `tools/ace/blueprint.py` | Flask Blueprint (`ace_bp` + `ace_api_bp`) that mounts the Co-Worker canvas at `/coworker` and the JSON API at `/api/ace`. Provides pages (active teams, role catalog, instance detail, trust leaderboard), launch/status/messages/artifacts/abort endpoints, profile generation helpers, event-bus feeds, and the `/api/ace/iqe-query` IQE widget route. Uses `get_canvas_connection()` because `ace_*` tables have no `tenant_id`/`classification` columns. |
| ACE Event Bus | `tools/ace/event_bus.py` | Ecosystem-wide event emission for automatic coworker dispatch. Any ICDEV canvas emits a topic/payload event; the dispatcher routes it to roles whose listen topics match. Public API: `emit(topic, payload, source_canvas, source_id)`, `get_pending(limit)`, `mark_processed(event_id)`, `store_result(...)`, `get_recent_results(limit)`, `pending_count()`. |
| FileAccessBroker | `tools/ace/file_access_broker.py` | Scoped filesystem read/write for co-worker agents. Validates paths against `folder_access` list in role YAML (mode `r`/`rw`). Prevents path traversal via `Path.resolve() + relative_to()`. Logs every operation to `ace_audit_log`. CLI: none (library API only). |
| ToolRunner | `tools/ace/tool_runner.py` | Allowlisted ICDEV tool execution via subprocess. Blocks destructive patterns (`--force`, `--drop`, `rm`, `DROP TABLE`). Only `green` trust tier can run autonomously; yellow/red gets HITL gate (`TrustKernelDeniedError`). Captures stdout/stderr and persists artifact. CLI: none (library API only). |
| SkillPromoter | `tools/ace/skill_promoter.py` | SIPA-gated daily genesis reflex. Loads pending `ace_skill_candidates`, runs SIPA Mode B, promotes clean/low_risk to `args/ace/roles/candidates/` for human review. Registered as `ace_skill_promoter` reflex (24h interval). CLI: `python tools/ace/skill_promoter.py --json`. |
| Evidence Report | `tools/ace/evidence_report.py` | ATO evidence chain generator. Walks `ace_audit_log`, `ace_coworkers`, `ace_instances`, `ace_artifacts` for an instance and renders a classification-marked evidence report (SSP / JSON / Markdown formats). Reuses `classification_manager` for banners. CLI: `python tools/ace/evidence_report.py --instance <id> --format ssp`. |
| Canvas Role Gap | `tools/ace/canvas_role_gap.py` | Detects canvases with no representative ACE role. Reads `args/component_registry.yaml` for `canvas`/`child_app` components, checks `args/ace/roles/*.yaml` for `canvas: <key>` field, reports gaps as JSON. CLI: `python tools/ace/canvas_role_gap.py --json`. |
| Profile Generator | `tools/ace/profile_generator.py` | LLM-assisted role YAML generation. `generate_canvas_candidate(canvas_key, display_name, description)` writes a candidate to `args/ace/roles/candidates/` for human review (NOT auto-promoted). `batch_generate_canvas_candidates(gaps)` takes gap-detector output and generates candidates for all. CLI: none (library API; invoked via blueprint `/api/ace/profiles/preview` and `/api/ace/profiles/save`). |
