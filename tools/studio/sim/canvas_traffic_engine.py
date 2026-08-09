# CUI // SP-CTI
"""Canvas Traffic Engine — per-canvas IP plan, ZTP scripts, and traffic matrix registry.

Wraps GNS3TrafficEngine with canvas-specific defaults so any canvas can run live
GNS3 traffic tests with a single call:

    from tools.studio.sim.canvas_traffic_engine import get_canvas_engine
    engine = get_canvas_engine("sdc", "http://localhost:3080")
    result = engine.run(do_ztp=True, do_traffic=True)

Or directly:
    from tools.studio.sim.canvas_traffic_engine import CanvasTrafficEngine
    engine = CanvasTrafficEngine("sdc", project="sdc-lab")
    result = engine.run(do_ztp=True, do_traffic=True)

IP subnet allocation (one /16 per canvas):
    NDC  10.10.x.x   (owned by gns3_traffic_engine.py — not redefined here)
    SDC  10.100.x.x
    BDC  10.101.x.x
    PDC  10.102.x.x
    ODC  10.103.x.x
    IDC  10.104.x.x
    QDC  10.105.x.x
    MDC  10.106.x.x
    AADC 10.107.x.x
    AIMC 10.108.x.x
    OHC  10.109.x.x
    DDC  10.110.x.x
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]

# ── Per-canvas IP plans ────────────────────────────────────────────────────────
# node_name → "ip/24 gateway"  (VPCS nodes only; switches/cloud/nat excluded)

_CANVAS_IP_PLAN: dict[str, dict[str, str]] = {
    "sdc": {
        "fw-edge":      "10.100.0.1/24 10.100.0.254",
        "web-server":   "10.100.1.1/24 10.100.1.254",
        "app-server":   "10.100.2.1/24 10.100.2.254",
        "db-server":    "10.100.3.1/24 10.100.3.254",
        "siem-node":    "10.100.10.1/24 10.100.10.254",
        "threat-actor": "10.100.100.1/24 10.100.100.254",
    },
    "bdc": {
        "boundary-fw":   "10.101.0.1/24 10.101.0.254",
        "pps-proxy":     "10.101.1.1/24 10.101.1.254",
        "isa-connector": "10.101.2.1/24 10.101.2.254",
        "audit-node":    "10.101.3.1/24 10.101.3.254",
    },
    "pdc": {
        "scm":          "10.102.0.1/24 10.102.0.254",
        "ci-runner":    "10.102.1.1/24 10.102.1.254",
        "sast-scanner": "10.102.2.1/24 10.102.2.254",
        "artifact-reg": "10.102.3.1/24 10.102.3.254",
        "deploy-agent": "10.102.4.1/24 10.102.4.254",
    },
    "odc": {
        "otel-collector": "10.103.0.1/24 10.103.0.254",
        "prometheus":     "10.103.1.1/24 10.103.1.254",
        "loki":           "10.103.2.1/24 10.103.2.254",
        "tempo":          "10.103.3.1/24 10.103.3.254",
        "grafana":        "10.103.4.1/24 10.103.4.254",
        "alertmanager":   "10.103.5.1/24 10.103.5.254",
    },
    "idc": {
        "compute-node": "10.104.0.1/24 10.104.0.254",
        "lb-node":      "10.104.1.1/24 10.104.1.254",
    },
    "qdc": {
        "test-runner-1": "10.105.0.1/24 10.105.0.254",
        "test-runner-2": "10.105.1.1/24 10.105.1.254",
        "e2e-runner":    "10.105.2.1/24 10.105.2.254",
        "coverage-srv":  "10.105.3.1/24 10.105.3.254",
        "quality-gate":  "10.105.4.1/24 10.105.4.254",
        "dora-metrics":  "10.105.5.1/24 10.105.5.254",
    },
    "mdc": {
        "src-db":     "10.106.0.1/24 10.106.0.254",
        "src-app":    "10.106.1.1/24 10.106.1.254",
        "dms-engine": "10.106.2.1/24 10.106.2.254",
        "vpn-tunnel": "10.106.3.1/24 10.106.3.254",
        "tgt-db":     "10.106.10.1/24 10.106.10.254",
        "tgt-app":    "10.106.11.1/24 10.106.11.254",
    },
    "aadc": {
        "orchestrator":   "10.107.0.1/24 10.107.0.254",
        "llm-gateway":    "10.107.1.1/24 10.107.1.254",
        "agent-planner":  "10.107.2.1/24 10.107.2.254",
        "agent-coder":    "10.107.3.1/24 10.107.3.254",
        "agent-tester":   "10.107.4.1/24 10.107.4.254",
        "agent-reviewer": "10.107.5.1/24 10.107.5.254",
        "memory-store":   "10.107.6.1/24 10.107.6.254",
    },
    "aimc": {
        "feature-store":  "10.108.0.1/24 10.108.0.254",
        "training-node":  "10.108.1.1/24 10.108.1.254",
        "eval-harness":   "10.108.2.1/24 10.108.2.254",
        "model-registry": "10.108.3.1/24 10.108.3.254",
        "inference-srv":  "10.108.4.1/24 10.108.4.254",
        "drift-monitor":  "10.108.5.1/24 10.108.5.254",
    },
    "ohc": {
        "incident-mgr":  "10.109.0.1/24 10.109.0.254",
        "runbook-exec":  "10.109.1.1/24 10.109.1.254",
        "cmdb":          "10.109.2.1/24 10.109.2.254",
        "sla-monitor":   "10.109.3.1/24 10.109.3.254",
        "on-call-node":  "10.109.4.1/24 10.109.4.254",
        "postmortem-db": "10.109.5.1/24 10.109.5.254",
    },
    "ddc": {
        "mgmt":       "10.110.0.1/24 10.110.0.254",
        "db-primary": "10.110.1.1/24 10.110.1.254",
        "db-replica": "10.110.2.1/24 10.110.2.254",
    },
}

# ── Per-canvas traffic matrices ────────────────────────────────────────────────
# (src_node_name, target_ip, label, scenario_tag)
# Only VPCS src nodes; target_ip from the IP plan above.

_CANVAS_TRAFFIC_MATRIX: dict[str, list[tuple[str, str, str, str]]] = {
    "sdc": [
        # s1: perimeter probe — threat actor hitting the firewall edge
        ("threat-actor", "10.100.0.1",   "Perimeter probe",       "s1"),
        # s2: DMZ traversal — FW edge to web tier
        ("fw-edge",      "10.100.1.1",   "DMZ ingress",           "s2"),
        # s3: tier traversal — web→app→db
        ("web-server",   "10.100.2.1",   "Web→App tier",          "s3"),
        ("app-server",   "10.100.3.1",   "App→DB tier",           "s3"),
        # s4: SIEM visibility — SIEM monitoring FW edge
        ("siem-node",    "10.100.0.1",   "SIEM visibility",       "s4"),
        ("siem-node",    "10.100.1.1",   "SIEM web-tier view",    "s4"),
    ],
    "bdc": [
        # s1: boundary enforcement — FW to ISA connector
        ("boundary-fw",   "10.101.2.1", "FW→ISA gateway",        "s1"),
        # s2: cross-enclave PPS path
        ("isa-connector", "10.101.1.1", "ISA→PPS proxy",         "s2"),
        ("pps-proxy",     "10.101.0.1", "PPS→FW return path",    "s2"),
        # s3: audit trail
        ("boundary-fw",   "10.101.3.1", "FW→Audit node",         "s3"),
        ("isa-connector", "10.101.3.1", "ISA→Audit node",        "s3"),
    ],
    "pdc": [
        # s1: SCM → CI trigger path
        ("scm",          "10.102.1.1", "SCM→CI trigger",         "s1"),
        # s2: CI pipeline stages
        ("ci-runner",    "10.102.2.1", "CI→SAST scan",           "s2"),
        ("ci-runner",    "10.102.3.1", "CI→Artifact push",       "s2"),
        # s3: deploy path
        ("deploy-agent", "10.102.3.1", "Deploy→Registry pull",  "s3"),
        ("deploy-agent", "10.102.0.1", "Deploy→SCM tag",         "s3"),
    ],
    "odc": [
        # s1: OTEL ingestion paths
        ("otel-collector", "10.103.1.1", "OTEL→Prometheus",      "s1"),
        ("otel-collector", "10.103.2.1", "OTEL→Loki",            "s1"),
        ("otel-collector", "10.103.3.1", "OTEL→Tempo",           "s1"),
        # s2: alerting chain
        ("prometheus",     "10.103.5.1", "Prometheus→Alertmgr",  "s2"),
        # s3: dashboard query path
        ("grafana",        "10.103.1.1", "Grafana→Prometheus",   "s3"),
        ("grafana",        "10.103.2.1", "Grafana→Loki",         "s3"),
    ],
    "idc": [
        # s1: load balancer → backend
        ("lb-node",      "10.104.0.1", "LB→Compute backend",    "s1"),
        # s2: compute health probe back to LB
        ("compute-node", "10.104.1.1", "Compute→LB health",     "s2"),
    ],
    "qdc": [
        # s1: test runners hitting the quality gate
        ("test-runner-1", "10.105.4.1", "Runner1→Quality gate",  "s1"),
        ("test-runner-2", "10.105.4.1", "Runner2→Quality gate",  "s1"),
        ("e2e-runner",    "10.105.4.1", "E2E→Quality gate",      "s1"),
        # s2: coverage and DORA
        ("coverage-srv",  "10.105.4.1", "Coverage→Gate",         "s2"),
        ("quality-gate",  "10.105.5.1", "Gate→DORA metrics",     "s2"),
    ],
    "mdc": [
        # s1: source-side connectivity to DMS
        ("src-db",    "10.106.2.1", "SrcDB→DMS engine",          "s1"),
        ("src-app",   "10.106.2.1", "SrcApp→DMS engine",         "s1"),
        # s2: migration tunnel
        ("dms-engine","10.106.3.1", "DMS→VPN tunnel",            "s2"),
        # s3: target-side write paths
        ("vpn-tunnel","10.106.10.1","Tunnel→Target DB",          "s3"),
        ("tgt-app",   "10.106.10.1","TgtApp→TgtDB",              "s3"),
    ],
    "aadc": [
        # s1: orchestrator ↔ LLM gateway
        ("orchestrator",  "10.107.1.1", "Orchestrator→LLM GW",  "s1"),
        ("llm-gateway",   "10.107.0.1", "LLM GW→Orchestrator",  "s1"),
        # s2: agent submission paths
        ("agent-planner", "10.107.0.1", "Planner→Orchestrator", "s2"),
        ("agent-coder",   "10.107.0.1", "Coder→Orchestrator",   "s2"),
        # s3: memory access
        ("orchestrator",  "10.107.6.1", "Orchestrator→Memory",  "s3"),
        ("agent-planner", "10.107.6.1", "Planner→Memory",       "s3"),
    ],
    "aimc": [
        # s1: training pipeline
        ("feature-store",  "10.108.1.1", "Features→Training",   "s1"),
        ("training-node",  "10.108.2.1", "Training→Eval",       "s1"),
        ("eval-harness",   "10.108.3.1", "Eval→Registry",       "s1"),
        # s2: serving path
        ("model-registry", "10.108.4.1", "Registry→Inference",  "s2"),
        # s3: drift monitoring
        ("drift-monitor",  "10.108.3.1", "Drift→Registry",      "s3"),
        ("drift-monitor",  "10.108.4.1", "Drift→Inference",     "s3"),
    ],
    "ohc": [
        # s1: incident response chain
        ("incident-mgr",  "10.109.1.1", "Incident→Runbook",     "s1"),
        ("runbook-exec",  "10.109.2.1", "Runbook→CMDB",         "s1"),
        # s2: SLA and on-call
        ("sla-monitor",   "10.109.0.1", "SLA→Incident mgr",     "s2"),
        ("on-call-node",  "10.109.0.1", "OnCall→Incident mgr",  "s2"),
        # s3: postmortem
        ("postmortem-db", "10.109.2.1", "Postmortem→CMDB",      "s3"),
    ],
    "ddc": [
        # s1: management plane
        ("mgmt",       "10.110.1.1", "Mgmt→DB primary",         "s1"),
        ("mgmt",       "10.110.2.1", "Mgmt→DB replica",         "s1"),
        # s2: replication path
        ("db-primary", "10.110.2.1", "Primary→Replica sync",    "s2"),
    ],
}

# Traceroute targets per canvas (end-to-end path verification)
_CANVAS_TRACE_TARGETS: dict[str, list[tuple[str, str, str, str]]] = {
    "sdc":  [("threat-actor", "10.100.3.1", "Full threat path to DB", "s1")],
    "bdc":  [("boundary-fw",  "10.101.1.1", "FW to PPS end-to-end",  "s2")],
    "pdc":  [("scm",          "10.102.4.1", "SCM to deploy agent",   "s3")],
    "odc":  [("otel-collector","10.103.4.1","OTEL to Grafana path",  "s3")],
    "idc":  [("lb-node",       "10.104.0.1","LB to compute",         "s1")],
    "qdc":  [("test-runner-1", "10.105.5.1","Runner to DORA",        "s2")],
    "mdc":  [("src-db",        "10.106.10.1","Source to target DB",  "s3")],
    "aadc": [("agent-planner", "10.107.1.1","Planner to LLM GW",    "s2")],
    "aimc": [("feature-store", "10.108.4.1","Features to inference", "s2")],
    "ohc":  [("sla-monitor",   "10.109.1.1","SLA to runbook",        "s2")],
    "ddc":  [("mgmt",          "10.110.2.1","Mgmt to replica",       "s2")],
}

# DB table names per canvas
_CANVAS_DB_TABLE: dict[str, str] = {
    "sdc":  "sdc_live_flows",
    "bdc":  "bdc_live_flows",
    "pdc":  "pdc_live_flows",
    "odc":  "odc_live_flows",
    "idc":  "idc_live_flows",
    "qdc":  "qdc_live_flows",
    "mdc":  "mdc_live_flows",
    "aadc": "aadc_live_flows",
    "aimc": "aimc_live_flows",
    "ohc":  "ohc_live_flows",
    "ddc":  "ddc_live_flows",
    "ndc":  "ndc_live_flows",
}

# GNS3 project name per canvas
_CANVAS_PROJECT_NAME: dict[str, str] = {
    "sdc":  "sdc-lab",
    "bdc":  "bdc-lab",
    "pdc":  "pdc-lab",
    "odc":  "odc-lab",
    "idc":  "idc-lab",
    "qdc":  "qdc-lab",
    "mdc":  "mdc-lab",
    "aadc": "aadc-lab",
    "aimc": "aimc-lab",
    "ohc":  "ohc-lab",
    "ddc":  "ddc-lab",
    "ndc":  "dod-bcap-lab",
}

# Convergence wait (seconds after ZTP before running traffic)
_CANVAS_CONVERGENCE_S: dict[str, int] = {
    "ndc":  45,  # BGP convergence
    "sdc":  20,
    "bdc":  20,
    "pdc":  15,
    "odc":  15,
    "idc":  15,
    "qdc":  10,
    "mdc":  20,
    "aadc": 10,
    "aimc": 10,
    "ohc":  10,
    "ddc":  15,
}


# ── ZTP script generator ───────────────────────────────────────────────────────

def generate_ztp_scripts(canvas: str, ztp_dir: Path) -> dict[str, Path]:
    """Write per-node VPCS ip-config files to ztp_dir. Returns {node_name: path}."""
    ip_plan = _CANVAS_IP_PLAN.get(canvas, {})
    if not ip_plan:
        return {}
    ztp_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for node_name, ip_spec in ip_plan.items():
        # VPCS format: "ip <addr>/<prefix> <gateway>"
        path = ztp_dir / f"vpcs_{node_name}.txt"
        path.write_text(f"ip {ip_spec}\n", encoding="utf-8", newline="")
        written[node_name] = path
    return written


# ── Canvas traffic engine ──────────────────────────────────────────────────────

class CanvasTrafficEngine:
    """Per-canvas wrapper around GNS3TrafficEngine.

    Generates ZTP scripts on-the-fly from the canvas IP plan, then delegates
    to GNS3TrafficEngine with the canvas-appropriate project name, traffic
    matrix, trace targets, and DB table.

    Args:
        canvas:  Canvas slug (sdc, bdc, pdc, odc, idc, qdc, mdc, aadc, aimc, ohc, ddc, ndc)
        server:  GNS3 server URL (default: http://localhost:3080)
        project: GNS3 project name (default: canvas default from _CANVAS_PROJECT_NAME)
    """

    def __init__(
        self,
        canvas: str,
        server: str = "http://localhost:3080",
        project: str = "",
    ):
        self.canvas = canvas.lower()
        self.server = server
        self.project = project or _CANVAS_PROJECT_NAME.get(self.canvas, f"{self.canvas}-lab")
        self._ztp_dir: Path | None = None

    def _build_engine(self) -> "Any":
        from tools.ndc.gns3_traffic_engine import GNS3TrafficEngine

        # NDC uses its own pre-defined ZTP dir and traffic matrix
        if self.canvas == "ndc":
            return GNS3TrafficEngine(server=self.server, project=self.project)

        # Generate ZTP scripts to a temp dir
        tmp = Path(tempfile.mkdtemp(prefix=f"icdev_{self.canvas}_ztp_"))
        self._ztp_dir = tmp
        generate_ztp_scripts(self.canvas, tmp)

        matrix = _CANVAS_TRAFFIC_MATRIX.get(self.canvas, [])
        traces = _CANVAS_TRACE_TARGETS.get(self.canvas, [])
        db_table = _CANVAS_DB_TABLE.get(self.canvas, f"{self.canvas}_live_flows")
        convergence_s = _CANVAS_CONVERGENCE_S.get(self.canvas, 15)

        return GNS3TrafficEngine(
            server=self.server,
            project=self.project,
            ztp_dir=tmp,
            traffic_matrix=matrix,
            trace_targets=traces,
            db_table=db_table,
            convergence_s=convergence_s,
        )

    def run(self, do_ztp: bool = True, do_traffic: bool = True,
            wait_s: int | None = None) -> dict:
        """Run ZTP + traffic phases. Returns the GNS3TrafficEngine result dict."""
        engine = self._build_engine()
        result = engine.run(do_ztp=do_ztp, do_traffic=do_traffic, wait_s=wait_s)
        result["canvas"] = self.canvas
        if self._ztp_dir:
            result["ztp_dir"] = str(self._ztp_dir)
        return result

    def ip_plan(self) -> dict[str, str]:
        """Return the IP plan for this canvas (node_name → 'ip/prefix gw')."""
        return dict(_CANVAS_IP_PLAN.get(self.canvas, {}))

    def traffic_matrix(self) -> list:
        """Return the traffic matrix for this canvas."""
        return list(_CANVAS_TRAFFIC_MATRIX.get(self.canvas, []))


# ── Factory ────────────────────────────────────────────────────────────────────

def get_canvas_engine(canvas: str, server: str = "http://localhost:3080",
                      project: str = "") -> CanvasTrafficEngine:
    """Return a CanvasTrafficEngine configured for the given canvas slug."""
    return CanvasTrafficEngine(canvas=canvas, server=server, project=project)


# ── Standalone CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="Canvas Traffic Engine — ZTP + traffic matrix")
    p.add_argument("--canvas",  required=True, help="Canvas slug (sdc, bdc, pdc, ...)")
    p.add_argument("--server",  default="http://localhost:3080")
    p.add_argument("--project", default="", help="GNS3 project name (default: canvas default)")
    p.add_argument("--ztp",     action="store_true", help="Push ZTP ip-configs")
    p.add_argument("--traffic", action="store_true", help="Run traffic matrix")
    p.add_argument("--all",     action="store_true", help="ZTP + traffic")
    p.add_argument("--plan",    action="store_true", help="Print IP plan and traffic matrix")
    p.add_argument("--json",    action="store_true")
    args = p.parse_args()

    engine = get_canvas_engine(args.canvas, args.server, args.project)

    if args.plan:
        out = {
            "canvas":         args.canvas,
            "project":        engine.project,
            "ip_plan":        engine.ip_plan(),
            "traffic_matrix": [
                {"src": r[0], "dst_ip": r[1], "label": r[2], "scenario": r[3]}
                for r in engine.traffic_matrix()
            ],
            "trace_targets": [
                {"src": r[0], "dst_ip": r[1], "label": r[2], "scenario": r[3]}
                for r in _CANVAS_TRACE_TARGETS.get(args.canvas, [])
            ],
            "db_table":        _CANVAS_DB_TABLE.get(args.canvas, ""),
            "convergence_s":   _CANVAS_CONVERGENCE_S.get(args.canvas, 15),
        }
        print(json.dumps(out, indent=2))
        return

    do_ztp     = args.ztp or args.all
    do_traffic = args.traffic or args.all

    if not do_ztp and not do_traffic:
        p.print_help(); sys.exit(0)

    result = engine.run(do_ztp=do_ztp, do_traffic=do_traffic)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        phases = result.get("phases", {})
        if "ztp" in phases:
            z = phases["ztp"]
            print(f"\nZTP [{args.canvas.upper()}]: pushed={z['pushed']}  "
                  f"failed={z['failed']}  skipped={z['skipped']}  ({z['duration_s']}s)")
        if "traffic" in phases:
            t = phases["traffic"]
            print(f"\nTraffic [{args.canvas.upper()}]: "
                  f"{t['reachable']}/{t['flows_tested']} reachable  "
                  f"({t['duration_s']}s)  stored={t['stored_to_db']} rows")
            for f in t.get("flows", []):
                icon = "OK  " if f["reachable"] else "FAIL"
                rtt  = f"{f['avg_rtt_ms']:.1f}ms" if f.get("avg_rtt_ms") is not None else "N/A"
                print(f"  [{icon}] {f['src']:<20} -> {f['dst_ip']:<18} "
                      f"{f['label']:<30} rtt={rtt}")


if __name__ == "__main__":
    main()
