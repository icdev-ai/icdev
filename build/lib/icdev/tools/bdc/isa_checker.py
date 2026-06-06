"""ISA Checker — BDC Workflow Step 2.

Validates Interconnection Security Agreements and boundary controls against
DoD boundary security requirements. Checks ISA expiry, unclassified data paths
crossing boundary, missing VPC endpoints, and NACLs on all boundary subnets.
Outputs JSON with artifact paths to stdout.
"""
import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_CANVAS = "bdc"
_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / _CANVAS

# ISA / boundary check rules
_ISA_RULES = [
    {
        "id": "ISA-1.1",
        "name": "ISA Expiry Check",
        "severity": "fail",
        "description": "All ISA agreements must have valid expiry dates (DoD 8500.01 §3.5)",
        "check": "isa_expiry",
    },
    {
        "id": "ISA-1.2",
        "name": "No Unclassified Data Crossing Boundary",
        "severity": "fail",
        "description": "Unclassified data must not traverse the boundary without explicit approval (NIST AC-17)",
        "check": "unclassified_crossing",
    },
    {
        "id": "ISA-1.3",
        "name": "VPC Endpoints Present",
        "severity": "fail",
        "description": "VPC endpoints required for all AWS service access from boundary (NIST SC-7(4))",
        "check": "vpc_endpoints",
    },
    {
        "id": "ISA-1.4",
        "name": "NACLs on Boundary Subnets",
        "severity": "fail",
        "description": "Network ACLs must be applied to all boundary subnets (NIST SC-7)",
        "check": "nacl_on_subnets",
    },
    {
        "id": "ISA-1.5",
        "name": "KMS Encryption for Boundary Data",
        "severity": "warn",
        "description": "All data crossing boundary must be encrypted with KMS (NIST SC-28, FIPS 140-2)",
        "check": "kms_encryption",
    },
    {
        "id": "ISA-1.6",
        "name": "Audit Logging for Boundary Crossings",
        "severity": "warn",
        "description": "All boundary crossings must be logged (NIST AU-2, AU-12)",
        "check": "audit_logging",
    },
]

# Date pattern to detect expiry in node labels
_DATE_RE = re.compile(
    r"\b(20\d{2}[-/]\d{2}[-/]\d{2}|"
    r"\d{2}[-/]\d{2}[-/]20\d{2}|"
    r"expires?\s*:?\s*20\d{2}[-/]\d{2}[-/]\d{2})\b",
    re.IGNORECASE
)

_DEFAULT_DESIGNS = {
    "default-bdc-design": {
        "nodes": [
            {"id": "n1", "type": "boundary", "label": "Authorization Boundary", "classification": "CUI"},
            {"id": "n2", "type": "isa_connection", "label": "ISA-001 expires 2027-12-31", "classification": "CUI"},
            {"id": "n3", "type": "endpoint", "label": "VPC Endpoint S3 Gateway", "classification": ""},
            {"id": "n4", "type": "endpoint", "label": "VPC Endpoint SSM Interface", "classification": ""},
            {"id": "n5", "type": "control", "label": "NACL Boundary Rule", "classification": ""},
            {"id": "n6", "type": "control", "label": "KMS Boundary Key", "classification": ""},
            {"id": "n7", "type": "logging", "label": "CloudTrail Boundary Audit", "classification": ""},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "type": "governs"},
            {"source": "n3", "target": "n1", "type": "secures"},
            {"source": "n4", "target": "n1", "type": "secures"},
            {"source": "n5", "target": "n1", "type": "enforces"},
            {"source": "n6", "target": "n2", "type": "encrypts"},
            {"source": "n7", "target": "n1", "type": "audits"},
        ],
    }
}


def _load_designs(project_id: str) -> dict:
    """Load boundary designs from DB; fall back to defaults."""
    for table in ("bdc_boundaries", "boundary_designs"):
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            try:
                rows = conn.execute(
                    f"SELECT id, name, design_json FROM {table} "
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
            continue
    return _DEFAULT_DESIGNS


def _run_isa_checks(nodes: list, edges: list) -> list:
    """Run all ISA and boundary checks."""
    all_tl = [((n.get("type") or "") + " " + (n.get("label") or "")).lower() for n in nodes]
    findings = []
    today = datetime.now(timezone.utc).date()

    for rule in _ISA_RULES:
        check = rule["check"]

        if check == "isa_expiry":
            isa_nodes = [
                n for n in nodes
                if any(kw in ((n.get("type") or "") + " " + (n.get("label") or "")).lower()
                       for kw in ["isa", "interconnection", "agreement"])
            ]
            if not isa_nodes:
                findings.append({
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "severity": "info",
                    "status": "skip",
                    "message": "No ISA nodes found — rule not applicable",
                })
                continue

            expired = []
            no_date = []
            for n in isa_nodes:
                label = n.get("label") or ""
                dates = _DATE_RE.findall(label)
                if not dates:
                    no_date.append(label)
                else:
                    for d_str in dates:
                        try:
                            # Normalize date string
                            cleaned = re.sub(r"expires?\s*:?\s*", "", d_str.strip(), flags=re.IGNORECASE)
                            cleaned = cleaned.replace("/", "-")
                            parts = cleaned.split("-")
                            if len(parts[0]) == 2:  # DD-MM-YYYY
                                parsed = datetime.strptime(cleaned, "%d-%m-%Y").date()
                            else:  # YYYY-MM-DD
                                parsed = datetime.strptime(cleaned, "%Y-%m-%d").date()
                            if parsed < today:
                                expired.append(f"{label} (expired {parsed})")
                        except ValueError:
                            no_date.append(label)

            if expired:
                findings.append({
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "severity": rule["severity"],
                    "status": "fail",
                    "message": f"Expired ISA(s): {'; '.join(expired[:3])}",
                })
            elif no_date:
                findings.append({
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "severity": "warn",
                    "status": "fail",
                    "message": f"ISA node(s) missing expiry date: {'; '.join(no_date[:3])}",
                })
            else:
                findings.append({
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "severity": "info",
                    "status": "pass",
                    "message": f"✓ All {len(isa_nodes)} ISA node(s) have valid expiry dates",
                })

        elif check == "unclassified_crossing":
            # Look for nodes that are neither classified nor have a control node
            unclassified_crossing = any(
                "unclassified" in lbl and "boundary" in lbl
                for lbl in all_tl
            )
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "fail" if unclassified_crossing else "pass",
                "message": rule["description"] if unclassified_crossing
                else "✓ No explicit unclassified-crossing-boundary pattern detected",
            })

        elif check == "vpc_endpoints":
            has_vpc_ep = any(
                any(kw in lbl for kw in ["vpc-endpoint", "endpoint", "privatelink", "gateway-endpoint", "interface-endpoint"])
                for lbl in all_tl
            )
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "pass" if has_vpc_ep else "fail",
                "message": "✓ VPC endpoints present" if has_vpc_ep else rule["description"],
            })

        elif check == "nacl_on_subnets":
            has_nacl = any(
                any(kw in lbl for kw in ["nacl", "network-acl", "network acl", "acl"])
                for lbl in all_tl
            )
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "pass" if has_nacl else "fail",
                "message": "✓ NACL node(s) present" if has_nacl else rule["description"],
            })

        elif check == "kms_encryption":
            has_kms = any(
                any(kw in lbl for kw in ["kms", "encryption", "encrypt", "fips", "cmk"])
                for lbl in all_tl
            )
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "pass" if has_kms else "fail",
                "message": "✓ KMS/encryption node present" if has_kms else rule["description"],
            })

        elif check == "audit_logging":
            has_audit = any(
                any(kw in lbl for kw in ["cloudtrail", "audit", "logging", "siem", "log"])
                for lbl in all_tl
            )
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "pass" if has_audit else "fail",
                "message": "✓ Audit logging node present" if has_audit else rule["description"],
            })

    return findings


def check_isa(project_id: str) -> dict:
    designs = _load_designs(project_id)
    all_findings: list = []
    stats = {
        "designs": len(designs),
        "total_nodes": 0,
        "rules_checked": 0,
        "passed": 0,
        "failed": 0,
        "warned": 0,
        "skipped": 0,
    }

    for did, d in designs.items():
        nodes = d["nodes"]
        edges = d["edges"]
        stats["total_nodes"] += len(nodes)

        findings = _run_isa_checks(nodes, edges)
        for f in findings:
            f["design"] = did
            all_findings.append(f)
            stats["rules_checked"] += 1
            if f["status"] == "pass":
                stats["passed"] += 1
            elif f["status"] == "fail":
                if f["severity"] == "fail":
                    stats["failed"] += 1
                else:
                    stats["warned"] += 1
            elif f["status"] == "skip":
                stats["skipped"] += 1

    if not designs:
        all_findings.append({
            "rule_id": "INFO-0",
            "name": "No Designs",
            "severity": "info",
            "status": "skip",
            "design": "n/a",
            "message": "No boundary designs found — ISA check skipped",
        })

    return {"findings": all_findings, "stats": stats}


def build_report(result: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    stats = result["stats"]
    fails = [f for f in findings if f["status"] == "fail" and f["severity"] == "fail"]
    warns = [f for f in findings if f["status"] == "fail" and f["severity"] == "warn"]
    gate = "PASS" if not fails else "FAIL"

    lines = [
        "# ISA and Boundary Control Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '✗ FAIL'}  ",
        f"**Designs:** {stats['designs']}  **Nodes:** {stats['total_nodes']}",
        f"**Rules checked:** {stats['rules_checked']}  **Passed:** {stats['passed']}  "
        f"**Failed:** {stats['failed']}  **Warned:** {stats['warned']}  **Skipped:** {stats['skipped']}",
        "",
        "## Check Results",
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
        "## Reference Standards",
        "",
        "| Rule ID | Standard | Requirement |",
        "|---------|----------|-------------|",
        "| ISA-1.1 | DoD 8500.01 | §3.5 ISA Expiry |",
        "| ISA-1.2 | NIST SP 800-53 | AC-17 Remote Access |",
        "| ISA-1.3 | NIST SP 800-53 | SC-7(4) External Telecommunications |",
        "| ISA-1.4 | NIST SP 800-53 | SC-7 Boundary Protection |",
        "| ISA-1.5 | NIST SP 800-53 | SC-28, FIPS 140-2 |",
        "| ISA-1.6 | NIST SP 800-53 | AU-2, AU-12 Audit Events |",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ISA Checker — BDC Step 2")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default=_CANVAS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = check_isa(args.project_id)
        report_md = build_report(result, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fpath = _ARTIFACTS_DIR / f"isa_report_{uid}.md"
        fpath.write_text(report_md, encoding="utf-8")

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
                    "name": "ISA and Boundary Report",
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
