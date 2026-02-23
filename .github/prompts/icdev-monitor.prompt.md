---
mode: agent
description: "Production monitoring with log analysis, metric collection, alerting, and self-healing trigger"
tools:
  - terminal
  - file_search
---

# icdev-monitor

1. Runs health checks on deployed services
2. Analyzes logs for error patterns
3. Collects and reviews metrics
4. Checks active alerts and correlates
5. Triggers self-healing for known patterns (if --self-heal)

## Steps

1. **Load Monitoring Configuration**
```bash
!cat args/monitoring_config.yaml
```

2. **Health Checks**
```bash
python tools/monitor/health_checker.py --project-id <id>
```

3. **Log Analysis**
```bash
python tools/monitor/log_analyzer.py --project-id <id> --since <timeframe>
```

4. **Metric Collection**
```bash
python tools/monitor/metric_collector.py --project-id <id>
```

5. **Alert Correlation**
```bash
python tools/monitor/alert_correlator.py --project-id <id>
```

6. **Pattern Matching**
Run the equivalent CLI command for analyze_failure:
- Match detected issues against knowledge base patterns
- Determine root cause with confidence score

7. **Self-Healing (if --self-heal)**
Run the equivalent CLI command for self_heal:
- Confidence >= 0.7 + auto_healable: execute fix automatically
- Confidence 0.3-0.7: suggest fix, require approval

8. **Record Events**
All monitoring events and self-healing actions are logged to:
- audit_trail (NIST AU compliance)
- self_healing_events table

9. **Output Summary**
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

10. **Output Summary**
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

## Example
```
#prompt:icdev-monitor abc123-uuid --check all --self-heal --since 24h
```