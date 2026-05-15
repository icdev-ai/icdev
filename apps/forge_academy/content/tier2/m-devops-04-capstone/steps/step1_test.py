
# Auto-grader for DevOps Capstone M4: CI/CD Pipeline Orchestrator

# ── Test: plan_infrastructure ─────────────────────────────────────────────────

services = ["api-gateway", "auth-service"]
plan = plan_infrastructure("staging", services)

assert plan is not None, "plan_infrastructure() returned None"
assert isinstance(plan, dict), "plan_infrastructure() must return dict"
assert plan["environment"] == "staging", f"environment mismatch: {plan['environment']}"
assert "vpc" in plan, "plan must have 'vpc'"
assert "buckets" in plan, "plan must have 'buckets'"
assert "total_resources" in plan, "plan must have 'total_resources'"

assert "staging-vpc" in plan["vpc"], "VPC block should reference staging-vpc"
assert "10.0.0.0/16" in plan["vpc"], "VPC block should have correct CIDR"

assert len(plan["buckets"]) == len(services), \
    f"Should have {len(services)} buckets, got {len(plan['buckets'])}"
for svc in services:
    assert svc in plan["buckets"], f"Bucket missing for service '{svc}'"
    assert "staging" in plan["buckets"][svc], "Bucket should reference env 'staging'"
    assert "versioning" in plan["buckets"][svc].lower() or "Enabled" in plan["buckets"][svc], \
        f"Bucket for '{svc}' should have versioning enabled"

# total_resources: 1 vpc + 2 per service (bucket + versioning)
assert plan["total_resources"] == 1 + len(services) * 2, \
    f"total_resources should be {1 + len(services)*2}, got {plan['total_resources']}"

# ── Test: validate_deployment — with drift ────────────────────────────────────

validation = validate_deployment(DESIRED_STATE, ACTUAL_STATE)
assert validation is not None, "validate_deployment() returned None"
assert isinstance(validation, dict), "validate_deployment() must return dict"

for key in ["ready_to_deploy", "total_services", "in_sync", "drifted"]:
    assert key in validation, f"Validation missing '{key}'"

assert validation["total_services"] == 2, f"2 desired services, got {validation['total_services']}"
assert validation["ready_to_deploy"] is False, "api-gateway image drift -> not ready"
assert validation["in_sync"] == 1, "auth-service is in sync -> in_sync=1"
assert isinstance(validation["drifted"], list), "drifted must be list"
assert len(validation["drifted"]) >= 1, "Should detect at least 1 drift (image)"

drift_services = {d["service"] for d in validation["drifted"]}
assert "api-gateway" in drift_services, "api-gateway image drift should be detected"

for d in validation["drifted"]:
    assert "service" in d and "field" in d and "desired" in d and "actual" in d, \
        f"Drift item missing required fields: {d}"

# ── Test: validate_deployment — in sync ──────────────────────────────────────

v_sync = validate_deployment(DESIRED_STATE, DESIRED_STATE)
assert v_sync["ready_to_deploy"] is True, "Identical states -> ready_to_deploy=True"
assert v_sync["in_sync"] == 2, "Both services in sync -> in_sync=2"
assert len(v_sync["drifted"]) == 0, "No drift -> empty drifted list"

# ── Test: CICDOrchestrator.run_pipeline ──────────────────────────────────────

orch = CICDOrchestrator()
result = orch.run_pipeline("staging", DESIRED_STATE, ACTUAL_STATE)

assert result is not None, "run_pipeline() returned None"
assert isinstance(result, dict), "run_pipeline() must return dict"

for key in ["environment", "infra_plan", "validation", "pipeline", "status"]:
    assert key in result, f"Result missing '{key}'"

assert result["environment"] == "staging", f"environment mismatch: {result['environment']}"
assert result["status"] == "blocked", "Drift present -> status='blocked'"

assert isinstance(result["pipeline"], list), "pipeline must be list"
assert len(result["pipeline"]) == len(PIPELINE_STAGES), \
    f"Pipeline must have {len(PIPELINE_STAGES)} stages"

stage_names = [s["stage"] for s in result["pipeline"]]
for stage in PIPELINE_STAGES:
    assert stage in stage_names, f"Pipeline missing stage '{stage}'"

for stage in result["pipeline"]:
    assert "stage" in stage and "status" in stage and "description" in stage, \
        f"Stage missing required fields: {stage}"
    assert stage["status"] in ("ready", "blocked"), \
        f"Stage status must be 'ready' or 'blocked', got '{stage['status']}'"

blocked_stages = [s["stage"] for s in result["pipeline"] if s["status"] == "blocked"]
assert "apply" in blocked_stages, "apply stage should be blocked when drift detected"
assert "verify" in blocked_stages, "verify stage should be blocked when drift detected"
assert "plan" not in blocked_stages, "plan stage should be ready regardless"

# In-sync pipeline
result_sync = orch.run_pipeline("prod", DESIRED_STATE, DESIRED_STATE)
assert result_sync["status"] == "ready", "In-sync -> status='ready'"
blocked_sync = [s["stage"] for s in result_sync["pipeline"] if s["status"] == "blocked"]
assert len(blocked_sync) == 0, f"No drift -> no blocked stages, got: {blocked_sync}"

print("PASS: CICDOrchestrator complete. plan_infrastructure + validate_deployment + run_pipeline all verified.")
