
# Auto-grader for SecOps-Eng M4: STIG Auto-Remediation Capstone
from datetime import date, timedelta

# ── Test: prioritize_findings ─────────────────────────────────────────────────

open_findings = prioritize_findings(STIG_FINDINGS, SCAN_EVIDENCE)
assert open_findings is not None, "prioritize_findings() returned None"
assert isinstance(open_findings, list), "prioritize_findings() must return list"
assert len(open_findings) == 2, f"Should return 2 open findings (V-220706 + V-220739), got {len(open_findings)}"

rule_ids = [f["rule_id"] for f in open_findings]
assert "V-220706" in rule_ids, "V-220706 (fail) should be in open findings"
assert "V-220739" in rule_ids, "V-220739 (fail) should be in open findings"
assert "V-257777" not in rule_ids, "V-257777 (pass) must NOT be in open findings"

# Sort order: CAT I first, CAT II second, CAT III third
cats = [f["cat"] for f in open_findings]
# V-220706 is CAT II, V-220739 is CAT III — so CAT II comes first
assert cats[0] == "CAT II", f"First finding should be CAT II (V-220706), got {cats[0]}"
assert cats[1] == "CAT III", f"Second finding should be CAT III (V-220739), got {cats[1]}"

# Evidence attached
for f in open_findings:
    assert "system" in f, f"Finding must include 'system', got keys: {list(f.keys())}"
    assert "scan_date" in f, f"Finding must include 'scan_date', got keys: {list(f.keys())}"
    assert f["system"] == "ICDEV-Dev", f"System should be ICDEV-Dev, got {f['system']}"

# ── Test: prioritize_findings — no open findings ──────────────────────────────

all_pass = {"V-220706": {"status": "pass", "system": "X", "date": "2026-04-30"}}
result_empty = prioritize_findings([STIG_FINDINGS[0]], all_pass)
assert result_empty == [], f"All-pass evidence -> empty list, got {result_empty}"

# ── Test: plan_remediation ────────────────────────────────────────────────────

finding_cat2 = {**open_findings[0]}  # V-220706 CAT II
plan = plan_remediation(finding_cat2)

assert plan is not None, "plan_remediation() returned None"
assert isinstance(plan, dict), "plan_remediation() must return dict"

for key in ["rule_id", "cat", "system", "title", "action", "due_date"]:
    assert key in plan, f"Plan missing '{key}'"

assert plan["rule_id"] == "V-220706", f"rule_id mismatch: {plan['rule_id']}"
assert plan["cat"] == "CAT II", f"cat mismatch: {plan['cat']}"
assert "HIGH PRIORITY" in plan["action"], f"CAT II action should say HIGH PRIORITY: {plan['action']}"
assert "Duo MFA" in plan["action"], f"action should include fix_text: {plan['action']}"

# Due date: today + 180 days for CAT II
expected_due = str(date.today() + timedelta(days=180))
assert plan["due_date"] == expected_due, f"CAT II due_date should be {expected_due}, got {plan['due_date']}"

# CAT III plan
finding_cat3 = {**open_findings[1]}  # V-220739 CAT III
plan3 = plan_remediation(finding_cat3)
assert "SCHEDULED" in plan3["action"], f"CAT III action should say SCHEDULED: {plan3['action']}"
expected_due3 = str(date.today() + timedelta(days=365))
assert plan3["due_date"] == expected_due3, f"CAT III due_date should be {expected_due3}, got {plan3['due_date']}"

# ── Test: RemediationAgent.run ────────────────────────────────────────────────

agent = RemediationAgent()
result = agent.run(STIG_FINDINGS, SCAN_EVIDENCE)

assert result is not None, "run() returned None"
assert isinstance(result, dict), "run() must return dict"

for key in ["run_date", "total_findings", "open_findings", "closed_findings", "plans", "summary"]:
    assert key in result, f"Result missing '{key}'"

assert result["run_date"] == str(date.today()), "run_date should be today"
assert result["total_findings"] == 3, f"total_findings should be 3, got {result['total_findings']}"
assert result["open_findings"] == 2, f"open_findings should be 2, got {result['open_findings']}"
assert result["closed_findings"] == 1, f"closed_findings should be 1, got {result['closed_findings']}"

assert isinstance(result["plans"], list), "plans must be list"
assert len(result["plans"]) == 2, f"Should have 2 plans, got {len(result['plans'])}"

summary = result["summary"]
assert summary["cat1"] == 0, f"cat1 should be 0 (V-257777 is closed), got {summary['cat1']}"
assert summary["cat2"] == 1, f"cat2 should be 1 (V-220706), got {summary['cat2']}"
assert summary["cat3"] == 1, f"cat3 should be 1 (V-220739), got {summary['cat3']}"
assert summary["immediate_actions"] == 0, f"immediate_actions should be 0 (no open CAT I), got {summary['immediate_actions']}"

# ── Test: RemediationAgent.run — all open ────────────────────────────────────

all_fail = {
    "V-220706": {"status": "fail", "system": "X", "date": "2026-04-30"},
    "V-220739": {"status": "fail", "system": "X", "date": "2026-04-30"},
    "V-257777": {"status": "fail", "system": "X", "date": "2026-04-30"},
}
result_all = agent.run(STIG_FINDINGS, all_fail)
assert result_all["open_findings"] == 3, "All-fail -> 3 open findings"
assert result_all["closed_findings"] == 0, "All-fail -> 0 closed"
assert result_all["summary"]["cat1"] == 1, "All-fail -> 1 CAT I (V-257777)"
assert result_all["summary"]["immediate_actions"] == 1, "All-fail -> 1 immediate action"

print("PASS: RemediationAgent complete. prioritize_findings + plan_remediation + run() all verified.")
