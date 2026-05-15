
# Auto-grader for NetOps M04 Step 1: NetOps Capstone

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    pipeline = NOCPipeline()
    report = pipeline.run(SAMPLE_CONFIGS, SAMPLE_METRICS)
    print(f"Nodes: {report['topology']['node_count']}")
    print(f"Devices checked: {report['devices_checked']}")
    print(f"With anomalies: {report['devices_with_anomalies']}")
    print(f"Status: {report['overall_status']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# parse_and_build_topology tests
topo = parse_and_build_topology(SAMPLE_CONFIGS)
assert topo is not None, "parse_and_build_topology() returned None"
assert topo.get("node_count") == 3, f"Expected 3 nodes, got {topo.get('node_count')}"
assert "edge-rtr-01" in topo.get("unknown_nodes", []), "edge-rtr-01 must be in unknown_nodes"

# detect_anomalies_for_device tests
anomaly = detect_anomalies_for_device("core-sw-01", SAMPLE_METRICS["core-sw-01"])
assert anomaly is not None, "core-sw-01 has anomalies — must return a dict"
assert anomaly.get("device") == "core-sw-01", "device field must be set"
assert anomaly.get("anomaly_count", 0) >= 2, "core-sw-01 has at least 2 anomalous metrics"
assert anomaly.get("highest_severity") == "critical", "latency_ms=620 -> highest_severity='critical'"

clean = detect_anomalies_for_device("access-sw-01", SAMPLE_METRICS["access-sw-01"])
assert clean is None, "access-sw-01 metrics are all normal — must return None"

# build_remediation_plan tests
sample_anomaly_summary = {
    "device": "core-sw-01",
    "anomaly_count": 2,
    "highest_severity": "critical",
    "anomalies": [
        {"metric": "latency_ms",    "value": 620, "state": "critical", "severity": "critical",
         "warning_threshold": 100, "critical_threshold": 500},
        {"metric": "port_util_pct", "value": 88,  "state": "warning",  "severity": "high",
         "warning_threshold": 75, "critical_threshold": 95},
    ],
}
plan = build_remediation_plan(sample_anomaly_summary)
assert plan is not None, "build_remediation_plan() returned None"
assert plan.get("device") == "core-sw-01", "plan must include device"
assert plan.get("plan_count") == 2, f"Expected plan_count=2, got {plan.get('plan_count')}"
assert "ready_to_execute" in plan, "plan must include ready_to_execute"
assert plan["ready_to_execute"] is True, "built-in playbooks are valid -> ready_to_execute=True"

# NOCPipeline.run tests
assert report is not None, "NOCPipeline.run() returned None"
assert "topology" in report, "Report must include topology"
assert "devices_checked" in report, "Report must include devices_checked"
assert "devices_with_anomalies" in report, "Report must include devices_with_anomalies"
assert "remediation_plans" in report, "Report must include remediation_plans"
assert "overall_status" in report, "Report must include overall_status"

assert report["topology"]["node_count"] == 3, \
    f"Expected topology node_count=3, got {report['topology']['node_count']}"
assert report["devices_checked"] == 2, \
    f"SAMPLE_METRICS has 2 devices, got {report['devices_checked']}"
assert report["devices_with_anomalies"] == 1, \
    f"Only core-sw-01 has anomalies, got {report['devices_with_anomalies']}"
assert report["overall_status"] == "ACTION_REQUIRED", \
    f"core-sw-01 has anomalies -> ACTION_REQUIRED, got {report['overall_status']}"

assert len(report["remediation_plans"]) == 1, "Only 1 device with anomalies -> 1 plan"
rp = report["remediation_plans"][0]
assert rp.get("device") == "core-sw-01", f"Plan device must be 'core-sw-01', got {rp.get('device')}"
assert rp.get("ready_to_execute") is True, "Remediation plan must be ready_to_execute=True"

assert len(output) > 10, "Print report fields in __main__ block"

print("PASS: NetOps Capstone complete. Full NOC pipeline verified: topology -> anomaly -> remediation.")
