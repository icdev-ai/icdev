# Auto-grader for SRE M03 Step 1: Chaos Engineering Agent

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    agent = ChaosAgent()
    report = agent.run(SAMPLE_SERVICE, FAILURE_MODE, SAMPLE_BASELINE, SAMPLE_POST)
    print(f"Experiment: {report['experiment']['experiment_id']}")
    print(f"Risk: {report['blast_radius']['risk_level']}")
    print(f"Conclusion: {report['conclusion']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# design_experiment tests
exp = design_experiment("auth-service", "network_delay")
assert exp is not None, "design_experiment() returned None"
assert exp.get("experiment_id") == "exp-auth-service-network_delay", \
    f"Expected 'exp-auth-service-network_delay', got {exp.get('experiment_id')}"
assert exp.get("service") == "auth-service", "service must be in experiment"
assert exp.get("failure_mode") == "network_delay", "failure_mode must be in experiment"
assert isinstance(exp.get("duration_seconds"), int), "duration_seconds must be int"
assert exp.get("rollback"), "rollback must be non-empty"
assert exp.get("hypothesis"), "hypothesis must be non-empty"
assert exp.get("description"), "description must be non-empty"

# Unknown failure mode gets a generic fallback
exp_unknown = design_experiment("auth-service", "memory_leak")
assert exp_unknown is not None, "Unknown failure mode must return fallback experiment"
assert exp_unknown.get("experiment_id") == "exp-auth-service-memory_leak", \
    f"experiment_id format must hold for unknown modes"

# estimate_blast_radius tests
blast = estimate_blast_radius(exp)
assert blast is not None, "estimate_blast_radius() returned None"
assert "affected_users_pct" in blast, "Must have affected_users_pct"
assert "affected_downstream" in blast, "Must have affected_downstream"
assert "risk_level" in blast, "Must have risk_level"
assert "safe_to_run" in blast, "Must have safe_to_run"
assert blast.get("experiment_id") == exp["experiment_id"], "experiment_id must match"
assert blast.get("risk_level") == "medium", \
    f"network_delay risk is 'medium', got {blast.get('risk_level')}"
assert blast.get("safe_to_run") is True, "medium risk -> safe_to_run=True"
# auth-service has downstream dependencies
assert len(blast.get("affected_downstream", [])) >= 1, \
    "auth-service has downstream services in SERVICE_DEPENDENCIES"

# High-risk experiment must NOT be safe to run
exp_high = design_experiment("auth-service", "pod_failure")
blast_high = estimate_blast_radius(exp_high)
assert blast_high.get("risk_level") == "high", \
    f"pod_failure risk is 'high', got {blast_high.get('risk_level')}"
assert blast_high.get("safe_to_run") is False, "high risk -> safe_to_run=False"

# verify_steady_state tests
results = verify_steady_state(SAMPLE_BASELINE, SAMPLE_POST)
assert results is not None, "verify_steady_state() returned None"
assert len(results) >= 1, "Must return at least one metric result"
for r in results:
    assert "metric" in r, "Each result must have 'metric'"
    assert "baseline" in r, "Each result must have 'baseline'"
    assert "post" in r, "Each result must have 'post'"
    assert "passed" in r, "Each result must have 'passed'"

# SAMPLE_POST should pass: error 0.1->0.3 (+0.2 ≤ 1.0 OK), latency 120->175 (1.46x ≤ 1.5 OK), success 99.9->99.7 (-0.2 ≤ 1.0 OK)
for r in results:
    assert r["passed"] is True, f"SAMPLE_POST metrics are within tolerance — {r['metric']} should pass"

# ChaosAgent.run tests
assert report is not None, "ChaosAgent.run() returned None"
assert "experiment" in report, "Report must include experiment"
assert "blast_radius" in report, "Report must include blast_radius"
assert "steady_state_passed" in report, "Report must include steady_state_passed"
assert "metric_results" in report, "Report must include metric_results"
assert "conclusion" in report, "Report must include conclusion"

assert report["conclusion"] == "PASSED", \
    f"SAMPLE metrics within tolerance -> PASSED, got {report['conclusion']}"
assert report["steady_state_passed"] is True, "All sample metrics pass -> steady_state_passed=True"

# High-risk experiment should be SKIPPED
skipped = agent.run("auth-service", "pod_failure", SAMPLE_BASELINE, SAMPLE_POST)
assert skipped["conclusion"] == "SKIPPED", \
    f"pod_failure is high risk -> SKIPPED, got {skipped['conclusion']}"
assert skipped["metric_results"] == [], "SKIPPED means no metric results were computed"

assert len(output) > 10, "Print report fields in __main__ block"

print("PASS: Chaos Engineering Agent complete. design_experiment + estimate_blast_radius + verify_steady_state + ChaosAgent.run all verified.")
