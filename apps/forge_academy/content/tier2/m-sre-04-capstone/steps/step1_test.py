# Auto-grader for SRE M04 Step 1: SRE Capstone

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    pipeline = SREPipeline()
    report = pipeline.run(SAMPLE_SERVICES)
    print(f"Services: {report['services_evaluated']}")
    print(f"Paged: {report['paged']}")
    print(f"Chaos: {report['monitored_with_chaos']}")
    print(f"Status: {report['overall_status']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# compute_slo_health tests
# auth-service: 99.9% SLO, 1h window, 50k reqs, 120 errors -> budget consumed 240% -> critical
slo = compute_slo_health(99.9, 1, 50000, 120)
assert slo is not None, "compute_slo_health() returned None"
assert "recommendation" in slo, "Must have recommendation"
assert "burn_rate_status" in slo, "Must have burn_rate_status"
assert "budget_consumed_pct" in slo, "Must have budget_consumed_pct"
assert slo.get("recommendation") == "PAGE_ONCALL", \
    f"240% budget consumed -> PAGE_ONCALL, got {slo.get('recommendation')}"
assert slo.get("burn_rate_status") == "critical", \
    f"Extreme burn rate -> critical, got {slo.get('burn_rate_status')}"

# cache-service: 99.5% SLO, 24h, 500k reqs, 100 errors -> healthy
slo_cache = compute_slo_health(99.5, 24, 500000, 100)
assert slo_cache.get("recommendation") in ("NO_ACTION", "MONITOR"), \
    f"cache-service is well within budget, got {slo_cache.get('recommendation')}"
assert slo_cache.get("burn_rate_status") in ("healthy", "elevated"), \
    f"cache-service should be healthy or elevated"

# build_incident_brief tests
incident = build_incident_brief("auth-service", SAMPLE_SERVICES[0]["metrics"])
assert incident is not None, "build_incident_brief() returned None"
assert "incident_id" in incident, "Must have incident_id"
assert "severity" in incident, "Must have severity"
assert "page_oncall" in incident, "Must have page_oncall"
assert "runbook_name" in incident, "Must have runbook_name"
assert incident.get("incident_id", "").startswith("INC-auth-service"), \
    f"incident_id must start with 'INC-auth-service', got {incident.get('incident_id')}"
# error_rate=2.5% > 1.0 -> alert_type=error_rate; user_facing=True -> SEV1 or SEV2
assert incident.get("severity") in ("SEV1", "SEV2"), \
    f"auth-service with user_facing=True, error_rate=2.5% -> SEV1 or SEV2, got {incident.get('severity')}"
assert incident.get("page_oncall") is True, "SEV1/2 -> page_oncall=True"

# schedule_chaos tests
chaos = schedule_chaos("cache-service")
assert chaos is not None, "schedule_chaos() returned None"
assert "experiment_id" in chaos, "Must have experiment_id"
assert "risk_level" in chaos, "Must have risk_level"
assert "safe_to_run" in chaos, "Must have safe_to_run"
assert chaos.get("experiment_id") == "exp-cache-service-network_delay", \
    f"Default chaos mode is network_delay, got {chaos.get('experiment_id')}"
assert chaos.get("risk_level") == "medium", "network_delay is medium risk"
assert chaos.get("safe_to_run") is True, "medium risk -> safe_to_run=True"

# SREPipeline.run tests
assert report is not None, "SREPipeline.run() returned None"
assert "services_evaluated" in report, "Report must include services_evaluated"
assert "paged" in report, "Report must include paged"
assert "monitored_with_chaos" in report, "Report must include monitored_with_chaos"
assert "results" in report, "Report must include results"
assert "overall_status" in report, "Report must include overall_status"

assert report["services_evaluated"] == 2, f"2 SAMPLE_SERVICES, got {report['services_evaluated']}"
assert report["paged"] == 1, f"auth-service -> paged=1, got {report['paged']}"
assert report["monitored_with_chaos"] == 1, f"cache-service -> chaos=1, got {report['monitored_with_chaos']}"
assert report["overall_status"] == "CRITICAL", \
    f"auth-service paged -> CRITICAL overall, got {report['overall_status']}"
assert len(report["results"]) == 2, "Must have one result per service"

# Verify structure of each result
for result in report["results"]:
    assert "service" in result, "Each result must have service"
    assert "slo_health" in result, "Each result must have slo_health"
    assert "action" in result, "Each result must have action"
    assert result["action"] in ("paged", "chaos_scheduled"), \
        f"action must be 'paged' or 'chaos_scheduled', got {result['action']}"

auth_result = next(r for r in report["results"] if r["service"] == "auth-service")
assert auth_result["action"] == "paged", "auth-service must be paged"
assert "incident" in auth_result, "paged result must have incident"

cache_result = next(r for r in report["results"] if r["service"] == "cache-service")
assert cache_result["action"] == "chaos_scheduled", "cache-service must have chaos scheduled"
assert "chaos" in cache_result, "chaos_scheduled result must have chaos"

assert len(output) > 10, "Print report fields in __main__ block"

print("PASS: SRE Capstone complete. Full reliability pipeline verified: SLO -> incident -> chaos across all services.")
