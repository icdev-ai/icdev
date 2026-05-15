
"""SRE M03 — Chaos Engineering Agent.
Goal: Design chaos experiments, estimate blast radius, verify steady-state.
"""

FAILURE_MODES = {
    "pod_failure": {
        "description": "Kill 50% of running pods",
        "duration_seconds": 120,
        "rollback": "Restart terminated pods via deployment rollout",
        "hypothesis": "Service maintains > 99% success rate with 50% pod failure",
        "risk": "high",
    },
    "network_delay": {
        "description": "Inject 300ms latency on all outbound calls",
        "duration_seconds": 60,
        "rollback": "Remove tc netem rule from service network namespace",
        "hypothesis": "Service maintains < 2000ms P99 latency and > 99% success rate",
        "risk": "medium",
    },
    "disk_full": {
        "description": "Fill disk to 95% capacity",
        "duration_seconds": 30,
        "rollback": "Delete injected dummy files to restore disk space",
        "hypothesis": "Service continues operating with degraded write performance",
        "risk": "low",
    },
    "cpu_stress": {
        "description": "Peg CPU at 100% for 60 seconds",
        "duration_seconds": 60,
        "rollback": "Kill CPU stress process",
        "hypothesis": "Service remains responsive under full CPU saturation",
        "risk": "medium",
    },
    "dependency_down": {
        "description": "Take a downstream service offline",
        "duration_seconds": 90,
        "rollback": "Restore downstream service connectivity via network policy",
        "hypothesis": "Service degrades gracefully with circuit breaker activation",
        "risk": "high",
    },
}

SERVICE_DEPENDENCIES = {
    "auth-service":    ["session-store", "user-db"],
    "payment-service": ["stripe-gateway", "fraud-service", "ledger-db"],
    "api-gateway":     ["auth-service", "payment-service", "inventory-service"],
}

SAMPLE_SERVICE = "auth-service"
FAILURE_MODE = "network_delay"

SAMPLE_BASELINE = {
    "error_rate_pct":  0.1,
    "latency_p99_ms":  120,
    "success_rate_pct": 99.9,
}

SAMPLE_POST = {
    "error_rate_pct":  0.3,
    "latency_p99_ms":  175,
    "success_rate_pct": 99.7,
}


def design_experiment(service: str, failure_mode: str) -> dict:
    """Create a chaos experiment spec.

    Look up failure_mode in FAILURE_MODES. If not found, use a generic fallback.
    Build experiment_id as "exp-{service}-{failure_mode}".

    Return format:
        {
            "experiment_id": str,
            "service": str,
            "failure_mode": str,
            "description": str,
            "duration_seconds": int,
            "rollback": str,
            "hypothesis": str
        }
    """
    # YOUR CODE HERE
    pass


def estimate_blast_radius(experiment: dict) -> dict:
    """Estimate the scope of impact for a chaos experiment.

    Use SERVICE_DEPENDENCIES to find affected downstream services.
    Use FAILURE_MODES risk level to set risk_level and safe_to_run:
      "high" risk  → safe_to_run=False
      "medium" risk → safe_to_run=True
      "low" risk   → safe_to_run=True
    affected_users_pct: use 100 for all services (assume user-facing).

    Return format:
        {
            "experiment_id": str,
            "affected_users_pct": int,
            "affected_downstream": [str, ...],
            "risk_level": str,
            "safe_to_run": bool
        }
    """
    # YOUR CODE HERE
    pass


def verify_steady_state(baseline: dict, post: dict) -> list:
    """Check that post-experiment metrics stayed within tolerance.

    Tolerances:
      error_rate_pct:  post - baseline <= 1.0 (percentage points)
      latency_p99_ms:  post / baseline <= 1.5 (50% increase)
      success_rate_pct: baseline - post <= 1.0 (percentage points)

    For each metric present in both dicts, return a result dict.

    Return format (list of dicts):
        [{"metric": str, "baseline": float, "post": float, "passed": bool}, ...]
    """
    # YOUR CODE HERE
    pass


class ChaosAgent:
    def run(self, service: str, failure_mode: str,
            baseline_metrics: dict, post_metrics: dict) -> dict:
        """Run full chaos engineering cycle.

        1. design_experiment(service, failure_mode)
        2. estimate_blast_radius(experiment)
        3. If not safe_to_run → conclusion="SKIPPED", skip steady-state
        4. Otherwise → verify_steady_state(baseline_metrics, post_metrics)
        5. conclusion="PASSED" if all passed, else "FAILED"

        Return format:
            {
                "experiment": dict,
                "blast_radius": dict,
                "steady_state_passed": bool,
                "metric_results": list,
                "conclusion": "PASSED" | "FAILED" | "SKIPPED"
            }
        """
        # YOUR CODE HERE
        pass


if __name__ == "__main__":
    agent = ChaosAgent()
    report = agent.run(SAMPLE_SERVICE, FAILURE_MODE, SAMPLE_BASELINE, SAMPLE_POST)
    print(f"Experiment: {report['experiment']['experiment_id']}")
    print(f"Risk: {report['blast_radius']['risk_level']}")
    print(f"Conclusion: {report['conclusion']}")
    for mr in report["metric_results"]:
        status = "✓" if mr["passed"] else "✗"
        print(f"  {status} {mr['metric']}: {mr['baseline']} → {mr['post']}")
