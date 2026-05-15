
# Auto-grader for SecOps-Eng M3: Compliance Evidence Pipeline

# ── Test: collect_scan_evidence ───────────────────────────────────────────────

# Known pass
scan_prod_ia2 = collect_scan_evidence("ICDEV-Prod", "IA-2")
assert scan_prod_ia2 is not None, "collect_scan_evidence() returned None"
assert isinstance(scan_prod_ia2, dict), "collect_scan_evidence() must return dict"
assert scan_prod_ia2["type"] == "scan", f"type must be 'scan', got '{scan_prod_ia2['type']}'"
assert scan_prod_ia2["status"] == "pass", f"ICDEV-Prod/IA-2 scan passes, got '{scan_prod_ia2['status']}'"
assert scan_prod_ia2["source"] == "Nessus", f"scanner is Nessus, got '{scan_prod_ia2['source']}'"
assert scan_prod_ia2.get("finding") is None, "Passing scan should have no finding"

# Known fail
scan_dev_ia2 = collect_scan_evidence("ICDEV-Dev", "IA-2")
assert scan_dev_ia2["status"] == "fail", f"ICDEV-Dev/IA-2 scan fails, got '{scan_dev_ia2['status']}'"
assert scan_dev_ia2.get("finding") is not None, "Failed scan should have finding"
assert "V-220706" in scan_dev_ia2["finding"], "Finding should mention V-220706"

# Not scanned (unknown combination)
scan_unknown = collect_scan_evidence("ICDEV-Test", "IA-2")
assert scan_unknown["status"] == "not_scanned", \
    f"Unknown combo -> 'not_scanned', got '{scan_unknown['status']}'"
assert scan_unknown["source"] == "none", "Not scanned -> source='none'"
assert scan_unknown["date"] is None, "Not scanned -> date=None"

# ── Test: collect_policy_evidence ─────────────────────────────────────────────

# Known current
policy_ia2 = collect_policy_evidence("IA-2")
assert policy_ia2 is not None, "collect_policy_evidence() returned None"
assert policy_ia2["type"] == "policy", f"type must be 'policy', got '{policy_ia2['type']}'"
assert policy_ia2["status"] == "current", f"IA-2 policy is current, got '{policy_ia2['status']}'"
assert policy_ia2["document"] is not None, "IA-2 should have a document"
assert "SSP" in policy_ia2["document"], "Document should reference SSP"

# Known outdated
policy_au2 = collect_policy_evidence("AU-2")
assert policy_au2["status"] == "outdated", f"AU-2 policy is outdated, got '{policy_au2['status']}'"

# Missing control
policy_missing = collect_policy_evidence("ZZ-99")
assert policy_missing["status"] == "missing", \
    f"Unknown control -> 'missing', got '{policy_missing['status']}'"
assert policy_missing["document"] is None, "Missing policy -> document=None"

# ── Test: score_evidence ──────────────────────────────────────────────────────

# Pass + current -> 1.0 * weight(IA-2=1.0) = 1.0
scan_pass = {"type":"scan","status":"pass","source":"Nessus","date":"2026-04-30","finding":None}
policy_current = {"type":"policy","status":"current","document":"SSP 3.5.1","last_review":"2026-03-01"}
score_top = score_evidence(scan_pass, policy_current, "IA-2")
assert score_top is not None, "score_evidence() returned None"
assert isinstance(score_top, float), "score_evidence() must return float"
assert abs(score_top - 1.0) < 0.01, f"pass+current+IA-2(1.0) -> 1.0, got {score_top}"

# Pass + outdated -> 0.7 * weight(AC-2=0.9) = 0.63
policy_outdated = {"type":"policy","status":"outdated","document":"SSP 3.1","last_review":"2024-01-01"}
score_outdated = score_evidence(scan_pass, policy_outdated, "AC-2")
assert abs(score_outdated - 0.63) < 0.02, f"pass+outdated+AC-2(0.9) -> 0.63, got {score_outdated}"

# Fail + current -> 0.2 * 1.0 = 0.2
scan_fail = {"type":"scan","status":"fail","source":"Nessus","date":"2026-04-30","finding":"V-xxx"}
score_fail = score_evidence(scan_fail, policy_current, "IA-2")
assert abs(score_fail - 0.2) < 0.01, f"fail -> 0.2 * 1.0 = 0.2, got {score_fail}"

# Not scanned + missing -> 0.0
scan_none = {"type":"scan","status":"not_scanned","source":"none","date":None,"finding":None}
policy_miss = {"type":"policy","status":"missing","document":None,"last_review":None}
score_zero = score_evidence(scan_none, policy_miss, "IA-2")
assert abs(score_zero - 0.0) < 0.01, f"not_scanned+missing -> 0.0, got {score_zero}"

# ── Test: EvidencePipeline.run ────────────────────────────────────────────────

pipeline = EvidencePipeline()
result = pipeline.run(
    systems=["ICDEV-Prod", "ICDEV-Dev"],
    controls=["IA-2", "AC-2", "AU-2"],
)

assert result is not None, "run() returned None"
assert isinstance(result, dict), "run() must return dict"
assert "run_date" in result, "Result must have 'run_date'"
assert "records" in result, "Result must have 'records'"
assert "summary" in result, "Result must have 'summary'"

assert len(result["records"]) == 6, \
    f"2 systems × 3 controls = 6 records, got {len(result['records'])}"

summary = result["summary"]
assert summary["total"] == 6, f"total should be 6, got {summary['total']}"
assert isinstance(summary["compliant"], int), "compliant must be int"
assert isinstance(summary["non_compliant"], int), "non_compliant must be int"
assert summary["compliant"] + summary["non_compliant"] == 6, \
    "compliant + non_compliant must equal total"
assert isinstance(summary["avg_score"], float), "avg_score must be float"
assert 0.0 <= summary["avg_score"] <= 1.0, f"avg_score out of range: {summary['avg_score']}"

# ICDEV-Prod should score higher than ICDEV-Dev (Prod has no failing scans)
prod_records = [r for r in result["records"] if r["system"] == "ICDEV-Prod"]
dev_records = [r for r in result["records"] if r["system"] == "ICDEV-Dev"]
prod_avg = sum(r["score"] for r in prod_records) / len(prod_records)
dev_avg = sum(r["score"] for r in dev_records) / len(dev_records)
assert prod_avg > dev_avg, \
    f"ICDEV-Prod should score higher than ICDEV-Dev ({prod_avg:.3f} vs {dev_avg:.3f})"

# Each record has required fields
for rec in result["records"]:
    for field in ["system", "control", "scan", "policy", "score", "compliant"]:
        assert field in rec, f"Record missing '{field}' field"
    assert isinstance(rec["compliant"], bool), "compliant must be bool"
    assert isinstance(rec["score"], float), "score must be float"
    assert 0.0 <= rec["score"] <= 1.0, f"score out of range: {rec['score']}"

print("PASS: EvidencePipeline complete. collect_scan_evidence + collect_policy_evidence + score_evidence + run() all verified.")
