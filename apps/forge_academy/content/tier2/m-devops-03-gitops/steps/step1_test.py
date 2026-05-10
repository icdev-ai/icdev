# Auto-grader for DevOps M3: GitOps Automation Agent

# ── Test: detect_drift ────────────────────────────────────────────────────────

drift = detect_drift(DESIRED_STATE, ACTUAL_STATE)
assert drift is not None, "detect_drift() returned None"
assert isinstance(drift, list), f"detect_drift() must return list"
assert len(drift) >= 3, f"Should detect ≥3 drift items (image, replicas, missing), got {len(drift)}: {drift}"

drift_types = {d["type"] for d in drift}
drift_services = {d["service"] for d in drift}

assert "image" in drift_types, "Should detect image version drift (api-gateway: 2.0.9 vs 2.1.0)"
assert "replicas" in drift_types, "Should detect replica drift (auth-service: 1 vs 2)"
assert "missing" in drift_types, "Should detect missing service (db-proxy)"
assert "db-proxy" in drift_services, "db-proxy should appear in drift"
assert "api-gateway" in drift_services, "api-gateway should appear in drift"
assert "auth-service" in drift_services, "auth-service should appear in drift"

# rag-service is in sync — should not appear
assert "rag-service" not in drift_services, "rag-service is in sync — should not appear in drift"

# Each drift item has required fields
for d in drift:
    for field in ["service", "type", "priority", "desired", "actual"]:
        assert field in d, f"Drift item missing '{field}'"
    assert d["priority"] in ("HIGH", "MEDIUM", "LOW"), f"priority must be HIGH/MEDIUM/LOW, got '{d['priority']}'"

# Missing service -> HIGH priority
missing = [d for d in drift if d["type"] == "missing"]
assert len(missing) >= 1, "Should have at least 1 missing drift item"
assert all(d["priority"] == "HIGH" for d in missing), "Missing type -> HIGH priority"
assert all(d["actual"] is None for d in missing), "Missing type -> actual=None"

# Image drift -> HIGH priority
image_drift = [d for d in drift if d["type"] == "image"]
assert len(image_drift) >= 1, "Should have at least 1 image drift"
assert all(d["priority"] == "HIGH" for d in image_drift), "Image type -> HIGH priority"

# Replica drift -> MEDIUM priority
replica_drift = [d for d in drift if d["type"] == "replicas"]
assert len(replica_drift) >= 1, "Should have at least 1 replica drift"
assert all(d["priority"] == "MEDIUM" for d in replica_drift), "Replicas type -> MEDIUM priority"

# ── Test: detect_drift — in-sync state ───────────────────────────────────────

no_drift = detect_drift(DESIRED_STATE, DESIRED_STATE)
assert len(no_drift) == 0, f"Identical states -> no drift, got {len(no_drift)} items"

# ── Test: plan_reconciliation ─────────────────────────────────────────────────

actions = plan_reconciliation(drift)
assert actions is not None, "plan_reconciliation() returned None"
assert isinstance(actions, list), "plan_reconciliation() must return list"
assert len(actions) == len(drift), f"One action per drift item, got {len(actions)} for {len(drift)} drifts"

action_types = {a["action"] for a in actions}
assert "deploy" in action_types, "Missing service -> deploy action"
assert "update" in action_types, "Image drift -> update action"
assert "scale" in action_types, "Replica drift -> scale action"

for a in actions:
    assert "action" in a, "Action must have 'action'"
    assert "service" in a, "Action must have 'service'"
    assert "reason" in a, "Action must have 'reason'"
    assert a["action"] in ("deploy", "update", "scale", "review"), \
        f"Invalid action '{a['action']}'"
    assert len(a["reason"]) > 5, "reason must be descriptive"

# HIGH priority actions should come before LOW
high_indices = [i for i, a in enumerate(actions) if any(d["service"]==a["service"] and d["priority"]=="HIGH" for d in drift)]
low_indices = [i for i, a in enumerate(actions) if any(d["service"]==a["service"] and d["priority"]=="LOW" for d in drift)]
if high_indices and low_indices:
    assert max(high_indices) < max(low_indices) or min(high_indices) < max(low_indices), \
        "HIGH priority actions should generally come before LOW priority"

# ── Test: GitOpsAgent.reconcile ───────────────────────────────────────────────

agent = GitOpsAgent()
report = agent.reconcile(DESIRED_STATE, ACTUAL_STATE)

assert report is not None, "reconcile() returned None"
assert isinstance(report, dict), "reconcile() must return dict"
assert "in_sync" in report, "Result must have 'in_sync'"
assert "drift_count" in report, "Result must have 'drift_count'"
assert "high_priority" in report, "Result must have 'high_priority'"
assert "drift" in report, "Result must have 'drift'"
assert "actions" in report, "Result must have 'actions'"

assert report["in_sync"] is False, "States are not in sync -> in_sync=False"
assert report["drift_count"] >= 3, f"≥3 drift items, got {report['drift_count']}"
assert report["high_priority"] >= 2, \
    f"At least 2 HIGH priority items (image+missing), got {report['high_priority']}"

# In-sync reconcile
report_sync = agent.reconcile(DESIRED_STATE, DESIRED_STATE)
assert report_sync["in_sync"] is True, "Identical states -> in_sync=True"
assert report_sync["drift_count"] == 0, "No drift in identical states"
assert len(report_sync["actions"]) == 0, "No actions needed when in sync"

print("PASS: GitOpsAgent complete. detect_drift + plan_reconciliation + reconcile() all verified.")
