# Monitoring & Self-Healing Administration Guide

> CUI // SP-CTI

## Overview

ICDEV provides a multi-layered monitoring and observability stack covering infrastructure health, agent behavior, compliance posture, and distributed request tracing. The system includes proactive self-healing capabilities that automatically detect, diagnose, and remediate common failure patterns.

---

## Dashboard Monitoring

### /monitoring Page

The dashboard monitoring page at `http://localhost:5000/monitoring` displays:

- Agent health status with heartbeat age indicators
- Status icons with WCAG accessibility (role="img", aria-label)
- Infrastructure component health (database, MCP servers, CI/CD)
- Active alerts and recent incidents
- Self-healing activity log

### Additional Observability Pages (Phase 46)

| Page | URL | Content |
|------|-----|---------|
| Trace Explorer | `/traces` | Stat grid, trace list with filtering, span waterfall SVG visualization |
| Provenance Viewer | `/provenance` | Entity/activity tables, lineage query interface, PROV-JSON export |
| XAI Dashboard | `/xai` | Assessment runner, coverage gauge chart, SHAP bar chart attribution |

---

## Heartbeat Daemon

The heartbeat daemon (Phase 29) performs 7 configurable health checks on a continuous schedule.

### Start in Foreground Mode

```bash
python tools/monitor/heartbeat_daemon.py
```

### Single Pass (Run All Checks Once)

```bash
python tools/monitor/heartbeat_daemon.py --once
```

### Run a Specific Check

```bash
python tools/monitor/heartbeat_daemon.py --check cato_evidence
```

### Check Status

```bash
python tools/monitor/heartbeat_daemon.py --status --json
```

### Notification Sinks

Heartbeat notifications fan out to three sinks (D163):

1. **Audit trail** -- Always recorded (append-only, NIST AU compliant).
2. **SSE** -- Pushed to dashboard if running (3-second batch debounce per D99).
3. **Gateway channels** -- Forwarded to configured messaging channels if gateway is active.

### Check Configuration

Each check type has its own cadence, configured in the heartbeat section of monitoring config. Checks are registered declaratively (D162 pattern).

---

## Auto-Resolver

The auto-resolver (Phase 29) receives alerts via webhook and applies the self-healing decision engine to automatically fix known issues.

### Analyze Without Acting

```bash
python tools/monitor/auto_resolver.py --analyze --alert-file alert.json --json
```

### Full Pipeline: Analyze + Fix + PR

```bash
python tools/monitor/auto_resolver.py --resolve --alert-file alert.json --json
```

### Resolution History

```bash
python tools/monitor/auto_resolver.py --history --json
```

### Webhook Integration

The auto-resolver extends the existing webhook server with an `/alert-webhook` endpoint (D164). Alerts are received, verified via HMAC-SHA256, and processed through the self-healing pipeline.

Fix branches and pull requests are created via the existing VCS abstraction (`tools/ci/modules/vcs.py`) per D166.

---

## Log Analysis

### ELK Stack

```bash
python tools/monitor/log_analyzer.py --source elk --query "error"
```

### Splunk

```bash
python tools/monitor/log_analyzer.py --source splunk --query "agent.builder AND status:failed"
```

---

## Health Checks

### Service Health

```bash
python tools/monitor/health_checker.py --target "http://service:8080/health"
```

### Full System Health Check

```bash
python tools/testing/health_check.py
python tools/testing/health_check.py --json
```

### CSP Health Check

Probe all configured cloud provider services:

```bash
python tools/cloud/csp_health_checker.py --check --json
```

Results are stored in the `cloud_provider_status` table.

---

## Monitoring Stack

ICDEV integrates with multiple monitoring backends. Configure endpoints in `args/monitoring_config.yaml`.

### Supported Backends

| Backend | Purpose | Configuration |
|---------|---------|--------------|
| ELK (Elasticsearch + Logstash + Kibana) | Log aggregation and search | `elk.host`, `elk.port` |
| Splunk | Enterprise log management | `splunk.host`, `splunk.token` |
| Prometheus | Metrics collection and alerting | `prometheus.host` |
| Grafana | Metrics visualization | `grafana.host` |
| CloudWatch (AWS) | AWS-native monitoring | Via `cloud_config.yaml` |
| Azure Monitor | Azure-native monitoring | Via `cloud_config.yaml` |
| Cloud Monitoring (GCP) | GCP-native monitoring | Via `cloud_config.yaml` |
| OCI Monitoring | Oracle Cloud monitoring | Via `cloud_config.yaml` |

### Prometheus Metrics Endpoint

The `/metrics` endpoint exposes 8 Prometheus metrics (D154). The endpoint is exempt from authentication.

| Metric | Type | Description |
|--------|------|-------------|
| `icdev_http_requests_total` | Counter | Total HTTP requests by method, endpoint, status |
| `icdev_http_request_duration_seconds` | Histogram | Request latency distribution |
| `icdev_errors_total` | Counter | Total errors by type and severity |
| `icdev_rate_limit_hits_total` | Counter | Rate limit rejections by tenant |
| `icdev_circuit_breaker_state` | Gauge | Circuit breaker state per service (0=closed, 1=open, 2=half_open) |
| `icdev_uptime_seconds` | Gauge | Process uptime |
| `icdev_active_tenants` | Gauge | Number of active tenants |
| `icdev_agent_health` | Gauge | Agent health status per agent (0=unhealthy, 1=healthy) |

Implementation uses optional `prometheus_client` with stdlib text-format fallback (D154).

---

## Self-Healing System

### Decision Engine

The self-healing system uses a 3-tier confidence model:

| Confidence | Action | Approval |
|------------|--------|----------|
| >= 0.7 | Auto-remediate | None required |
| 0.3 - 0.7 | Suggest fix | Human approval required |
| < 0.3 | Escalate | Full context provided to operator |

### Rate Limits

- Maximum 5 auto-heals per hour.
- 10-minute cooldown between same-pattern heals.
- Limits are enforced globally across all auto-heal triggers.

### Knowledge Base Integration

The knowledge base stores patterns detected from past failures and their resolutions.

#### Pattern Detection

```bash
python tools/knowledge/pattern_detector.py --log-data "/path/to/logs"
```

Uses statistical methods for pattern detection (D4). Identifies recurring failure signatures across log data.

#### Self-Heal Analysis

```bash
python tools/knowledge/self_heal_analyzer.py --failure-id "fail-123"
```

Uses Bedrock LLM for root cause analysis (D4). Produces a diagnosis with confidence score and recommended remediation.

#### Recommendations

```bash
python tools/knowledge/recommendation_engine.py --project-id "proj-123"
```

Generates recommendations based on accumulated knowledge patterns, project history, and compliance posture.

---

## Distributed Tracing (Phase 46)

### Architecture

ICDEV implements dual-mode tracing (D280):

| Mode | Backend | Activation |
|------|---------|-----------|
| `otel` | MLflow (OTLP-native, Apache 2.0) | `ICDEV_MLFLOW_TRACKING_URI` env var set |
| `sqlite` | Local SQLite (`otel_spans` table) | Default when MLflow not configured |
| `null` | No-op | Fallback when both unavailable |

### Check Active Tracer

```python
python -c "from tools.observability import get_tracer; print(type(get_tracer()).__name__)"
```

### Trace Queries

```bash
# Query traces by ID
python tools/observability/shap/agent_shap.py --trace-id "<trace-id>" --iterations 1000 --json

# Query last N traces for a project
python tools/observability/shap/agent_shap.py --project-id "proj-123" --last-n 10 --json
```

### W3C Traceparent Propagation

Correlation IDs (D149) are extended to W3C `traceparent` format (D281). Trace context propagates through:

- MCP tool calls (auto-instrumented at `base_server.py._handle_tools_call()`)
- A2A JSON-RPC metadata (3-line additions to `agent_client.py` and `agent_server.py`)
- LLM calls (router-level instrumentation with GenAI semantic conventions)

### Content Tracing Policy

Content tracing is **opt-in** via `ICDEV_CONTENT_TRACING_ENABLED` env var (D282). In CUI environments, prompts and responses are recorded as SHA-256 hashes only. Plaintext recording requires explicit opt-in.

Configuration in `args/observability_tracing_config.yaml`:

```yaml
content_tracing:
  enabled: false                    # Override with ICDEV_CONTENT_TRACING_ENABLED=true
  policy: hash_only                 # hash_only | plaintext
  cui_environments_require_approval: true
```

---

## W3C PROV-AGENT Provenance (Phase 46)

Provenance records the causal chain of all agent activities using the W3C PROV standard.

### Provenance Lineage Query

```bash
python tools/observability/provenance/prov_query.py \
  --entity-id "<entity-id>" \
  --direction backward \
  --json
```

### PROV-JSON Export

```bash
python tools/observability/provenance/prov_export.py \
  --project-id "proj-123" \
  --json
```

### Storage

Provenance data is stored in three append-only SQLite tables (D287):

| Table | Content |
|-------|---------|
| `prov_entities` | Artifacts, files, data objects |
| `prov_activities` | Agent actions, tool invocations |
| `prov_relations` | wasGeneratedBy, used, wasAssociatedWith, wasDerivedFrom |

---

## AgentSHAP Tool Attribution (Phase 46)

AgentSHAP computes Shapley values for agent tool usage, providing explainable AI attribution for agent decisions.

### Run SHAP Analysis on a Trace

```bash
python tools/observability/shap/agent_shap.py \
  --trace-id "<trace-id>" \
  --iterations 1000 \
  --json
```

### Run SHAP for Recent Traces

```bash
python tools/observability/shap/agent_shap.py \
  --project-id "proj-123" \
  --last-n 10 \
  --json
```

Uses Monte Carlo Shapley value estimation with 0.945 consistency (arXiv:2512.12597). Sampling uses stdlib `random` for air-gap safety (D288).

---

## XAI Compliance Assessment (Phase 46)

The XAI assessor performs 10 automated checks for explainability compliance.

### Run Assessment

```bash
python tools/compliance/xai_assessor.py --project-id "proj-123" --json
```

### Gate Evaluation

```bash
python tools/compliance/xai_assessor.py --project-id "proj-123" --gate
```

Gate blocking conditions (from `args/security_gates.yaml`):
- `tracing_not_active`
- `provenance_graph_empty`
- `xai_assessment_not_completed`
- `content_tracing_active_in_cui_without_approval`

---

## CSP Service Monitor

The CSP Service Monitor (Phase 38, D239-D241) tracks cloud service changes across all configured CSPs.

### Scan All CSPs

```bash
python tools/cloud/csp_monitor.py --scan --all --json
```

### Scan Specific CSP

```bash
python tools/cloud/csp_monitor.py --scan --csp aws --json
```

### Diff Registry vs Recent Signals (Offline)

```bash
python tools/cloud/csp_monitor.py --diff --json
```

### Monitor Status

```bash
python tools/cloud/csp_monitor.py --status --json
```

### Continuous Monitoring Daemon

```bash
python tools/cloud/csp_monitor.py --daemon --json
```

### Changelog Generation

```bash
# Full changelog with recommendations
python tools/cloud/csp_changelog.py --generate --days 30 --json

# Markdown report
python tools/cloud/csp_changelog.py --generate --format markdown --output .tmp/csp_changelogs/

# Summary statistics
python tools/cloud/csp_changelog.py --summary --json
```

Configuration: `args/csp_monitor_config.yaml`

---

## Configuration Reference

### args/monitoring_config.yaml

| Key | Description |
|-----|-------------|
| `elk.host` | Elasticsearch host |
| `elk.port` | Elasticsearch port |
| `splunk.host` | Splunk HEC endpoint |
| `splunk.token` | Splunk HEC token (use secrets manager) |
| `prometheus.host` | Prometheus pushgateway |
| `grafana.host` | Grafana instance URL |
| `self_healing.max_auto_heals_per_hour` | Auto-heal rate limit |
| `self_healing.cooldown_minutes` | Same-pattern cooldown |
| `sla_targets` | SLA thresholds for alerting |

### args/observability_tracing_config.yaml

| Key | Description |
|-----|-------------|
| `tracer_mode` | `otel` or `sqlite` (auto-detected) |
| `sampling_rate` | Trace sampling rate (0.0-1.0) |
| `sqlite_retention_days` | SQLite trace retention |
| `mlflow_retention_days` | MLflow trace retention |
| `content_tracing.enabled` | Enable content tracing |
| `content_tracing.policy` | `hash_only` or `plaintext` |
| `shap.default_iterations` | Default SHAP iterations |
| `shap.seed` | SHAP random seed for reproducibility |
| `xai.min_coverage_pct` | Minimum XAI coverage for gate pass |

### args/resilience_config.yaml

| Key | Description |
|-----|-------------|
| `circuit_breaker.default.*` | Default circuit breaker settings |
| `circuit_breaker.services.*` | Per-service overrides (bedrock, redis, jira, etc.) |
| `retry.max_retries` | Default max retry attempts |
| `retry.base_delay` | Base delay for exponential backoff |
| `retry.max_delay` | Maximum delay cap |

---

## Operational Procedures

### Responding to Agent Health Alerts

1. Check agent status on `/monitoring` dashboard.
2. Verify agent container is running: `kubectl get pods -n icdev -l agent=<name>`.
3. Check agent logs: `kubectl logs -n icdev <pod-name>`.
4. If auto-healing is active, check resolution history:
   ```bash
   python tools/monitor/auto_resolver.py --history --json
   ```
5. If auto-heal failed, manually investigate using the self-heal analyzer:
   ```bash
   python tools/knowledge/self_heal_analyzer.py --failure-id "<id>"
   ```

### Investigating Slow Requests

1. Open the Trace Explorer at `/traces`.
2. Filter by duration (sort descending).
3. Click a trace to view the span waterfall SVG.
4. Identify the slow span (tool call, LLM invocation, or database query).
5. Check SHAP attribution for the trace to understand tool contribution.

### Verifying Provenance Chain

1. Open the Provenance Viewer at `/provenance`.
2. Query lineage for the entity in question:
   ```bash
   python tools/observability/provenance/prov_query.py \
     --entity-id "<artifact-id>" \
     --direction backward \
     --json
   ```
3. Verify the chain is complete (no orphaned activities).
4. Export PROV-JSON for external audit:
   ```bash
   python tools/observability/provenance/prov_export.py --project-id "proj-123" --json
   ```

### Circuit Breaker Recovery

Circuit breakers use 3-state machine: CLOSED (normal) -> OPEN (failing) -> HALF_OPEN (testing recovery).

1. Check current state: `/metrics` endpoint, `icdev_circuit_breaker_state` gauge.
2. If OPEN, the service is failing. Wait for the reset timeout (configured per service in `args/resilience_config.yaml`).
3. HALF_OPEN state allows a single test request. If it succeeds, the breaker closes. If it fails, the breaker reopens.
4. For persistent failures, investigate the downstream service and check CSP health:
   ```bash
   python tools/cloud/csp_health_checker.py --check --json
   ```
