# Goal: Production Monitoring

## Purpose
Provide comprehensive observability for ICDEV-built applications through log analysis, metric collection, alert correlation, and health checking. Feeds data into the self-healing system for automated remediation.

## Trigger
- Scheduled monitoring checks (cron-based)
- `/icdev-monitor` skill invoked
- Alert notification received
- Deployment completed (post-deploy health check)

## Inputs
- Monitoring configuration (`args/monitoring_config.yaml`)
- Project deployment information (from `deployments` table)
- Alert rules and thresholds
- Log sources (ELK/Splunk endpoints)
- Metric sources (Prometheus/Grafana endpoints)

## Process

### Step 1: Health Checks
**Tool:** `tools/monitor/health_checker.py`
- HTTP endpoint checks (expect 200 OK within timeout)
- Database connectivity verification
- Dependent service availability
- Response time measurement against thresholds
- Record results in `metric_snapshots` table

### Step 2: Log Analysis
**Tool:** `tools/monitor/log_analyzer.py`
- Connect to ELK/Splunk log aggregation
- Parse application logs for:
  - Error patterns (exceptions, stack traces)
  - Warning accumulation
  - Performance degradation indicators
  - Security events (failed auth, unusual access patterns)
- Calculate error rate over time window
- Detect anomalies vs. baseline

### Step 3: Metric Collection
**Tool:** `tools/monitor/metric_collector.py`
- Collect from Prometheus/Grafana:
  - CPU utilization
  - Memory usage
  - Request rate (req/sec)
  - Latency percentiles (p50, p95, p99)
  - Error rate (4xx, 5xx)
  - Database connection pool utilization
  - Queue depth (if applicable)
- Store snapshot in `metric_snapshots` table

### Step 4: Alert Correlation
**Tool:** `tools/monitor/alert_correlator.py`
- Fetch active alerts from monitoring stack
- Correlate related alerts:
  - Group alerts by root cause (e.g., DB down → cascade of 5xx errors)
  - Determine primary vs. secondary alerts
  - Calculate severity roll-up
- Record in `alerts` table

### Step 5: Pattern Detection
**Tool:** `tools/knowledge/pattern_detector.py`
- Match detected anomalies against knowledge base patterns
- Statistical methods (no GPU required):
  - Frequency analysis
  - Time correlation
  - BM25 + cosine similarity for text matching
- Return matched patterns with confidence scores

### Step 6: Trigger Self-Healing (if enabled)
If monitoring detects actionable patterns and self-healing is enabled:
- Follow self_healing.md goal workflow
- Apply confidence thresholds and rate limits
- Execute or suggest remediation

### Step 7: Generate Report
Compile monitoring report:
```
Health:    X/Y checks passing
Logs:      X errors, Y warnings (last N hours)
Metrics:   CPU XX%, Memory XX%, p95 XXms
Alerts:    X active (Y critical, Z warning)
Patterns:  X detected, Y actionable
```

### Step 8: Audit Trail
**Tool:** `tools/audit/audit_logger.py`
- Record: event_type=monitoring.check
- Include: health results, alert count, pattern matches
- **NIST Controls:** AU-6 (Audit Record Review), SI-4 (System Monitoring), IR-5 (Incident Monitoring)

## Outputs
- Health check results (pass/fail per endpoint)
- Log analysis summary (error count, anomalies)
- Metric snapshot (current values vs. thresholds)
- Active alerts with correlation
- Pattern match results
- Monitoring report (human-readable)

## Monitoring Stack Integration
| Component | Purpose | ICDEV Integration |
|-----------|---------|-------------------|
| ELK Stack | Log aggregation | `log_analyzer.py` queries Elasticsearch |
| Splunk | Log analysis | `log_analyzer.py` queries Splunk API |
| Prometheus | Metric collection | `metric_collector.py` queries PromQL |
| Grafana | Visualization | Dashboard links in reports |

## Alert Thresholds (from monitoring_config.yaml)
| Metric | Warning | Critical |
|--------|---------|----------|
| CPU | > 70% | > 90% |
| Memory | > 75% | > 90% |
| Error rate | > 1% | > 5% |
| p95 latency | > 500ms | > 2000ms |
| Health check | 1 failure | 3 consecutive |

## Edge Cases
- Monitoring stack unreachable: use cached data, report staleness
- Metric flapping: require sustained threshold breach (2+ minutes)
- Log volume spike: sample if necessary, flag for investigation
- Post-deployment: more aggressive checking for first 30 minutes

## Related Goals
- `self_healing.md` — Automated remediation
- `deploy_workflow.md` — Post-deployment monitoring
- `agent_management.md` — Agent health monitoring
