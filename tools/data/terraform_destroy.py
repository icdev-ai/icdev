"""Terraform Destroy — DDC Teardown Workflow Step 2.

Destroys all infrastructure created by terraform_apply.py.
Finds the Terraform state file from a prior apply (by run_id or most-recent)
and runs `terraform destroy -auto-approve` in Docker.

SAFETY: Requires explicit confirmation — this is irreversible.
        In the DDC workflow, gate via a HITL approval step before this runs.

State file location: data/studio_artifacts/ddc/tfstate/state_<uid>.tfstate
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "ddc"
_TFSTATE_DIR = _ARTIFACTS_DIR / "tfstate"
_TF_IMAGE = "hashicorp/terraform:1.9"

_LOCALSTACK_OVERRIDE_TPL = """\
provider "aws" {{
  access_key                  = "test"
  secret_key                  = "test"
  region                      = "{region}"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  endpoints {{
    s3          = "{ep}"
    ec2         = "{ep}"
    rds         = "{ep}"
    neptune     = "{ep}"
    elasticache = "{ep}"
    iam         = "{ep}"
    ssm         = "{ep}"
    sts         = "{ep}"
    kms         = "{ep}"
  }}
}}
"""


def _load_dotenv() -> dict:
    try:
        from dotenv import dotenv_values
        return dotenv_values(_ROOT / ".env")
    except Exception:
        return {}


def _aws_env() -> dict[str, str]:
    import os as _os
    merged = {**_os.environ, **_load_dotenv()}
    keys = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION",
            "AWS_SESSION_TOKEN", "AWS_REGION", "LOCALSTACK_ENDPOINT", "AWS_SAM_LOCAL"]
    return {k: merged[k] for k in keys if merged.get(k)}


def _detect_mode(env: dict) -> str:
    if env.get("LOCALSTACK_ENDPOINT"):
        return "localstack"
    if env.get("AWS_SAM_LOCAL", "").lower() in ("true", "1"):
        return "sam"
    if env.get("AWS_ACCESS_KEY_ID"):
        return "aws"
    return "dry_run"


def _localstack_endpoint_for_docker(ep: str) -> str:
    return ep.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")


def _get_iac_artifacts(run_id: str) -> list[dict]:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT stdout FROM studio_workflow_run_steps "
                "WHERE run_id = %s AND step_name = 'Generate IaC'",
                (run_id,),
            ).fetchone()
            if row and row["stdout"]:
                return json.loads(row["stdout"]).get("artifacts", [])
        finally:
            conn.close()
    except Exception:
        pass
    return []


def _find_state_for_run(run_id: str) -> Path | None:
    """Find state file produced by the apply step for this run."""
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
    # Fallback: most recent state file
    if _TFSTATE_DIR.exists():
        states = sorted(_TFSTATE_DIR.glob("state_*.tfstate"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        return states[0] if states else None
    return None


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


def _docker_run(workspace: str, env_vars: list[str], *args: str, timeout: int = 600) -> tuple[int, str, str]:
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


def run_destroy(run_id: str, project_id: str) -> dict:
    env = _aws_env()
    mode = _detect_mode(env)
    dry_run = (mode == "dry_run")

    findings: list[dict] = []
    gate = "PASS"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uid = uuid.uuid4().hex[:8]
    resources_destroyed: list[str] = []

    findings.append({"severity": "info", "check": "mode",
                      "message": f"Mode: {mode.upper()}"
                                 + (" — plan only (no credentials)" if dry_run else "")})

    # Find state file
    state_file = _find_state_for_run(run_id)
    if not state_file:
        return {
            "gate": "WARN",
            "mode": mode,
            "findings": [{"severity": "warn", "check": "no_state",
                           "message": "No Terraform state file found. Nothing to destroy. "
                                      "Run Terraform Apply first, or supply --run-id of an applied run."}],
            "resources_destroyed": 0,
        }

    findings.append({"severity": "info", "check": "state_file",
                      "message": f"Using state: {state_file.name}"})

    # Find TF files matching the state
    artifacts = _get_iac_artifacts(run_id)
    tf_artifacts = [a for a in artifacts if a.get("type") == "tf"]
    tf_paths = [_ROOT / a["path"] for a in tf_artifacts if (_ROOT / a["path"]).exists()]

    if not tf_paths:
        # Try to find TF files from the state file's uid
        state_uid = state_file.stem.replace("state_", "")
        tf_dir = _ARTIFACTS_DIR / "terraform"
        main_tf = tf_dir / f"main_{state_uid}.tf"
        if main_tf.exists():
            tf_paths = [main_tf]
            vars_tf = tf_dir / "variables.tf"
            if vars_tf.exists():
                tf_paths.append(vars_tf)

    if not tf_paths and not dry_run:
        findings.append({"severity": "warn", "check": "no_tf",
                          "message": "No matching TF files found — destroy may be incomplete"})

    if not _docker_available() or not _pull_image():
        findings.append({"severity": "fail", "check": "docker",
                          "message": "Docker unavailable — cannot run terraform destroy"})
        gate = "FAIL"
    else:
        docker_env: list[str] = []
        if mode == "aws":
            for k in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION",
                      "AWS_SESSION_TOKEN", "AWS_REGION"]:
                if env.get(k):
                    docker_env += ["-e", f"{k}={env[k]}"]
        elif mode in ("localstack", "sam"):
            docker_env += ["-e", "AWS_ACCESS_KEY_ID=test",
                           "-e", "AWS_SECRET_ACCESS_KEY=test",
                           "-e", f"AWS_DEFAULT_REGION={env.get('AWS_DEFAULT_REGION', 'us-east-1')}"]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Copy TF files
            for p in tf_paths:
                shutil.copy2(p, tmp_path / p.name)

            # Copy state file — REQUIRED for destroy
            shutil.copy2(state_file, tmp_path / "terraform.tfstate")

            # Inject LocalStack override
            if mode in ("localstack", "sam"):
                raw_ep = env.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
                docker_ep = _localstack_endpoint_for_docker(raw_ep)
                region = env.get("AWS_DEFAULT_REGION", "us-east-1")
                (tmp_path / "localstack_override.tf").write_text(
                    _LOCALSTACK_OVERRIDE_TPL.format(ep=docker_ep, region=region),
                    encoding="utf-8", newline="",
                )

            # Inject tfvars
            (tmp_path / "auto.tfvars").write_text(
                'vpc_id              = "vpc-00000000"\n'
                'private_subnet_ids  = ["subnet-00000000"]\n'
                'db_password         = "changeme-rotate-immediately"\n'
                'dw_password         = "changeme-rotate-immediately"\n'
                'artifacts_bucket    = "icdev-artifacts"\n',
                encoding="utf-8", newline="",
            )

            # Init
            rc, out, err = _docker_run(tmp, docker_env,
                                        "init", "-backend=false", "-input=false", "-no-color",
                                        timeout=180)
            if rc != 0:
                findings.append({"severity": "fail", "check": "terraform_init",
                                  "message": f"Init failed: {err[:400]}"})
                gate = "FAIL"
            else:
                if dry_run:
                    # Show what would be destroyed
                    rc, out, err = _docker_run(tmp, docker_env,
                                                "plan", "-destroy", "-input=false", "-no-color",
                                                timeout=300)
                    for line in (out or err or "").splitlines():
                        if "will be destroyed" in line or "destroy" in line.lower():
                            resources_destroyed.append(line.strip())
                    findings.append({"severity": "pass", "check": "destroy_plan",
                                      "message": f"[dry-run] Plan shows {len(resources_destroyed)} resource(s) would be destroyed"})
                else:
                    rc, out, err = _docker_run(tmp, docker_env,
                                                "destroy", "-auto-approve", "-input=false", "-no-color",
                                                timeout=600)
                    if rc == 0:
                        for line in out.splitlines():
                            if "destroyed" in line.lower() or "Destroy complete" in line:
                                resources_destroyed.append(line.strip())

                        # Remove the state file — environment is gone
                        state_file.unlink(missing_ok=True)
                        findings.append({"severity": "pass", "check": "terraform_destroy",
                                          "message": f"Destroy complete — {len(resources_destroyed)} resource(s) removed. State file deleted."})
                    else:
                        combined = (out + err).strip()
                        findings.append({"severity": "fail", "check": "terraform_destroy",
                                          "message": combined[:600]})
                        gate = "FAIL"

    fails = [f for f in findings if f["severity"] == "fail"]
    gate = "FAIL" if fails else gate

    lines = [
        "# Terraform Destroy Report",
        f"**Generated:** {ts}  ",
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
        action = "Would destroy" if dry_run else "Destroyed"
        lines += [f"## {action}", ""]
        for r in resources_destroyed[:30]:
            lines.append(f"- `{r}`")
        lines.append("")

    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _ARTIFACTS_DIR / f"destroy_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    return {
        "gate": gate,
        "mode": mode,
        "findings": findings,
        "resources_destroyed": len(resources_destroyed),
        "report_path": report_path.relative_to(_ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="Terraform Destroy")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_destroy(args.run_id, args.project_id)
        gate = result["gate"]
        output = {
            "status": "success" if gate in ("PASS", "WARN") else "failed",
            "gate": gate,
            "mode": result["mode"],
            "resources_destroyed": result["resources_destroyed"],
            "findings": len(result["findings"]),
            "failures": sum(1 for f in result["findings"] if f["severity"] == "fail"),
            "artifacts": [
                {"name": "Terraform Destroy Report",
                 "path": result["report_path"], "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if gate in ("PASS", "WARN") else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
