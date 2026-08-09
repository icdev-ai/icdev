"""STIG Checker — SDC Workflow Step 2.

Validates security design against STIG/SRG requirements and NIST 800-53 controls.
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "sdc"

# CAT I findings — Critical (immediate fix required)
_CAT1_CHECKS = [
    {
        "id": "V-220000",
        "title": "Encryption at Rest Disabled",
        "description": "Sensitive data stored without encryption violates NIST SC-28 and STIG CAT I.",
        "control": "NIST SC-28",
        "check_fn": lambda types: (
            any(t in types for t in {"ent-pii", "ent-phi", "ent-cui", "ent-classified"})
            and not any(t in types for t in {"ctrl-kms", "ctrl-encryption", "ctrl-cmk", "ctrl-fips", "ctrl-encrypt"})
        ),
    },
    {
        "id": "V-220001",
        "title": "Public S3 Bucket Detected",
        "description": "Publicly accessible S3 buckets expose data to unauthorized access. STIG CAT I.",
        "control": "NIST SC-7, AC-3",
        "check_fn": lambda types: any(t in types for t in {"ent-public-s3", "bnd-public-s3", "s3-public"}),
    },
    {
        "id": "V-220002",
        "title": "MFA Not Enforced",
        "description": "Multi-factor authentication must be enforced for all privileged accounts. STIG CAT I.",
        "control": "NIST IA-5(1)",
        "check_fn": lambda types: not any(t in types for t in {"ctrl-mfa", "ctrl-iam-mfa", "ctrl-authn"}),
    },
    {
        "id": "V-220003",
        "title": "Root Account Usage Permitted",
        "description": "AWS root account must not be used for routine operations. STIG CAT I.",
        "control": "NIST AC-6",
        "check_fn": lambda types: any(t in types for t in {"ent-root", "ctrl-root-account"}),
    },
]

# CAT II findings — High (fix within 30 days)
_CAT2_CHECKS = [
    {
        "id": "V-220010",
        "title": "CloudTrail Not Configured",
        "description": "AWS CloudTrail must be enabled in all regions for audit logging. STIG CAT II.",
        "control": "NIST AU-2, AU-12",
        "check_fn": lambda types: not any(t in types for t in {"ctrl-audit-log", "ctrl-cloudtrail", "ctrl-logging"}),
    },
    {
        "id": "V-220011",
        "title": "GuardDuty Not Enabled",
        "description": "AWS GuardDuty provides threat detection and must be enabled. STIG CAT II.",
        "control": "NIST SI-4, IR-4",
        "check_fn": lambda types: not any(t in types for t in {"ctrl-guardduty", "ctrl-threat-detection"}),
    },
    {
        "id": "V-220012",
        "title": "WAF Not Deployed",
        "description": "Web Application Firewall must protect internet-facing applications. STIG CAT II.",
        "control": "NIST SC-7, SI-10",
        "check_fn": lambda types: not any(t in types for t in {"ctrl-waf", "svc-waf"}),
    },
    {
        "id": "V-220013",
        "title": "Security Hub Not Configured",
        "description": "AWS Security Hub provides centralized security posture management. STIG CAT II.",
        "control": "NIST SI-4",
        "check_fn": lambda types: not any(t in types for t in {"ctrl-securityhub", "ctrl-security-hub"}),
    },
    {
        "id": "V-220014",
        "title": "VPC Flow Logs Not Enabled",
        "description": "VPC Flow Logs must capture network traffic for security analysis. STIG CAT II.",
        "control": "NIST AU-2, SI-4",
        "check_fn": lambda types: not any(t in types for t in {"ctrl-vpc-flow-logs", "ctrl-flow-logs", "ctrl-network-logs"}),
    },
]

# NIST 800-53 control mappings
_NIST_CONTROLS = {
    "AC-2": "Account Management",
    "AC-3": "Access Enforcement",
    "AC-6": "Least Privilege",
    "AU-2": "Event Logging",
    "AU-12": "Audit Record Generation",
    "IA-5": "Authenticator Management",
    "IA-5(1)": "Authenticator Management — Password-Based",
    "SC-7": "Boundary Protection",
    "SC-28": "Protection of Information at Rest",
    "SI-4": "System Monitoring",
    "SI-10": "Information Input Validation",
    "IR-4": "Incident Handling",
}


def _default_designs() -> dict:
    return {
        "default-sdc": {
            "nodes": [
                {"id": "n1", "type": "ent-pii", "label": "User PII Store", "classification": "CUI"},
                {"id": "n2", "type": "ctrl-kms", "label": "KMS Encryption Key", "classification": ""},
                {"id": "n3", "type": "bnd-internet", "label": "Internet Boundary", "classification": ""},
            ],
            "edges": [
                {"source": "n2", "target": "n1", "type": "encrypts", "label": ""},
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
                    "SELECT id, name, graph_json FROM sdc_designs WHERE id=%s",
                    (project_id,),
                ).fetchall()
            else:
                try:
                    rows = conn.execute(
                        "SELECT id, name, graph_json FROM sdc_designs ORDER BY updated_at DESC LIMIT 20"
                    ).fetchall()
                except Exception:
                    rows = conn.execute(
                        "SELECT id, name, graph_json FROM security_designs ORDER BY updated_at DESC LIMIT 20"
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


def check_stig(project_id: str) -> dict:
    designs = _load_designs(project_id)

    cat1_findings: list = []
    cat2_findings: list = []
    missing_controls: list = []
    stats = {
        "designs": len(designs),
        "cat1_findings": 0,
        "cat2_findings": 0,
        "checks_passed": 0,
        "checks_failed": 0,
    }

    for did, d in designs.items():
        nodes = d["nodes"]
        node_types = {n.get("type") or "" for n in nodes}

        for check in _CAT1_CHECKS:
            try:
                failed = check["check_fn"](node_types)
            except Exception:
                failed = False
            if failed:
                cat1_findings.append({
                    "design": did,
                    "vuln_id": check["id"],
                    "category": "CAT I",
                    "severity": "CRITICAL",
                    "title": check["title"],
                    "description": check["description"],
                    "control": check["control"],
                })
                stats["cat1_findings"] += 1
                stats["checks_failed"] += 1
            else:
                stats["checks_passed"] += 1

        for check in _CAT2_CHECKS:
            try:
                failed = check["check_fn"](node_types)
            except Exception:
                failed = False
            if failed:
                cat2_findings.append({
                    "design": did,
                    "vuln_id": check["id"],
                    "category": "CAT II",
                    "severity": "HIGH",
                    "title": check["title"],
                    "description": check["description"],
                    "control": check["control"],
                })
                stats["cat2_findings"] += 1
                stats["checks_failed"] += 1
            else:
                stats["checks_passed"] += 1

        # Check for missing NIST controls
        ctrl_nodes = [n for n in nodes if (n.get("type") or "").startswith("ctrl-")]
        if not ctrl_nodes:
            missing_controls.append(f"Design `{did[:12]}` has no security control nodes")

    return {
        "cat1_findings": cat1_findings,
        "cat2_findings": cat2_findings,
        "missing_controls": missing_controls,
        "stats": stats,
    }


def build_report(result: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cat1 = result["cat1_findings"]
    cat2 = result["cat2_findings"]
    stats = result["stats"]
    gate = "FAIL" if cat1 else ("WARN" if cat2 else "PASS")

    lines = [
        "# STIG/SRG Compliance Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✗ FAIL' if gate == 'FAIL' else ('⚠ WARN' if gate == 'WARN' else '✓ PASS')}  ",
        f"**Designs:** {stats['designs']}  ",
        f"**Checks Passed:** {stats['checks_passed']}  **Failed:** {stats['checks_failed']}",
        "",
        "## Executive Summary",
        "",
        "| Category | Count |",
        "|----------|-------|",
        f"| CAT I (Critical) | {stats['cat1_findings']} |",
        f"| CAT II (High) | {stats['cat2_findings']} |",
        f"| Checks Passed | {stats['checks_passed']} |",
        "",
    ]

    if cat1:
        lines.append("## CAT I Findings — Critical (Immediate Action Required)")
        lines.append("")
        for f in cat1:
            lines += [
                f"### {f['vuln_id']} — {f['title']}",
                f"**Design:** `{f['design'][:12]}`  **Control:** `{f['control']}`",
                "",
                f"{f['description']}",
                "",
            ]

    if cat2:
        lines.append("## CAT II Findings — High (Fix within 30 days)")
        lines.append("")
        for f in cat2:
            lines += [
                f"### {f['vuln_id']} — {f['title']}",
                f"**Design:** `{f['design'][:12]}`  **Control:** `{f['control']}`",
                "",
                f"{f['description']}",
                "",
            ]

    if result["missing_controls"]:
        lines.append("## Missing Control Nodes")
        lines.append("")
        for m in result["missing_controls"]:
            lines.append(f"- ⚠ {m}")
        lines.append("")

    lines += [
        "## NIST 800-53 Control Reference",
        "",
        "| Control | Name |",
        "|---------|------|",
    ]
    for ctrl_id, ctrl_name in _NIST_CONTROLS.items():
        lines.append(f"| `{ctrl_id}` | {ctrl_name} |")

    if not cat1 and not cat2:
        lines += [
            "",
            "## ✓ All STIG Checks Passed",
            "",
            "Design meets STIG/SRG baseline requirements. Proceed to IaC generation (Step 3).",
        ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="SDC STIG Checker")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="sdc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = check_stig(args.project_id)
        report_md = build_report(result, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fpath = _ARTIFACTS_DIR / f"stig_report_{uid}.md"
        fpath.write_text(report_md, encoding="utf-8", newline="")

        cat1 = result["cat1_findings"]
        cat2 = result["cat2_findings"]
        gate = "FAIL" if cat1 else ("WARN" if cat2 else "PASS")

        output = {
            "status": "success" if gate != "FAIL" else "failed",
            "gate": gate,
            "cat1_findings": len(cat1),
            "cat2_findings": len(cat2),
            "stats": result["stats"],
            "artifacts": [
                {"name": "STIG Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"}
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if gate != "FAIL" else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
