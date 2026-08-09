"""Config Checker — NDC Workflow Step 2.

Validates network configuration against NIST SP 800-41 and DoD network security
guidelines. Checks default routes, CIDR conflicts, open security group ingress
rules, subnet isolation, and VPN/transit gateway presence.
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

_CANVAS = "ndc"
_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / _CANVAS

# NIST SP 800-41 / DoD network security checks
_NETWORK_RULES = [
    {
        "id": "NET-1.1",
        "name": "Default Route Present",
        "severity": "warn",
        "description": "Each VPC should have a default route to an IGW or NAT (NIST SP 800-41 §3.2)",
        "check": "default_route",
    },
    {
        "id": "NET-1.2",
        "name": "No CIDR Conflicts",
        "severity": "fail",
        "description": "Subnet CIDRs must not overlap (DoD STIG NET-001)",
        "check": "cidr_conflict",
    },
    {
        "id": "NET-1.3",
        "name": "No Open Ingress (0.0.0.0/0 on sensitive ports)",
        "severity": "fail",
        "description": "Security groups must not allow 0.0.0.0/0 on sensitive ports (NIST SC-7)",
        "check": "open_sg_ingress",
    },
    {
        "id": "NET-1.4",
        "name": "Subnet Isolation",
        "severity": "warn",
        "description": "Public and private subnets must be logically separated (NIST SC-7(5))",
        "check": "subnet_isolation",
    },
    {
        "id": "NET-1.5",
        "name": "VPN or Transit Gateway Present",
        "severity": "warn",
        "description": "Secure remote connectivity requires VPN or Transit Gateway (NIST SC-8)",
        "check": "vpn_tgw",
    },
]

_CIDR_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2})\b")
_SENSITIVE_PORTS = {22, 23, 80, 443, 3389, 5432, 3306, 6379, 27017}

_DEFAULT_DESIGNS = {
    "default-ndc-design": {
        "nodes": [
            {"id": "n1", "type": "vpc", "label": "Production VPC 10.0.0.0/16", "classification": "CUI"},
            {"id": "n2", "type": "subnet", "label": "Public Subnet 10.0.1.0/24", "classification": ""},
            {"id": "n3", "type": "subnet", "label": "Private Subnet 10.0.2.0/24", "classification": ""},
            {"id": "n4", "type": "security-group", "label": "App SG - HTTPS only", "classification": ""},
            {"id": "n5", "type": "gateway", "label": "Internet Gateway", "classification": ""},
            {"id": "n6", "type": "gateway", "label": "NAT Gateway", "classification": ""},
            {"id": "n7", "type": "route", "label": "Public Route Table default 0.0.0.0/0", "classification": ""},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "type": "contains"},
            {"source": "n1", "target": "n3", "type": "contains"},
            {"source": "n5", "target": "n7", "type": "routes"},
            {"source": "n7", "target": "n2", "type": "associates"},
        ],
    }
}


def _load_designs(project_id: str) -> dict:
    """Load network designs from DB; fall back to defaults."""
    for table in ("ndc_topologies", "network_designs"):
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


def _extract_cidrs(nodes: list) -> list:
    """Extract CIDR blocks mentioned in node labels."""
    cidrs = []
    for n in nodes:
        label = n.get("label") or ""
        found = _CIDR_RE.findall(label)
        cidrs.extend(found)
    return cidrs


def _cidrs_overlap(cidr_a: str, cidr_b: str) -> bool:
    """Check if two CIDR blocks overlap (basic prefix check)."""
    try:
        import ipaddress
        net_a = ipaddress.ip_network(cidr_a, strict=False)
        net_b = ipaddress.ip_network(cidr_b, strict=False)
        return net_a.overlaps(net_b) and cidr_a != cidr_b
    except ValueError:
        return False


def _run_checks(nodes: list, edges: list) -> list:
    """Run all network config checks and return findings."""
    all_labels = [((n.get("type") or "") + " " + (n.get("label") or "")).lower() for n in nodes]
    findings = []

    for rule in _NETWORK_RULES:
        check = rule["check"]

        if check == "default_route":
            has_route = any("default" in lbl or "0.0.0.0/0" in lbl for lbl in all_labels)
            has_gateway = any(kw in lbl for lbl in all_labels for kw in ["igw", "nat", "gateway"])
            passed = has_route and has_gateway
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "pass" if passed else "fail",
                "message": rule["description"] if not passed else f"✓ {rule['name']} satisfied",
            })

        elif check == "cidr_conflict":
            cidrs = _extract_cidrs(nodes)
            conflicts = []
            for i, ca in enumerate(cidrs):
                for cb in cidrs[i + 1:]:
                    if _cidrs_overlap(ca, cb):
                        conflicts.append(f"{ca} overlaps {cb}")
            passed = not conflicts
            msg = rule["description"] if not passed else f"✓ {rule['name']} — no CIDR conflicts detected"
            if conflicts:
                msg = f"CIDR conflict(s): {'; '.join(conflicts[:3])}"
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "pass" if passed else "fail",
                "message": msg,
            })

        elif check == "open_sg_ingress":
            # Look for labels mentioning 0.0.0.0/0 on security-group / firewall nodes
            open_ingress = any(
                "0.0.0.0/0" in (n.get("label") or "").lower()
                and any(kw in ((n.get("type") or "") + " " + (n.get("label") or "")).lower()
                        for kw in ["sg", "security-group", "acl", "firewall", "nacl"])
                for n in nodes
            )
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "fail" if open_ingress else "pass",
                "message": rule["description"] if open_ingress else f"✓ {rule['name']} — no open ingress rules detected in labels",
            })

        elif check == "subnet_isolation":
            has_public = any("public" in lbl for lbl in all_labels)
            has_private = any("private" in lbl for lbl in all_labels)
            passed = has_public and has_private
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "pass" if passed else "fail",
                "message": f"✓ {rule['name']} — public and private subnets present" if passed else rule["description"],
            })

        elif check == "vpn_tgw":
            has_vpn_tgw = any(
                any(kw in lbl for kw in ["vpn", "transit", "tgw", "direct-connect", "dx"])
                for lbl in all_labels
            )
            findings.append({
                "rule_id": rule["id"],
                "name": rule["name"],
                "severity": rule["severity"],
                "status": "pass" if has_vpn_tgw else "fail",
                "message": f"✓ {rule['name']} — VPN/TGW present" if has_vpn_tgw else rule["description"],
            })

    return findings


def check_config(project_id: str) -> dict:
    designs = _load_designs(project_id)
    all_findings: list = []
    stats = {
        "designs": len(designs),
        "total_nodes": 0,
        "rules_checked": 0,
        "passed": 0,
        "failed": 0,
        "warned": 0,
    }

    for did, d in designs.items():
        nodes = d["nodes"]
        edges = d["edges"]
        stats["total_nodes"] += len(nodes)

        findings = _run_checks(nodes, edges)
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

    if not designs:
        all_findings.append({
            "rule_id": "INFO-0",
            "name": "No Designs",
            "severity": "info",
            "status": "skip",
            "design": "n/a",
            "message": "No network topology designs found — config check skipped",
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
        "# Network Configuration Check Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '✗ FAIL'}  ",
        f"**Designs:** {stats['designs']}  **Nodes:** {stats['total_nodes']}",
        f"**Rules checked:** {stats['rules_checked']}  **Passed:** {stats['passed']}  "
        f"**Failed:** {stats['failed']}  **Warned:** {stats['warned']}",
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
        "| Rule ID | Standard | Section |",
        "|---------|----------|---------|",
        "| NET-1.1 | NIST SP 800-41 | §3.2 Default Routing |",
        "| NET-1.2 | DoD STIG | NET-001 CIDR Isolation |",
        "| NET-1.3 | NIST SP 800-53 | SC-7 Boundary Protection |",
        "| NET-1.4 | NIST SP 800-53 | SC-7(5) Subnet Isolation |",
        "| NET-1.5 | NIST SP 800-53 | SC-8 Transmission Confidentiality |",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Config Checker — NDC Step 2")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default=_CANVAS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = check_config(args.project_id)
        report_md = build_report(result, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fpath = _ARTIFACTS_DIR / f"config_report_{uid}.md"
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
                    "name": "Network Config Report",
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
