"""Hardening Checker — IDC Workflow Step 2.

Validates infrastructure design against CIS Benchmark and STIG hardening rules.
Checks encryption at rest, encryption in transit, least-privilege IAM, MFA,
logging, and backup configuration.
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

_CANVAS = "idc"
_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / _CANVAS

# CIS / STIG hardening rule definitions
_HARDENING_RULES = [
    {
        "id": "CIS-1.1",
        "name": "Encryption at Rest",
        "severity": "fail",
        "keywords_pass": ["encrypted", "kms", "sse", "encrypt-at-rest", "aes", "fips"],
        "keywords_storage": ["ebs", "s3", "rds", "db", "volume", "storage", "bucket", "disk"],
        "description": "All storage nodes must have encryption at rest configured (STIG V-220917)",
    },
    {
        "id": "CIS-1.2",
        "name": "Encryption in Transit",
        "severity": "fail",
        "keywords_pass": ["tls", "ssl", "https", "transit-encrypt", "mtls", "starttls"],
        "keywords_network": ["lb", "alb", "nlb", "api", "gateway", "endpoint", "listener"],
        "description": "All network endpoints must enforce TLS (STIG V-220918)",
    },
    {
        "id": "CIS-1.3",
        "name": "Least Privilege IAM",
        "severity": "warn",
        "keywords_pass": ["least-privilege", "readonly", "restricted", "scoped", "minimal"],
        "keywords_iam": ["iam", "role", "policy", "permission", "identity"],
        "description": "IAM roles should follow least-privilege principle (NIST AC-6)",
    },
    {
        "id": "CIS-1.4",
        "name": "MFA Enabled",
        "severity": "warn",
        "keywords_pass": ["mfa", "multi-factor", "2fa", "totp", "authenticator"],
        "keywords_iam": ["iam", "user", "console", "admin", "identity"],
        "description": "MFA must be enabled for privileged access (STIG V-220920)",
    },
    {
        "id": "CIS-1.5",
        "name": "Logging Enabled",
        "severity": "fail",
        "keywords_pass": ["cloudtrail", "cloudwatch", "logging", "audit-log", "siem", "splunk", "flow-log"],
        "description": "Audit logging must be enabled (NIST AU-2, AU-12)",
    },
    {
        "id": "CIS-1.6",
        "name": "Backup Configured",
        "severity": "warn",
        "keywords_pass": ["backup", "snapshot", "dr", "recovery", "rpo", "rto", "replication"],
        "description": "Backup and recovery must be configured (NIST CP-9)",
    },
]

_DEFAULT_DESIGNS = {
    "default-idc-design": {
        "nodes": [
            {"id": "n1", "type": "compute", "label": "App Server", "classification": "CUI"},
            {"id": "n2", "type": "storage", "label": "Encrypted EBS Volume", "classification": "CUI"},
            {"id": "n3", "type": "logging", "label": "CloudTrail", "classification": ""},
            {"id": "n4", "type": "backup", "label": "AWS Backup", "classification": ""},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "type": "attaches"},
            {"source": "n3", "target": "n1", "type": "monitors"},
            {"source": "n4", "target": "n2", "type": "backs-up"},
        ],
    }
}


def _load_designs(project_id: str) -> dict:
    """Load designs from DB; fall back to defaults."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, design_json FROM infra_canvas_designs "
                "ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()
        finally:
            conn.close()
        if rows:
            designs = {}
            for row in rows:
                did = row[0]
                try:
                    g = json.loads(row[2]) if row[2] else {}
                except (json.JSONDecodeError, TypeError):
                    g = {}
                raw_nodes = g.get("nodes", [])
                raw_edges = g.get("edges", [])
                designs[did] = {
                    "nodes": [
                        {
                            "id": n.get("id", ""),
                            "type": n.get("type", ""),
                            "label": n.get("label") or n.get("id", ""),
                            "classification": n.get("classification") or "",
                        }
                        for n in raw_nodes
                    ],
                    "edges": [
                        {
                            "source": e.get("source") or e.get("from", ""),
                            "target": e.get("target") or e.get("to", ""),
                            "type": e.get("type") or e.get("label", ""),
                        }
                        for e in raw_edges
                    ],
                }
            return designs
    except Exception:
        pass
    return _DEFAULT_DESIGNS


def _all_labels_lower(nodes: list) -> list:
    return [
        ((n.get("type") or "") + " " + (n.get("label") or "")).lower()
        for n in nodes
    ]


def _check_rule(rule: dict, nodes: list) -> dict:
    """Evaluate a single hardening rule against the node set."""
    labels = _all_labels_lower(nodes)

    # Check for presence of passing keyword in any node
    has_pass = any(
        any(kw in lbl for kw in rule["keywords_pass"])
        for lbl in labels
    )

    # Some rules only fire if a relevant resource type is present
    scope_key = None
    for k in ("keywords_storage", "keywords_network", "keywords_iam"):
        if k in rule:
            scope_key = k
            break

    if scope_key:
        in_scope = any(
            any(kw in lbl for kw in rule[scope_key])
            for lbl in labels
        )
        if not in_scope:
            return {
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": "info",
                "status": "skip",
                "message": f"No in-scope resources found — rule {rule['id']} not applicable",
            }

    status = "pass" if has_pass else "fail"
    return {
        "rule_id": rule["id"],
        "name": rule["name"],
        "severity": rule["severity"] if status == "fail" else "info",
        "status": status,
        "message": rule["description"] if status == "fail" else f"✓ {rule['name']} satisfied",
    }


def check_hardening(project_id: str) -> dict:
    designs = _load_designs(project_id)
    findings: list = []
    stats = {
        "designs": len(designs),
        "total_nodes": 0,
        "rules_checked": 0,
        "passed": 0,
        "warned": 0,
        "failed": 0,
        "skipped": 0,
    }

    for did, d in designs.items():
        nodes = d["nodes"]
        stats["total_nodes"] += len(nodes)

        for rule in _HARDENING_RULES:
            finding = _check_rule(rule, nodes)
            finding["design"] = did
            findings.append(finding)
            stats["rules_checked"] += 1

            if finding["status"] == "pass":
                stats["passed"] += 1
            elif finding["status"] == "fail":
                if finding["severity"] == "fail":
                    stats["failed"] += 1
                else:
                    stats["warned"] += 1
            elif finding["status"] == "skip":
                stats["skipped"] += 1

    if not designs:
        findings.append({
            "rule_id": "INFO-0",
            "name": "No Designs",
            "severity": "info",
            "status": "skip",
            "design": "n/a",
            "message": "No infrastructure designs found — hardening check skipped",
        })

    return {"findings": findings, "stats": stats, "designs": designs}


def build_report(result: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    stats = result["stats"]
    fails = [f for f in findings if f["status"] == "fail" and f["severity"] == "fail"]
    warns = [f for f in findings if f["status"] == "fail" and f["severity"] == "warn"]
    gate = "PASS" if not fails else "FAIL"

    lines = [
        "# Infrastructure Hardening Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '✗ FAIL'}  ",
        f"**Designs:** {stats['designs']}  **Nodes:** {stats['total_nodes']}",
        f"**Rules:** {stats['rules_checked']}  **Passed:** {stats['passed']}  "
        f"**Failed:** {stats['failed']}  **Warned:** {stats['warned']}  **Skipped:** {stats['skipped']}",
        "",
        "## Hardening Rules Summary",
        "",
        "| Rule ID | Name | Status | Severity |",
        "|---------|------|--------|----------|",
    ]
    for f in findings:
        icon = "✓" if f["status"] == "pass" else ("✗" if f["status"] == "fail" else "–")
        lines.append(
            f"| {f['rule_id']} | {f['name']} | {icon} {f['status'].upper()} | {f['severity']} |"
        )
    lines.append("")

    if fails:
        lines.append("## ✗ Failures (blocking)")
        for f in fails:
            lines.append(f"- **[{f['rule_id']}]** `{f.get('design','')[:12]}` — {f['message']}")
        lines.append("")

    if warns:
        lines.append("## ⚠ Warnings")
        for f in warns:
            lines.append(f"- **[{f['rule_id']}]** `{f.get('design','')[:12]}` — {f['message']}")
        lines.append("")

    lines += [
        "## CIS Benchmark Reference",
        "",
        "| Control | STIG ID | NIST 800-53 |",
        "|---------|---------|-------------|",
        "| Encryption at Rest | V-220917 | SC-28 |",
        "| Encryption in Transit | V-220918 | SC-8 |",
        "| Least Privilege IAM | V-220919 | AC-6 |",
        "| MFA Enabled | V-220920 | IA-2(1) |",
        "| Logging | V-220921 | AU-2, AU-12 |",
        "| Backup | V-220922 | CP-9 |",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Hardening Checker — IDC Step 2")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default=_CANVAS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = check_hardening(args.project_id)
        report_md = build_report(result, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fpath = _ARTIFACTS_DIR / f"hardening_report_{uid}.md"
        fpath.write_text(report_md, encoding="utf-8", newline="")

        fails = [f for f in result["findings"] if f["status"] == "fail" and f["severity"] == "fail"]
        output = {
            "status": "success" if not fails else "failed",
            "gate": "PASS" if not fails else "FAIL",
            "canvas": args.canvas,
            "findings": len(result["findings"]),
            "failures": len(fails),
            "stats": result["stats"],
            "artifacts": [
                {
                    "name": "Hardening Report",
                    "path": fpath.relative_to(_ROOT).as_posix(),
                    "type": "md",
                }
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if not fails else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
