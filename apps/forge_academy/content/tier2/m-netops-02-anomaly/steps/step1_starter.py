"""NetOps M02 — Anomaly Detection Agent.
Goal: Classify network metrics against thresholds, score severity, rank anomalies.
"""

THRESHOLDS = {
    "latency_ms":        {"warning": 100,  "critical": 500},
    "packet_loss_pct":   {"warning": 1.0,  "critical": 5.0},
    "port_util_pct":     {"warning": 75,   "critical": 95},
    "bgp_prefix_delta":  {"warning": 500,  "critical": 2000},
}

SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}

SAMPLE_SNAPSHOT = {
    "device": "core-sw-01",
    "metrics": {
        "latency_ms":       620,
        "packet_loss_pct":  0.3,
        "port_util_pct":    88,
        "bgp_prefix_delta": 150,
    }
}


def classify_anomaly(metric_name: str, value: float, threshold_dict: dict):
    """Classify a single metric value against its thresholds.

    Look up metric_name in threshold_dict. If not found, return None.
    Compare value to warning and critical thresholds:
      - value > critical threshold → state = "critical"
      - value > warning threshold  → state = "warning"
      - otherwise                  → return None (normal, no anomaly)

    Return format:
        {
            "metric": metric_name,
            "value": value,
            "warning_threshold": float,
            "critical_threshold": float,
            "state": "warning" | "critical"
        }
    """
    # YOUR CODE HERE
    pass


def score_severity(anomalies: list) -> list:
    """Add a 'severity' field to each anomaly and sort descending by severity.

    Severity rules:
      - state == "critical"                                     → "critical"
      - state == "warning" and value > 0.9 * critical_threshold → "high"
      - state == "warning" and value > 0.5 * critical_threshold → "medium"
      - otherwise                                               → "low"

    Return the same list with 'severity' added, sorted critical→high→medium→low.
    """
    # YOUR CODE HERE
    pass


class AnomalyAgent:
    def run(self, metrics_snapshot: dict) -> dict:
        """Classify all metrics in snapshot, score and rank anomalies.

        Iterate snapshot["metrics"] items, call classify_anomaly for each.
        Collect non-None results, call score_severity, return report.

        Return format:
            {
                "device": str,
                "anomaly_count": int,
                "anomalies": [list from score_severity],
                "highest_severity": str  # severity of first anomaly, or "none"
            }
        """
        # YOUR CODE HERE
        pass


if __name__ == "__main__":
    agent = AnomalyAgent()
    report = agent.run(SAMPLE_SNAPSHOT)
    print(f"Device: {report['device']}")
    print(f"Anomalies: {report['anomaly_count']}")
    print(f"Highest: {report['highest_severity']}")
    for a in report["anomalies"]:
        print(f"  {a['metric']}: {a['value']} [{a['severity']}]")
