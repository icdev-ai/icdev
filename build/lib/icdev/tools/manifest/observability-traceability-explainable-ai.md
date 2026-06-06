# Observability, Traceability & Explainable AI (Phase 46)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Observability, Traceability & Explainable AI (Phase 46)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Tracer ABC | tools/observability/tracer.py | Span/Tracer ABCs, NullTracer, NullSpan, ProxyTracer, set_content_tag() (D280) | (library) | Tracer classes |
| SQLite Tracer | tools/observability/sqlite_tracer.py | Writes spans to otel_spans table — air-gapped default backend (D280) | (library) | SQLiteTracer class |
| OTel Tracer | tools/observability/otel_tracer.py | Wraps opentelemetry-api/sdk with OTLP exporter — production backend (D280) | (library) | OTelTracer class |
| Trace Context | tools/observability/trace_context.py | W3C traceparent parse/generate, contextvars propagation (D281) | (library) | TraceparentContext class |
| GenAI Attributes | tools/observability/genai_attributes.py | OTel GenAI semantic convention constants for LLM spans (D286) | (library) | Attribute constants |
| Instrumentation | tools/observability/instrumentation.py | @traced() decorator for auto-span creation on functions (D284) | (library) | Decorator |
| MLflow Exporter | tools/observability/mlflow_exporter.py | Batch export SQLite spans to MLflow REST API (D283) | --export, --status, --json | Export results |
| Prov Recorder | tools/observability/provenance/prov_recorder.py | W3C PROV entity/activity/relation recording, span callbacks (D287) | (library) | ProvRecorder class |
| Prov Query | tools/observability/provenance/prov_query.py | Lineage queries — backward ("what produced this?") and forward (D287) | --entity-id, --direction, --json | Lineage graph |
| Prov Export | tools/observability/provenance/prov_export.py | Export provenance graph as W3C PROV-JSON for interoperability (D287) | --project-id, --json | PROV-JSON |
| AgentSHAP | tools/observability/shap/agent_shap.py | Monte Carlo Shapley value tool attribution analysis (D288) | --trace-id, --iterations, --json | Shapley values |
| SHAP Reporter | tools/observability/shap/shap_reporter.py | JSON/markdown/dashboard report generation for SHAP results (D288) | (library) | Reports |
| XAI Assessor | tools/compliance/xai_assessor.py | Explainable AI compliance assessor — 10 automated checks (D289) | --project-id, --gate, --json | Assessment results |
| XAI Requirements | context/compliance/xai_requirements.json | XAI requirements catalog (NIST AI RMF + DoD RAI + ISO 42001) | (data) | Requirements JSON |
| Observability Config | args/observability_tracing_config.yaml | Tracer backend, sampling, retention, content policy, PROV/SHAP settings (D290) | (config) | YAML config |
| Observability MCP | tools/mcp/observability_server.py | MCP server: trace_query, trace_summary, prov_lineage, prov_export, shap_analyze, xai_assess | (server) | 6 tools, 2 resources |
| Unified MCP Gateway | tools/mcp/unified_server.py | Unified MCP gateway (D301): aggregates all 225 tools from 18 servers + 55 new tools into one process with lazy module loading | (server) | 225 tools, 6 resources |
| Tool Registry | tools/mcp/tool_registry.py | Declarative registry mapping tool name to (module, handler, schema) for unified gateway | (data) | Python dict |
| Gap Handlers | tools/mcp/gap_handlers.py | 55 handler functions for CLI tools not previously exposed via MCP (translation, dx, cloud, registry, security, testing, installer) | (handlers) | Python functions |
| Registry Generator | tools/mcp/generate_registry.py | Utility to auto-generate tool_registry.py by introspecting all 18 MCP server modules | (utility) | Python script |
| Traces API | tools/dashboard/api/traces.py | Flask API Blueprint for trace, provenance, and XAI endpoints | (api) | REST endpoints |
| Traces Page | tools/dashboard/templates/traces.html | Trace explorer: stat grid, trace list, span waterfall SVG | (template) | HTML page |
| Provenance Page | tools/dashboard/templates/provenance.html | Provenance viewer: entity/activity tables, lineage query | (template) | HTML page |
| XAI Page | tools/dashboard/templates/xai.html | XAI dashboard: assessment runner, coverage gauge, SHAP chart | (template) | HTML page |
| ODC Splunk Exporter | tools/observability_canvas/exporters/splunk.py | Splunk SPL export adapter for ODC Digital Twin Sigma rules — converts a single Sigma rule YAML string to one Splunk SPL search stanza. Field modifiers (contains, gt, lt, cidr) translated deterministically. No LLM. | `sigma_to_spl(sigma_yaml)` | SPL string |
| MITRE ATT&CK Loader | tools/observability_canvas/mitre_loader.py | Parses MITRE Enterprise ATT&CK catalog (context/mitre/enterprise.json) into sorted MitreTechnique dataclasses; optional tactic filter; deterministic, no LLM | `load_techniques(catalog_path, tactic_filter)` | `list[MitreTechnique]` |
| ODC MITRE Matrix Page | tools/dashboard/templates/observability_canvas/mitre.html | MITRE ATT&CK Enterprise matrix dashboard: tactic columns, technique cards color-coded by coverage (catalog/gap/covered), drill-through links | (template) | HTML page |
| ODC MITRE Detail Page | tools/dashboard/templates/observability_canvas/mitre_detail.html | Technique detail view: ID/name/description, sub-techniques list, ATT&CK link, Sigma rule generator form | (template) | HTML page |
| ODC Blueprint MITRE Routes | tools/observability_canvas/blueprint.py | Flask routes: GET /observability/mitre (matrix), GET /observability/mitre/<tid> (detail), POST /observability/mitre/sigma (Sigma rule generation) | URL params: tid, tactic_filter; POST: technique_id, technique_name | HTML/JSON |

