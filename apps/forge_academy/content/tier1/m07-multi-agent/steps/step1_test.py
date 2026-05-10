# Auto-grader for M07 Step 1: Multi-Agent Coordination

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    result1 = run_orchestrator("Run a full security assessment for ICDEV-Prod and generate a remediation report.")
    result2 = run_orchestrator("Assess ICDEV-Dev system compliance status.")
    if result1:
        print(f"System: {result1.get('system')}")
        print(f"Workers: {result1.get('workers_called')}")
        print(f"Report: {str(result1.get('report',''))[:80]}")
    if result2:
        print(f"Dev system: {result2.get('system')}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

assert result1 is not None, "run_orchestrator() returned None — implement the function"
assert isinstance(result1, dict), f"run_orchestrator() must return a dict, got {type(result1)}"

required_keys = {"system", "findings", "context_docs", "report", "workers_called"}
missing = required_keys - set(result1.keys())
assert not missing, f"Result missing keys: {missing}"

# All 3 workers must have been called
workers = result1.get("workers_called", [])
assert len(workers) == 3, f"Expected 3 workers called, got {len(workers)}: {workers}"

# Findings from ICDEV-Prod
findings = result1.get("findings", {})
assert "cat1" in findings, "Findings must include 'cat1' from worker_stig_scanner"
assert findings["cat1"] == 2, f"ICDEV-Prod should have 2 CAT1 findings, got {findings['cat1']}"

# Context docs
ctx = result1.get("context_docs", {})
assert "docs_found" in ctx, "context_docs must include 'docs_found' from worker_rag_context"

# Report generated
report = result1.get("report", "")
assert isinstance(report, str) and len(report) > 20, "Report must be a non-empty string from worker_report_writer"
assert any(kw in report for kw in ["ICDEV-Prod", "CAT", "Security", "CRITICAL"]), \
    "Report should reference system name and findings"

# Print output required
assert "Dispatching" in output or "dispatch" in output.lower(), \
    "Print each dispatch step: 'Dispatching <worker_name>...'"

# ICDEV-Dev has 0 CAT1 findings
dev_findings = result2.get("findings", {})
assert dev_findings.get("cat1") == 0, f"ICDEV-Dev should have 0 CAT1 findings, got {dev_findings.get('cat1')}"

print(f"PASS: Orchestrator-worker pattern working. {len(workers)} workers dispatched, report synthesized.")
