"""Deployment Checker — AIMC Workflow Step 2.

Validates ML deployment readiness: model card, bias testing, SageMaker config.
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "aimc"

_DEFAULT_CHECKS = {
    "model_card_present": False,
    "bias_testing_done": False,
    "performance_benchmarks_met": True,
    "sagemaker_domain_configured": False,
    "ecr_repo_for_models": False,
    "model_monitoring_enabled": False,
    "data_capture_configured": False,
    "endpoint_autoscaling_configured": False,
    "p90_latency_ms": 250.0,
    "latency_sla_ms": 500.0,
}


def run_deployment_checks(project_id: str) -> dict:
    """Load deployment check state from DB; fall back to defaults if absent."""
    checks = dict(_DEFAULT_CHECKS)
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT check_key, check_value FROM aimc_deployment WHERE project_id = ? ORDER BY created_at DESC LIMIT 30",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    key, val = row[0], row[1]
                    numeric_keys = {"p90_latency_ms", "latency_sla_ms"}
                    bool_keys = set(checks.keys()) - numeric_keys
                    if key in bool_keys:
                        checks[key] = str(val).lower() in ("1", "true", "yes")
                    elif key in numeric_keys:
                        try:
                            checks[key] = float(val)
                        except (TypeError, ValueError):
                            pass
    except Exception:
        pass  # table may not exist — use defaults

    findings = []

    # Blocking: model card, bias testing
    if not checks.get("model_card_present", False):
        findings.append({"severity": "fail", "check": "model_card_present",
                         "message": "Model card not present — required before deployment"})
    if not checks.get("bias_testing_done", False):
        findings.append({"severity": "fail", "check": "bias_testing_done",
                         "message": "Bias testing not completed — required for deployment approval"})
    if not checks.get("performance_benchmarks_met", True):
        findings.append({"severity": "fail", "check": "performance_benchmarks_met",
                         "message": "Performance benchmarks not met — SLA thresholds violated"})

    # Warnings: SageMaker config
    if not checks.get("sagemaker_domain_configured", False):
        findings.append({"severity": "warn", "check": "sagemaker_domain_configured",
                         "message": "SageMaker domain not configured — required for managed inference"})
    if not checks.get("ecr_repo_for_models", False):
        findings.append({"severity": "warn", "check": "ecr_repo_for_models",
                         "message": "ECR repository for model images not configured"})
    if not checks.get("model_monitoring_enabled", False):
        findings.append({"severity": "warn", "check": "model_monitoring_enabled",
                         "message": "Model monitoring not enabled — drift/degradation will be undetected"})
    if not checks.get("data_capture_configured", False):
        findings.append({"severity": "warn", "check": "data_capture_configured",
                         "message": "Data capture not configured — inference logging disabled"})

    # Latency check
    p90 = checks.get("p90_latency_ms", 250.0)
    sla = checks.get("latency_sla_ms", 500.0)
    if p90 > sla:
        findings.append({"severity": "fail", "check": "latency_sla",
                         "message": f"P90 latency {p90}ms exceeds SLA of {sla}ms"})

    gate = "PASS" if not any(f["severity"] == "fail" for f in findings) else "FAIL"
    return {"findings": findings, "checks": checks, "gate": gate, "project_id": project_id}


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    checks = result["checks"]
    gate = result["gate"]
    project_id = result["project_id"]

    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]

    def yn(key):
        return "PASS" if checks.get(key) else "FAIL"

    lines = [
        "# ML Deployment Report — AIMC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Deployment Gate:** {'PASS' if gate == 'PASS' else 'FAIL'}",
        "",
        "## Model Readiness Checks",
        "| Check | Status |",
        "|-------|--------|",
        f"| Model Card Present | {yn('model_card_present')} |",
        f"| Bias Testing Done | {yn('bias_testing_done')} |",
        f"| Performance Benchmarks Met | {yn('performance_benchmarks_met')} |",
        f"| P90 Latency ({checks.get('p90_latency_ms')}ms) vs SLA ({checks.get('latency_sla_ms')}ms) | {'PASS' if checks.get('p90_latency_ms', 0) <= checks.get('latency_sla_ms', 500) else 'FAIL'} |",
        "",
        "## SageMaker & MLOps Infrastructure",
        "| Check | Status |",
        "|-------|--------|",
        f"| SageMaker Domain Configured | {yn('sagemaker_domain_configured')} |",
        f"| ECR Repo for Model Images | {yn('ecr_repo_for_models')} |",
        f"| Model Monitoring Enabled | {yn('model_monitoring_enabled')} |",
        f"| Data Capture Configured | {yn('data_capture_configured')} |",
        f"| Endpoint Autoscaling | {yn('endpoint_autoscaling_configured')} |",
        "",
    ]
    if fails:
        lines += ["## Failures (Blocking)"]
        for f in fails:
            lines.append(f"- FAIL [{f['check']}]: {f['message']}")
        lines.append("")
    if warns:
        lines += ["## Warnings"]
        for f in warns:
            lines.append(f"- WARN [{f['check']}]: {f['message']}")
        lines.append("")
    if not findings:
        lines += ["## All Deployment Checks Passed", "", "Model is ready for SageMaker deployment."]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Deployment Checker — AIMC Step 2")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="aimc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_deployment_checks(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"deployment_report_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8")

        fails = [f for f in result["findings"] if f["severity"] == "fail"]
        output = {
            "status": "success" if not fails else "failed",
            "gate": result["gate"],
            "findings": len(result["findings"]),
            "failures": len(fails),
            "artifacts": [
                {"name": "Deployment Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if not fails else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
