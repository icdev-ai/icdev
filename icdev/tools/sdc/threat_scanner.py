"""Threat Scanner — SDC Workflow Step 1.

Scans security design for STRIDE threats and missing security controls.
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

# STRIDE threat categories
_STRIDE_THREATS = {
    "Spoofing": "Identity spoofing — attacker impersonates a legitimate user or system",
    "Tampering": "Data tampering — unauthorized modification of data in transit or at rest",
    "Repudiation": "Action repudiation — users deny performing actions without audit trail",
    "Information Disclosure": "Data exposure — sensitive data leaked to unauthorized parties",
    "Denial of Service": "Availability attack — resource exhaustion or service disruption",
    "Elevation of Privilege": "Privilege escalation — attacker gains elevated permissions",
}

# Required security controls
_REQUIRED_CONTROLS = {
    "WAF": ["waf", "web-application-firewall", "ctrl-waf"],
    "GuardDuty": ["guardduty", "guard-duty", "ctrl-guardduty", "ctrl-threat-detection"],
    "CloudTrail": ["cloudtrail", "cloud-trail", "ctrl-audit-log", "ctrl-cloudtrail"],
    "KMS": ["kms", "ctrl-kms", "ctrl-encryption", "ctrl-cmk", "ctrl-fips"],
}

# Attack surface node types
_ATTACK_SURFACE_TYPES = {
    "bnd-internet", "bnd-public", "flow-api", "flow-http",
    "bnd-dmz", "ent-public", "flow-public", "svc-alb", "svc-waf",
    "ent-user", "ent-external", "bnd",
}


def _default_designs() -> dict:
    """Return a default design when DB is empty."""
    return {
        "default-sdc": {
            "nodes": [
                {"id": "n1", "type": "bnd-internet", "label": "Internet Boundary", "classification": ""},
                {"id": "n2", "type": "flow-api", "label": "API Gateway", "classification": ""},
                {"id": "n3", "type": "ent-pii", "label": "User PII Store", "classification": "CUI"},
                {"id": "n4", "type": "ctrl-kms", "label": "KMS Encryption Key", "classification": ""},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "type": "flow", "label": ""},
                {"source": "n2", "target": "n3", "type": "data-flow", "label": ""},
                {"source": "n4", "target": "n3", "type": "encrypts", "label": ""},
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


def _detect_missing_controls(node_types: set) -> list:
    missing = []
    node_types_lower = {t.lower() for t in node_types}
    for control_name, aliases in _REQUIRED_CONTROLS.items():
        found = any(alias in node_types_lower for alias in aliases)
        if not found:
            missing.append(control_name)
    return missing


def _detect_attack_surface(nodes: list) -> list:
    surface = []
    for n in nodes:
        ntype = (n.get("type") or "").lower()
        label = n.get("label") or n.get("id", "")
        if any(ntype == t or ntype.startswith(t.rstrip("*")) for t in _ATTACK_SURFACE_TYPES):
            surface.append(label)
        elif "public" in ntype or "internet" in ntype or "external" in ntype:
            surface.append(label)
    return surface


def scan_threats(project_id: str) -> dict:
    designs = _load_designs(project_id)

    all_threats: list = []
    all_surface: list = []
    all_missing: list = []
    designs_scanned = 0

    for did, d in designs.items():
        nodes = d["nodes"]
        node_types = {n.get("type") or "" for n in nodes}
        designs_scanned += 1

        attack_surface = _detect_attack_surface(nodes)
        missing_controls = _detect_missing_controls(node_types)

        # STRIDE analysis
        for threat, description in _STRIDE_THREATS.items():
            severity = "HIGH"
            mitigated = False

            if threat == "Spoofing":
                mitigated = any(t in node_types for t in {"ctrl-mfa", "ctrl-iam", "ctrl-authn", "ctrl-rbac"})
            elif threat == "Tampering":
                mitigated = any(t in node_types for t in {"ctrl-kms", "ctrl-encryption", "ctrl-integrity", "ctrl-cmk"})
            elif threat == "Repudiation":
                mitigated = any(t in node_types for t in {"ctrl-audit-log", "ctrl-cloudtrail", "ctrl-logging"})
                if mitigated:
                    severity = "LOW"
            elif threat == "Information Disclosure":
                mitigated = any(t in node_types for t in {"ctrl-kms", "ctrl-encryption", "ctrl-waf"})
            elif threat == "Denial of Service":
                mitigated = any(t in node_types for t in {"ctrl-waf", "svc-waf", "ctrl-shield", "ctrl-rate-limit"})
                severity = "MEDIUM"
            elif threat == "Elevation of Privilege":
                mitigated = any(t in node_types for t in {"ctrl-rbac", "ctrl-iam", "ctrl-least-privilege"})

            all_threats.append({
                "design": did,
                "threat": threat,
                "description": description,
                "severity": severity,
                "mitigated": mitigated,
            })

        all_surface.extend(attack_surface)
        all_missing.extend(missing_controls)

    return {
        "designs": designs,
        "designs_scanned": designs_scanned,
        "threats": all_threats,
        "attack_surface_nodes": list(set(all_surface)),
        "missing_controls": list(set(all_missing)),
    }


def build_report(result: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    threats = result["threats"]
    surface = result["attack_surface_nodes"]
    missing = result["missing_controls"]

    unmitigated = [t for t in threats if not t["mitigated"]]
    high_threats = [t for t in unmitigated if t["severity"] == "HIGH"]
    gate = "FAIL" if high_threats else ("WARN" if unmitigated else "PASS")

    lines = [
        "# Threat Scan Report (STRIDE)",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Designs Scanned:** {result['designs_scanned']}  ",
        f"**Gate:** {'✗ FAIL' if gate == 'FAIL' else ('⚠ WARN' if gate == 'WARN' else '✓ PASS')}",
        "",
        "## STRIDE Threat Assessment",
        "",
        "| Threat | Severity | Mitigated | Design |",
        "|--------|----------|-----------|--------|",
    ]

    for t in threats:
        status = "✓ Yes" if t["mitigated"] else "✗ No"
        lines.append(
            f"| **{t['threat']}** | {t['severity']} | {status} | `{t['design'][:12]}` |"
        )

    lines += ["", "### Threat Descriptions", ""]
    for threat, desc in _STRIDE_THREATS.items():
        lines.append(f"- **{threat}:** {desc}")

    lines += [
        "",
        "## Attack Surface Nodes",
        "",
    ]
    if surface:
        for node in surface:
            lines.append(f"- `{node}`")
    else:
        lines.append("- No public-facing attack surface nodes detected")

    lines += [
        "",
        "## Missing Security Controls",
        "",
    ]
    if missing:
        for ctrl in missing:
            lines.append(f"- ✗ **{ctrl}** — not present in design")
    else:
        lines.append("- ✓ All required security controls are present")

    lines += [
        "",
        "## Recommendations",
        "",
    ]
    if missing:
        lines.append("Add the following controls to the SDC canvas:")
        for ctrl in missing:
            if ctrl == "WAF":
                lines.append("  - Add `ctrl-waf` node (AWS WAF) to protect internet-facing APIs")
            elif ctrl == "GuardDuty":
                lines.append("  - Add `ctrl-guardduty` node (AWS GuardDuty) for threat detection")
            elif ctrl == "CloudTrail":
                lines.append("  - Add `ctrl-audit-log` node (AWS CloudTrail) for audit logging")
            elif ctrl == "KMS":
                lines.append("  - Add `ctrl-kms` node (AWS KMS) for data encryption")
    if not missing and not unmitigated:
        lines.append("- Design meets baseline security control requirements")
        lines.append("- Proceed to STIG validation (Step 2)")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="SDC Threat Scanner")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="sdc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = scan_threats(args.project_id)
        report_md = build_report(result, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"threat_scan_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        unmitigated = [t for t in result["threats"] if not t["mitigated"]]
        high = [t for t in unmitigated if t["severity"] == "HIGH"]
        gate = "FAIL" if high else ("WARN" if unmitigated else "PASS")

        output = {
            "status": "success" if gate != "FAIL" else "warn",
            "gate": gate,
            "designs_scanned": result["designs_scanned"],
            "threats_found": len(result["threats"]),
            "unmitigated": len(unmitigated),
            "attack_surface_nodes": result["attack_surface_nodes"],
            "missing_controls": result["missing_controls"],
            "artifacts": [
                {"name": "Threat Scan Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"}
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
