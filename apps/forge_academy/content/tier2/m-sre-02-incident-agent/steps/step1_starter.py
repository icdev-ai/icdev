
"""SRE M02 — Incident Response Agent.
Goal: Classify incoming alerts by severity, select runbook, return incident brief.
"""

RUNBOOKS = {
    ("auth-service", "latency"): {
        "runbook_name": "Auth Service Latency Runbook",
        "steps": [
            "Check DB connection pool saturation",
            "Review slow query log for long-running queries",
            "Scale read replicas if DB is the bottleneck",
        ],
        "escalation_path": ["oncall-sre", "auth-team-lead"],
    },
    ("auth-service", "error_rate"): {
        "runbook_name": "Auth Service Error Rate Runbook",
        "steps": [
            "Check upstream token service health",
            "Review 5xx error breakdown by endpoint",
            "Roll back last deployment if error spike correlates",
        ],
        "escalation_path": ["oncall-sre", "auth-team-lead"],
    },
    ("payment-service", "latency"): {
        "runbook_name": "Payment Service Latency Runbook",
        "steps": [
            "Check Stripe API status page",
            "Review payment gateway timeout settings",
            "Enable fallback payment processor if Stripe is degraded",
        ],
        "escalation_path": ["oncall-sre", "payment-team-lead", "finance-oncall"],
    },
    ("payment-service", "error_rate"): {
        "runbook_name": "Payment Service Error Rate Runbook",
        "steps": [
            "Check fraud detection service circuit breaker state",
            "Review declined transaction error codes",
            "Page payment team lead — financial impact",
        ],
        "escalation_path": ["oncall-sre", "payment-team-lead", "finance-oncall"],
    },
    "default": {
        "runbook_name": "General Incident Runbook",
        "steps": [
            "Check service logs and metrics dashboard",
            "Identify blast radius — which users/regions affected",
            "Escalate to service owner if not resolved in 15 minutes",
        ],
        "escalation_path": ["oncall-sre"],
    },
}

SAMPLE_ALERT = {
    "service": "auth-service",
    "alert_type": "latency",
    "user_facing": True,
    "error_rate_pct": 2.5,
    "latency_p99_ms": 5200,
    "region": "us-east-1",
}


def classify_incident(alert: dict) -> dict:
    """Classify an alert dict and assign SEV1–SEV4 severity.

    Check conditions in order (first match wins):
      SEV1: user_facing=True AND error_rate_pct > 10
      SEV2: user_facing=True AND (1 < error_rate_pct <= 10 OR latency_p99_ms > 2000)
      SEV3: user_facing=False OR latency_p99_ms in [500, 2000]
      SEV4: everything else

    page_oncall = True for SEV1 and SEV2 only.
    impact should be a short human-readable description of why this severity was assigned.

    Return format:
        {
            "severity": "SEV1" | "SEV2" | "SEV3" | "SEV4",
            "user_facing": bool,
            "impact": str,
            "page_oncall": bool
        }
    """
    # YOUR CODE HERE
    pass


def select_runbook(service: str, incident_type: str) -> dict:
    """Select the runbook for the given service and incident type.

    Try the (service, incident_type) tuple key in RUNBOOKS first.
    Fall back to the "default" key if not found.
    Include service and incident_type in the returned dict.

    Return format:
        {
            "service": str,
            "incident_type": str,
            "runbook_name": str,
            "steps": [str, ...],
            "escalation_path": [str, ...]
        }
    """
    # YOUR CODE HERE
    pass


class IncidentAgent:
    def run(self, alert: dict) -> dict:
        """Classify alert, select runbook, return incident brief.

        incident_id format: "INC-{service}-{alert_type}"
        summary: single string mentioning severity, service, region, and page status.

        Return format:
            {
                "incident_id": str,
                "service": str,
                "classification": dict,
                "runbook": dict,
                "summary": str
            }
        """
        # YOUR CODE HERE
        pass


if __name__ == "__main__":
    agent = IncidentAgent()
    brief = agent.run(SAMPLE_ALERT)
    print(f"ID: {brief['incident_id']}")
    print(f"Severity: {brief['classification']['severity']}")
    print(f"Page oncall: {brief['classification']['page_oncall']}")
    print(f"Runbook: {brief['runbook']['runbook_name']}")
    print(f"Summary: {brief['summary']}")
