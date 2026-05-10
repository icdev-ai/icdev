from __future__ import annotations
"""NetOps M04 — NetOps Capstone.
Goal: Wire topology → anomaly → remediation into a unified NOC pipeline.
"""

# ── Shared config (same as M01-M03) ──────────────────────────────────────────

THRESHOLDS = {
    "latency_ms":       {"warning": 100,  "critical": 500},
    "packet_loss_pct":  {"warning": 1.0,  "critical": 5.0},
    "port_util_pct":    {"warning": 75,   "critical": 95},
    "bgp_prefix_delta": {"warning": 500,  "critical": 2000},
}

PLAYBOOKS = {
    "latency_ms":      {"playbook_name": "Latency Remediation",       "steps": [
        {"action": "flush_arp",     "target": "device",    "description": "Flush ARP cache"},
        {"action": "restart_iface", "target": "interface", "description": "Bounce interface"},
        {"action": "open_tac_case", "target": "tac",       "description": "Open TAC case", "critical_only": True},
    ]},
    "port_util_pct":   {"playbook_name": "Port Utilization Remediation", "steps": [
        {"action": "top_talkers", "target": "device",    "description": "Identify top talkers"},
        {"action": "apply_qos",   "target": "interface", "description": "Apply QoS policy"},
    ]},
    "default":         {"playbook_name": "General Escalation",        "steps": [
        {"action": "collect_diag", "target": "device", "description": "Collect diagnostics"},
        {"action": "escalate_l2",  "target": "team",   "description": "Escalate to L2"},
    ]},
}

SAMPLE_CONFIGS = [
    "hostname core-sw-01\n!\ninterface GigabitEthernet0/1\n ip address 10.0.1.1 255.255.255.0\n!\ncdp neighbor\n neighbor: edge-rtr-01 port: Gi0/2\n neighbor: access-sw-01 port: Gi0/1",
    "hostname access-sw-01\n!\ninterface GigabitEthernet0/0\n ip address 10.0.1.2 255.255.255.0\n!\ncdp neighbor\n neighbor: core-sw-01 port: Gi0/0",
    "hostname dmz-fw-01\n!\ninterface Ethernet1\n ip address 192.168.0.1 255.255.255.128\n!\ncdp neighbor\n neighbor: edge-rtr-01 port: Eth1",
]

SAMPLE_METRICS = {
    "core-sw-01": {
        "latency_ms": 620,
        "packet_loss_pct": 0.3,
        "port_util_pct": 88,
        "bgp_prefix_delta": 150,
    },
    "access-sw-01": {
        "latency_ms": 45,
        "packet_loss_pct": 0.1,
        "port_util_pct": 40,
        "bgp_prefix_delta": 10,
    },
}


# ── You must implement these helper functions ─────────────────────────────────

def parse_and_build_topology(raw_configs: list) -> dict:
    """Parse all raw device configs and build a topology summary.

    Re-implement your M01 logic: parse hostnames + neighbors from each config,
    build nodes/edges (dedup bidirectional), identify unknown nodes.

    Return format:
        {"node_count": int, "edge_count": int, "unknown_nodes": [str, ...]}
    """
    # YOUR CODE HERE
    pass


def detect_anomalies_for_device(device_name: str, metrics: dict) -> dict | None:
    """Run anomaly detection for a single device's metrics dict.

    Re-implement your M02 logic: classify each metric against THRESHOLDS,
    score severity, collect non-normal results.

    Return an anomaly summary dict if any anomalies found, else None:
        {"device": str, "anomaly_count": int, "highest_severity": str,
         "anomalies": [{"metric": str, "severity": str, ...}, ...]}
    """
    # YOUR CODE HERE
    pass


def build_remediation_plan(anomaly_summary: dict) -> dict:
    """Generate a remediation plan from an anomaly summary.

    Re-implement your M03 logic: for each anomaly, select playbook,
    validate steps, return execution plan.

    Return format:
        {"device": str, "plan_count": int, "ready_to_execute": bool}
    """
    # YOUR CODE HERE
    pass


# ── Main pipeline ─────────────────────────────────────────────────────────────

class NOCPipeline:
    def run(self, raw_configs: list, metrics_by_device: dict) -> dict:
        """End-to-end NOC pipeline: topology → anomaly detection → remediation.

        1. Call parse_and_build_topology(raw_configs) → topology summary
        2. For each device in metrics_by_device, call detect_anomalies_for_device
        3. For devices with anomalies, call build_remediation_plan
        4. Return unified NOC report

        Return format:
            {
                "topology": {"node_count": int, "edge_count": int, "unknown_nodes": list},
                "devices_checked": int,
                "devices_with_anomalies": int,
                "remediation_plans": [{"device": str, "plan_count": int, "ready_to_execute": bool}, ...],
                "overall_status": "CLEAN" | "ACTION_REQUIRED"
            }
        """
        # YOUR CODE HERE
        pass


if __name__ == "__main__":
    pipeline = NOCPipeline()
    report = pipeline.run(SAMPLE_CONFIGS, SAMPLE_METRICS)
    print(f"Nodes: {report['topology']['node_count']}")
    print(f"Devices checked: {report['devices_checked']}")
    print(f"With anomalies: {report['devices_with_anomalies']}")
    print(f"Status: {report['overall_status']}")
    for rp in report["remediation_plans"]:
        print(f"  {rp['device']}: {rp['plan_count']} plans, ready={rp['ready_to_execute']}")

