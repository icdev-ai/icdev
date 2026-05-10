# Auto-grader for DevOps M1 Step 1: CI/CD Pipeline Agent

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    agent = PipelineAgent()
    report = agent.run(SAMPLE_PIPELINE_EVENT)
    print(f"Pipeline: {report.get('pipeline_id')}")
    print(f"Failed: {len(report.get('failed_stages', []))}")
    if report.get('failed_stages'):
        fs = report['failed_stages'][0]
        print(f"Stage: {fs.get('stage')}")
        print(f"Category: {fs.get('analysis', {}).get('category')}")
        print(f"Fix: {fs.get('fix', {}).get('fix_command', '')[:40]}")
    print(f"ETA: {report.get('total_estimated_minutes')}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# analyze_failure tests
result = analyze_failure("test", "FAILED test_foo.py::test_bar - AssertionError")
assert result is not None, "analyze_failure() returned None"
assert result.get("category") == "test_failure", f"Expected 'test_failure', got {result.get('category')}"

result2 = analyze_failure("install", "ModuleNotFoundError: No module named 'flask'")
assert result2.get("category") == "missing_dependency", f"Expected 'missing_dependency', got {result2.get('category')}"

result3 = analyze_failure("deploy", "Connection refused: http://registry:5000")
assert result3.get("category") == "network_error", f"Expected 'network_error', got {result3.get('category')}"

result4 = analyze_failure("lint", "no obvious error here, just noise")
assert result4.get("category") == "unknown", f"Expected 'unknown' for unmatched logs, got {result4.get('category')}"

# generate_fix tests
fix = generate_fix("FAILED assertion", "test_failure")
assert fix is not None, "generate_fix() returned None"
assert fix.get("fix_command"), "fix_command must be non-empty"
assert fix.get("estimated_minutes") is not None, "estimated_minutes must be present"
assert isinstance(fix["estimated_minutes"], int), "estimated_minutes must be an int"

fix_unknown = generate_fix("unknown", "unknown")
assert fix_unknown.get("fix_command"), "Unknown category should return a default fix"

# PipelineAgent.run tests
assert report is not None, "PipelineAgent.run() returned None"
assert "pipeline_id" in report, "Report must include pipeline_id"
assert "failed_stages" in report, "Report must include failed_stages"
assert "total_estimated_minutes" in report, "Report must include total_estimated_minutes"
assert "recommendation" in report, "Report must include recommendation"

failed_stages = report["failed_stages"]
assert len(failed_stages) == 1, f"SAMPLE_PIPELINE_EVENT has 1 failed stage, got {len(failed_stages)}"

fs = failed_stages[0]
assert fs["stage"] == "test", f"Failed stage should be 'test', got {fs['stage']}"
assert fs["analysis"]["category"] == "test_failure", \
    f"Test stage failure should categorize as 'test_failure', got {fs['analysis']['category']}"
assert fs["fix"]["fix_command"], "Fix command must be non-empty"

assert report["total_estimated_minutes"] == 20, \
    f"test_failure fix is 20 min, got {report['total_estimated_minutes']}"

assert len(output) > 20, "Print report in main block"

print("PASS: CI/CD Pipeline Agent complete. analyze_failure + generate_fix + PipelineAgent.run all verified.")
