"""Shared utilities for all canvas workflow executors.

Every executor in this package is canvas-agnostic: it reads its inputs from
the run's memory (``tools/studio/run_memory.py``), then writes outputs to
  data/studio_artifacts/<canvas>/

Run memory is the record of truth for both the canvas and the artifacts a run
has produced. The runner publishes them: the canvas before the first step, and
each step's artifacts as that step completes. Everything below labelled
"fallback" exists only for runs that predate dwo-mem-02, or for an executor
invoked outside the runner (e.g. by hand with --run-id).

Canvas is resolved in priority order:
  1. --canvas CLI arg
  2. Run memory `canvas`
  3. The `canvas:` key declared by the run's workflow template  (fallback)
  4. Inferred from Generate IaC artifact paths in the run       (fallback)
  5. Workflow name prefix  (e.g. 'DDC Test Workflow' → 'ddc')    (fallback)
  6. CanvasResolutionError

There is deliberately no default canvas. Defaulting made an unresolvable run
write its artifacts into another canvas' directory, silently — see
docs/spikes/dwo-00-superplane-workflow-adaptation.md (gap MEM).

IaC Generator Contract (all canvases must honour):
  step_name = 'Generate IaC'
  stdout JSON = {
      "artifacts": [
          {"name": "...", "path": "data/studio_artifacts/<canvas>/...", "type": "tf|yml|ini|py|md|yaml"},
          ...
      ]
  }

The stdout contract still stands — it is how a step *declares* its artifacts,
and the runner is what lifts that declaration into run memory under
``artifacts[<step name>]``. What changed in dwo-mem-02 is the read path:
consumers read memory, not each other's stdout.
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

class CanvasResolutionError(RuntimeError):
    """Raised when the canvas for a run cannot be determined.

    Deliberately fatal. The previous behaviour was to fall back to 'ddc', which
    made an unresolvable run write its artifacts into the DDC directory and
    report success.
    """


# Used only when the component registry cannot be loaded (air-gap, no PyYAML).
_FALLBACK_CANVAS_SLUGS = ("ddc", "ndc", "sdc", "pdc", "bdc", "odc", "idc")

#: The step every canvas' IaC generator runs under (see the module docstring).
IAC_STEP_NAME = "Generate IaC"


def _memory(run_id: str, key_attr: str, default):
    """Read one run-memory key, or ``default`` if memory is unreachable.

    ``key_attr`` names the key constant on ``tools.studio.run_memory``, so the
    key strings stay single-sourced there.

    Unreachable covers a run that predates dwo-mem-02, a missing
    ``studio_run_memory`` table, and an executor invoked outside the runner.
    Each case falls back to the pre-memory lookup path, so this must not raise.
    """
    if not run_id:
        return default
    try:
        from tools.studio import run_memory  # noqa: PLC0415
        value = run_memory.get(run_id, getattr(run_memory, key_attr), default=default)
    except Exception:
        return default
    return default if value is None else value


def known_canvas_slugs() -> tuple[str, ...]:
    """Canvas slugs from `args/component_registry.yaml`, longest first.

    The registry is the single source of truth for which canvases exist
    (CLAUDE.md). The static fallback only covers the case where the registry
    cannot be read at all; it is not a second catalogue to keep in sync.
    """
    slugs: tuple[str, ...] = ()
    try:
        from tools.config.component_registry import get_registry
        slugs = tuple(c.key.lower() for c in get_registry().iter_canvases() if c.key)
    except Exception:
        slugs = ()
    if not slugs:
        slugs = _FALLBACK_CANVAS_SLUGS
    # Longest first so 'aadc' is matched before 'adc'-style shorter prefixes.
    return tuple(sorted(set(slugs), key=len, reverse=True))


def _canvas_from_memory(run_id: str) -> str:
    """Return the canvas slug the runner published for this run."""
    return str(_memory(run_id, "CANVAS_KEY", "") or "").strip().lower()


def _canvas_from_template(run_id: str) -> str:
    """Return the `canvas:` key declared by this run's workflow template.

    Authoritative: 25 of the shipped templates declare it, including every
    template that invokes an executor in this package.
    """
    if not run_id:
        return ""
    try:
        import yaml
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT w.template_yaml FROM studio_workflow_runs r "
                "JOIN studio_workflows w ON w.workflow_id = r.workflow_id "
                "WHERE r.run_id = %s",
                (run_id,),
            ).fetchone()
            if row and row["template_yaml"]:
                data = yaml.safe_load(row["template_yaml"]) or {}
                return str(data.get("canvas") or "").strip().lower()
        finally:
            conn.close()
    except Exception:
        pass
    return ""


def _canvas_from_workflow_name(run_id: str) -> str:
    """Return the canvas slug prefixing this run's workflow name, if any."""
    if not run_id:
        return ""
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
                for canvas in known_canvas_slugs():
                    if name.startswith(canvas):
                        return canvas
        finally:
            conn.close()
    except Exception:
        pass
    return ""


def resolve_canvas(run_id: str = "", hint: str = "") -> str:
    """Return the canvas slug for a run.

    Raises:
        CanvasResolutionError: if no strategy resolves a canvas. Never guesses.
    """
    if hint:
        return hint.lower()

    # 1. What the runner published for this run — the record of truth.
    remembered = _canvas_from_memory(run_id)
    if remembered:
        return remembered

    # 2. The template's own `canvas:` declaration — the same value the runner
    #    publishes, re-read directly for runs that predate run memory.
    declared = _canvas_from_template(run_id)
    if declared:
        return declared

    # 3. Infer from Generate IaC artifact paths.
    for a in get_iac_artifacts(run_id):
        # e.g. "data/studio_artifacts/ddc/..." → "ddc"
        parts = Path(a.get("path", "")).parts
        if "studio_artifacts" in parts:
            idx = parts.index("studio_artifacts")
            if idx + 1 < len(parts):
                return parts[idx + 1]

    # 4. Infer from the workflow name.
    named = _canvas_from_workflow_name(run_id)
    if named:
        return named

    raise CanvasResolutionError(
        f"Cannot determine the canvas for run {run_id or '<no run id>'}. "
        "Declare `canvas: <slug>` at the top level of the workflow template, "
        "or pass --canvas <slug> to this executor. "
        "Refusing to fall back to a default: that writes this run's artifacts "
        "into another canvas' data/studio_artifacts/<canvas>/ directory."
    )


def artifacts_dir(canvas: str) -> Path:
    """Return the artifact output directory for a canvas, creating it if needed."""
    p = _ROOT / "data" / "studio_artifacts" / canvas
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── IaC artifact lookup ────────────────────────────────────────────────────

def get_iac_artifacts(run_id: str) -> list[dict]:
    """Return the artifacts the 'Generate IaC' step of this run produced.

    Read from run memory, which the runner writes as each step completes. The
    stdout re-parse below is the fallback for runs that predate dwo-mem-02.
    """
    if not run_id:
        return []

    remembered = _memory(run_id, "ARTIFACTS_KEY", {})
    if isinstance(remembered, dict) and remembered.get(IAC_STEP_NAME):
        return remembered[IAC_STEP_NAME]

    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT stdout FROM studio_workflow_run_steps "
                "WHERE run_id = %s AND step_name = %s",
                (run_id, IAC_STEP_NAME),
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
