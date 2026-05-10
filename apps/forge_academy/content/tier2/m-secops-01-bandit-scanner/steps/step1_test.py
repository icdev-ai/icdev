# Auto-grader for SecOps M1 Step 1: Bandit Security Scanner Agent

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    scanner = BanditScanner()
    findings = scanner.scan(VULNERABLE_CODE_SAMPLE)
    triaged = triage_findings(findings)
    report = generate_report(triaged)
    print(f"Findings: {len(findings)}")
    print(f"Critical: {len(triaged.get('critical', []))}")
    print(f"High: {len(triaged.get('high', []))}")
    print(f"Report len: {len(report)}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# BanditScanner.scan tests
assert findings is not None, "BanditScanner.scan() returned None"
assert isinstance(findings, list), f"scan() must return a list, got {type(findings)}"
assert len(findings) >= 3, f"scan() should find ≥3 issues in VULNERABLE_CODE_SAMPLE, found {len(findings)}"

# All findings must have required fields
for f in findings:
    assert "test_id" in f, "Each finding must have 'test_id'"
    assert "severity" in f, "Each finding must have 'severity'"
    assert "confidence" in f, "Each finding must have 'confidence'"

# triage_findings tests
assert triaged is not None, "triage_findings() returned None"
for bucket in ["critical", "high", "medium", "low"]:
    assert bucket in triaged, f"triaged must have '{bucket}' bucket"
    assert isinstance(triaged[bucket], list), f"triaged['{bucket}'] must be a list"

# Verify CRITICAL: B105 (HIGH/HIGH) + B602/similar (HIGH/HIGH) should be critical
assert len(triaged["critical"]) >= 1, \
    f"HIGH+HIGH findings should be CRITICAL, found {len(triaged['critical'])} critical"

# Verify total count matches
total_triaged = sum(len(v) for v in triaged.values())
assert total_triaged == len(findings), \
    f"All findings must be triaged: {len(findings)} found, {total_triaged} triaged"

# generate_report tests
assert report is not None, "generate_report() returned None"
assert isinstance(report, str), f"generate_report() must return str, got {type(report)}"
assert len(report) > 50, "Report too short — include finding details"
assert "CWE-" in report, "Report must include at least one CWE reference"

# Report must contain at least critical or high count
report_lower = report.lower()
assert any(kw in report_lower for kw in ["critical", "high", "finding", "total"]), \
    "Report must reference finding severity or count"

assert len(output) > 20, "Print scan results in main block"

print("PASS: Bandit Security Scanner Agent complete. scan + triage + report all verified.")
