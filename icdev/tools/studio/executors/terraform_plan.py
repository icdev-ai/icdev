"""Terraform Plan Executor — Shared Workflow Step.

Validates generated Terraform IaC using Docker (hashicorp/terraform:1.9).
Runs: fmt -check → init -backend=false → validate
Canvas-agnostic: reads artifacts from the 'Generate IaC' step of any canvas.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from tools.studio.executors._base import (  # noqa: E402
    artifacts_dir, resolve_canvas, get_iac_artifacts, filter_artifacts,
    docker_available, pull_image, docker_run,
    docker_aws_flags, aws_env, detect_mode,
    LOCALSTACK_PROVIDER_OVERRIDE, TFVARS_DEFAULTS,
    localstack_docker_endpoint,
)

_TF_IMAGE = "hashicorp/terraform:1.9"


def run(run_id: str, project_id: str, canvas: str = "") -> dict:
    canvas = resolve_canvas(run_id, canvas)
    out_dir = artifacts_dir(canvas)
    artifacts = get_iac_artifacts(run_id)
    tf_paths = filter_artifacts(artifacts, ["tf"])
    uid = uuid.uuid4().hex[:8]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings: list[dict] = []
    gate = "PASS"
    docker_used = False
    env = aws_env()
    mode = detect_mode(env)

    # Fallback: latest .tf files for this canvas
    if not tf_paths:
        tf_dir = out_dir / "terraform"
        if tf_dir.exists():
            tfs = sorted(tf_dir.glob("main_*.tf"), key=lambda p: p.stat().st_mtime, reverse=True)
            if tfs:
                tf_paths = [tfs[0]]
                vars_tf = tf_dir / "variables.tf"
                if vars_tf.exists():
                    tf_paths.append(vars_tf)

    if not tf_paths:
        return {"gate": "WARN", "canvas": canvas, "docker_used": False,
                "findings": [{"severity": "warn", "check": "no_tf_files",
                               "message": f"No Terraform files found for canvas '{canvas}'"}]}

    if not docker_available() or not pull_image(_TF_IMAGE):
        # Fallback: brace-count heuristic
        issues = []
        for f in tf_paths:
            text = f.read_text(encoding="utf-8")
            if text.count("{") != text.count("}"):
                issues.append(f"{f.name}: mismatched braces")
        ok = len(issues) == 0
        findings.append({"severity": "pass" if ok else "fail", "check": "hcl_syntax",
                          "message": f"HCL brace check: {'OK' if ok else '; '.join(issues)}"})
        findings.append({"severity": "info", "check": "docker_unavailable",
                          "message": "Docker not available — full terraform validate skipped"})
        if not ok:
            gate = "FAIL"
    else:
        docker_used = True
        env_flags = docker_aws_flags(env, mode)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for p in tf_paths:
                shutil.copy2(p, tmp_path / p.name)
            if mode in ("localstack", "sam"):
                raw_ep = env.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
                docker_ep = localstack_docker_endpoint(raw_ep)
                region = env.get("AWS_DEFAULT_REGION", "us-east-1")
                (tmp_path / "localstack_override.tf").write_text(
                    LOCALSTACK_PROVIDER_OVERRIDE.format(ep=docker_ep, region=region),
                    encoding="utf-8", newline="",
                )
            (tmp_path / "auto.tfvars").write_text(TFVARS_DEFAULTS, encoding="utf-8", newline="")

            rc, out, err = docker_run(_TF_IMAGE, tmp, env_flags, "terraform",
                                       "fmt", "-check", "-diff", "-no-color")
            findings.append({"severity": "warn" if rc != 0 else "pass",
                              "check": "terraform_fmt",
                              "message": ("Formatting issues:\n" + (out or err))[:300] if rc != 0 else "HCL formatting OK"})

            rc, out, err = docker_run(_TF_IMAGE, tmp, env_flags, "terraform",
                                       "init", "-backend=false", "-input=false", "-no-color",
                                       timeout=180)
            if rc != 0:
                findings.append({"severity": "warn", "check": "terraform_init",
                                  "message": f"Init failed (non-blocking): {err[:400]}"})
            else:
                rc, out, err = docker_run(_TF_IMAGE, tmp, env_flags, "terraform",
                                           "validate", "-json")
                if rc == 0:
                    try:
                        vdata = json.loads(out)
                        if vdata.get("valid"):
                            findings.append({"severity": "pass", "check": "terraform_validate",
                                              "message": "No errors"})
                        else:
                            for diag in vdata.get("diagnostics", []):
                                sev = "fail" if diag.get("severity") == "error" else "warn"
                                findings.append({"severity": sev, "check": "terraform_validate",
                                                  "message": diag.get("summary", "")})
                                if sev == "fail":
                                    gate = "FAIL"
                    except json.JSONDecodeError:
                        findings.append({"severity": "warn", "check": "terraform_validate",
                                          "message": f"Non-JSON output: {out[:200]}"})
                else:
                    findings.append({"severity": "fail", "check": "terraform_validate",
                                      "message": (err or out)[:400]})
                    gate = "FAIL"

    if any(f["severity"] == "fail" for f in findings):
        gate = "FAIL"

    lines = [
        f"# Terraform Plan Report — {canvas.upper()}",
        f"**Generated:** {ts}  ",
        f"**Canvas:** {canvas}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '✗ FAIL'}  ",
        f"**Docker:** {'Yes — ' + _TF_IMAGE if docker_used else 'No (fallback)'}  ",
        f"**Files checked:** {len(tf_paths)}",
        "",
    ]
    for grp, label in [
        ([f for f in findings if f["severity"] == "fail"], "✗ Failures"),
        ([f for f in findings if f["severity"] == "warn"], "⚠ Warnings"),
        ([f for f in findings if f["severity"] == "pass"], "✓ Passed"),
    ]:
        if grp:
            lines.append(f"## {label}")
            for f in grp:
                lines.append(f"- **[{f['check']}]** {f['message'][:200]}")
            lines.append("")

    report_path = out_dir / f"terraform_plan_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    return {
        "gate": gate,
        "canvas": canvas,
        "docker_used": docker_used,
        "tf_files_checked": len(tf_paths),
        "findings": findings,
        "report_path": report_path.relative_to(Path(__file__).resolve().parents[3]).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="Terraform Plan (shared executor)")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="", help="Canvas slug (auto-detected if omitted)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.run_id, args.project_id, args.canvas)
        gate = result["gate"]
        output = {
            "status": "success" if gate == "PASS" else "failed",
            "gate": gate,
            "canvas": result["canvas"],
            "docker_used": result["docker_used"],
            "tf_files_checked": result["tf_files_checked"],
            "findings": len(result["findings"]),
            "failures": sum(1 for f in result["findings"] if f["severity"] == "fail"),
            "artifacts": [{"name": "Terraform Plan Report",
                           "path": result["report_path"], "type": "md"}],
        }
        print(json.dumps(output))
        sys.exit(0 if gate == "PASS" else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
