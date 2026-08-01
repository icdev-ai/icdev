# CUI // SP-CTI
"""Deployment Checker — AIMC Workflow Step 2.

Validates ML deployment readiness: model card, bias testing, SageMaker config.
Outputs JSON with artifact paths to stdout.

Reads the tenant-less ``aimc_deployment`` table via ``get_canvas_connection``
(RLS disabled) — matching the connection policy documented in ``db/init_db.py``.
On a read failure or an empty check set the checker returns an explicit
``no-data`` result instead of fabricating demo readiness values.
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

# Check keys and their types. Booleans default to "not proven" (False) and the
# latency numerics default to None (unmeasured) — populated ONLY from real rows.
_BOOL_CHECK_KEYS = [
    "model_card_present",
    "bias_testing_done",
    "performance_benchmarks_met",
    "sagemaker_domain_configured",
    "ecr_repo_for_models",
    "model_monitoring_enabled",
    "data_capture_configured",
    "endpoint_autoscaling_configured",
]
_NUMERIC_CHECK_KEYS = ["p90_latency_ms", "latency_sla_ms"]


def _load_deployment_rows(project_id: str):
    """Fetch raw deployment-check rows via the canvas (RLS-disabled) connection."""
    from tools.db.storage import get_canvas_connection
    with get_canvas_connection("AIMC_DB_URL") as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT check_key, check_value FROM aimc_deployment "
            "WHERE project_id = %s ORDER BY created_at DESC LIMIT 30",
            (project_id,),
        )
        return cur.fetchall()


def run_deployment_checks(project_id: str) -> dict:
    """Load deployment check state from DB via the canvas (RLS-disabled) connection.

    Returns a ``status: success`` result when real check rows exist, or a
    ``status: no-data`` result on read failure / empty check set.  Never returns
    fabricated demo values.
    """
    try:
        from tools.aimc.db.init_db import init_db as _init_aimc_db
        _init_aimc_db()
    except Exception:
        pass

    try:
        rows = _load_deployment_rows(project_id)
    except Exception as exc:
        return {
            "status": "no-data",
            "reason": "read-error",
            "detail": str(exc),
            "project_id": project_id,
        }

    if not rows:
        return {
            "status": "no-data",
            "reason": "empty-checks",
            "detail": "No aimc_deployment rows for this project — checks not recorded.",
            "project_id": project_id,
        }

    checks = {key: False for key in _BOOL_CHECK_KEYS}
    for key in _NUMERIC_CHECK_KEYS:
        checks[key] = None

    bool_keys = set(_BOOL_CHECK_KEYS)
    numeric_keys = set(_NUMERIC_CHECK_KEYS)
    for row in rows:
        key, val = row[0], row[1]
        if key in bool_keys:
            checks[key] = str(val).lower() in ("1", "true", "yes")
        elif key in numeric_keys:
            try:
                checks[key] = float(val)
            except (TypeError, ValueError):
                pass

    findings = []

    # Blocking: model card, bias testing
    if not checks.get("model_card_present", False):
        findings.append({"severity": "fail", "check": "model_card_present",
                         "message": "Model card not present — required before deployment"})
    if not checks.get("bias_testing_done", False):
        findings.append({"severity": "fail", "check": "bias_testing_done",
                         "message": "Bias testing not completed — required for deployment approval"})
    if not checks.get("performance_benchmarks_met", False):
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

    # Latency check — only when both values were actually recorded.
    p90 = checks.get("p90_latency_ms")
    sla = checks.get("latency_sla_ms")
    if p90 is not None and sla is not None and p90 > sla:
        findings.append({"severity": "fail", "check": "latency_sla",
                         "message": f"P90 latency {p90}ms exceeds SLA of {sla}ms"})

    gate = "PASS" if not any(f["severity"] == "fail" for f in findings) else "FAIL"
    return {"status": "success", "findings": findings, "checks": checks,
            "gate": gate, "project_id": project_id}


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    project_id = result.get("project_id", "unknown")

    if result.get("status") == "no-data":
        return "\n".join([
            "# ML Deployment Report — AIMC",
            f"**Generated:** {ts}  ",
            f"**Project:** {project_id}  ",
            "**Deployment Gate:** N/A (no checks recorded)",
            "",
            "## No Data",
            "",
            f"No ML deployment-readiness checks are recorded for project "
            f"`{project_id}` (reason: {result.get('reason', 'unknown')}).",
            "",
            "No deployment gate decision can be produced until check state is "
            "written to `aimc_deployment`. This report intentionally reports no "
            "data rather than placeholder pass/fail values.",
        ])

    findings = result["findings"]
    checks = result["checks"]
    gate = result["gate"]

    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]

    def yn(key):
        return "PASS" if checks.get(key) else "FAIL"

    p90 = checks.get("p90_latency_ms")
    sla = checks.get("latency_sla_ms")
    if p90 is not None and sla is not None:
        latency_status = "PASS" if p90 <= sla else "FAIL"
        latency_label = f"P90 Latency ({p90}ms) vs SLA ({sla}ms)"
    else:
        latency_status = "N/A"
        latency_label = "P90 Latency vs SLA (not measured)"

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
        f"| {latency_label} | {latency_status} |",
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

        artifacts = [
            {"name": "Deployment Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
        ]

        if result.get("status") == "no-data":
            output = {
                "status": "no-data",
                "reason": result.get("reason"),
                "project_id": result.get("project_id"),
                "artifacts": artifacts,
            }
            print(json.dumps(output))
            sys.exit(0)

        fails = [f for f in result["findings"] if f["severity"] == "fail"]
        output = {
            "status": "success" if not fails else "failed",
            "gate": result["gate"],
            "findings": len(result["findings"]),
            "failures": len(fails),
            "artifacts": artifacts,
        }
        print(json.dumps(output))
        sys.exit(0 if not fails else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
