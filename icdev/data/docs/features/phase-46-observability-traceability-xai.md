# Phase 46 — Observability, Traceability & Explainable AI

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 46 |
| Title | Observability, Traceability & Explainable AI |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 39 (Observability & Operations), Phase 37 (MITRE ATLAS Integration), Phase 45 (OWASP Agentic AI Security) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV's 15-agent multi-agent architecture makes autonomous decisions across code generation, compliance assessment, security scanning, infrastructure provisioning, and deployment. Phase 39 provides hook-based event capture and SIEM forwarding, but operators still cannot answer fundamental questions: Which agent made a specific decision? What tool calls contributed to a given output? Why did the system choose one approach over another? Can we trace the lineage of a compliance artifact back to the requirements that drove it?

These questions are not academic — they are mandated by compliance frameworks. NIST AI RMF MEASURE 2.5/2.7/2.8 requires traceable AI decision-making. The DoD Responsible AI (RAI) "Traceable" principle demands that AI systems provide audit trails of their reasoning. ISO 42001 requires documentation of AI system behavior and outputs. Without distributed tracing, provenance tracking, and explainability metrics, ICDEV cannot satisfy these requirements for ATO submissions involving agentic AI components.

Phase 46 delivers three interconnected capabilities: distributed tracing (OpenTelemetry + SQLite dual-mode) for span-level visibility into every tool call, LLM invocation, and A2A message; W3C PROV-AGENT provenance tracking for entity-activity-relation lineage of all artifacts; and AgentSHAP tool attribution using Monte Carlo Shapley values for quantitative explainability of which tools contributed most to each outcome. These capabilities are exposed through 3 new dashboard pages (/traces, /provenance, /xai), an MCP server with 6 tools, and an XAI compliance assessor with 10 automated checks.

---

## 2. Goals

1. Provide distributed tracing across all 15 agents with pluggable backends (OTel for production, SQLite for air-gapped) and automatic fallback
2. Propagate W3C `traceparent` headers through A2A JSON-RPC metadata for cross-agent span linking
3. Auto-instrument all MCP server tool calls and LLM router invocations with a single code change each
4. Track artifact provenance using W3C PROV standard (Entity, Activity, Relation) in append-only tables
5. Compute AgentSHAP Shapley values for tool attribution using Monte Carlo sampling (air-gap safe, stdlib only)
6. Provide an XAI compliance assessor with 10 automated checks covering tracing, provenance, SHAP, content policy, and retention
7. Gate content tracing on `ICDEV_CONTENT_TRACING_ENABLED` to prevent plaintext leakage in CUI environments
8. Expose observability data through 3 dashboard pages (/traces, /provenance, /xai) and an MCP server with 6 tools

---

## 3. Architecture

```
                        Distributed Tracing
                    ┌──────────────────────────┐
                    │  Tracer ABC (D280)        │
                    │  ┌──────────────────────┐ │
                    │  │  NullTracer          │ │ (fallback)
                    │  │  SQLiteTracer        │ │ (air-gapped default)
                    │  │  OTelTracer          │ │ (production, optional)
                    │  │  ProxyTracer         │ │ (lazy init, Haystack pattern)
                    │  └──────────────────────┘ │
                    └────────────┬─────────────┘
                                 │
               ┌─────────────────┼──────────────────┐
               ↓                 ↓                   ↓
    MCP Auto-Instrument    LLM Router Spans    A2A Traceparent
    (base_server.py D284)  (GenAI attrs D286)  (JSON-RPC D285)
               │                 │                   │
               └─────────────────┼──────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │  otel_spans table       │ (SQLite)
                    │  or MLflow backend      │ (OTel export)
                    └────────────┬────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ↓                  ↓                   ↓
    W3C PROV-AGENT         AgentSHAP           XAI Assessor
    (D287)                 (D288)              (D289)
    prov_entities          Monte Carlo         10 automated
    prov_activities        Shapley values      checks
    prov_relations         tool attribution    BaseAssessor
              │                  │                   │
              └──────────────────┼──────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ↓                  ↓                   ↓
         /traces            /provenance           /xai
       (dashboard)         (dashboard)          (dashboard)
         trace list          entity tables       assessment
         span waterfall      lineage query       coverage gauge
         stat grid           PROV-JSON export    SHAP bar chart
```

The tracer abstraction (D280) provides a pluggable backend: `SQLiteTracer` writes spans to the `otel_spans` table (zero-config, air-gap safe), `OTelTracer` wraps the OpenTelemetry SDK for production environments with MLflow as the trace backend (D283), and `NullTracer` provides a no-op fallback. The `ProxyTracer` follows the Haystack pattern for lazy initialization. Auto-detection selects the tracer based on the presence of `ICDEV_MLFLOW_TRACKING_URI` environment variable.

Content tracing is gated by `ICDEV_CONTENT_TRACING_ENABLED` (D282): SHA-256 hashes are always recorded for audit purposes, but plaintext prompt/response content is only stored when explicitly opted in. CUI environments must never leak content to telemetry.

---

## 4. Requirements

### 4.1 Distributed Tracing

#### REQ-46-001: Pluggable Tracer Backend
The system SHALL provide a Tracer ABC with implementations for NullTracer (fallback), SQLiteTracer (air-gapped), and OTelTracer (production). Backend selection SHALL auto-detect based on environment configuration.

#### REQ-46-002: MCP Auto-Instrumentation
The system SHALL automatically instrument all MCP server tool calls by wrapping `base_server.py._handle_tools_call()`, creating spans for every tool invocation across all 15 MCP servers with a single code change.

#### REQ-46-003: A2A Distributed Tracing
The system SHALL propagate W3C `traceparent` headers through A2A JSON-RPC metadata, enabling cross-agent span linking by injecting traceparent in `agent_client.py` and extracting it in `agent_server.py`.

#### REQ-46-004: LLM Instrumentation
The system SHALL instrument LLM router invocations with OpenTelemetry GenAI semantic conventions (`gen_ai.request.model`, `gen_ai.usage.*`, `gen_ai.response.*`).

#### REQ-46-005: Content Tracing Policy
The system SHALL gate plaintext content tracing on `ICDEV_CONTENT_TRACING_ENABLED`. SHA-256 hashes SHALL always be recorded; plaintext fields SHALL be null when content tracing is disabled.

### 4.2 Provenance Tracking

#### REQ-46-006: W3C PROV-AGENT Model
The system SHALL track artifact provenance using the W3C PROV standard with 3 append-only tables: `prov_entities` (prompts, responses, documents, code, reports), `prov_activities` (tool invocations, LLM calls, decisions, reviews), and `prov_relations` (wasGeneratedBy, used, wasInformedBy, wasDerivedFrom, wasAttributedTo).

#### REQ-46-007: Provenance Lineage Queries
The system SHALL support forward and backward lineage queries with configurable max depth to prevent infinite recursion on cyclic references.

#### REQ-46-008: PROV-JSON Export
The system SHALL export provenance data in W3C PROV-JSON format for interoperability with external provenance systems.

### 4.3 Explainable AI

#### REQ-46-009: AgentSHAP Tool Attribution
The system SHALL compute Monte Carlo Shapley values for tool attribution using stdlib `random` for sampling (air-gap safe). Shapley value computation SHALL achieve 0.945 consistency per published research (arXiv:2512.12597).

#### REQ-46-010: XAI Compliance Assessment
The system SHALL provide an XAI compliance assessor with 10 automated checks: tracing active, MCP instrumentation enabled, A2A tracing active, provenance populated, content tracing policy documented, SHAP analysis recent, decision rationale recorded, trace retention configured, AI telemetry active, agent trust scoring active.

### 4.4 Dashboard and MCP

#### REQ-46-011: Trace Explorer Page
The `/traces` dashboard page SHALL display a stat grid (total traces, avg duration, error rate), trace list with filtering, and span waterfall SVG visualization.

#### REQ-46-012: Provenance Viewer Page
The `/provenance` dashboard page SHALL display entity and activity tables, support lineage queries, and provide PROV-JSON export.

#### REQ-46-013: XAI Dashboard Page
The `/xai` dashboard page SHALL display the assessment runner, coverage gauge, and SHAP bar chart for tool attribution visualization.

#### REQ-46-014: Observability MCP Server
The `icdev-observability` MCP server SHALL provide 6 tools (trace_query, trace_summary, prov_lineage, prov_export, shap_analyze, xai_assess) and 2 resources (observability://config, observability://stats).

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `otel_spans` | Trace span storage — trace_id, span_id, parent_span_id, operation_name, service_name, start_time, end_time, status, attributes (JSON), content_hash |
| `prov_entities` | W3C PROV entities — entity_id, entity_type (prompt/response/document/code/report), project_id, content_hash, created_at, metadata (JSON) |
| `prov_activities` | W3C PROV activities — activity_id, activity_type (tool_invocation/llm_call/decision/review), agent_id, started_at, ended_at, metadata (JSON) |
| `prov_relations` | W3C PROV relations — relation_type (wasGeneratedBy/used/wasInformedBy/wasDerivedFrom/wasAttributedTo), subject_id, object_id, timestamp |
| `shap_attributions` | AgentSHAP results — trace_id, tool_name, shapley_value, rank, iterations, confidence_interval, computed_at |
| `xai_assessments` | XAI compliance assessment results — project_id, check_id (XAI-001 to XAI-010), status (satisfied/not_satisfied), evidence, timestamp |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/observability/tracer.py` | Span and Tracer ABCs, NullTracer, ProxyTracer, content tag gating |
| `tools/observability/sqlite_tracer.py` | SQLite span writer — air-gapped default, auto-creates table |
| `tools/observability/otel_tracer.py` | OpenTelemetry SDK wrapper — optional, activates when OTLP available |
| `tools/observability/trace_context.py` | W3C traceparent parse/generate, contextvars propagation |
| `tools/observability/genai_attributes.py` | OTel GenAI semantic convention constants |
| `tools/observability/instrumentation.py` | `@traced()` decorator for automatic span creation |
| `tools/observability/mlflow_exporter.py` | Batch export SQLite spans to MLflow REST API |
| `tools/observability/provenance/prov_recorder.py` | Entity, Activity, Relation recording with span callbacks |
| `tools/observability/provenance/prov_query.py` | Forward/backward lineage queries with max depth |
| `tools/observability/provenance/prov_export.py` | W3C PROV-JSON export |
| `tools/observability/shap/agent_shap.py` | Monte Carlo Shapley value computation for tool attribution |
| `tools/observability/shap/shap_reporter.py` | Report generation in JSON and markdown formats |
| `tools/compliance/xai_assessor.py` | XAI compliance assessor — 10 automated checks, BaseAssessor pattern, gate |
| `tools/mcp/observability_server.py` | MCP server — 6 tools, 2 resources for observability data access |
| `tools/dashboard/api/traces.py` | Flask API blueprint for traces, provenance, and XAI dashboard endpoints |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D280 | Pluggable Tracer ABC: OTelTracer, SQLiteTracer, NullTracer | Haystack ProxyTracer pattern; opentelemetry-sdk stays optional; air-gap safe default |
| D281 | Extend correlation ID (D149) to W3C traceparent | Additive, backward compatible; enables cross-agent span linking |
| D282 | Content tracing opt-in via ICDEV_CONTENT_TRACING_ENABLED | CUI environments must never leak content to telemetry; SHA-256 hashes always recorded |
| D283 | MLflow as unified trace backend (Apache 2.0, self-hosted) | DoD-safe license, accepts OTLP natively (3.6+), built-in trace UI, SQLite/PG backend |
| D284 | MCP auto-instrumentation at base_server.py | Single code change instruments all 15 MCP servers |
| D285 | A2A distributed tracing via traceparent in JSON-RPC metadata | 3-line additions to agent_client.py and agent_server.py |
| D286 | LLM instrumentation with GenAI semantic conventions | Standard OTLP attributes: gen_ai.request.model, gen_ai.usage.*, gen_ai.response.* |
| D287 | W3C PROV-AGENT in 3 append-only SQLite tables | DOE-funded standard; Entity/Activity/Relation model; air-gap safe |
| D288 | AgentSHAP via Monte Carlo Shapley values | 0.945 consistency (arXiv:2512.12597); stdlib random for sampling (D22 air-gap safe) |
| D289 | XAI assessor via BaseAssessor pattern (D116) | ~200 LOC; crosswalk to NIST 800-53 US hub cascades to FedRAMP/CMMC |
| D290 | Dual-mode config in observability_tracing_config.yaml | Auto-detect: ICDEV_MLFLOW_TRACKING_URI set -> otel mode, else -> sqlite mode |

---

## 8. Security Gate

**Observability & XAI Gate:**
- **Blocking:** Tracing not active, provenance graph empty, XAI assessment not completed, content tracing active in CUI environment without explicit approval
- **Warning:** SHAP analysis older than 30 days, XAI coverage below 80%, provenance not exported for ATO projects
- **Thresholds:** tracing_required=true, provenance_required=true, shap_max_age_days=30, min_xai_coverage_pct=80

---

## 9. Commands

```bash
# Check active tracer
python -c "from tools.observability import get_tracer; print(type(get_tracer()).__name__)"

# AgentSHAP analysis
python tools/observability/shap/agent_shap.py --trace-id "<trace-id>" --iterations 1000 --json
python tools/observability/shap/agent_shap.py --project-id "proj-123" --last-n 10 --json

# Provenance queries
python tools/observability/provenance/prov_query.py --entity-id "<id>" --direction backward --json
python tools/observability/provenance/prov_export.py --project-id "proj-123" --json

# XAI compliance assessment
python tools/compliance/xai_assessor.py --project-id "proj-123" --json
python tools/compliance/xai_assessor.py --project-id "proj-123" --gate

# Dashboard pages
# /traces — Trace explorer: stat grid, trace list, span waterfall SVG
# /provenance — Provenance viewer: entity/activity tables, lineage query, PROV-JSON export
# /xai — XAI dashboard: assessment runner, coverage gauge, SHAP bar chart

# MCP server tools
# icdev-observability: trace_query, trace_summary, prov_lineage, prov_export, shap_analyze, xai_assess

# Configuration
# args/observability_tracing_config.yaml — tracer backend, sampling, retention, content policy,
#   PROV settings, AgentSHAP defaults, XAI thresholds
# args/security_gates.yaml — observability_xai gate conditions
# context/compliance/xai_requirements.json — XAI requirements catalog (NIST AI RMF + DoD RAI + ISO 42001)
```
