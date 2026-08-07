# Observability Hooks (Phase 39)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Observability Hooks (Phase 39)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Send Event | .claude/hooks/send_event.py | Shared utility: HMAC-signed event storage + SSE forwarding | session_id, hook_type, payload | Event ID |
| Post-Tool-Use Hook | .claude/hooks/post_tool_use.py | Log tool results to hook_events table (always exits 0) | tool_name, tool_input, tool_output | — |
| Notification Hook | .claude/hooks/notification.py | Log user notifications (always exits 0) | message | — |
| Stop Hook | .claude/hooks/stop.py | Capture session completion event (always exits 0) | session_id, reason | — |
| Subagent Stop Hook | .claude/hooks/subagent_stop.py | Log subagent task completion (always exits 0) | subagent_id, result | — |

## ODC Closed-Loop Hook (SDC Replay → ODC Verify)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SDC Replay Verifier | tools/observability_canvas/replay_verify.py | Verify TTP detection coverage for an SDC attack path; writes od_ttp_coverage + od_audit rows | ttp_ids: list[str], design_id: str | {path, results[{ttp_id, state, coverage_row_id}], summary{full,partial,none,total}} |

States: `full` = Sigma snippet + covered baseline; `partial` = one signal only; `none` = no coverage.
CLI: `python tools/observability_canvas/replay_verify.py T1059 T1078 --json`

## ODC MITRE Coverage Digital Twin
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Sigma Rule Generator (standalone) | tools/observability/sigma_generator.py | Deterministic single-technique Sigma YAML generator (no LLM). Produces well-formed Sigma YAML from a MITRE ATT&CK technique ID. Used by `/api/sigma/generate` endpoint and for seeding `sigma_template` in `odc_mitre_techniques`. Complements the graph-centric ODC generator. 17 built-in technique templates (T1055, T1059, T1078, T1003, T1110, T1566, T1083, T1082, T1021, T1053, T1071, T1190, T1486, T1562, T1218, T1027, T1059.001); falls back to keyword-match detection for unknown techniques. | `generate_sigma(technique_id: str)` → Sigma YAML string | Sigma YAML string (title:, id:, status:, logsource:, detection:, tags: keys) |
| Sigma Rule Generator (graph) | tools/observability_canvas/sigma_generator.py | Deterministic Sigma YAML + Splunk SPL + Elastic KQL + Sentinel KQL from ODC graph | graph_data, design_name | {rules, rule_count, exports, volume_estimate} |
| MITRE Coverage Gap Engine | tools/observability_canvas/mitre_coverage_twin.py | Per-technique coverage scoring (covered/partial/gap), gap score, remediation steps | design_id, graph_data | {gap_score, coverage_by_technique, by_tactic, quick_wins} |
| OTel Event Ingest | tools/observability_canvas/mitre_coverage_twin.py:ingest_otel_batch | OTLP-over-HTTP JSON event ingest mapped to MITRE techniques | design_id, events[] | {ingested, technique_counts} |
| SDC Closed-Loop Verify | tools/observability_canvas/mitre_coverage_twin.py:verify_sdc_attack_path | Verify ODC detection coverage for SDC attack path TTP list | design_id, ttp_list | {per_ttp_result, covered_ttps, gap_ttps, coverage_pct} |
| MITRE Coverage DB | tools/observability_canvas/mitre_coverage_db.py | Append-only CRUD for ODC MITRE ATT&CK coverage state — `record_coverage(technique_id, signal_source, state, last_observed_at, project_id)`; current state = latest row per (technique_id, signal_source, project_id) | Function calls (library) | Rows in observability_canvas.db |

### API Routes (blueprint: /observability)
| Route | Method | Description |
|-------|--------|-------------|
| /api/designs/\<id\>/mitre-coverage | GET | Per-technique coverage map |
| /api/designs/\<id\>/gap-report | POST | Full gap analysis + remediation steps |
| /api/designs/\<id\>/otel-ingest | POST | Ingest OTel detection events |
| /api/designs/\<id\>/sdc-verify | POST | Closed-loop SDC attack path verification |
| /api/replay-verify/\<design_id\> | POST | Replay SDC attack path → verify_path; persists od_ttp_coverage + od_audit. Body: {ttp_ids:[...]} |
| /api/mitre/ingest | POST | Ingest MITRE techniques into odc_mitre_techniques. Body: {source:'local'\|'stix'}; local seeds from mitre_catalog |
| /api/export/\<id\>/sigma | POST | Export Sigma YAML/SPL/KQL rules |
| /api/designs/\<id\>/volume-estimate | GET | Log volume + SIEM cost estimate |

### DB Tables Added (observability_canvas.db)
| Table | Purpose |
|-------|---------|
| odc_technique_coverage | Per-design × per-technique coverage state |
| odc_gap_scores | Aggregate gap score per assessment run |
| odc_otel_events | OTel-format detection events (append-only) |
| odc_sdc_verifications | SDC closed-loop attack path verification results |

## Runtime Invocation Telemetry (migration 341)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Invocation Recorder | tools/observability/invocation_recorder.py | `record()` context manager observing MCP tools, agents, personas and roles. Never raises; records argument KEY NAMES only, never values. Disable with `ICDEV_OBS_INVOCATIONS=0`. | surface, name, arg_keys, session_id, project_id, parent_id | Row in `runtime_invocations` |
| Invocation Summary | tools/observability/invocation_recorder.py::summary | Per-name rollup: calls, errors, avg/max duration. Thin wrapper over `InvocationStore.by_name` — kept for existing callers. | surface (optional), limit | List of rollup dicts |
| Invocation Store | tools/observability/invocation_store.py | Read-only query layer (mirrors `tools/audit/store.py`). `by_name()` per-(surface,name) rollup, `by_surface()` per-surface totals, `report()` both from one read. Injectable `connection_factory`; rolls back an aborted PG transaction; returns `[]` on an un-migrated DB. | InvocationFilter(surface, name, status, since, limit) | Rollup dicts |
| Invocation Rollup Fold | tools/observability/invocation_store.py::rollup_by_surface | Pure fold from per-name rows to per-surface totals. `avg_ms` is weighted by `timed` (rows that HAVE a duration), not by `calls` — an in-flight invocation has no duration and must not drag the mean. | list of per-name rows | Per-surface rows |
| Runtime Top CLI | tools/cli/runtime.py | `icdev runtime top` — per-surface totals + per-name table. Flags: `--surface --name --status --since --limit --sort --errors-only --surfaces-only --json`. Names the backend it read when empty. | argv | stdout table / JSON |

### Reading it (obs-cov-02)

| Surface | Entry point |
|---------|-------------|
| CLI | `icdev runtime top [--surface mcp] [--errors-only] [--json]` — **all time** by default |
| Dashboard | `/sre` — "Runtime Invocations" panel, fed by `GET /api/sre/invocations?surface=&days=&since=&limit=` (`tools/dashboard/api/sre.py::api_sre_invocations`) — **30-day window** by default, echoed as `window_days` and shown in the panel heading |

Both render `InvocationStore.report`, so the terminal and the panel cannot disagree.

The default-window asymmetry is deliberate: `runtime_invocations` has no entry in
`args/retention_policies.yaml`, so it grows without bound and the rollup is a `GROUP BY`
with no ceiling. Acceptable for a report an operator asked for once; not acceptable on a
page that re-runs it on every load. `started_at` is indexed
(`idx_runtime_inv_surface_started`), so the window is also the cheap filter. Passing an
explicit `since` hands the range back to the caller and reports `window_days: null`.

Instrumented choke points — one per surface, chosen so a single wrap covers everything on it:

| Surface | Choke point | Covers |
|---------|-------------|--------|
| mcp | `tools/mcp/unified_server.py::_register_lazy_tool.lazy_handler` | all 512 registered tools |
| agent | `tools/agent/agent_executor.py::execute_agent` | every Claude CLI agent execution |
| persona | `tools/ace/controller.py::_run` | every ACE co-worker run (wraps `_run`, not `launch` — launch only submits) |
| role | `tools/ace/coworker_thread.py::_run_step_mode` | every role step |

| Table | Purpose |
|-------|---------|
| runtime_invocations | What actually ran: surface, name, duration, status, error class, arg keys. Telemetry, NOT append-only audit evidence — deliberately absent from APPEND_ONLY_TABLES. |
