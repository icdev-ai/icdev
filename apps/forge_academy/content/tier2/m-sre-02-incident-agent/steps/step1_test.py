
# Auto-grader for SRE M02 Step 1: Incident Response Agent

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    agent = IncidentAgent()
    brief = agent.run(SAMPLE_ALERT)
    print(f"ID: {brief['incident_id']}")
    print(f"Severity: {brief['classification']['severity']}")
    print(f"Page: {brief['classification']['page_oncall']}")
    print(f"Summary: {brief['summary']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# classify_incident tests
cls = classify_incident(SAMPLE_ALERT)
assert cls is not None, "classify_incident() returned None"
assert "severity" in cls, "Must have severity field"
assert "page_oncall" in cls, "Must have page_oncall field"
assert "impact" in cls, "Must have impact field"
assert cls.get("severity") == "SEV2", \
    f"user_facing + latency_p99=5200ms + error_rate=2.5% -> SEV2, got {cls.get('severity')}"
assert cls.get("page_oncall") is True, "SEV2 must page oncall"
assert cls.get("user_facing") is True, "user_facing must be carried through"

# SEV1 scenario
sev1_alert = {**SAMPLE_ALERT, "error_rate_pct": 15.0, "latency_p99_ms": 100}
cls1 = classify_incident(sev1_alert)
assert cls1.get("severity") == "SEV1", \
    f"user_facing + error_rate=15% -> SEV1, got {cls1.get('severity')}"
assert cls1.get("page_oncall") is True, "SEV1 must page oncall"

# SEV3 scenario — non-user-facing
sev3_alert = {**SAMPLE_ALERT, "user_facing": False, "error_rate_pct": 0.5, "latency_p99_ms": 300}
cls3 = classify_incident(sev3_alert)
assert cls3.get("severity") in ("SEV3", "SEV4"), \
    f"non-user-facing, low error rate -> SEV3 or SEV4, got {cls3.get('severity')}"
assert cls3.get("page_oncall") is False, "SEV3/4 must not page oncall"

# SEV4 scenario
sev4_alert = {**SAMPLE_ALERT, "user_facing": False, "error_rate_pct": 0.1, "latency_p99_ms": 200}
cls4 = classify_incident(sev4_alert)
assert cls4.get("severity") == "SEV4", \
    f"non-user-facing, normal metrics -> SEV4, got {cls4.get('severity')}"

# select_runbook tests
rb = select_runbook("auth-service", "latency")
assert rb is not None, "select_runbook() returned None"
assert rb.get("service") == "auth-service", "service must be in runbook result"
assert rb.get("incident_type") == "latency", "incident_type must be in runbook result"
assert rb.get("runbook_name") == "Auth Service Latency Runbook", \
    f"Expected 'Auth Service Latency Runbook', got {rb.get('runbook_name')}"
assert len(rb.get("steps", [])) >= 1, "Runbook must have at least 1 step"
assert len(rb.get("escalation_path", [])) >= 1, "Runbook must have escalation path"

rb_default = select_runbook("unknown-service", "cpu")
assert rb_default is not None, "Unknown service must return default runbook"
assert rb_default.get("runbook_name") == "General Incident Runbook", \
    f"Expected 'General Incident Runbook', got {rb_default.get('runbook_name')}"

# IncidentAgent.run tests
assert brief is not None, "IncidentAgent.run() returned None"
assert "incident_id" in brief, "Brief must include incident_id"
assert "service" in brief, "Brief must include service"
assert "classification" in brief, "Brief must include classification"
assert "runbook" in brief, "Brief must include runbook"
assert "summary" in brief, "Brief must include summary"

assert brief["incident_id"] == "INC-auth-service-latency", \
    f"Expected 'INC-auth-service-latency', got {brief['incident_id']}"
assert brief["service"] == "auth-service", f"Expected 'auth-service', got {brief['service']}"
assert brief["classification"]["severity"] == "SEV2", "classification.severity must be SEV2"
assert brief["runbook"]["runbook_name"] == "Auth Service Latency Runbook", \
    "runbook must be auth-service latency runbook"
assert isinstance(brief["summary"], str) and len(brief["summary"]) > 5, "summary must be non-empty string"
assert "SEV2" in brief["summary"], "summary must mention the severity level"

assert len(output) > 10, "Print brief fields in __main__ block"

print("PASS: Incident Response Agent complete. classify_incident + select_runbook + IncidentAgent.run all verified.")
