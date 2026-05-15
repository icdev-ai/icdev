
"""SRE M01 — SLO Monitoring Agent.
Goal: Compute error budget consumption and burn rate, recommend SRE action.
"""

SAMPLE_SLO_CONFIG = {
    "service": "auth-service",
    "slo_target_pct": 99.9,
    "window_hours": 1,
}

SAMPLE_METRICS = {
    "total_requests": 50000,
    "error_requests": 120,
}


def calculate_error_budget(slo_target_pct: float, window_hours: int,
                           total_requests: int, error_requests: int) -> dict:
    """Compute error budget consumption for the given window.

    budget_total_pct  = 100.0 - slo_target_pct
    actual_error_pct  = (error_requests / total_requests) * 100
    budget_consumed_pct = (actual_error_pct / budget_total_pct) * 100

    Round actual_error_pct and budget_consumed_pct to 4 decimal places.

    Return format:
        {
            "slo_target_pct": float,
            "budget_total_pct": float,
            "actual_error_pct": float,
            "budget_consumed_pct": float,
            "window_hours": int
        }
    """
    # YOUR CODE HERE
    pass


def assess_burn_rate(budget_consumed_pct: float, window_hours: int) -> dict:
    """Compute burn rate and classify it.

    burn_rate = (budget_consumed_pct / 100) / (window_hours / 720)

    Status thresholds:
      burn_rate < 1         → "healthy",   alert=False
      1 <= burn_rate < 2    → "elevated",  alert=False
      2 <= burn_rate < 5    → "fast_burn", alert=True
      burn_rate >= 5        → "critical",  alert=True

    Round burn_rate to 2 decimal places.

    Return format:
        {"burn_rate": float, "status": str, "alert": bool}
    """
    # YOUR CODE HERE
    pass


class SLOAgent:
    def run(self, slo_config: dict, metrics: dict) -> dict:
        """Compute SLO health and return a recommendation.

        Extract slo_target_pct and window_hours from slo_config.
        Extract total_requests and error_requests from metrics.
        Call calculate_error_budget → call assess_burn_rate → pick recommendation.

        Recommendation logic:
          "healthy"               → "NO_ACTION"
          "elevated"              → "MONITOR"
          "fast_burn" | "critical"→ "PAGE_ONCALL"

        Return format:
            {
                "service": str,
                "slo_target_pct": float,
                "budget": dict,
                "burn_rate": dict,
                "recommendation": str
            }
        """
        # YOUR CODE HERE
        pass


if __name__ == "__main__":
    agent = SLOAgent()
    result = agent.run(SAMPLE_SLO_CONFIG, SAMPLE_METRICS)
    print(f"Service: {result['service']}")
    print(f"Budget consumed: {result['budget']['budget_consumed_pct']}%")
    print(f"Burn rate: {result['burn_rate']['burn_rate']}x ({result['burn_rate']['status']})")
    print(f"Recommendation: {result['recommendation']}")
