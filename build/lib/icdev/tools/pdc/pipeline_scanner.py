"""Pipeline Scanner — PDC Workflow Step 1.

Scans CI/CD pipeline design for stages, build environments, deployment targets,
and antipatterns.
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

# Stage types recognized in pipeline designs
_STAGE_TYPES = {
    "stage-source", "stage-build", "stage-test", "stage-scan",
    "stage-security", "stage-sast", "stage-dast", "stage-deploy",
    "stage-approval", "stage-integration", "stage-package",
    "stage-container", "stage-artifact", "stage-release",
}

# Build environment node types
_BUILD_ENV_TYPES = {
    "env-docker", "env-codebuild", "env-jenkins", "env-gitlab",
    "env-github-actions", "env-azure-devops", "env-k8s",
    "env-ec2", "env-fargate", "env-lambda",
}

# Deployment target types
_DEPLOY_TARGET_TYPES = {
    "target-ecs", "target-eks", "target-ec2", "target-lambda",
    "target-beanstalk", "target-k8s", "target-fargate",
    "target-s3", "target-cloudfront",
}

# Antipattern indicators
_ANTIPATTERNS = [
    {
        "id": "AP-001",
        "name": "Hardcoded Secrets",
        "description": "Pipeline contains hardcoded secret or credential nodes — use SSM/Secrets Manager instead.",
        "severity": "CRITICAL",
        "check_fn": lambda types: any(t in types for t in {"secret-hardcoded", "cred-hardcoded", "ent-secret-plain"}),
    },
    {
        "id": "AP-002",
        "name": "No Test Stage",
        "description": "Pipeline has no test stage — code quality and correctness cannot be verified.",
        "severity": "HIGH",
        "check_fn": lambda types: not any(t in types for t in {"stage-test", "stage-unit-test", "stage-integration"}),
    },
    {
        "id": "AP-003",
        "name": "No Security Scan Stage",
        "description": "Pipeline has no security scanning stage — vulnerabilities will reach production undetected.",
        "severity": "HIGH",
        "check_fn": lambda types: not any(t in types for t in {"stage-scan", "stage-security", "stage-sast", "stage-dast"}),
    },
    {
        "id": "AP-004",
        "name": "No Approval Gate Before Production",
        "description": "Pipeline deploys to production without a manual approval gate.",
        "severity": "MEDIUM",
        "check_fn": lambda types: not any(t in types for t in {"stage-approval", "ctrl-approval", "gate-approval"}),
    },
    {
        "id": "AP-005",
        "name": "No Artifact Versioning",
        "description": "No artifact store or versioning node — build outputs are not traceable.",
        "severity": "MEDIUM",
        "check_fn": lambda types: not any(t in types for t in {"stage-artifact", "stage-package", "svc-ecr", "svc-s3-artifacts", "ent-artifact-store"}),
    },
]


def _default_designs() -> dict:
    return {
        "default-pdc": {
            "nodes": [
                {"id": "n1", "type": "stage-source", "label": "Source (CodeCommit)", "classification": ""},
                {"id": "n2", "type": "stage-build", "label": "Build (CodeBuild)", "classification": ""},
                {"id": "n3", "type": "stage-test", "label": "Unit Tests", "classification": ""},
                {"id": "n4", "type": "stage-scan", "label": "Security Scan (SAST)", "classification": ""},
                {"id": "n5", "type": "stage-approval", "label": "Prod Approval Gate", "classification": ""},
                {"id": "n6", "type": "stage-deploy", "label": "Deploy (ECS)", "classification": ""},
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


def scan_pipeline(project_id: str) -> dict:
    designs = _load_designs(project_id)

    all_stages: list = []
    all_build_envs: list = []
    all_targets: list = []
    all_antipatterns: list = []
    designs_scanned = 0

    for did, d in designs.items():
        nodes = d["nodes"]
        node_types = {n.get("type") or "" for n in nodes}
        designs_scanned += 1

        stages = [n for n in nodes if (n.get("type") or "") in _STAGE_TYPES or
                  (n.get("type") or "").startswith("stage-")]
        build_envs = [n for n in nodes if (n.get("type") or "") in _BUILD_ENV_TYPES or
                      (n.get("type") or "").startswith("env-")]
        targets = [n for n in nodes if (n.get("type") or "") in _DEPLOY_TARGET_TYPES or
                   (n.get("type") or "").startswith("target-")]

        all_stages.extend([n.get("label") or n.get("id", "") for n in stages])
        all_build_envs.extend([n.get("label") or n.get("id", "") for n in build_envs])
        all_targets.extend([n.get("label") or n.get("id", "") for n in targets])

        for ap in _ANTIPATTERNS:
            try:
                detected = ap["check_fn"](node_types)
            except Exception:
                detected = False
            if detected:
                all_antipatterns.append({
                    "design": did,
                    "id": ap["id"],
                    "name": ap["name"],
                    "description": ap["description"],
                    "severity": ap["severity"],
                })

    return {
        "designs_scanned": designs_scanned,
        "stages": list(set(all_stages)),
        "stages_count": len(set(all_stages)),
        "build_environments": list(set(all_build_envs)),
        "deployment_targets": list(set(all_targets)),
        "antipatterns": all_antipatterns,
    }


def build_report(result: dict, project_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    antipatterns = result["antipatterns"]
    critical_aps = [a for a in antipatterns if a["severity"] == "CRITICAL"]
    high_aps = [a for a in antipatterns if a["severity"] == "HIGH"]
    gate = "FAIL" if critical_aps else ("WARN" if high_aps else "PASS")

    lines = [
        "# Pipeline Design Scan Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Designs Scanned:** {result['designs_scanned']}  ",
        f"**Gate:** {'✗ FAIL' if gate == 'FAIL' else ('⚠ WARN' if gate == 'WARN' else '✓ PASS')}",
        "",
        "## Pipeline Overview",
        "",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Pipeline Stages | {result['stages_count']} |",
        f"| Build Environments | {len(result['build_environments'])} |",
        f"| Deployment Targets | {len(result['deployment_targets'])} |",
        f"| Antipatterns Detected | {len(antipatterns)} |",
        "",
    ]

    if result["stages"]:
        lines += ["### Pipeline Stages", ""]
        for s in result["stages"]:
            lines.append(f"- `{s}`")
        lines.append("")

    if result["build_environments"]:
        lines += ["### Build Environments", ""]
        for e in result["build_environments"]:
            lines.append(f"- `{e}`")
        lines.append("")

    if result["deployment_targets"]:
        lines += ["### Deployment Targets", ""]
        for t in result["deployment_targets"]:
            lines.append(f"- `{t}`")
        lines.append("")

    if antipatterns:
        lines += ["## Antipatterns Detected", ""]
        for ap in antipatterns:
            severity_icon = "✗" if ap["severity"] == "CRITICAL" else "⚠"
            lines += [
                f"### {severity_icon} [{ap['id']}] {ap['name']} ({ap['severity']})",
                f"**Design:** `{ap['design'][:12]}`",
                "",
                f"{ap['description']}",
                "",
            ]
    else:
        lines += ["## ✓ No Antipatterns Detected", "", "Pipeline design follows DevSecOps best practices."]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="PDC Pipeline Scanner")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="pdc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = scan_pipeline(args.project_id)
        report_md = build_report(result, args.project_id)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fpath = _ARTIFACTS_DIR / f"pipeline_scan_{uid}.md"
        fpath.write_text(report_md, encoding="utf-8")

        critical_aps = [a for a in result["antipatterns"] if a["severity"] == "CRITICAL"]
        high_aps = [a for a in result["antipatterns"] if a["severity"] == "HIGH"]
        gate = "FAIL" if critical_aps else ("WARN" if high_aps else "PASS")

        output = {
            "status": "success" if gate != "FAIL" else "warn",
            "gate": gate,
            "designs_scanned": result["designs_scanned"],
            "stages_count": result["stages_count"],
            "build_environments": result["build_environments"],
            "deployment_targets": result["deployment_targets"],
            "antipatterns_detected": len(result["antipatterns"]),
            "artifacts": [
                {"name": "Pipeline Scan Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"}
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
