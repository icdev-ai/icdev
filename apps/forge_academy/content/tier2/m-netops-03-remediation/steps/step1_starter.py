
"""NetOps M03 — Auto-Remediation Agent.
Goal: Map anomaly types to playbooks, validate steps, produce execution plan.
"""

PLAYBOOKS = {
    "latency_ms": {
        "playbook_name": "Latency Remediation",
        "steps": [
            {"action": "flush_arp",     "target": "device",    "description": "Flush ARP cache on affected device"},
            {"action": "restart_iface", "target": "interface", "description": "Bounce interface to reset counters"},
            {"action": "open_tac_case", "target": "tac",       "description": "Open TAC case — critical severity", "critical_only": True},
        ],
    },
    "packet_loss_pct": {
        "playbook_name": "Packet Loss Remediation",
        "steps": [
            {"action": "check_duplex",   "target": "interface", "description": "Check duplex mismatch on affected port"},
            {"action": "bounce_port",    "target": "interface", "description": "Disable then re-enable port"},
            {"action": "capture_traffic","target": "device",    "description": "Start packet capture for 60s"},
        ],
    },
    "port_util_pct": {
        "playbook_name": "Port Utilization Remediation",
        "steps": [
            {"action": "top_talkers",   "target": "device",    "description": "Identify top-5 traffic sources"},
            {"action": "apply_qos",     "target": "interface", "description": "Apply QoS policy to throttle burst"},
            {"action": "consider_lag",  "target": "device",    "description": "Evaluate link aggregation", "critical_only": True},
        ],
    },
    "bgp_prefix_delta": {
        "playbook_name": "BGP Prefix Remediation",
        "steps": [
            {"action": "check_peer",      "target": "bgp",    "description": "Verify BGP peer state"},
            {"action": "apply_filter",    "target": "bgp",    "description": "Apply prefix-list route filter"},
            {"action": "notify_noc",      "target": "team",   "description": "Notify NOC team of BGP change"},
        ],
    },
    "default": {
        "playbook_name": "General Escalation",
        "steps": [
            {"action": "collect_diag",  "target": "device", "description": "Collect show tech-support output"},
            {"action": "escalate_l2",   "target": "team",   "description": "Escalate to L2 network engineering"},
        ],
    },
}

SAMPLE_ANOMALY_REPORT = {
    "device": "core-sw-01",
    "anomaly_count": 2,
    "anomalies": [
        {"metric": "latency_ms",    "value": 620, "state": "critical", "severity": "critical",
         "warning_threshold": 100, "critical_threshold": 500},
        {"metric": "port_util_pct", "value": 88,  "state": "warning",  "severity": "high",
         "warning_threshold": 75, "critical_threshold": 95},
    ],
    "highest_severity": "critical",
}


def select_playbook(anomaly_type: str, severity: str) -> dict:
    """Select the remediation playbook for the given anomaly type.

    Look up anomaly_type in PLAYBOOKS. If not found, use 'default'.
    Build the result dict including playbook_name and steps.
    For 'warning' severity, exclude steps where critical_only is True.
    For 'critical' severity, include all steps.

    Return format:
        {
            "anomaly_type": anomaly_type,
            "severity": severity,
            "playbook_name": str,
            "steps": [{"action": str, "target": str, "description": str}, ...]
        }
    """
    # YOUR CODE HERE
    pass


def validate_playbook(steps: list) -> dict:
    """Validate that every step has the required fields: action, target, description.

    Iterate steps with index. For any step missing a required field, add an error
    string: "Step <i+1> missing '<field>' field".

    Return format:
        {
            "valid": bool,
            "step_count": int,
            "errors": [str, ...]
        }
    """
    # YOUR CODE HERE
    pass


class RemediationAgent:
    def run(self, anomaly_report: dict) -> dict:
        """Generate an execution plan from an anomaly report.

        For each anomaly in anomaly_report["anomalies"]:
          - Call select_playbook(anomaly["metric"], anomaly["severity"])
          - Call validate_playbook(playbook["steps"])
          - Add both to execution_plan list

        ready_to_execute = True only if ALL validations pass.

        Return format:
            {
                "device": str,
                "plan_count": int,
                "execution_plan": [
                    {"anomaly": str, "severity": str, "playbook": dict, "validation": dict},
                    ...
                ],
                "ready_to_execute": bool
            }
        """
        # YOUR CODE HERE
        pass


if __name__ == "__main__":
    agent = RemediationAgent()
    plan = agent.run(SAMPLE_ANOMALY_REPORT)
    print(f"Device: {plan['device']}")
    print(f"Plans: {plan['plan_count']}")
    print(f"Ready: {plan['ready_to_execute']}")
    for entry in plan["execution_plan"]:
        print(f"  {entry['anomaly']} → {entry['playbook']['playbook_name']} ({entry['validation']['step_count']} steps)")
