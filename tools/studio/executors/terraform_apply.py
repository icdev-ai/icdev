"""Terraform Apply — Shared Workflow Step (canvas-agnostic).

Applies Terraform IaC using Docker (hashicorp/terraform:1.9). Three modes:

  Real AWS   — AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION in .env
  LocalStack — LOCALSTACK_ENDPOINT=http://localhost:4566 in .env
  SAM local  — AWS_SAM_LOCAL=true in .env
  Dry-run    — no credentials → terraform plan only (safe)

Canvas auto-detected from run artifacts; override with --canvas.
State saved to: data/studio_artifacts/<canvas>/tfstate/<uid>.tfstate
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
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
    localstack_docker_endpoint, LOCALSTACK_PROVIDER_OVERRIDE, TFVARS_DEFAULTS,
)
_TFSTATE_DIR = _ARTIFACTS_DIR / "tfstate"
_TF_IMAGE = "hashicorp/terraform:1.9"

# Non-free-tier resource types — warn user
_PAID_RESOURCES = {
    "aws_neptune_cluster": "Neptune (~$0.10/hr) — NOT free tier",
    "aws_neptune_cluster_instance": "Neptune instance (~$0.20/hr) — NOT free tier",
    "aws_elasticache_replication_group": "ElastiCache (~$0.017/hr) — NOT free tier",
    "aws_elasticache_cluster": "ElastiCache (~$0.017/hr) — NOT free tier",
    "aws_db_instance": "RDS — free tier eligible only for db.t2.micro/db.t3.micro single-AZ",
}

def _load_dotenv() -> dict:
    try:
        from dotenv import dotenv_values
        return dotenv_values(_ROOT / ".env")
    except Exception:
        return {}


def _aws_env() -> dict[str, str]:
    """Merge real environment + .env, return only AWS-relevant vars."""
    merged = {**os.environ, **_load_dotenv()}
    keys = [
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION",
        "AWS_SESSION_TOKEN", "AWS_REGION", "LOCALSTACK_ENDPOINT", "AWS_SAM_LOCAL",
    ]
    return {k: merged[k] for k in keys if merged.get(k)}


def _detect_mode(env: dict[str, str]) -> str:
    """Return 'localstack', 'sam', 'aws', or 'dry_run'."""
    if env.get("LOCALSTACK_ENDPOINT"):
        return "localstack"
    if env.get("AWS_SAM_LOCAL", "").lower() in ("true", "1"):
        return "sam"
    if env.get("AWS_ACCESS_KEY_ID"):
        return "aws"
    return "dry_run"


def _localstack_endpoint_for_docker(endpoint: str) -> str:
    """Convert localhost/127.0.0.1 to host.docker.internal for Docker-in-Docker."""
    return endpoint.replace("localhost", "host.docker.internal").replace(
        "127.0.0.1", "host.docker.internal"
    )


def _get_iac_artifacts(run_id: str) -> list[dict]:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT stdout FROM studio_workflow_run_steps "
                "WHERE run_id = ? AND step_name = 'Generate IaC'",
                (run_id,),
            ).fetchone()
            if row and row["stdout"]:
                return json.loads(row["stdout"]).get("artifacts", [])
        finally:
            conn.close()
    except Exception:
        pass
    # Fallback: latest tf files
    tf_dir = _ARTIFACTS_DIR / "terraform"
    if tf_dir.exists():
        tfs = sorted(tf_dir.glob("main_*.tf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if tfs:
            uid = tfs[0].stem.split("_", 1)[1]
            return [
                {"name": "Terraform Main", "path": f"data/studio_artifacts/ddc/terraform/main_{uid}.tf", "type": "tf"},
                {"name": "Terraform Variables", "path": "data/studio_artifacts/ddc/terraform/variables.tf", "type": "tf"},
                {"name": "Terraform tfvars", "path": f"data/studio_artifacts/ddc/terraform/terraform.tfvars.example", "type": "tf"},
            ]
    return []


def _find_existing_state(uid: str) -> Path | None:
    """Look for a prior state file matching this TF uid."""
    _TFSTATE_DIR.mkdir(parents=True, exist_ok=True)
    candidate = _TFSTATE_DIR / f"state_{uid}.tfstate"
    if candidate.exists():
        return candidate
    # Also accept latest state for any uid
    states = sorted(_TFSTATE_DIR.glob("state_*.tfstate"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    return states[0] if states else None


def _scan_paid_resources(tf_paths: list[Path]) -> list[str]:
    """Warn about non-free-tier resource types found in TF files."""
    warnings = []
    for p in tf_paths:
        text = p.read_text(encoding="utf-8")
        for res_type, msg in _PAID_RESOURCES.items():
            if f'resource "{res_type}"' in text:
                if msg not in warnings:
                    warnings.append(msg)
    return warnings


def _docker_run(workspace: str, env_vars: list[str], *args: str, timeout: int = 300) -> tuple[int, str, str]:
    ws_posix = Path(workspace).as_posix()
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{ws_posix}:/workspace",
        "-w", "/workspace",
        *env_vars,
        "--entrypoint", "terraform",
        _TF_IMAGE,
        *args,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Timed out after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
        return True
    except Exception:
        return False


def _pull_image() -> bool:
    try:
        r = subprocess.run(["docker", "image", "inspect", _TF_IMAGE], capture_output=True, timeout=10)
        if r.returncode == 0:
            return True
        subprocess.run(["docker", "pull", _TF_IMAGE], capture_output=True, timeout=120, check=True)
        return True
    except Exception:
        return False


def run_apply(run_id: str, project_id: str, canvas: str = "") -> dict:
    canvas = resolve_canvas(run_id, canvas)
    out_dir = artifacts_dir(canvas)
    _TFSTATE_DIR = out_dir / "tfstate"
    env = aws_env()
    mode = detect_mode(env)
    iac_artifacts = get_iac_artifacts(run_id)
    tf_paths = filter_artifacts(iac_artifacts, ["tf"])

    findings: list[dict] = []
    gate = "PASS"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uid = uuid.uuid4().hex[:8]
    resources_created: list[str] = []
    state_path: str | None = None

    # Alias for legacy helpers in this file that still reference _ARTIFACTS_DIR
    _ARTIFACTS_DIR = out_dir

    if not tf_paths:
        return {
            "gate": "WARN",
            "mode": mode,
            "findings": [{"severity": "warn", "check": "no_tf_files",
                          "message": "No Terraform files found from IaC step"}],
            "resources_created": [],
        }

    # Cost warnings
    paid = _scan_paid_resources(tf_paths)
    for w in paid:
        findings.append({"severity": "warn", "check": "cost", "message": w})

    # Mode info
    findings.append({"severity": "info", "check": "mode",
                      "message": f"Mode: {mode.upper()} — "
                                 + {"aws": "deploying to real AWS",
                                    "localstack": f"LocalStack at {env.get('LOCALSTACK_ENDPOINT')}",
                                    "sam": "AWS SAM local endpoint",
                                    "dry_run": "no credentials — running terraform plan only (safe)"}[mode]})

    if not docker_available() or not pull_image(_TF_IMAGE):
        findings.append({"severity": "warn", "check": "docker",
                          "message": "Docker unavailable — cannot run terraform apply"})
        gate = "WARN"
    else:
        docker_env = docker_aws_flags(env, mode)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Copy TF files
            for p in tf_paths:
                shutil.copy2(p, tmp_path / p.name)

            # Inject LocalStack/SAM provider override
            if mode in ("localstack", "sam"):
                raw_ep = env.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
                docker_ep = _localstack_endpoint_for_docker(raw_ep)
                region = env.get("AWS_DEFAULT_REGION", "us-east-1")
                override = _LOCALSTACK_OVERRIDE_TPL.format(ep=docker_ep, region=region)
                (tmp_path / "localstack_override.tf").write_text(override, encoding="utf-8")
                findings.append({"severity": "info", "check": "localstack_override",
                                  "message": f"Provider overridden → {docker_ep}"})

            (tmp_path / "auto.tfvars").write_text(TFVARS_DEFAULTS, encoding="utf-8")

            # Restore previous state if available
            prior_state = _find_existing_state(uid)
            if prior_state:
                shutil.copy2(prior_state, tmp_path / "terraform.tfstate")
                findings.append({"severity": "info", "check": "state",
                                  "message": f"Resuming from prior state: {prior_state.name}"})

            rc, out, err = _docker_run(tmp, docker_env,
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
                    # Plan only — safe, no AWS charges
                    rc, out, err = _docker_run(tmp, docker_env,
                                                "plan", "-input=false", "-no-color",
                                                timeout=300)
                    if rc == 0:
                        # Count planned resources
                        for line in out.splitlines():
                            if "will be created" in line or "must be replaced" in line:
                                resources_created.append(line.strip())
                        findings.append({"severity": "pass", "check": "terraform_plan",
                                          "message": f"Plan OK — {len(resources_created)} resource(s) would be created (dry-run, not applied)"})
                    else:
                        findings.append({"severity": "warn", "check": "terraform_plan",
                                          "message": f"Plan returned non-zero: {(err or out)[:400]}"})
                else:
                    # Real apply
                    rc, out, err = _docker_run(tmp, docker_env,
                                                "apply", "-auto-approve", "-input=false", "-no-color",
                                                timeout=600)
                    if rc == 0:
                        for line in out.splitlines():
                            if "created" in line.lower() or "Apply complete" in line:
                                resources_created.append(line.strip())

                        # Save state file for later destroy
                        state_src = tmp_path / "terraform.tfstate"
                        if state_src.exists():
                            _TFSTATE_DIR.mkdir(parents=True, exist_ok=True)
                            state_dst = _TFSTATE_DIR / f"state_{uid}.tfstate"
                            shutil.copy2(state_src, state_dst)
                            state_path = state_dst.relative_to(_ROOT).as_posix()
                            findings.append({"severity": "info", "check": "state_saved",
                                              "message": f"State saved → {state_path}"})

                        findings.append({"severity": "pass", "check": "terraform_apply",
                                          "message": f"Apply complete — {len(resources_created)} resource change(s)"})
                    else:
                        combined = (out + err).strip()
                        findings.append({"severity": "fail", "check": "terraform_apply",
                                          "message": combined[:600]})
                        gate = "FAIL"

    fails = [f for f in findings if f["severity"] == "fail"]
    gate = "FAIL" if fails else gate

    # Report
    lines = [
        "# Terraform Apply Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Mode:** {mode.upper()}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '⚠ WARN' if gate == 'WARN' else '✗ FAIL'}  ",
        f"**Files applied:** {len(tf_paths)}",
        "",
    ]
    if paid:
        lines += ["## ⚠ Cost Warning", ""]
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
        lines += [f"## {'Planned' if mode == 'dry_run' else 'Created'} Resources", ""]
        for r in resources_created[:30]:
            lines.append(f"- `{r}`")
        lines.append("")

    if state_path:
        lines += [
            "## Teardown",
            "",
            f"State file: `{state_path}`",
            "To destroy: trigger **DDC Teardown Workflow** or run:",
            "```",
            f"python tools/data/terraform_destroy.py --run-id {run_id} --project-id {project_id}",
            "```",
            "",
        ]

    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _ARTIFACTS_DIR / f"apply_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "gate": gate,
        "mode": mode,
        "findings": findings,
        "resources_created": len(resources_created),
        "state_path": state_path,
        "report_path": report_path.relative_to(_ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="Terraform Apply")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_apply(args.run_id, args.project_id)
        gate = result["gate"]
        output = {
            "status": "success" if gate in ("PASS", "WARN") else "failed",
            "gate": gate,
            "mode": result["mode"],
            "resources_created": result["resources_created"],
            "findings": len(result["findings"]),
            "failures": sum(1 for f in result["findings"] if f["severity"] == "fail"),
            "artifacts": [
                {"name": "Terraform Apply Report",
                 "path": result["report_path"], "type": "md"},
            ],
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
