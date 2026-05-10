# Auto-grader for NetOps M03 Step 1: Auto-Remediation Agent

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    agent = RemediationAgent()
    plan = agent.run(SAMPLE_ANOMALY_REPORT)
    print(f"Device: {plan['device']}")
    print(f"Plans: {plan['plan_count']}")
    print(f"Ready: {plan['ready_to_execute']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# select_playbook tests
pb = select_playbook("latency_ms", "critical")
assert pb is not None, "select_playbook() returned None"
assert pb.get("anomaly_type") == "latency_ms", f"anomaly_type must be 'latency_ms', got {pb.get('anomaly_type')}"
assert pb.get("severity") == "critical", "severity field must match input"
assert pb.get("playbook_name") == "Latency Remediation", f"Expected 'Latency Remediation', got {pb.get('playbook_name')}"
assert len(pb.get("steps", [])) == 3, f"Critical severity includes all 3 latency steps, got {len(pb.get('steps', []))}"

pb_warn = select_playbook("port_util_pct", "warning")
assert pb_warn is not None, "select_playbook() returned None for warning severity"
# warning severity should exclude critical_only steps (consider_lag is critical_only)
for step in pb_warn.get("steps", []):
    assert not step.get("critical_only"), f"Warning severity must exclude critical_only steps, found: {step}"

pb_default = select_playbook("unknown_metric_xyz", "critical")
assert pb_default is not None, "select_playbook() must return default playbook for unknown anomaly type"
assert pb_default.get("playbook_name") == "General Escalation", \
    f"Expected 'General Escalation' for unknown type, got {pb_default.get('playbook_name')}"

# validate_playbook tests
good_steps = [
    {"action": "flush_arp",     "target": "device",    "description": "Flush ARP"},
    {"action": "restart_iface", "target": "interface", "description": "Bounce iface"},
]
val = validate_playbook(good_steps)
assert val is not None, "validate_playbook() returned None"
assert val.get("valid") is True, "All good steps -> valid=True"
assert val.get("step_count") == 2, f"Expected step_count=2, got {val.get('step_count')}"
assert val.get("errors") == [], f"Expected no errors, got {val.get('errors')}"

bad_steps = [
    {"action": "flush_arp", "description": "Missing target"},
]
val_bad = validate_playbook(bad_steps)
assert val_bad.get("valid") is False, "Missing 'target' field -> valid=False"
assert len(val_bad.get("errors", [])) >= 1, "Must report at least one error"

# RemediationAgent.run tests
assert plan is not None, "RemediationAgent.run() returned None"
assert "device" in plan, "Plan must include device"
assert "plan_count" in plan, "Plan must include plan_count"
assert "execution_plan" in plan, "Plan must include execution_plan"
assert "ready_to_execute" in plan, "Plan must include ready_to_execute"

assert plan["device"] == "core-sw-01", f"Expected device 'core-sw-01', got {plan['device']}"
assert plan["plan_count"] == 2, f"SAMPLE has 2 anomalies -> 2 plans, got {plan['plan_count']}"
assert plan["ready_to_execute"] is True, "Built-in playbooks are all valid -> ready_to_execute=True"

anomaly_names = [e["anomaly"] for e in plan["execution_plan"]]
assert "latency_ms" in anomaly_names, "execution_plan must include latency_ms entry"
assert "port_util_pct" in anomaly_names, "execution_plan must include port_util_pct entry"

for entry in plan["execution_plan"]:
    assert "playbook" in entry, "Each entry must have 'playbook'"
    assert "validation" in entry, "Each entry must have 'validation'"
    assert entry["validation"]["valid"] is True, f"Playbook for {entry['anomaly']} must be valid"

assert len(output) > 10, "Print plan fields in __main__ block"

print("PASS: Auto-Remediation Agent complete. select_playbook + validate_playbook + RemediationAgent.run all verified.")
