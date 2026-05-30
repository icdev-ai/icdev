"""Antipattern Checker — PDC Workflow Step 2.

Validates pipeline design against DevSecOps best practices.
Checks for SAST, DAST, dependency scanning, container scanning, secrets detection,
and approval gates.
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "pdc"

# DevSecOps best practice checks
_DEVSECOPS_CHECKS = [
    {
        "id": "DSO-001",
        "name": "SAST Stage Present",
        "description": "Static Application Security Testing (SAST) must be integrated into the pipeline.",
        "category": "Security Scanning",
        "severity": "HIGH",
        "control": "NIST SA-11, SI-10",
        "check_fn": lambda types: any(t in types for t in {"stage-sast", "stage-scan", "stage-security", "svc-sonarqube", "svc-checkmarx", "svc-bandit"}),
    },
    {
        "id": "DSO-002",
        "name": "DAST Stage Present",
        "description": "Dynamic Application Security Testing (DAST) must test the running application.",
        "category": "Security Scanning",
        "severity": "HIGH",
        "control": "NIST SA-11(1)",
        "check_fn": lambda types: any(t in types for t in {"stage-dast", "svc-zap", "svc-burp", "svc-owasp-zap"}),
    },
    {
        "id": "DSO-003",
        "name": "Dependency Scanning Present",
        "description": "Software Composition Analysis (SCA) must scan third-party dependencies for CVEs.",
        "category": "Security Scanning",
        "severity": "HIGH",
        "control": "NIST SA-12, SR-3",
        "check_fn": lambda types: any(t in types for t in {"stage-sca", "stage-dependency-scan", "svc-snyk", "svc-dependabot", "svc-grype"}),
    },
    {
        "id": "DSO-004",
        "name": "Container Scanning Present",
        "description": "Container images must be scanned for vulnerabilities before deployment.",
        "category": "Security Scanning",
        "severity": "HIGH",
        "control": "NIST CM-7, SI-2",
        "check_fn": lambda types: any(t in types for t in {"stage-container-scan", "svc-trivy", "svc-clair", "svc-ecr-scan", "stage-image-scan"}),
    },
    {
        "id": "DSO-005",
        "name": "Secrets Detection Present",
        "description": "Secrets detection must prevent credentials from being committed to source control.",
        "category": "Security Scanning",
        "severity": "HIGH",
        "control": "NIST IA-5, SC-28",
        "check_fn": lambda types: any(t in types for t in {"stage-secrets-scan", "svc-gitleaks", "svc-trufflehog", "svc-detect-secrets"}),
    },
    {
        "id": "DSO-006",
        "name": "Approval Gate Before Production",
        "description": "A manual approval gate must exist before any production deployment.",
        "category": "Change Control",
        "severity": "HIGH",
        "control": "NIST CM-3, CM-4",
        "check_fn": lambda types: any(t in types for t in {"stage-approval", "ctrl-approval", "gate-approval", "gate-prod-approval"}),
    },
    {
        "id": "DSO-007",
        "name": "Unit Test Stage Present",
        "description": "Unit tests must be executed as part of every build.",
        "category": "Quality Assurance",
        "severity": "MEDIUM",
        "control": "NIST SA-11(1)",
        "check_fn": lambda types: any(t in types for t in {"stage-test", "stage-unit-test", "stage-integration"}),
    },
    {
        "id": "DSO-008",
        "name": "Artifact Storage Configured",
        "description": "Build artifacts must be stored in a versioned artifact repository.",
        "category": "Traceability",
        "severity": "MEDIUM",
        "control": "NIST CM-3, AU-9",
        "check_fn": lambda types: any(t in types for t in {"stage-artifact", "stage-package", "svc-ecr", "svc-s3-artifacts", "ent-artifact-store", "stage-container"}),
    },
    {
        "id": "DSO-009",
        "name": "SBOM Generation Present",
        "description": "Software Bill of Materials (SBOM) must be generated for supply chain compliance.",
        "category": "Supply Chain",
        "severity": "MEDIUM",
        "control": "NIST SR-3, SR-4",
        "check_fn": lambda types: any(t in types for t in {"stage-sbom", "svc-syft", "svc-cyclonedx", "ctrl-sbom"}),
    },
    {
        "id": "DSO-010",
        "name": "Infrastructure as Code Scanning",
        "description": "IaC templates must be scanned for misconfigurations before apply.",
        "category": "Security Scanning",
        "severity": "MEDIUM",
        "control": "NIST CM-7, SI-10",
        "check_fn": lambda types: any(t in types for t in {"stage-iac-scan", "svc-checkov", "svc-tfsec", "svc-terrascan"}),
    },
]


def _default_designs() -> dict:
    return {
        "default-pdc": {
            "nodes": [
                {"id": "n1", "type": "stage-source", "label": "Source Stage", "classification": ""},
                {"id": "n2", "type": "stage-build", "label": "Build Stage", "classification": ""},
                {"id": "n3", "type": "stage-sast", "label": "SAST Scan", "classification": ""},
                {"id": "n4", "type": "stage-test", "label": "Unit Tests", "classification": ""},
                {"id": "n5", "type": "stage-approval", "label": "Prod Approval", "classification": ""},
                {"id": "n6", "type": "stage-deploy", "label": "Deploy Stage", "classification": ""},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "type": "flow", "label": ""},
                {"source": "n2", "target": "n3", "type": "flow", "label": ""},
                {"source": "n3", "target": "n4", "type": "flow", "label": ""},
                {"source": "n4", "target": "n5", "type": "flow", "label": ""},
                {"source": "n5", "target": "n6", "type": "flow", "label": ""},
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
                    "SELECT id, name, graph_json FROM pdc_designs WHERE id=?",
                    (project_id,),
                ).fetchall()
            else:
                try:
                    rows = conn.execute(
                        "SELECT id, name, graph_json FROM pdc_designs ORDER BY updated_at DESC LIMIT 20"
                    ).fetchall()
                except Exception:
                    rows = conn.execute(
                        "SELECT id, name, graph_json FROM pipeline_designs ORDER BY updated_at DESC LIMIT 20"
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


def check_antipatterns(project_id: str) -> dict:
    designs = _load_designs(project_id)

    all_findings: list = []
    all_passed: list = []
    stats = {
        "designs": len(designs),
        "checks_total": len(_DEVSECOPS_CHECKS),
        "checks_passed": 0,
        "checks_failed": 0,
        "high_findings": 0,
        "medium_findings": 0,
    }

    for did, d in designs.items():
        nodes = d["nodes"]
        node_types = {n.get("type") or "" for n in nodes}

        for check in _DEVSECOPS_CHECKS:
            try:
                passed = check["check_fn"](node_types)
            except Exception:
                passed = False
            if passed:
                all_passed.append({
                    "design": did,
                    "id": check["id"],
                    "name": check["name"],
                    "category": check["category"],
                })
                stats["checks_passed"] += 1
            else:
                all_findings.append({
                    "design": did,
                    "id": check["id"],
                    "name": check["name"],
                    "description": check["description"],
                    "category": check["category"],
                    "severity": check["severity"],
                    "control": check["control"],
                })
                stats["checks_failed"] += 1
                if check["severity"] == "HIGH":
                    stats["high_findings"] += 1
                else:
                    stats["medium_findings"] += 1

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
    gate = "FAIL" if high else ("WARN" if medium else "PASS")

    lines = [
        "# DevSecOps Antipattern Check Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✗ FAIL' if gate == 'FAIL' else ('⚠ WARN' if gate == 'WARN' else '✓ PASS')}",
        "",
        "## Summary",
        "",
        "| Category | Count |",
        "|----------|-------|",
        f"| Checks Total | {stats['checks_total']} |",
        f"| Checks Passed | {stats['checks_passed']} |",
        f"| Checks Failed | {stats['checks_failed']} |",
        f"| High Findings | {stats['high_findings']} |",
        f"| Medium Findings | {stats['medium_findings']} |",
        "",
    ]

    if high:
        lines += ["## High Severity Findings (Blocking)", ""]
        for f in high:
            lines += [
                f"### [{f['id']}] {f['name']}",
                f"**Category:** {f['category']}  **Control:** `{f['control']}`  **Design:** `{f['design'][:12]}`",
                "",
                f"{f['description']}",
                "",
            ]

    if medium:
        lines += ["## Medium Severity Findings", ""]
        for f in medium:
            lines += [
                f"### [{f['id']}] {f['name']}",
                f"**Category:** {f['category']}  **Control:** `{f['control']}`  **Design:** `{f['design'][:12]}`",
                "",
                f"{f['description']}",
                "",
            ]

    if result["passed_checks"]:
        lines += ["## Passed Checks", ""]
        # Group by category
        by_cat: dict = {}
        for p in result["passed_checks"]:
            by_cat.setdefault(p["category"], []).append(p)
        for cat, checks in sorted(by_cat.items()):
            lines.append(f"**{cat}:**")
            for c in checks:
                lines.append(f"- ✓ [{c['id']}] {c['name']}")
        lines.append("")

    if not findings:
        lines += [
            "## ✓ All DevSecOps Checks Passed",
            "",
            "Pipeline design meets all DevSecOps best practices.",
            "Proceed to IaC generation (Step 3).",
        ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="PDC Antipattern Checker")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="pdc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = check_antipatterns(args.project_id)
        report_md = build_report(result, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fpath = _ARTIFACTS_DIR / f"antipattern_report_{uid}.md"
        fpath.write_text(report_md, encoding="utf-8")

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
                {"name": "Antipattern Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"}
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if gate != "FAIL" else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
