"""Terraform Apply — Shared Workflow Executor (canvas-agnostic).

Applies Terraform IaC using Docker (hashicorp/terraform:1.9).

Modes (auto-detected from .env):
  aws        — real AWS  (AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_DEFAULT_REGION)
  floci      — local AWS emulator (FLOCI_ENABLED=true; FLOCI_ENDPOINT, default http://localhost:4566)
  sam        — SAM local  (AWS_SAM_LOCAL=true)
  dry_run    — no credentials → terraform plan only (safe, no charges)

Canvas auto-detected from run artifacts; override with --canvas.
State saved to: data/studio_artifacts/<canvas>/tfstate/<uid>.tfstate
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
    docker_run, localstack_docker_endpoint, is_emulated,
    LOCALSTACK_PROVIDER_OVERRIDE, TFVARS_DEFAULTS,
)
from tools.cloud import emulator  # noqa: E402

_TF_IMAGE = "hashicorp/terraform:1.9"

_PAID_RESOURCES = {
    "aws_neptune_cluster": "Neptune (~$0.10/hr) — NOT free tier",
    "aws_neptune_cluster_instance": "Neptune instance (~$0.20/hr) — NOT free tier",
    "aws_elasticache_replication_group": "ElastiCache (~$0.017/hr) — NOT free tier",
    "aws_elasticache_cluster": "ElastiCache (~$0.017/hr) — NOT free tier",
    "aws_db_instance": "RDS — free tier eligible only for db.t2.micro/db.t3.micro single-AZ",
}


def _scan_paid_resources(tf_paths: list[Path]) -> list[str]:
    warnings = []
    for p in tf_paths:
        text = p.read_text(encoding="utf-8")
        for res_type, msg in _PAID_RESOURCES.items():
            if f'resource "{res_type}"' in text and msg not in warnings:
                warnings.append(msg)
    return warnings


def _find_prior_state(tfstate_dir: Path, uid: str) -> Path | None:
    candidate = tfstate_dir / f"state_{uid}.tfstate"
    if candidate.exists():
        return candidate
    states = sorted(tfstate_dir.glob("state_*.tfstate"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    return states[0] if states else None


def run_apply(run_id: str, project_id: str, canvas: str = "") -> dict:
    canvas = resolve_canvas(run_id, canvas)
    out_dir = artifacts_dir(canvas)
    tfstate_dir = out_dir / "tfstate"
    env = aws_env()
    mode = detect_mode(env)
    iac_arts = get_iac_artifacts(run_id)
    tf_paths = filter_artifacts(iac_arts, ["tf"])

    # Fallback: latest .tf in canvas terraform dir
    if not tf_paths:
        tf_dir = out_dir / "terraform"
        if tf_dir.exists():
            tfs = sorted(tf_dir.glob("main_*.tf"), key=lambda p: p.stat().st_mtime, reverse=True)
            if tfs:
                tf_paths = [tfs[0]]
                vars_tf = tf_dir / "variables.tf"
                if vars_tf.exists():
                    tf_paths.append(vars_tf)

    findings: list[dict] = []
    gate = "PASS"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uid = uuid.uuid4().hex[:8]
    resources_created: list[str] = []
    state_path: str | None = None

    if not tf_paths:
        return {
            "gate": "WARN", "mode": mode, "canvas": canvas,
            "findings": [{"severity": "warn", "check": "no_tf_files",
                           "message": f"No Terraform files found for canvas '{canvas}'"}],
            "resources_created": [],
        }

    paid = _scan_paid_resources(tf_paths)
    for w in paid:
        findings.append({"severity": "warn", "check": "cost", "message": w})

    findings.append({"severity": "info", "check": "mode",
                      "message": f"Mode: {mode.upper()} — "
                                 + {"aws": "deploying to real AWS",
                                    emulator.MODE: f"emulator at {emulator.endpoint(env)}",
                                    "sam": "SAM local endpoint",
                                    "dry_run": "no credentials — running terraform plan only (safe)"}[mode]})

    if not docker_available() or not pull_image(_TF_IMAGE):
        findings.append({"severity": "warn", "check": "docker",
                          "message": "Docker unavailable — cannot run terraform apply"})
        gate = "WARN"
    else:
        docker_env = docker_aws_flags(env, mode)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for p in tf_paths:
                shutil.copy2(p, tmp_path / p.name)

            if is_emulated(mode):
                docker_ep = localstack_docker_endpoint(emulator.endpoint(env))
                region = env.get("AWS_DEFAULT_REGION") or emulator.region(env)
                (tmp_path / "localstack_override.tf").write_text(
                    LOCALSTACK_PROVIDER_OVERRIDE.format(ep=docker_ep, region=region),
                    encoding="utf-8", newline="",
                )
                findings.append({"severity": "info", "check": "localstack_override",
                                  "message": f"Provider overridden → {docker_ep}"})

            (tmp_path / "auto.tfvars").write_text(TFVARS_DEFAULTS, encoding="utf-8", newline="")

            prior_state = _find_prior_state(tfstate_dir, uid)
            if prior_state:
                shutil.copy2(prior_state, tmp_path / "terraform.tfstate")
                findings.append({"severity": "info", "check": "state",
                                  "message": f"Resuming from prior state: {prior_state.name}"})

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

                if mode == "dry_run":
                    rc, out, err = docker_run(_TF_IMAGE, tmp, docker_env, "terraform",
                                               "plan", "-input=false", "-no-color", timeout=300)
                    for line in (out or "").splitlines():
                        if "will be created" in line or "must be replaced" in line:
                            resources_created.append(line.strip())
                    sev = "pass" if rc == 0 else "warn"
                    findings.append({"severity": sev, "check": "terraform_plan",
                                      "message": f"Plan {'OK' if rc == 0 else 'warnings'} — "
                                                 f"{len(resources_created)} resource(s) would be created (dry-run)"})
                else:
                    rc, out, err = docker_run(_TF_IMAGE, tmp, docker_env, "terraform",
                                               "apply", "-auto-approve", "-input=false", "-no-color",
                                               timeout=600)
                    if rc == 0:
                        for line in (out or "").splitlines():
                            if "created" in line.lower() or "Apply complete" in line:
                                resources_created.append(line.strip())
                        state_src = tmp_path / "terraform.tfstate"
                        if state_src.exists():
                            tfstate_dir.mkdir(parents=True, exist_ok=True)
                            state_dst = tfstate_dir / f"state_{uid}.tfstate"
                            shutil.copy2(state_src, state_dst)
                            state_path = state_dst.relative_to(_ROOT).as_posix()
                            findings.append({"severity": "info", "check": "state_saved",
                                              "message": f"State saved → {state_path}"})
                        findings.append({"severity": "pass", "check": "terraform_apply",
                                          "message": f"Apply complete — {len(resources_created)} change(s)"})
                    else:
                        findings.append({"severity": "fail", "check": "terraform_apply",
                                          "message": (out + err).strip()[:600]})
                        gate = "FAIL"

    if any(f["severity"] == "fail" for f in findings):
        gate = "FAIL"

    lines = [
        f"# Terraform Apply Report — {canvas.upper()}",
        f"**Generated:** {ts}  ",
        f"**Canvas:** {canvas}  ",
        f"**Project:** {project_id}  ",
        f"**Mode:** {mode.upper()}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '⚠ WARN' if gate == 'WARN' else '✗ FAIL'}  ",
        f"**Files applied:** {len(tf_paths)}",
        "",
    ]
    if paid:
        lines += ["## ⚠ Cost Warning (Free-Tier Alert)", ""]
        for w in paid:
            lines.append(f"- {w}")
        lines.append("")

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

    if resources_created:
        action = "Planned" if mode == "dry_run" else "Created"
        lines += [f"## {action} Resources", ""]
        for r in resources_created[:30]:
            lines.append(f"- `{r}`")
        lines.append("")

    if state_path:
        lines += [
            "## Teardown",
            "",
            f"State file: `{state_path}`",
            f"To destroy: trigger **{canvas.upper()} Teardown Workflow** or run:",
            "```",
            f"python tools/studio/executors/terraform_destroy.py --run-id {run_id} --canvas {canvas}",
            "```",
            "",
        ]

    report_path = out_dir / f"apply_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    return {
        "gate": gate, "mode": mode, "canvas": canvas,
        "findings": findings,
        "resources_created": len(resources_created),
        "state_path": state_path,
        "report_path": report_path.relative_to(_ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="Terraform Apply (shared executor)")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="", help="Canvas slug (auto-detected if omitted)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = run_apply(args.run_id, args.project_id, args.canvas)
        gate = result["gate"]
        output = {
            "status": "success" if gate in ("PASS", "WARN") else "failed",
            "gate": gate, "canvas": result["canvas"], "mode": result["mode"],
            "resources_created": result["resources_created"],
            "findings": len(result["findings"]),
            "failures": sum(1 for f in result["findings"] if f["severity"] == "fail"),
            "artifacts": [{"name": "Terraform Apply Report",
                           "path": result["report_path"], "type": "md"}],
        }
        if result.get("state_path"):
            output["artifacts"].append(
                {"name": "Terraform State", "path": result["state_path"], "type": "tfstate"}
            )
        print(json.dumps(output))
        sys.exit(0 if gate in ("PASS", "WARN") else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
