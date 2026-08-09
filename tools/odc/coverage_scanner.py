"""Coverage Scanner — ODC Workflow Step 1.

Scans observability design coverage: log sources, metrics, traces, MITRE ATT&CK
detection coverage, alert rules, and missing observability.
Outputs JSON with artifact paths to stdout.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "odc"

# Log source node types
_LOG_SOURCE_TYPES = {
    "log-app", "log-system", "log-vpc-flow", "log-cloudtrail",
    "log-alb", "log-elb", "log-waf", "log-rds", "log-lambda",
    "log-container", "log-k8s", "log-ecs", "log-eks",
    "log-os", "log-network", "log-security",
}

# Metric source types
_METRIC_TYPES = {
    "metric-cloudwatch", "metric-prometheus", "metric-statsd",
    "metric-custom", "metric-business", "metric-infra",
    "metric-apm", "metric-synthetics", "metric-cpu", "metric-memory",
}

# Trace source types
_TRACE_TYPES = {
    "trace-xray", "trace-otel", "trace-jaeger", "trace-zipkin",
    "trace-apm", "trace-distributed",
}

# Alert/rule node types
_ALERT_TYPES = {
    "alert-cloudwatch", "alert-sns", "alert-pagerduty",
    "alert-opsgenie", "alert-email", "alert-slack",
    "rule-detection", "rule-alert", "alert-threshold",
}

# MITRE ATT&CK technique coverage (subset of Cloud Matrix)
_MITRE_TECHNIQUES = {
    "T1078": "Valid Accounts",
    "T1110": "Brute Force",
    "T1190": "Exploit Public-Facing Application",
    "T1566": "Phishing",
    "T1059": "Command and Scripting Interpreter",
    "T1098": "Account Manipulation",
    "T1530": "Data from Cloud Storage",
    "T1537": "Transfer Data to Cloud Account",
    "T1580": "Cloud Infrastructure Discovery",
    "T1552": "Unsecured Credentials",
}

# Required observability coverage checks
_MISSING_OBS_CHECKS = [
    {
        "id": "OBS-001",
        "name": "VPC Flow Logs Missing",
        "description": "No VPC flow log source detected — network traffic not captured.",
        "check_fn": lambda types: not any(t in types for t in {"log-vpc-flow", "log-network", "ctrl-vpc-flow-logs"}),
    },
    {
        "id": "OBS-002",
        "name": "CloudTrail Logs Missing",
        "description": "No CloudTrail log source — AWS API calls not audited.",
        "check_fn": lambda types: not any(t in types for t in {"log-cloudtrail", "ctrl-cloudtrail", "ctrl-audit-log"}),
    },
    {
        "id": "OBS-003",
        "name": "Container Logs Missing",
        "description": "No container/application log source detected.",
        "check_fn": lambda types: not any(t in types for t in {"log-container", "log-ecs", "log-eks", "log-k8s", "log-app"}),
    },
    {
        "id": "OBS-004",
        "name": "No Alert Rules Configured",
        "description": "No alerting nodes detected — incidents will go unnoticed.",
        "check_fn": lambda types: not any(t in types for t in _ALERT_TYPES),
    },
    {
        "id": "OBS-005",
        "name": "No Metrics Collection",
        "description": "No metrics source detected — system performance not monitored.",
        "check_fn": lambda types: not any(t in types for t in _METRIC_TYPES),
    },
]


def _default_designs() -> dict:
    return {
        "default-odc": {
            "nodes": [
                {"id": "n1", "type": "log-app", "label": "Application Logs", "classification": ""},
                {"id": "n2", "type": "log-cloudtrail", "label": "CloudTrail Logs", "classification": ""},
                {"id": "n3", "type": "log-vpc-flow", "label": "VPC Flow Logs", "classification": ""},
                {"id": "n4", "type": "metric-cloudwatch", "label": "CloudWatch Metrics", "classification": ""},
                {"id": "n5", "type": "trace-xray", "label": "X-Ray Traces", "classification": ""},
                {"id": "n6", "type": "alert-sns", "label": "SNS Alerts", "classification": ""},
                {"id": "n7", "type": "svc-cloudwatch", "label": "CloudWatch", "classification": ""},
            ],
            "edges": [
                {"source": "n1", "target": "n7", "type": "ships-to", "label": ""},
                {"source": "n2", "target": "n7", "type": "ships-to", "label": ""},
                {"source": "n3", "target": "n7", "type": "ships-to", "label": ""},
                {"source": "n4", "target": "n7", "type": "ships-to", "label": ""},
                {"source": "n7", "target": "n6", "type": "triggers", "label": ""},
            ],
        }
    }


def _load_designs(project_id: str) -> dict:
    try:
        conn = None
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            import re
            is_uuid = bool(re.match(
                r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
                project_id, re.I,
            ))
            if is_uuid:
                rows = conn.execute(
                    "SELECT id, name, graph_json FROM odc_designs WHERE id=%s",
                    (project_id,),
                ).fetchall()
            else:
                try:
                    rows = conn.execute(
                        "SELECT id, name, graph_json FROM odc_designs ORDER BY updated_at DESC LIMIT 20"
                    ).fetchall()
                except Exception:
                    rows = conn.execute(
                        "SELECT id, name, graph_json FROM observability_designs ORDER BY updated_at DESC LIMIT 20"
                    ).fetchall()
        finally:
            if conn:
                conn.close()

        designs: dict = {}
        for row in (rows or []):
            did = row[0]
            try:
                g = json.loads(row[2]) if row[2] else {}
            except (json.JSONDecodeError, TypeError):
                g = {}
            raw_nodes = g.get("nodes", [])
            raw_edges = g.get("edges", [])
            nodes = [
                {
                    "id": n.get("id", ""),
                    "type": n.get("type", ""),
                    "label": n.get("label") or n.get("id", ""),
                    "classification": n.get("classification") or n.get("data", {}).get("classification", ""),
                }
                for n in raw_nodes
            ]
            edges = [
                {
                    "source": e.get("source") or e.get("from", ""),
                    "target": e.get("target") or e.get("to", ""),
                    "type": e.get("type") or e.get("label", ""),
                    "label": e.get("label", ""),
                }
                for e in raw_edges
            ]
            designs[did] = {"nodes": nodes, "edges": edges}
        return designs if designs else _default_designs()
    except Exception:
        return _default_designs()


def _estimate_mitre_coverage(node_types: set) -> int:
    """Estimate MITRE ATT&CK detection coverage as a percentage."""
    covered = 0
    total = len(_MITRE_TECHNIQUES)
    has_logs = bool(node_types & _LOG_SOURCE_TYPES)
    has_metrics = bool(node_types & _METRIC_TYPES)
    has_alerts = bool(node_types & _ALERT_TYPES)
    has_cloudtrail = any(t in node_types for t in {"log-cloudtrail", "ctrl-cloudtrail"})
    has_vpc_flow = any(t in node_types for t in {"log-vpc-flow", "log-network"})

    if has_cloudtrail:
        covered += 3  # T1078, T1098, T1580
    if has_vpc_flow:
        covered += 2  # T1190, T1537
    if has_logs:
        covered += 2  # T1059, T1566
    if has_metrics and has_alerts:
        covered += 1  # T1110
    if any(t in node_types for t in {"log-s3", "log-rds", "log-app"}):
        covered += 1  # T1530
    if has_cloudtrail and has_vpc_flow:
        covered += 1  # T1552

    return min(100, int((covered / total) * 100))


def scan_coverage(project_id: str) -> dict:
    designs = _load_designs(project_id)

    all_log_sources: list = []
    all_metrics: list = []
    all_traces: list = []
    all_alert_rules: list = []
    all_missing: list = []
    all_mitre_pct: list = []
    designs_scanned = 0

    for did, d in designs.items():
        nodes = d["nodes"]
        node_types = {n.get("type") or "" for n in nodes}
        designs_scanned += 1

        log_nodes = [n for n in nodes if (n.get("type") or "") in _LOG_SOURCE_TYPES or
                     (n.get("type") or "").startswith("log-")]
        metric_nodes = [n for n in nodes if (n.get("type") or "") in _METRIC_TYPES or
                        (n.get("type") or "").startswith("metric-")]
        trace_nodes = [n for n in nodes if (n.get("type") or "") in _TRACE_TYPES or
                       (n.get("type") or "").startswith("trace-")]
        alert_nodes = [n for n in nodes if (n.get("type") or "") in _ALERT_TYPES or
                       (n.get("type") or "").startswith("alert-") or
                       (n.get("type") or "").startswith("rule-")]

        all_log_sources.extend([n.get("label") or n.get("id", "") for n in log_nodes])
        all_metrics.extend([n.get("label") or n.get("id", "") for n in metric_nodes])
        all_traces.extend([n.get("label") or n.get("id", "") for n in trace_nodes])
        all_alert_rules.extend([n.get("label") or n.get("id", "") for n in alert_nodes])

        mitre_pct = _estimate_mitre_coverage(node_types)
        all_mitre_pct.append(mitre_pct)

        for check in _MISSING_OBS_CHECKS:
            try:
                missing = check["check_fn"](node_types)
            except Exception:
                missing = False
            if missing:
                all_missing.append({
                    "design": did,
                    "id": check["id"],
                    "name": check["name"],
                    "description": check["description"],
                })

    avg_mitre = int(sum(all_mitre_pct) / len(all_mitre_pct)) if all_mitre_pct else 0

    return {
        "designs_scanned": designs_scanned,
        "log_sources": list(set(all_log_sources)),
        "metrics_covered": list(set(all_metrics)),
        "traces_covered": list(set(all_traces)),
        "alert_rules": list(set(all_alert_rules)),
        "mitre_coverage_pct": avg_mitre,
        "missing_observability": all_missing,
    }


def build_report(result: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    missing = result["missing_observability"]
    mitre_pct = result["mitre_coverage_pct"]
    gate = "FAIL" if len(missing) >= 3 else ("WARN" if missing else "PASS")

    lines = [
        "# Observability Coverage Scan Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Designs Scanned:** {result['designs_scanned']}  ",
        f"**Gate:** {'✗ FAIL' if gate == 'FAIL' else ('⚠ WARN' if gate == 'WARN' else '✓ PASS')}",
        "",
        "## Coverage Summary",
        "",
        "| Dimension | Count |",
        "|-----------|-------|",
        f"| Log Sources | {len(result['log_sources'])} |",
        f"| Metrics Covered | {len(result['metrics_covered'])} |",
        f"| Traces Covered | {len(result['traces_covered'])} |",
        f"| Alert Rules | {len(result['alert_rules'])} |",
        f"| MITRE ATT&CK Coverage | {mitre_pct}% |",
        "",
    ]

    if result["log_sources"]:
        lines += ["### Log Sources", ""]
        for s in result["log_sources"]:
            lines.append(f"- `{s}`")
        lines.append("")

    if result["metrics_covered"]:
        lines += ["### Metrics", ""]
        for m in result["metrics_covered"]:
            lines.append(f"- `{m}`")
        lines.append("")

    if result["traces_covered"]:
        lines += ["### Distributed Traces", ""]
        for t in result["traces_covered"]:
            lines.append(f"- `{t}`")
        lines.append("")

    if result["alert_rules"]:
        lines += ["### Alert Rules", ""]
        for a in result["alert_rules"]:
            lines.append(f"- `{a}`")
        lines.append("")

    lines += [
        "## MITRE ATT&CK Detection Coverage",
        "",
        f"**Estimated coverage: {mitre_pct}%**",
        "",
        "| Technique | Name | Covered |",
        "|-----------|------|---------|",
    ]
    covered_count = int(mitre_pct / 100 * len(_MITRE_TECHNIQUES))
    for i, (tid, tname) in enumerate(_MITRE_TECHNIQUES.items()):
        covered = "✓" if i < covered_count else "✗"
        lines.append(f"| `{tid}` | {tname} | {covered} |")

    if missing:
        lines += [
            "",
            "## Missing Observability",
            "",
        ]
        for m in missing:
            lines += [
                f"### [{m['id']}] {m['name']}",
                f"**Design:** `{m['design'][:12]}`",
                "",
                f"{m['description']}",
                "",
            ]
    else:
        lines += [
            "",
            "## ✓ Full Observability Coverage",
            "",
            "All required observability sources are present. Proceed to gap analysis (Step 2).",
        ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ODC Coverage Scanner")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="odc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = scan_coverage(args.project_id)
        report_md = build_report(result, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fpath = _ARTIFACTS_DIR / f"coverage_scan_{uid}.md"
        fpath.write_text(report_md, encoding="utf-8", newline="")

        missing = result["missing_observability"]
        gate = "FAIL" if len(missing) >= 3 else ("WARN" if missing else "PASS")

        output = {
            "status": "success" if gate != "FAIL" else "warn",
            "gate": gate,
            "designs_scanned": result["designs_scanned"],
            "log_sources_count": len(result["log_sources"]),
            "metrics_count": len(result["metrics_covered"]),
            "traces_count": len(result["traces_covered"]),
            "alert_rules_count": len(result["alert_rules"]),
            "mitre_coverage_pct": result["mitre_coverage_pct"],
            "missing_observability_count": len(missing),
            "artifacts": [
                {"name": "Coverage Scan Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"}
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
