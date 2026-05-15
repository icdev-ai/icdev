
# Auto-grader for SWE M1 Step 1: Multi-Agent DAG Runner

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    runner = DAGRunner(COMPLIANCE_PIPELINE)
    results = runner.run()
    print(f"Tasks: {sorted(results.keys())}")
    print(f"Scan systems: {results.get('stig_scan', {}).get('systems_scanned')}")
    print(f"Risk level: {results.get('risk_score', {}).get('risk_level')}")
    print(f"Summary: {results.get('executive_summary', {}).get('summary', '')[:60]}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# Basic result structure
assert results is not None, "DAGRunner.run() returned None"
assert isinstance(results, dict), f"run() must return a dict, got {type(results)}"

# All 4 tasks must have run
for task in ["stig_scan", "risk_score", "poam_draft", "executive_summary"]:
    assert task in results, f"Missing task result: '{task}'"
    assert results[task] is not None, f"Task '{task}' returned None"
    assert isinstance(results[task], dict), f"Task '{task}' must return a dict"

# stig_scan correctness
scan = results["stig_scan"]
assert scan.get("systems_scanned") == 3, f"Expected 3 systems, got {scan.get('systems_scanned')}"
assert "findings" in scan, "stig_scan must return 'findings'"
assert len(scan["findings"]) == 3, f"Expected 3 findings entries, got {len(scan['findings'])}"

# risk_score depends on stig_scan
risk = results["risk_score"]
assert "risk_score" in risk, "risk_score result must have 'risk_score'"
assert "risk_level" in risk, "risk_score result must have 'risk_level'"
assert risk["risk_level"] in ("CRITICAL", "HIGH", "MEDIUM"), \
    f"risk_level must be CRITICAL/HIGH/MEDIUM, got {risk['risk_level']}"
# ICDEV-Prod has 2 CAT1, ICDEV-Test has 1 = 3 total -> score=30 -> CRITICAL
assert risk["risk_level"] == "CRITICAL", \
    f"3 CAT1 findings -> score=30 -> CRITICAL, got {risk['risk_level']}"

# poam_draft depends on stig_scan
poam = results["poam_draft"]
assert "poam_entries" in poam, "poam_draft must have 'poam_entries'"
assert poam.get("total_entries") == 2, \
    f"ICDEV-Prod (2 CAT1) and ICDEV-Test (1 CAT1) -> 2 POA&M entries, got {poam.get('total_entries')}"

# executive_summary depends on BOTH risk_score and poam_draft
summary = results["executive_summary"]
assert "summary" in summary, "executive_summary must have 'summary'"
assert isinstance(summary["summary"], str) and len(summary["summary"]) > 20, \
    "Summary must be a non-empty string"
assert summary.get("risk_level") == "CRITICAL", \
    f"Summary risk_level should be CRITICAL, got {summary.get('risk_level')}"
assert summary.get("action_required") is True, \
    "action_required should be True when CAT I findings exist"

# Verify execution order: stig_scan must complete before dependents
# If order was wrong, risk_score.context would lack stig_scan results
# The grader verifies this indirectly by checking correct propagation above

# Test _build_order produces correct batch structure
runner2 = DAGRunner(COMPLIANCE_PIPELINE)
order = runner2._build_order()
assert order is not None, "_build_order() returned None"
assert isinstance(order, list), f"_build_order() must return list of batches"
assert len(order) >= 2, f"Pipeline has at least 3 dependency levels, got {len(order)} batches"

# First batch must only contain stig_scan (no deps)
assert order[0] == ["stig_scan"] or set(order[0]) == {"stig_scan"}, \
    f"First batch must be ['stig_scan'], got {order[0]}"

# Last batch must contain executive_summary
assert any("executive_summary" in batch for batch in order), \
    "executive_summary must appear in some batch"
last_batch = order[-1]
assert "executive_summary" in last_batch, \
    f"executive_summary must be in the LAST batch, got last={last_batch}"

assert len(output) > 20, "Print results in main block"

print("PASS: Multi-Agent DAG complete. _build_order + run + context propagation all verified.")
