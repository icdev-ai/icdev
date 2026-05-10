# Auto-grader for SRE M01 Step 1: SLO Monitoring Agent

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    agent = SLOAgent()
    result = agent.run(SAMPLE_SLO_CONFIG, SAMPLE_METRICS)
    print(f"Service: {result['service']}")
    print(f"Budget consumed: {result['budget']['budget_consumed_pct']}%")
    print(f"Burn rate: {result['burn_rate']['burn_rate']}x")
    print(f"Recommendation: {result['recommendation']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# calculate_error_budget tests
budget = calculate_error_budget(99.9, 1, 50000, 120)
assert budget is not None, "calculate_error_budget() returned None"
assert budget.get("slo_target_pct") == 99.9, "slo_target_pct must be 99.9"
assert budget.get("budget_total_pct") == 0.1, f"budget_total_pct=0.1 for 99.9% SLO, got {budget.get('budget_total_pct')}"
assert abs(budget.get("actual_error_pct", 0) - 0.24) < 0.001, \
    f"actual_error_pct=120/50000*100=0.24, got {budget.get('actual_error_pct')}"
assert abs(budget.get("budget_consumed_pct", 0) - 240.0) < 0.1, \
    f"budget_consumed_pct=240.0, got {budget.get('budget_consumed_pct')}"
assert budget.get("window_hours") == 1, "window_hours must be 1"

# Healthy scenario: 50k requests, 30 errors -> error_pct=0.06, consumed=60%
healthy_budget = calculate_error_budget(99.9, 24, 1000000, 600)
assert healthy_budget.get("budget_consumed_pct", 100) < 100, \
    "1000k requests, 600 errors over 24h should not exhaust budget"

# assess_burn_rate tests
burn = assess_burn_rate(240.0, 1)
assert burn is not None, "assess_burn_rate() returned None"
assert "burn_rate" in burn, "Must have burn_rate field"
assert "status" in burn, "Must have status field"
assert "alert" in burn, "Must have alert field"
# burn_rate = (240/100) / (1/720) = 2.4 * 720 = 1728 -> critical
assert burn.get("status") == "critical", f"240% consumed in 1h -> critical, got {burn.get('status')}"
assert burn.get("alert") is True, "critical burn rate must alert"

burn_healthy = assess_burn_rate(5.0, 24)
# burn = (0.05) / (24/720) = 0.05 / 0.0333 = 1.5 -> elevated
assert burn_healthy.get("status") in ("healthy", "elevated"), \
    f"5% consumed in 24h should be healthy or elevated, got {burn_healthy.get('status')}"

burn_nominal = assess_burn_rate(3.33, 720)
# burn = (0.0333) / (720/720) = 0.0333 -> healthy
assert burn_nominal.get("status") == "healthy", \
    f"3.33% consumed over full 720h window -> healthy (burn_rate≈0.033), got {burn_nominal.get('status')}"
assert burn_nominal.get("alert") is False, "healthy burn rate must not alert"

# SLOAgent.run tests
assert result is not None, "SLOAgent.run() returned None"
assert "service" in result, "Result must include service"
assert "slo_target_pct" in result, "Result must include slo_target_pct"
assert "budget" in result, "Result must include budget"
assert "burn_rate" in result, "Result must include burn_rate"
assert "recommendation" in result, "Result must include recommendation"

assert result["service"] == "auth-service", f"Expected service 'auth-service', got {result['service']}"
assert result["slo_target_pct"] == 99.9, "slo_target_pct must be 99.9"
assert result["recommendation"] == "PAGE_ONCALL", \
    f"Critical burn rate -> PAGE_ONCALL, got {result['recommendation']}"
assert result["burn_rate"]["alert"] is True, "Alert must be True for critical burn"

assert len(output) > 10, "Print result fields in __main__ block"

print("PASS: SLO Monitoring Agent complete. calculate_error_budget + assess_burn_rate + SLOAgent.run all verified.")
