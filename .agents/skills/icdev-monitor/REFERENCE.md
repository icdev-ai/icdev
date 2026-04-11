# icdev-monitor — Step-by-Step Reference

## Steps

### 1. Load Monitoring Configuration
```bash
!cat args/monitoring_config.yaml
```

### 2. Health Checks
```bash
python tools/monitor/health_checker.py --project-id <id>
```
- HTTP endpoint checks (200 OK)
- Database connectivity
- Dependent service availability
- Response time thresholds

### 3. Log Analysis
```bash
python tools/monitor/log_analyzer.py --project-id <id> --since <timeframe>
```
- Parse application logs for error patterns
- Detect anomalies (error rate spikes, unusual patterns)
- Correlate with known knowledge base patterns

### 4. Metric Collection
```bash
python tools/monitor/metric_collector.py --project-id <id>
```
- CPU/memory usage
- Request rate and latency (p50, p95, p99)
- Error rate
- Database connection pool

### 5. Alert Correlation
```bash
python tools/monitor/alert_correlator.py --project-id <id>
```
- Active alerts from Prometheus/Grafana
- Correlate alerts to find root cause
- Group related alerts

### 6. Pattern Matching
Run the CLI command or use MCP tool `analyze_failure` MCP tool from icdev-knowledge:
- Match detected issues against knowledge base patterns
- Determine root cause with confidence score

### 7. Self-Healing (if --self-heal)
Run the CLI command or use MCP tool `self_heal` MCP tool from icdev-knowledge:
- Confidence >= 0.7 + auto_healable: execute fix automatically
- Confidence 0.3-0.7: suggest fix, require approval
- Confidence < 0.3: escalate with full context
- Rate limit: max 5 self-heal actions per hour
- Cooldown: 10 minutes between identical actions

### 8. Record Events
All monitoring events and self-healing actions are logged to:
- audit_trail (NIST AU compliance)
- self_healing_events table
- metric_snapshots table

### 9. Output Summary
Display:
```
Monitoring Report — <project-name>
Since: <timeframe>
───────────────────────────────
Health:  ● All checks passing / ○ X failing
Logs:    X errors, Y warnings detected
Metrics: CPU XX%, Memory XX%, Latency p95 XXms
Alerts:  X active (Y critical)

Patterns Detected: X
Self-Heal Actions: X executed, Y suggested, Z escalated
```
