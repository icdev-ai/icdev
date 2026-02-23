# CUI // SP-CTI
# Observability, Traceability & Explainable AI Goal (Phase 46)

## Purpose
Provide full observability into ICDEV's 15-agent architecture through distributed tracing,
artifact provenance, and explainable AI. Operators can see inside agent runs, trace tool call
chains, understand why agents chose specific tools, and demonstrate explainability for ATO compliance.

Maps to: NIST AI RMF MEASURE 2.5/2.7/2.8, DoD RAI "Traceable" principle, ISO 42001.

## Trigger
- Automatically active — SQLiteTracer is the default (zero-config, air-gap safe)
- OTelTracer activates when `ICDEV_MLFLOW_TRACKING_URI` is set
- XAI assessment triggered via `/icdev-trace` skill or `xai_assess` MCP tool

## Workflow

### 1. Tracing Infrastructure
Pluggable tracer abstraction (D280):
- `NullTracer` — fallback when nothing configured
- `SQLiteTracer` — writes spans to `otel_spans` table (air-gapped default)
- `OTelTracer` — wraps OpenTelemetry SDK for production (optional)
- `ProxyTracer` — Haystack-pattern proxy for lazy initialization

```bash
# Check which tracer is active
python -c "from tools.observability import get_tracer; print(type(get_tracer()).__name__)"
```

### 2. Auto-Instrumentation
Three instrumentation points cover the entire system:
- **MCP base_server.py** (D284) — wraps `_handle_tools_call()`, auto-instruments all 15 MCP servers
- **LLM router** (D286) — GenAI semantic conventions on every LLM call
- **A2A protocol** (D285) — W3C traceparent propagation in JSON-RPC metadata

Content tracing is gated by `ICDEV_CONTENT_TRACING_ENABLED` (D282):
- SHA-256 hashes always recorded
- Plaintext only when explicitly opted in

### 3. W3C Traceparent Propagation
Extends D149 correlation ID to W3C `traceparent` format (D281):
- `agent_client.py` injects traceparent into A2A metadata
- `agent_server.py` extracts traceparent and restores trace context
- Creates linked cross-agent span hierarchies

### 4. Provenance Tracking (PROV-AGENT)
W3C PROV standard provenance in 3 append-only tables (D287):
- `prov_entities` — prompts, responses, documents, code, reports
- `prov_activities` — tool invocations, LLM calls, decisions, reviews
- `prov_relations` — wasGeneratedBy, used, wasInformedBy, wasDerivedFrom, wasAttributedTo

```bash
# Query provenance lineage
python tools/observability/provenance/prov_query.py --project-id proj-123 --lineage --json

# Export as PROV-JSON
python tools/observability/provenance/prov_export.py --project-id proj-123 --json
```

### 5. AgentSHAP Tool Attribution
Monte Carlo Shapley value analysis for tool importance (D288):
- Model-agnostic, stdlib `random` for sampling (air-gap safe)
- 0.945 consistency per arXiv:2512.12597
- Deterministic complement to CoT (Oxford study confirms CoT is NOT reliable explainability)

```bash
# Run SHAP analysis on a trace
python tools/observability/shap/agent_shap.py --trace-id <id> --iterations 1000 --json

# Analyze last N traces for a project
python tools/observability/shap/agent_shap.py --project-id proj-123 --last-n 10 --json
```

### 6. XAI Compliance Assessment
10 automated checks via BaseAssessor pattern (D289):
- XAI-001: Tracing active
- XAI-002: MCP instrumentation enabled
- XAI-003: A2A distributed tracing active
- XAI-004: Provenance graph populated
- XAI-005: Content tracing policy documented
- XAI-006: SHAP analysis recent (within 30 days)
- XAI-007: Decision rationale recorded
- XAI-008: Trace retention configured
- XAI-009: AI telemetry active
- XAI-010: Agent trust scoring active

```bash
# Run XAI assessment
python tools/compliance/xai_assessor.py --project-id proj-123 --json

# Gate evaluation
python tools/compliance/xai_assessor.py --project-id proj-123 --gate
```

### 7. Dashboard Visibility
Three new dashboard pages:
- `/traces` — Trace explorer: stat grid, trace list, span waterfall SVG
- `/provenance` — Provenance viewer: entity/activity tables, lineage query, PROV-JSON export
- `/xai` — XAI dashboard: assessment runner, coverage gauge, SHAP bar chart

### 8. MCP Server
`icdev-observability` MCP server provides 6 tools + 2 resources:
- `trace_query` — Query traces and spans
- `trace_summary` — Aggregate trace statistics
- `prov_lineage` — Query provenance lineage
- `prov_export` — Export PROV-JSON
- `shap_analyze` — Run AgentSHAP analysis
- `xai_assess` — Run XAI compliance assessment
- `observability://config` — Current configuration
- `observability://stats` — Live statistics

## Tools Used
| Tool | Purpose |
|------|---------|
| `tools/observability/tracer.py` | Span/Tracer ABCs, NullTracer, ProxyTracer, content tag gating |
| `tools/observability/sqlite_tracer.py` | SQLite span writer (air-gapped default) |
| `tools/observability/otel_tracer.py` | OpenTelemetry SDK wrapper (optional) |
| `tools/observability/trace_context.py` | W3C traceparent parse/generate, contextvars propagation |
| `tools/observability/genai_attributes.py` | OTel GenAI semantic convention constants |
| `tools/observability/instrumentation.py` | `@traced()` decorator for auto-span creation |
| `tools/observability/mlflow_exporter.py` | Batch export SQLite spans to MLflow REST API |
| `tools/observability/provenance/prov_recorder.py` | Entity/Activity/Relation recording, span callbacks |
| `tools/observability/provenance/prov_query.py` | Lineage queries (backward/forward) |
| `tools/observability/provenance/prov_export.py` | PROV-JSON export |
| `tools/observability/shap/agent_shap.py` | Monte Carlo Shapley value computation |
| `tools/observability/shap/shap_reporter.py` | Report generation (JSON/markdown) |
| `tools/compliance/xai_assessor.py` | XAI compliance assessor (10 auto-checks) |
| `tools/mcp/observability_server.py` | MCP server (6 tools, 2 resources) |
| `tools/dashboard/api/traces.py` | Flask API Blueprint (traces, provenance, XAI) |

## Args
- `args/observability_tracing_config.yaml` — Tracer backend, sampling, retention, content policy, PROV/SHAP settings (D290)
- `args/security_gates.yaml` — `observability_xai` gate (blocking + warning conditions)

## Context
- `context/compliance/xai_requirements.json` — XAI requirements catalog (NIST AI RMF + DoD RAI + ISO 42001)

## Success Criteria
- Every MCP tool call produces a trace span in `otel_spans`
- A2A cross-agent calls have linked parent-child span hierarchies
- Provenance graph populated with entities and activities for project artifacts
- SHAP analysis produces deterministic Shapley values for tool attribution
- XAI assessment covers all 10 checks with valid statuses
- Dashboard pages render trace waterfall, provenance lineage, and SHAP bar chart
- Content tracing respects `ICDEV_CONTENT_TRACING_ENABLED` (never leaks plaintext in CUI mode)
- `observability_xai` security gate blocks on: tracing not active, provenance empty, XAI not assessed

## Edge Cases
- Database not initialized → SQLiteTracer creates `otel_spans` table on first write
- OpenTelemetry not installed → graceful fallback to SQLiteTracer (D280)
- MLflow unreachable → spans buffered in SQLite, exported when connectivity restored
- Content tracing off → SHA-256 hashes recorded, plaintext fields null
- No SHAP data → XAI-006 returns `not_satisfied`, warning in gate
- Provenance query cycles → max_depth parameter prevents infinite recursion
