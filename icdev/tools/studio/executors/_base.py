"""Shared utilities for all canvas workflow executors.

Every executor in this package is canvas-agnostic: it locates its inputs
by querying the 'Generate IaC' step in the run DB, then writes outputs to
  data/studio_artifacts/<canvas>/

Canvas is resolved in priority order:
  1. --canvas CLI arg
  2. Inferred from Generate IaC artifact paths in the run
  3. Workflow name prefix  (e.g. 'DDC Test Workflow' → 'ddc')
  4. Default: 'ddc'

IaC Generator Contract (all canvases must honour):
  step_name = 'Generate IaC'
  stdout JSON = {
      "artifacts": [
          {"name": "...", "path": "data/studio_artifacts/<canvas>/...", "type": "tf|yml|ini|py|md|yaml"},
          ...
      ]
  }
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Canvas resolution ──────────────────────────────────────────────────────

def resolve_canvas(run_id: str = "", hint: str = "") -> str:
    """Return the canvas slug for a given run, using multiple fallback strategies."""
    if hint:
        return hint.lower()

    # Try to infer from Generate IaC artifacts path
    artifacts = get_iac_artifacts(run_id)
    for a in artifacts:
        path = a.get("path", "")
        # e.g. "data/studio_artifacts/ddc/..." → "ddc"
        parts = Path(path).parts
        if "studio_artifacts" in parts:
            idx = parts.index("studio_artifacts")
            if idx + 1 < len(parts):
                return parts[idx + 1]

    # Infer from workflow name in DB
    if run_id:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT workflow_name FROM studio_workflow_runs WHERE run_id = %s",
                    (run_id,),
                ).fetchone()
                if row:
                    name = (row["workflow_name"] or "").lower()
                    for canvas in ("ddc", "ndc", "sdc", "pdc", "bdc", "odc", "idc"):
                        if name.startswith(canvas):
                            return canvas
            finally:
                conn.close()
        except Exception:
            pass

    return "ddc"  # safe default


def artifacts_dir(canvas: str) -> Path:
    """Return the artifact output directory for a canvas, creating it if needed."""
    p = _ROOT / "data" / "studio_artifacts" / canvas
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── IaC artifact lookup ────────────────────────────────────────────────────

def get_iac_artifacts(run_id: str) -> list[dict]:
    """Return artifact list from the Generate IaC step of this run."""
    if run_id:
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


def filter_artifacts(artifacts: list[dict], types: list[str]) -> list[Path]:
    """Return existing local paths matching the given type(s)."""
    paths: list[Path] = []
    for a in artifacts:
        if a.get("type") in types:
            p = _ROOT / a["path"]
            if p.exists():
                paths.append(p)
    return paths


def find_artifact(artifacts: list[dict], type_: str,
                  name_contains: str = "") -> Optional[Path]:
    """Return the first artifact matching type and optional name substring."""
    for a in artifacts:
        if a.get("type") != type_:
            continue
        if name_contains and name_contains.lower() not in a.get("name", "").lower():
            continue
        p = _ROOT / a["path"]
        if p.exists():
            return p
    return None


# ── AWS / credential helpers ───────────────────────────────────────────────

def load_dotenv() -> dict:
    try:
        from dotenv import dotenv_values
        return dotenv_values(_ROOT / ".env")
    except Exception:
        return {}


def aws_env() -> dict[str, str]:
    """Merge OS env + .env; return only AWS-relevant keys."""
    merged = {**os.environ, **load_dotenv()}
    keys = [
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION",
        "AWS_SESSION_TOKEN", "AWS_REGION", "LOCALSTACK_ENDPOINT", "AWS_SAM_LOCAL",
    ]
    return {k: merged[k] for k in keys if merged.get(k)}


def detect_mode(env: dict[str, str]) -> str:
    """Return 'localstack' | 'sam' | 'aws' | 'dry_run'."""
    if env.get("LOCALSTACK_ENDPOINT"):
        return "localstack"
    if env.get("AWS_SAM_LOCAL", "").lower() in ("true", "1"):
        return "sam"
    if env.get("AWS_ACCESS_KEY_ID"):
        return "aws"
    return "dry_run"


def localstack_docker_endpoint(endpoint: str) -> str:
    """Rewrite localhost/127.0.0.1 → host.docker.internal for Docker containers."""
    return endpoint.replace("localhost", "host.docker.internal").replace(
        "127.0.0.1", "host.docker.internal"
    )


def docker_aws_flags(env: dict[str, str], mode: str) -> list[str]:
    """Build Docker -e flags for AWS credentials based on mode."""
    flags: list[str] = []
    if mode == "aws":
        for k in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION",
                   "AWS_SESSION_TOKEN", "AWS_REGION"]:
            if env.get(k):
                flags += ["-e", f"{k}={env[k]}"]
    elif mode in ("localstack", "sam"):
        flags += [
            "-e", "AWS_ACCESS_KEY_ID=test",
            "-e", "AWS_SECRET_ACCESS_KEY=test",
            "-e", f"AWS_DEFAULT_REGION={env.get('AWS_DEFAULT_REGION', 'us-east-1')}",
        ]
    return flags


LOCALSTACK_PROVIDER_OVERRIDE = """\
# LocalStack/SAM endpoint override — auto-injected by ICDEV Studio executor
provider "aws" {{
  access_key                  = "test"
  secret_key                  = "test"
  region                      = "{region}"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  # Force path-style S3 URLs so Docker containers can reach LocalStack
  # (virtual-hosted style — bucket.host — breaks inside Docker networking)
  s3_use_path_style           = true
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

TFVARS_DEFAULTS = (
    'vpc_id             = "vpc-00000000"\n'
    'private_subnet_ids = ["subnet-00000000"]\n'
    'db_password        = "changeme-rotate-immediately"\n'
    'dw_password        = "changeme-rotate-immediately"\n'
    'artifacts_bucket   = "icdev-artifacts"\n'
)


# ── Docker helpers ─────────────────────────────────────────────────────────

def docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
        return True
    except Exception:
        return False


def pull_image(image: str) -> bool:
    try:
        r = subprocess.run(["docker", "image", "inspect", image],
                           capture_output=True, timeout=10)
        if r.returncode == 0:
            return True
        subprocess.run(["docker", "pull", image],
                       capture_output=True, timeout=120, check=True)
        return True
    except Exception:
        return False


def docker_run(image: str, workspace: str, env_flags: list[str],
               entrypoint: str, *args: str, timeout: int = 300) -> tuple[int, str, str]:
    ws_posix = Path(workspace).as_posix()
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{ws_posix}:/workspace",
        "-w", "/workspace",
        *env_flags,
        "--entrypoint", entrypoint,
        image,
        *args,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Timed out after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


# ── boto3 client factory ───────────────────────────────────────────────────

def boto3_client(service: str):
    """Build a boto3 client, honouring LocalStack/SAM endpoint overrides."""
    merged = {**os.environ, **load_dotenv()}
    region = merged.get("AWS_DEFAULT_REGION") or merged.get("AWS_REGION") or "us-east-1"
    kwargs: dict = {"region_name": region}

    endpoint = merged.get("LOCALSTACK_ENDPOINT")
    if not endpoint and merged.get("AWS_SAM_LOCAL", "").lower() in ("true", "1"):
        endpoint = "http://localhost:4566"
    if endpoint:
        kwargs["endpoint_url"] = endpoint
        kwargs["aws_access_key_id"] = "test"
        kwargs["aws_secret_access_key"] = "test"
    elif merged.get("AWS_ACCESS_KEY_ID"):
        kwargs["aws_access_key_id"] = merged["AWS_ACCESS_KEY_ID"]
        kwargs["aws_secret_access_key"] = merged.get("AWS_SECRET_ACCESS_KEY", "")
        if merged.get("AWS_SESSION_TOKEN"):
            kwargs["aws_session_token"] = merged["AWS_SESSION_TOKEN"]

    import boto3
    return boto3.client(service, **kwargs)
