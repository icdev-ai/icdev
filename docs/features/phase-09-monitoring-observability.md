# Phase 9 — Monitoring & Observability

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 9 |
| Title | Monitoring & Observability |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 7 (Security Scanning), Phase 8 (Self-Healing System) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV-built applications deploy into Gov/DoD environments where operational visibility is not optional -- it is a compliance requirement. NIST 800-53 controls AU-6 (Audit Record Review), SI-4 (System Monitoring), and IR-5 (Incident Monitoring) mandate continuous monitoring of system health, security events, and performance metrics. Without a unified observability layer, operators are blind to degradation until users report outages.

Gov/DoD environments typically run heterogeneous monitoring stacks. Some agencies use ELK (Elasticsearch, Logstash, Kibana), others use Splunk, and most require Prometheus/Grafana for metric collection. ICDEV must integrate with all of these, providing a single pane of glass that correlates logs, metrics, and alerts across the entire application stack regardless of which monitoring tools are deployed.

The monitoring system also serves as the sensor layer for the Self-Healing System (Phase 8). Without reliable anomaly detection and alert correlation, self-healing cannot trigger. Monitoring must detect issues, correlate them into root causes, and hand off actionable intelligence to the remediation pipeline -- all while maintaining an immutable audit trail of every observation.

---

## 2. Goals

1. Provide health check capabilities that validate HTTP endpoints, database connectivity, dependent service availability, and response time thresholds
2. Integrate with ELK Stack and Splunk for log aggregation, parsing application logs for error patterns, warning accumulation, performance degradation, and security events
3. Collect metrics from Prometheus/Grafana including CPU utilization, memory usage, request rate, latency percentiles (p50/p95/p99), error rate, and connection pool utilization
4. Correlate related alerts into single root cause groups, distinguishing primary from secondary alerts and calculating severity roll-ups
5. Detect anomalies versus baseline using statistical pattern detection (BM25 + cosine similarity for text matching, frequency analysis, time correlation)
6. Feed actionable intelligence to the Self-Healing System (Phase 8) when monitoring detects patterns above confidence thresholds
7. Generate human-readable monitoring reports with health status, log summaries, metric snapshots, and active alert counts
8. Record all monitoring events in the append-only audit trail satisfying NIST AU-6, SI-4, and IR-5

---

## 3. Architecture

```
+-----------------------------------------------------------+
|              External Monitoring Stack                     |
|                                                           |
|  +--------+  +--------+  +-----------+  +--------+       |
|  |  ELK   |  | Splunk |  | Prometheus|  | Grafana|       |
|  | Stack  |  |        |  |           |  |        |       |
|  +---+----+  +---+----+  +-----+-----+  +---+----+       |
|      |           |              |            |            |
+------+-----------+--------------+------------+------------+
       |           |              |            |
       v           v              v            v
+-----------------------------------------------------------+
|              ICDEV Monitoring Layer                        |
|                                                           |
|  +------------------+  +-------------------+              |
|  | Log Analyzer     |  | Metric Collector  |              |
|  | (ELK + Splunk)   |  | (Prometheus)      |              |
|  +--------+---------+  +---------+---------+              |
|           |                      |                        |
|           v                      v                        |
|  +------------------+  +-------------------+              |
|  | Alert Correlator |  | Health Checker    |              |
|  | (root cause)     |  | (HTTP + DB + deps)|              |
|  +--------+---------+  +---------+---------+              |
|           |                      |                        |
+-----------|----------------------|------------------------+
            |                      |
            v                      v
+-----------------------------------------------------------+
|              Pattern Detection (Knowledge Base)           |
|  BM25 + cosine similarity + frequency + time correlation  |
+----------------------------+------------------------------+
                             |
               +-------------+-------------+
               |                           |
               v                           v
+---------------------+    +---------------------------+
| Self-Healing System |    | Monitoring Report         |
| (Phase 8)           |    | Health + Logs + Metrics   |
+---------------------+    | + Alerts + Patterns       |
                            +---------------------------+
```

### Alert Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| CPU | > 70% | > 90% |
| Memory | > 75% | > 90% |
| Error rate | > 1% | > 5% |
| p95 latency | > 500ms | > 2000ms |
| Health check | 1 failure | 3 consecutive failures |

---

## 4. Requirements

### 4.1 Health Checks

#### REQ-09-001: HTTP Endpoint Health Checks
The system SHALL perform HTTP endpoint health checks, expecting 200 OK within a configurable timeout, and record results in the `metric_snapshots` table.

#### REQ-09-002: Dependent Service Checks
The system SHALL verify database connectivity and dependent service availability as part of each health check cycle.

#### REQ-09-003: Response Time Measurement
The system SHALL measure response time for each health check endpoint and compare against configured thresholds (warning > 500ms, critical > 2000ms at p95).

### 4.2 Log Analysis

#### REQ-09-004: Multi-Source Log Integration
The system SHALL connect to both ELK (Elasticsearch) and Splunk log aggregation platforms, with the source configurable via `args/monitoring_config.yaml`.

#### REQ-09-005: Log Pattern Parsing
The system SHALL parse application logs for error patterns (exceptions, stack traces), warning accumulation, performance degradation indicators, and security events (failed authentication, unusual access patterns).

#### REQ-09-006: Anomaly Detection
The system SHALL calculate error rate over configurable time windows and detect anomalies versus established baselines using statistical methods.

### 4.3 Metric Collection

#### REQ-09-007: Prometheus Metric Collection
The system SHALL collect metrics from Prometheus/Grafana including CPU utilization, memory usage, request rate (req/sec), latency percentiles (p50, p95, p99), error rate (4xx, 5xx), database connection pool utilization, and queue depth.

#### REQ-09-008: Metric Snapshot Storage
The system SHALL store metric snapshots in the `metric_snapshots` table for historical trend analysis and baseline computation.

### 4.4 Alert Correlation

#### REQ-09-009: Root Cause Grouping
The system SHALL correlate related alerts by grouping them by root cause (e.g., database failure causing cascading 5xx errors), determining primary versus secondary alerts.

#### REQ-09-010: Severity Roll-Up
The system SHALL calculate severity roll-up for correlated alert groups and record them in the `alerts` table.

### 4.5 Reporting and Compliance

#### REQ-09-011: Monitoring Report Generation
The system SHALL generate human-readable monitoring reports showing health check results, log analysis summaries, metric snapshots, active alerts, and pattern matches.

#### REQ-09-012: NIST Compliance Audit Trail
All monitoring events SHALL be recorded in the append-only audit trail with event_type=monitoring.check, satisfying NIST 800-53 controls AU-6, SI-4, and IR-5.

### 4.6 Edge Cases

#### REQ-09-013: Graceful Degradation
When the monitoring stack is unreachable, the system SHALL use cached data and report data staleness rather than failing completely.

#### REQ-09-014: Metric Flapping Prevention
The system SHALL require sustained threshold breaches (2+ minutes) before triggering alerts, preventing flapping from momentary spikes.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `metric_snapshots` | Time-series metric data (CPU, memory, latency, error rate) per project per health check cycle |
| `alerts` | Correlated alerts with severity, root cause grouping, primary/secondary classification, and status |
| `knowledge_patterns` | Failure pattern signatures used for anomaly matching (shared with Phase 8) |
| `audit_trail` | Append-only monitoring event records (NIST AU compliance) |
| `deployments` | Deployment records used for post-deployment monitoring context |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/monitor/health_checker.py` | HTTP endpoint health checks, database connectivity, dependent service validation |
| `tools/monitor/log_analyzer.py` | Log analysis via ELK/Splunk — error patterns, anomalies, security events |
| `tools/monitor/metric_collector.py` | Prometheus/Grafana metric collection — CPU, memory, latency, error rate |
| `tools/monitor/alert_correlator.py` | Alert correlation — root cause grouping, severity roll-up, primary/secondary classification |
| `tools/knowledge/pattern_detector.py` | Statistical pattern matching against knowledge base (BM25, frequency, time correlation) |
| `tools/audit/audit_logger.py` | Append-only audit trail recording for all monitoring events |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D4 | Statistical methods for pattern detection | No GPU available; BM25 + frequency analysis + time correlation are deterministic and air-gap safe |
| D29 | SSE over WebSocket for dashboard live updates | Flask-native, simpler, no additional dependencies, unidirectional data flow is sufficient for monitoring |
| D148 | Structured error hierarchy for new monitoring code | ICDevError base with Transient/Permanent subtypes allows monitoring to distinguish retryable from permanent failures |

---

## 8. Security Gate

**Monitoring Gate:**
- Health check failures exceeding 3 consecutive on any endpoint trigger critical alert
- Error rate exceeding 5% triggers critical alert and self-healing evaluation
- All monitoring events recorded in append-only audit trail (NIST AU-6, SI-4, IR-5)
- Monitoring stack unreachable for > 5 minutes triggers staleness warning
- Post-deployment: aggressive monitoring for first 30 minutes after each deployment

---

## 9. Commands

```bash
# Health checks
python tools/monitor/health_checker.py --target "http://service:8080/health"

# Log analysis
python tools/monitor/log_analyzer.py --source elk --query "error"
python tools/monitor/log_analyzer.py --source splunk --query "exception"

# Metric collection
python tools/monitor/metric_collector.py --source prometheus --project-id "proj-123"

# Pattern detection
python tools/knowledge/pattern_detector.py --log-data "/path/to/logs"

# Full system health check
python tools/testing/health_check.py                 # Human output
python tools/testing/health_check.py --json           # JSON output

# Heartbeat daemon (Phase 29 extension)
python tools/monitor/heartbeat_daemon.py              # Foreground daemon (7 checks)
python tools/monitor/heartbeat_daemon.py --once       # Single pass
python tools/monitor/heartbeat_daemon.py --status --json  # Check statuses
```

**CUI // SP-CTI**
