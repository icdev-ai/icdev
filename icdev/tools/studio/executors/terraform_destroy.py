"""Terraform Destroy — Shared Workflow Executor (canvas-agnostic).

Destroys infrastructure created by terraform_apply.py.
Finds the Terraform state file from a prior apply run (via DB or most-recent)
and runs `terraform destroy -auto-approve` inside Docker (hashicorp/terraform:1.9).

Modes (auto-detected from .env):
  aws        — destroys real AWS resources (irreversible — gate with HITL first)
  localstack — destroys LocalStack resources
  sam        — destroys SAM local resources
  dry_run    — no credentials → shows plan of what WOULD be destroyed (safe)

Canvas auto-detected from run artifacts; override with --canvas.
State file location: data/studio_artifacts/<canvas>/tfstate/state_<uid>.tfstate
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
    aws_env, detect_mode, docker_aws_flags, docker_available, pull_image,
    docker_run, localstack_docker_endpoint,
    LOCALSTACK_PROVIDER_OVERRIDE, TFVARS_DEFAULTS,
)

_TF_IMAGE = "hashicorp/terraform:1.9"


def _find_state_for_run(run_id: str, tfstate_dir: Path) -> Path | None:
    """Locate the state file written by the Terraform Apply step for this run."""
    if run_id:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT stdout FROM studio_workflow_run_steps "
                    "WHERE run_id = %s AND step_name = 'Terraform Apply'",
                    (run_id,),
                ).fetchone()
                if row and row["stdout"]:
                    arts = json.loads(row["stdout"]).get("artifacts", [])
                    for a in arts:
                        if a.get("type") == "tfstate":
                            p = _ROOT / a["path"]
                            if p.exists():
                                return p
            finally:
                conn.close()
        except Exception:
            pass

    # Fallback: most recent state file in this canvas's tfstate directory
    if tfstate_dir.exists():
        states = sorted(tfstate_dir.glob("state_*.tfstate"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        return states[0] if states else None
    return None


def run_destroy(run_id: str, project_id: str, canvas: str = "") -> dict:
    canvas = resolve_canvas(run_id, canvas)
    out_dir = artifacts_dir(canvas)
    tfstate_dir = out_dir / "tfstate"
    env = aws_env()
    mode = detect_mode(env)
    dry_run = (mode == "dry_run")

    findings: list[dict] = []
    gate = "PASS"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uid = uuid.uuid4().hex[:8]
    resources_destroyed: list[str] = []

    findings.append({"severity": "info", "check": "mode",
                      "message": f"Mode: {mode.upper()} — "
                                 + ("plan only (no credentials — showing what WOULD be destroyed)"
                                    if dry_run else
                                    f"destroying real {'LocalStack' if mode == 'localstack' else 'AWS'} resources")})

    state_file = _find_state_for_run(run_id, tfstate_dir)
    if not state_file:
        return {
            "gate": "WARN", "mode": mode, "canvas": canvas,
            "findings": [{"severity": "warn", "check": "no_state",
                           "message": f"No Terraform state file found for canvas '{canvas}'. "
                                      "Run Terraform Apply first, or supply --run-id of an applied run."}],
            "resources_destroyed": 0,
        }

    findings.append({"severity": "info", "check": "state_file",
                      "message": f"Using state: {state_file.name}"})

    # Locate the matching TF files
    iac_arts = get_iac_artifacts(run_id)
    tf_paths = filter_artifacts(iac_arts, ["tf"])

    if not tf_paths:
        # Derive state uid and look for corresponding TF files
        state_uid = state_file.stem.replace("state_", "")
        tf_dir = out_dir / "terraform"
        main_tf = tf_dir / f"main_{state_uid}.tf"
        if main_tf.exists():
            tf_paths = [main_tf]
            vars_tf = tf_dir / "variables.tf"
            if vars_tf.exists():
                tf_paths.append(vars_tf)

    if not tf_paths:
        findings.append({"severity": "warn", "check": "no_tf",
                          "message": "No matching TF files found — destroy may be incomplete without provider config"})

    if not docker_available() or not pull_image(_TF_IMAGE):
        findings.append({"severity": "fail", "check": "docker",
                          "message": "Docker unavailable — cannot run terraform destroy"})
        gate = "FAIL"
    else:
        docker_env = docker_aws_flags(env, mode)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            for p in tf_paths:
                shutil.copy2(p, tmp_path / p.name)

            # State file is REQUIRED for destroy
            shutil.copy2(state_file, tmp_path / "terraform.tfstate")
            findings.append({"severity": "info", "check": "state_loaded",
                              "message": f"State loaded from {state_file.name}"})

            if mode in ("localstack", "sam"):
                raw_ep = env.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
                docker_ep = localstack_docker_endpoint(raw_ep)
                region = env.get("AWS_DEFAULT_REGION", "us-east-1")
                (tmp_path / "localstack_override.tf").write_text(
                    LOCALSTACK_PROVIDER_OVERRIDE.format(ep=docker_ep, region=region),
                    encoding="utf-8", newline="",
                )
                findings.append({"severity": "info", "check": "localstack_override",
                                  "message": f"Provider overridden → {docker_ep}"})

            (tmp_path / "auto.tfvars").write_text(TFVARS_DEFAULTS, encoding="utf-8", newline="")

            rc, out, err = docker_run(_TF_IMAGE, tmp, docker_env, "terraform",
                                       "init", "-backend=false", "-input=false", "-no-color",
                                       timeout=180)
            if rc != 0:
                findings.append({"severity": "fail", "check": "terraform_init",
                                  "message": f"Init failed: {err[:400]}"})
                gate = "FAIL"
            else:
                findings.append({"severity": "pass", "check": "terraform_init",
                                  "message": "Init OK"})

                if dry_run:
                    rc, out, err = docker_run(_TF_IMAGE, tmp, docker_env, "terraform",
                                               "plan", "-destroy", "-input=false", "-no-color",
                                               timeout=300)
                    for line in (out or "").splitlines():
                        if "will be destroyed" in line or "destroy" in line.lower():
                            resources_destroyed.append(line.strip())
                    sev = "pass" if rc == 0 else "warn"
                    findings.append({"severity": sev, "check": "destroy_plan",
                                      "message": f"[dry-run] {len(resources_destroyed)} resource(s) would be destroyed"})
                else:
                    rc, out, err = docker_run(_TF_IMAGE, tmp, docker_env, "terraform",
                                               "destroy", "-auto-approve", "-input=false", "-no-color",
                                               timeout=600)
                    if rc == 0:
                        for line in (out or "").splitlines():
                            if "destroyed" in line.lower() or "Destroy complete" in line:
                                resources_destroyed.append(line.strip())

                        # Remove state file — environment is gone
                        state_file.unlink(missing_ok=True)
                        findings.append({"severity": "pass", "check": "terraform_destroy",
                                          "message": f"Destroy complete — {len(resources_destroyed)} resource(s) removed. State deleted."})
                    else:
                        findings.append({"severity": "fail", "check": "terraform_destroy",
                                          "message": (out + err).strip()[:600]})
                        gate = "FAIL"

    if any(f["severity"] == "fail" for f in findings):
        gate = "FAIL"

    lines = [
        f"# Terraform Destroy Report — {canvas.upper()}",
        f"**Generated:** {ts}  ",
        f"**Canvas:** {canvas}  ",
        f"**Project:** {project_id}  ",
        f"**Mode:** {mode.upper()}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '⚠ WARN' if gate == 'WARN' else '✗ FAIL'}  ",
        "",
    ]
    for grp, label in [
        ([f for f in findings if f["severity"] == "fail"], "✗ Failures"),
        ([f for f in findings if f["severity"] == "warn"], "⚠ Warnings"),
        ([f for f in findings if f["severity"] == "pass"], "✓ Passed"),
        ([f for f in findings if f["severity"] == "info"], "ℹ Info"),
    ]:
        if grp:
            lines.append(f"## {label}")
            for f in grp:
                lines.append(f"- **[{f['check']}]** {f['message'][:300]}")
            lines.append("")

    if resources_destroyed:
        action = "Would Destroy (dry-run)" if dry_run else "Destroyed Resources"
        lines += [f"## {action}", ""]
        for r in resources_destroyed[:30]:
            lines.append(f"- `{r}`")
        lines.append("")

    report_path = out_dir / f"destroy_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    return {
        "gate": gate, "mode": mode, "canvas": canvas,
        "findings": findings,
        "resources_destroyed": len(resources_destroyed),
        "report_path": report_path.relative_to(_ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="Terraform Destroy (shared executor)")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="", help="Canvas slug (auto-detected if omitted)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = run_destroy(args.run_id, args.project_id, args.canvas)
        gate = result["gate"]
        output = {
            "status": "success" if gate in ("PASS", "WARN") else "failed",
            "gate": gate, "canvas": result["canvas"], "mode": result["mode"],
            "resources_destroyed": result["resources_destroyed"],
            "findings": len(result["findings"]),
            "failures": sum(1 for f in result["findings"] if f["severity"] == "fail"),
            "artifacts": [{"name": "Terraform Destroy Report",
                           "path": result["report_path"], "type": "md"}],
        }
        print(json.dumps(output))
        sys.exit(0 if gate in ("PASS", "WARN") else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
