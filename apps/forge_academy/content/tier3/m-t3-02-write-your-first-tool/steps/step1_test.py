
# Auto-grader for T3 M2 Step 1: Write Your First ICDEV Tool

# ── Test: lookup_evidence ─────────────────────────────────────────────────────

result = lookup_evidence("ICDEV-Prod", "IA-2")
assert result is not None, "lookup_evidence() returned None"
assert isinstance(result, list), f"lookup_evidence() must return list, got {type(result)}"
assert len(result) == 3, f"ICDEV-Prod/IA-2 has 3 evidence items, got {len(result)}"
for item in result:
    assert "type" in item, f"Each evidence item must have 'type' key, got: {item}"
    assert "description" in item, f"Each evidence item must have 'description' key"

# Unknown key → empty list (no raise)
unknown = lookup_evidence("ICDEV-Unknown", "AC-3")
assert isinstance(unknown, list), "lookup_evidence() for unknown key must return list"
assert len(unknown) == 0, f"Unknown system/control should return [], got {unknown}"

# Known key with empty evidence list
empty_ev = lookup_evidence("ICDEV-Test", "IA-2")
assert isinstance(empty_ev, list), "lookup_evidence() must return list for empty evidence"
assert len(empty_ev) == 0, f"ICDEV-Test/IA-2 has no evidence yet, got {empty_ev}"

# ── Test: derive_compliance_status ───────────────────────────────────────────

# Empty evidence → partial
s_empty = derive_compliance_status([])
assert s_empty == "partial", f"Empty evidence → 'partial', got '{s_empty}'"

# Any finding → non-compliant
s_nc = derive_compliance_status([
    {"type": "finding", "description": "STIG V-220706: MFA not enforced"},
    {"type": "config", "description": "MFA partially configured"},
])
assert s_nc == "non-compliant", f"finding present → 'non-compliant', got '{s_nc}'"

# All compliant types → compliant
s_ok = derive_compliance_status([
    {"type": "config", "description": "MFA enforced"},
    {"type": "scan", "description": "Nessus scan: compliant"},
    {"type": "policy", "description": "SSP documents IA-2"},
])
assert s_ok == "compliant", f"All compliant types → 'compliant', got '{s_ok}'"

# Single log type → compliant
s_log = derive_compliance_status([{"type": "log", "description": "Account review log"}])
assert s_log == "compliant", f"Single log type → 'compliant', got '{s_log}'"

# ── Test: collect_evidence — known system with evidence ───────────────────────

r1 = collect_evidence("ICDEV-Prod", "IA-2")
assert r1 is not None, "collect_evidence() returned None"
assert isinstance(r1, dict), f"collect_evidence() must return dict"
assert "status" in r1, "Result must have 'status' key"
assert "data" in r1, "Result must have 'data' key"
assert "error" in r1, "Result must have 'error' key"
assert "meta" in r1, "Result must have 'meta' key"

assert r1["status"] == "ok", f"ICDEV-Prod/IA-2 has evidence → status='ok', got '{r1['status']}'"
assert r1["error"] is None, f"No error for found system, got: {r1['error']}"

d1 = r1["data"]
assert d1.get("control") == "IA-2", f"data.control should be 'IA-2', got '{d1.get('control')}'"
assert d1.get("system") == "ICDEV-Prod", f"data.system should be 'ICDEV-Prod'"
assert isinstance(d1.get("evidence"), list), "data.evidence must be a list"
assert d1.get("evidence_count") == 3, f"evidence_count should be 3, got {d1.get('evidence_count')}"
assert d1.get("compliance_status") == "compliant", \
    f"ICDEV-Prod/IA-2 is compliant, got '{d1.get('compliance_status')}'"
assert d1.get("evidence_date") is not None, "evidence_date must be present"
assert len(d1.get("evidence_date", "")) == 10, "evidence_date should be YYYY-MM-DD"

# ── Test: collect_evidence — key with empty evidence list ────────────────────

r_test = collect_evidence("ICDEV-Test", "IA-2")
assert r_test["status"] == "ok", \
    f"ICDEV-Test/IA-2 key exists (empty list) → status='ok', got '{r_test['status']}'"
assert r_test["data"].get("compliance_status") == "partial", \
    f"Empty evidence list → compliance_status='partial', got '{r_test['data'].get('compliance_status')}'"
assert r_test["data"].get("evidence_count") == 0, \
    f"evidence_count should be 0 for empty evidence"

# ── Test: collect_evidence — non-compliant system ────────────────────────────

r2 = collect_evidence("ICDEV-Dev", "IA-2")
assert r2["status"] == "ok", f"ICDEV-Dev/IA-2 key exists → status='ok', got '{r2['status']}'"
assert r2["data"].get("compliance_status") == "non-compliant", \
    f"ICDEV-Dev/IA-2 has a finding → 'non-compliant', got '{r2['data'].get('compliance_status')}'"

# ── Test: collect_evidence — completely unknown key ───────────────────────────

r3 = collect_evidence("ICDEV-Unknown", "AC-3")
assert r3["status"] == "partial", \
    f"Unknown system/control → status='partial', got '{r3['status']}'"
assert "message" in r3.get("data", {}), \
    f"Unknown system should have data.message explaining the situation"
assert r3["error"] is None, "error key must be None for partial (not error) status"

# ── Test: run_batch ───────────────────────────────────────────────────────────

batch = run_batch([
    {"system": "ICDEV-Prod",  "control": "IA-2"},
    {"system": "ICDEV-Dev",   "control": "IA-2"},
    {"system": "ICDEV-Test",  "control": "IA-2"},
])
assert batch is not None, "run_batch() returned None"
assert isinstance(batch, dict), "run_batch() must return dict"
assert "status" in batch and batch["status"] == "ok", \
    f"run_batch status should be 'ok', got '{batch.get('status')}'"
assert "results" in batch, "run_batch must have 'results' key"
assert "summary" in batch, "run_batch must have 'summary' key"
assert "meta" in batch, "run_batch must have 'meta' key"

results = batch["results"]
assert len(results) == 3, f"run_batch with 3 requests should return 3 results, got {len(results)}"

summary = batch["summary"]
assert summary.get("total") == 3, f"summary.total should be 3, got {summary.get('total')}"
assert isinstance(summary.get("compliant"), int), "summary.compliant must be int"
assert isinstance(summary.get("non_compliant"), int), "summary.non_compliant must be int"
assert isinstance(summary.get("partial"), int), "summary.partial must be int"
assert summary.get("compliant") == 1, \
    f"Only ICDEV-Prod/IA-2 is compliant → count=1, got {summary.get('compliant')}"
assert summary.get("non_compliant") == 1, \
    f"Only ICDEV-Dev/IA-2 is non-compliant → count=1, got {summary.get('non_compliant')}"

print("PASS: compliance_evidence_collector complete. All functions verified: lookup_evidence + derive_compliance_status + collect_evidence + run_batch.")
