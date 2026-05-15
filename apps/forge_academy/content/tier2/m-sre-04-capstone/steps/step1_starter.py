
"""SRE M04 — SRE Capstone.
Goal: Wire SLO monitoring → incident response → chaos into a unified reliability pipeline.
"""

# ── Shared constants (from M01-M03) ──────────────────────────────────────────

RUNBOOKS = {
    ("auth-service", "latency"):   {"runbook_name": "Auth Latency Runbook",    "steps": ["Check DB pool", "Review slow queries"], "escalation_path": ["oncall-sre"]},
    ("auth-service", "error_rate"):{"runbook_name": "Auth Error Rate Runbook", "steps": ["Check upstream token svc", "Review 5xx breakdown"], "escalation_path": ["oncall-sre"]},
    ("payment-service", "latency"):{"runbook_name": "Payment Latency Runbook", "steps": ["Check Stripe status", "Review gateway timeouts"], "escalation_path": ["oncall-sre", "finance-oncall"]},
    "default":                     {"runbook_name": "General Runbook",         "steps": ["Check logs/metrics", "Identify blast radius"], "escalation_path": ["oncall-sre"]},
}

FAILURE_MODES = {
    "network_delay":   {"description": "Inject 300ms latency", "duration_seconds": 60, "rollback": "Remove tc netem rule", "hypothesis": "Service maintains SLO under 300ms latency injection", "risk": "medium"},
    "pod_failure":     {"description": "Kill 50% pods",        "duration_seconds": 120,"rollback": "Rollout restart",      "hypothesis": "Service survives 50% pod loss", "risk": "high"},
    "cpu_stress":      {"description": "100% CPU for 60s",     "duration_seconds": 60, "rollback": "Kill stress process",  "hypothesis": "Service stays responsive under CPU saturation", "risk": "medium"},
    "disk_full":       {"description": "Fill disk to 95%",     "duration_seconds": 30, "rollback": "Delete dummy files",   "hypothesis": "Service operates with degraded write perf", "risk": "low"},
}

SERVICE_DEPENDENCIES = {
    "auth-service":    ["session-store", "user-db"],
    "payment-service": ["stripe-gateway", "fraud-service"],
    "cache-service":   ["redis-primary"],
}

SAMPLE_SERVICES = [
    {
        "service": "auth-service",
        "slo_target_pct": 99.9,
        "window_hours": 1,
        "metrics": {
            "total_requests": 50000,
            "error_requests": 120,
            "latency_p99_ms": 5200,
            "error_rate_pct": 2.5,
            "user_facing": True,
        },
    },
    {
        "service": "cache-service",
        "slo_target_pct": 99.5,
        "window_hours": 24,
        "metrics": {
            "total_requests": 500000,
            "error_requests": 100,
            "latency_p99_ms": 30,
            "error_rate_pct": 0.02,
            "user_facing": False,
        },
    },
]


# ── You must implement these helpers ─────────────────────────────────────────

def compute_slo_health(slo_target_pct: float, window_hours: int,
                       total_requests: int, error_requests: int) -> dict:
    """Re-implement M01 SLO logic: compute error budget and burn rate.

    Return format:
        {
            "recommendation": "NO_ACTION" | "MONITOR" | "PAGE_ONCALL",
            "burn_rate_status": "healthy" | "elevated" | "fast_burn" | "critical",
            "budget_consumed_pct": float,
            "burn_rate": float
        }
    """
    # YOUR CODE HERE
    pass


def build_incident_brief(service: str, metrics: dict) -> dict:
    """Re-implement M02 incident classification for a service.

    Synthesize an alert from metrics:
      - alert_type = "error_rate" if error_rate_pct > 1.0, else "latency"
    Classify severity (SEV1–SEV4) using M02 rules.
    Select runbook from RUNBOOKS.

    Return format:
        {
            "incident_id": str,
            "severity": str,
            "page_oncall": bool,
            "runbook_name": str
        }
    """
    # YOUR CODE HERE
    pass


def schedule_chaos(service: str) -> dict:
    """Design a network_delay chaos experiment for a healthy service.

    Use FAILURE_MODES["network_delay"] and SERVICE_DEPENDENCIES.
    Return format:
        {
            "experiment_id": str,
            "risk_level": str,
            "safe_to_run": bool
        }
    """
    # YOUR CODE HERE
    pass


# ── Main pipeline ─────────────────────────────────────────────────────────────

class SREPipeline:
    def run(self, services: list) -> dict:
        """Unified reliability pipeline across all services.

        For each service:
          1. compute_slo_health → get recommendation
          2. If PAGE_ONCALL → build_incident_brief → add to results with action="paged"
          3. If MONITOR or NO_ACTION → schedule_chaos → add to results with action="chaos_scheduled"

        overall_status:
          "CRITICAL" if any service → PAGE_ONCALL
          "DEGRADED"  if any service → MONITOR (and none → PAGE_ONCALL)
          "HEALTHY"   if all → NO_ACTION

        Return format:
            {
                "services_evaluated": int,
                "paged": int,
                "monitored_with_chaos": int,
                "results": [
                    {
                        "service": str,
                        "slo_health": {"recommendation": str, "burn_rate_status": str},
                        "action": "paged" | "chaos_scheduled",
                        "incident": dict,   # present only when action="paged"
                        "chaos": dict       # present only when action="chaos_scheduled"
                    },
                    ...
                ],
                "overall_status": "HEALTHY" | "DEGRADED" | "CRITICAL"
            }
        """
        # YOUR CODE HERE
        pass


if __name__ == "__main__":
    pipeline = SREPipeline()
    report = pipeline.run(SAMPLE_SERVICES)
    print(f"Services evaluated: {report['services_evaluated']}")
    print(f"Paged: {report['paged']}")
    print(f"Chaos scheduled: {report['monitored_with_chaos']}")
    print(f"Overall status: {report['overall_status']}")
    for svc in report["results"]:
        print(f"  {svc['service']}: {svc['action']} ({svc['slo_health']['burn_rate_status']})")
