# Auto-grader for NetOps M02 Step 1: Anomaly Detection Agent

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    agent = AnomalyAgent()
    report = agent.run(SAMPLE_SNAPSHOT)
    print(f"Device: {report['device']}")
    print(f"Anomalies: {report['anomaly_count']}")
    print(f"Highest: {report['highest_severity']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# classify_anomaly tests
result = classify_anomaly("latency_ms", 620, THRESHOLDS)
assert result is not None, "classify_anomaly() returned None for value above critical"
assert result.get("state") == "critical", f"Expected 'critical', got {result.get('state')}"
assert result.get("metric") == "latency_ms", "metric field must match input"
assert result.get("value") == 620, "value field must match input"
assert result.get("critical_threshold") == 500, "critical_threshold must be 500"

result2 = classify_anomaly("port_util_pct", 88, THRESHOLDS)
assert result2 is not None, "88% port util is above warning threshold (75)"
assert result2.get("state") == "warning", f"Expected 'warning', got {result2.get('state')}"

result3 = classify_anomaly("packet_loss_pct", 0.3, THRESHOLDS)
assert result3 is None, "0.3% packet loss is normal (below warning 1.0) — must return None"

result4 = classify_anomaly("unknown_metric", 999, THRESHOLDS)
assert result4 is None, "Unknown metric not in thresholds — must return None"

# score_severity tests
anomalies_in = [
    {"metric": "latency_ms",    "value": 620, "state": "critical", "warning_threshold": 100, "critical_threshold": 500},
    {"metric": "port_util_pct", "value": 88,  "state": "warning",  "warning_threshold": 75,  "critical_threshold": 95},
]
scored = score_severity(anomalies_in)
assert scored is not None, "score_severity() returned None"
assert len(scored) == 2, "score_severity must return same number of anomalies"
assert scored[0].get("severity") == "critical", f"latency_ms=620 (critical state) -> severity 'critical', got {scored[0].get('severity')}"
assert scored[0].get("metric") == "latency_ms", "critical anomaly must be first after sort"
# 88 > 0.9*95=85.5 -> high
assert scored[1].get("severity") == "high", f"port_util_pct=88 (>90% of 95) -> 'high', got {scored[1].get('severity')}"

# AnomalyAgent.run tests — SAMPLE_SNAPSHOT has latency (critical) + port_util (warning)
assert report is not None, "AnomalyAgent.run() returned None"
assert "device" in report, "Report must include device"
assert "anomaly_count" in report, "Report must include anomaly_count"
assert "anomalies" in report, "Report must include anomalies"
assert "highest_severity" in report, "Report must include highest_severity"

assert report["device"] == "core-sw-01", f"Expected device 'core-sw-01', got {report['device']}"
assert report["anomaly_count"] == 2, f"SAMPLE_SNAPSHOT has 2 anomalous metrics, got {report['anomaly_count']}"
assert report["highest_severity"] == "critical", f"Expected 'critical', got {report['highest_severity']}"

anomaly_metrics = [a["metric"] for a in report["anomalies"]]
assert "latency_ms" in anomaly_metrics, "latency_ms must appear in anomalies"
assert "port_util_pct" in anomaly_metrics, "port_util_pct must appear in anomalies"
assert report["anomalies"][0]["severity"] == "critical", "First anomaly must be critical (highest severity first)"

assert len(output) > 10, "Print report in __main__ block"

print("PASS: Anomaly Detection Agent complete. classify_anomaly + score_severity + AnomalyAgent.run all verified.")
