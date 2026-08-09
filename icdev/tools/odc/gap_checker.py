"""Gap Checker — ODC Workflow Step 2.

Validates observability design against NIST SP 800-92 log management and MITRE D3FEND.
Checks log retention, encryption, centralized aggregation, alerting thresholds,
missing CloudWatch metrics, and SNS alerts.
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

# Minimum log retention per NIST SP 800-92
_MIN_RETENTION_DAYS = 90

# Gap checks against NIST SP 800-92 and MITRE D3FEND
_GAP_CHECKS = [
    {
        "id": "GAP-001",
        "name": "Log Retention < 90 Days",
        "description": "NIST SP 800-92 requires log retention of at least 90 days. No retention policy node detected.",
        "standard": "NIST SP 800-92 Section 4.3",
        "severity": "HIGH",
        "check_fn": lambda types: not any(t in types for t in {
            "ctrl-retention", "ctrl-log-retention", "ctrl-lifecycle",
            "policy-retention", "log-retention",
        }),
    },
    {
        "id": "GAP-002",
        "name": "Log Encryption Not Configured",
        "description": "Logs must be encrypted at rest per NIST SP 800-92 and SC-28.",
        "standard": "NIST SP 800-92, NIST SC-28",
        "severity": "HIGH",
        "check_fn": lambda types: not any(t in types for t in {
            "ctrl-kms", "ctrl-encryption", "ctrl-log-encrypt", "ctrl-cmk",
        }),
    },
    {
        "id": "GAP-003",
        "name": "No Centralized Log Aggregation",
        "description": "Logs must be centrally aggregated per NIST SP 800-92 Section 3.2.",
        "standard": "NIST SP 800-92 Section 3.2",
        "severity": "HIGH",
        "check_fn": lambda types: not any(t in types for t in {
            "svc-cloudwatch", "svc-opensearch", "svc-splunk", "svc-elk",
            "svc-datadog", "svc-sumologic", "svc-siem", "agg-central",
        }),
    },
    {
        "id": "GAP-004",
        "name": "No Alerting Thresholds Configured",
        "description": "CloudWatch alarms or equivalent alerting thresholds must be defined.",
        "standard": "NIST SP 800-92, IR-4",
        "severity": "HIGH",
        "check_fn": lambda types: not any(t in types for t in {
            "alert-cloudwatch", "alert-threshold", "rule-alert",
            "ctrl-alarm", "svc-alarm",
        }),
    },
    {
        "id": "GAP-005",
        "name": "Missing CloudWatch Metrics",
        "description": "CloudWatch custom or standard metrics are not present in the design.",
        "standard": "NIST SP 800-92, SI-4",
        "severity": "MEDIUM",
        "check_fn": lambda types: not any(t in types for t in {
            "metric-cloudwatch", "metric-custom", "metric-infra",
        }),
    },
    {
        "id": "GAP-006",
        "name": "No SNS Alerts Configured",
        "description": "SNS topic for alert notifications is not present.",
        "standard": "NIST SP 800-92, IR-6",
        "severity": "MEDIUM",
        "check_fn": lambda types: not any(t in types for t in {
            "alert-sns", "svc-sns", "ctrl-sns",
        }),
    },
    {
        "id": "GAP-007",
        "name": "No Distributed Tracing",
        "description": "Distributed tracing is absent — latency and error attribution are not available.",
        "standard": "MITRE D3FEND: Network Traffic Analysis",
        "severity": "MEDIUM",
        "check_fn": lambda types: not any(t in types for t in {
            "trace-xray", "trace-otel", "trace-jaeger", "trace-distributed",
        }),
    },
    {
        "id": "GAP-008",
        "name": "No Anomaly Detection",
        "description": "No anomaly detection or ML-based alerting node detected (D3FEND: Anomaly Analysis).",
        "standard": "MITRE D3FEND: D3-AA",
        "severity": "MEDIUM",
        "check_fn": lambda types: not any(t in types for t in {
            "ctrl-anomaly", "svc-anomaly-detection", "ml-anomaly", "ctrl-cloudwatch-anomaly",
        }),
    },
    {
        "id": "GAP-009",
        "name": "SIEM Not Integrated",
        "description": "No SIEM integration — correlated threat detection is unavailable.",
        "standard": "MITRE D3FEND: D3-SIEM",
        "severity": "MEDIUM",
        "check_fn": lambda types: not any(t in types for t in {
            "svc-siem", "svc-splunk", "svc-qradar", "svc-opensearch-siem", "svc-elk",
        }),
    },
    {
        "id": "GAP-010",
        "name": "No Dashboard / Visualization",
        "description": "No observability dashboard node — operational visibility is limited.",
        "standard": "NIST SP 800-92 Section 5.1",
        "severity": "LOW",
        "check_fn": lambda types: not any(t in types for t in {
            "svc-grafana", "svc-cloudwatch-dashboard", "svc-dashboard",
            "vis-dashboard", "ctrl-dashboard",
        }),
    },
]

# D3FEND defense tactic coverage
_D3FEND_TACTICS = {
    "D3-NTA": "Network Traffic Analysis",
    "D3-LFE": "Log File Evidence",
    "D3-LFA": "Log File Analysis",
    "D3-AA": "Anomaly Analysis",
    "D3-SIEM": "Security Information and Event Management",
    "D3-PCSV": "Process Code Segment Verification",
}


def _default_designs() -> dict:
    return {
        "default-odc": {
            "nodes": [
                {"id": "n1", "type": "log-app", "label": "Application Logs", "classification": ""},
                {"id": "n2", "type": "svc-cloudwatch", "label": "CloudWatch", "classification": ""},
                {"id": "n3", "type": "ctrl-kms", "label": "Log Encryption (KMS)", "classification": ""},
                {"id": "n4", "type": "alert-cloudwatch", "label": "CloudWatch Alarms", "classification": ""},
                {"id": "n5", "type": "alert-sns", "label": "SNS Notifications", "classification": ""},
                {"id": "n6", "type": "metric-cloudwatch", "label": "CloudWatch Metrics", "classification": ""},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "type": "ships-to", "label": ""},
                {"source": "n3", "target": "n2", "type": "encrypts", "label": ""},
                {"source": "n2", "target": "n4", "type": "triggers", "label": ""},
                {"source": "n4", "target": "n5", "type": "notifies", "label": ""},
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


def check_gaps(project_id: str) -> dict:
    designs = _load_designs(project_id)

    all_findings: list = []
    all_passed: list = []
    stats = {
        "designs": len(designs),
        "checks_total": len(_GAP_CHECKS),
        "checks_passed": 0,
        "checks_failed": 0,
        "high_findings": 0,
        "medium_findings": 0,
        "low_findings": 0,
    }

    for did, d in designs.items():
        nodes = d["nodes"]
        node_types = {n.get("type") or "" for n in nodes}

        for check in _GAP_CHECKS:
            try:
                has_gap = check["check_fn"](node_types)
            except Exception:
                has_gap = False
            if has_gap:
                all_findings.append({
                    "design": did,
                    "id": check["id"],
                    "name": check["name"],
                    "description": check["description"],
                    "standard": check["standard"],
                    "severity": check["severity"],
                })
                stats["checks_failed"] += 1
                sev = check["severity"]
                if sev == "HIGH":
                    stats["high_findings"] += 1
                elif sev == "MEDIUM":
                    stats["medium_findings"] += 1
                else:
                    stats["low_findings"] += 1
            else:
                all_passed.append({
                    "design": did,
                    "id": check["id"],
                    "name": check["name"],
                    "standard": check["standard"],
                })
                stats["checks_passed"] += 1

    return {
        "findings": all_findings,
        "passed_checks": all_passed,
        "stats": stats,
    }


def build_report(result: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    stats = result["stats"]
    high = [f for f in findings if f["severity"] == "HIGH"]
    medium = [f for f in findings if f["severity"] == "MEDIUM"]
    low = [f for f in findings if f["severity"] == "LOW"]
    gate = "FAIL" if high else ("WARN" if medium else "PASS")

    lines = [
        "# Observability Gap Check Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        "**Standard:** NIST SP 800-92 + MITRE D3FEND  ",
        f"**Gate:** {'✗ FAIL' if gate == 'FAIL' else ('⚠ WARN' if gate == 'WARN' else '✓ PASS')}",
        "",
        "## Summary",
        "",
        "| Category | Count |",
        "|----------|-------|",
        f"| Checks Total | {stats['checks_total']} |",
        f"| Checks Passed | {stats['checks_passed']} |",
        f"| High Findings | {stats['high_findings']} |",
        f"| Medium Findings | {stats['medium_findings']} |",
        f"| Low Findings | {stats['low_findings']} |",
        "",
    ]

    if high:
        lines += ["## High Severity Gaps (Blocking)", ""]
        for f in high:
            lines += [
                f"### [{f['id']}] {f['name']}",
                f"**Standard:** `{f['standard']}`  **Design:** `{f['design'][:12]}`",
                "",
                f"{f['description']}",
                "",
            ]

    if medium:
        lines += ["## Medium Severity Gaps", ""]
        for f in medium:
            lines += [
                f"### [{f['id']}] {f['name']}",
                f"**Standard:** `{f['standard']}`  **Design:** `{f['design'][:12]}`",
                "",
                f"{f['description']}",
                "",
            ]

    if low:
        lines += ["## Low Severity Gaps", ""]
        for f in low:
            lines += [
                f"### [{f['id']}] {f['name']}",
                f"**Standard:** `{f['standard']}`  **Design:** `{f['design'][:12]}`",
                "",
                f"{f['description']}",
                "",
            ]

    lines += [
        "## D3FEND Defense Coverage",
        "",
        "| Tactic | Name |",
        "|--------|------|",
    ]
    for tactic_id, tactic_name in _D3FEND_TACTICS.items():
        lines.append(f"| `{tactic_id}` | {tactic_name} |")

    if result["passed_checks"]:
        lines += [
            "",
            "## Passed Checks",
            "",
        ]
        for p in result["passed_checks"]:
            lines.append(f"- ✓ [{p['id']}] {p['name']} — `{p['standard']}`")

    if not findings:
        lines += [
            "",
            "## ✓ No Observability Gaps",
            "",
            "Design meets NIST SP 800-92 and MITRE D3FEND requirements.",
            "Proceed to IaC generation (Step 3).",
        ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ODC Gap Checker")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="odc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = check_gaps(args.project_id)
        report_md = build_report(result, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fpath = _ARTIFACTS_DIR / f"gap_report_{uid}.md"
        fpath.write_text(report_md, encoding="utf-8", newline="")

        high = [f for f in result["findings"] if f["severity"] == "HIGH"]
        medium = [f for f in result["findings"] if f["severity"] == "MEDIUM"]
        gate = "FAIL" if high else ("WARN" if medium else "PASS")

        output = {
            "status": "success" if gate != "FAIL" else "failed",
            "gate": gate,
            "findings": len(result["findings"]),
            "high_findings": len(high),
            "medium_findings": len(medium),
            "passed_checks": len(result["passed_checks"]),
            "stats": result["stats"],
            "artifacts": [
                {"name": "Gap Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"}
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if gate != "FAIL" else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
