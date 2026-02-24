#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV SaaS Phase 5 -- Artifact Delivery Engine.

CUI // SP-CTI

Pushes generated compliance artifacts (SSP, POAM, SBOM, STIG, OSCAL, etc.)
to a tenant's configured storage destination.  Supports three delivery
methods:

  - **S3**:   boto3 upload with STS assume-role into tenant's bucket
  - **Git**:  clone repo, add artifact, commit, push (subprocess)
  - **SFTP**: paramiko (preferred) or subprocess sftp fallback

Tenant configuration is read from the ``artifact_config`` JSON column in the
platform.db ``tenants`` table.  Every delivery is audit-logged to the
``audit_platform`` table (append-only, NIST AU compliant).

Usage (library):
    from tools.saas.artifacts.delivery_engine import deliver_artifact
    result = deliver_artifact("tenant-abc123", ".tmp/ssp.json", "ssp")

Usage (CLI):
    python tools/saas/artifacts/delivery_engine.py \\
        --tenant-id tenant-abc123 \\
        --artifact .tmp/ssp.json \\
        --type ssp

    python tools/saas/artifacts/delivery_engine.py \\
        --tenant-id tenant-abc123 --history --limit 20
"""

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.saas.platform_db import get_platform_connection, log_platform_audit  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("saas.artifacts.delivery")

# ---------------------------------------------------------------------------
# Optional dependency flags
# ---------------------------------------------------------------------------
try:
    import boto3  # noqa: F401
    from botocore.exceptions import ClientError  # noqa: F401
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
    logger.debug("boto3 not available -- S3 delivery disabled")

try:
    import paramiko  # noqa: F401
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False
    logger.debug("paramiko not available -- SFTP will use subprocess fallback")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_tenant_artifact_config(tenant_id: str) -> dict:
    """Load and parse the artifact_config JSON for a tenant.

    Returns:
        dict with at least ``type`` key (s3 | git | sftp).

    Raises:
        ValueError: if tenant not found or artifact_config is empty/invalid.
    """
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT artifact_config FROM tenants WHERE id = ?",
            (tenant_id,),
        ).fetchone()
        if not row:
            raise ValueError(
                "Tenant not found: {}".format(tenant_id))

        raw = row[0] if isinstance(row, (list, tuple)) else row["artifact_config"]
        if not raw or raw in ("{}", "null", ""):
            raise ValueError(
                "Tenant {} has no artifact_config configured. "
                "Set via tenant_manager --update.".format(tenant_id))

        config = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(config, dict) or "type" not in config:
            raise ValueError(
                "artifact_config for tenant {} is missing 'type' field. "
                "Expected s3, git, or sftp.".format(tenant_id))

        return config
    finally:
        conn.close()


def _record_delivery(tenant_id: str, artifact_type: str, destination: str,
                     status: str, details: dict = None):
    """Record a delivery event in the platform audit trail."""
    try:
        log_platform_audit(
            event_type="artifact.delivered",
            action="Delivered {} artifact to {}".format(artifact_type, destination),
            tenant_id=tenant_id,
            details={
                "artifact_type": artifact_type,
                "destination": destination,
                "status": status,
                "delivered_at": _utcnow(),
                **(details or {}),
            },
        )
    except Exception as exc:
        logger.warning("Failed to record delivery audit: %s", exc)


# ============================================================================
# S3 Delivery
# ============================================================================

def _deliver_s3(config: dict, artifact_path: str,
                artifact_type: str) -> dict:
    """Upload an artifact to an S3 bucket using STS assume-role.

    Config keys:
        bucket (str):       Target S3 bucket name (required).
        region (str):       AWS region, defaults to us-gov-west-1.
        role_arn (str):     IAM role ARN to assume for cross-account upload.
        path_prefix (str):  Key prefix inside the bucket (e.g. "artifacts/").
        kms_key_id (str):   Optional KMS key for server-side encryption.

    Returns:
        dict with s3_key, bucket, etag, uploaded_at.
    """
    if not HAS_BOTO3:
        raise RuntimeError(
            "boto3 is required for S3 delivery. "
            "Install with: pip install boto3")

    bucket = config.get("bucket")
    if not bucket:
        raise ValueError("artifact_config.bucket is required for S3 delivery")

    region = config.get("region", "us-gov-west-1")
    role_arn = config.get("role_arn")
    path_prefix = config.get("path_prefix", "artifacts/").rstrip("/") + "/"
    kms_key_id = config.get("kms_key_id")

    # Resolve credentials via STS assume-role if role_arn provided
    if role_arn:
        sts = boto3.client("sts", region_name=region)
        assumed = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName="icdev-artifact-delivery-{}".format(
                uuid.uuid4().hex[:8]),
            DurationSeconds=900,
        )
        creds = assumed["Credentials"]
        s3_client = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    else:
        s3_client = boto3.client("s3", region_name=region)

    filename = Path(artifact_path).name
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    s3_key = "{}{}/{}-{}".format(path_prefix, artifact_type, timestamp, filename)

    extra_args = {}
    if kms_key_id:
        extra_args["ServerSideEncryption"] = "aws:kms"
        extra_args["SSEKMSKeyId"] = kms_key_id

    logger.info("Uploading %s -> s3://%s/%s", artifact_path, bucket, s3_key)
    s3_client.upload_file(
        str(artifact_path), bucket, s3_key, ExtraArgs=extra_args or None)

    # upload_file returns None on success; head the object for ETag
    head = s3_client.head_object(Bucket=bucket, Key=s3_key)
    etag = head.get("ETag", "").strip('"')

    return {
        "method": "s3",
        "bucket": bucket,
        "s3_key": s3_key,
        "etag": etag,
        "region": region,
        "uploaded_at": _utcnow(),
    }


# ============================================================================
# Git Delivery
# ============================================================================

def _deliver_git(config: dict, artifact_path: str,
                 artifact_type: str) -> dict:
    """Commit and push an artifact to a Git repository.

    Config keys:
        repo_url (str):     Git remote URL (required).
        branch (str):       Target branch, defaults to "main".
        path_prefix (str):  Directory inside the repo for artifacts.
        commit_user (str):  Git commit author name.
        commit_email (str): Git commit author email.

    Returns:
        dict with repo_url, branch, commit_sha, pushed_at.
    """
    repo_url = config.get("repo_url")
    if not repo_url:
        raise ValueError(
            "artifact_config.repo_url is required for Git delivery")

    branch = config.get("branch", "main")
    path_prefix = config.get("path_prefix", "artifacts").strip("/")
    commit_user = config.get("commit_user", "ICDEV Bot")
    commit_email = config.get("commit_email", "icdev-bot@icdev.local")

    filename = Path(artifact_path).name
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    tmpdir = tempfile.mkdtemp(prefix="icdev-git-delivery-")
    try:
        # Clone (shallow, single branch)
        logger.info("Cloning %s (branch=%s)", repo_url, branch)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch,
             repo_url, tmpdir],
            check=True, capture_output=True, text=True, timeout=120,
        )

        # Copy artifact into repo
        dest_dir = Path(tmpdir) / path_prefix / artifact_type
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / "{}-{}".format(timestamp, filename)
        shutil.copy2(str(artifact_path), str(dest_file))

        # Configure git user
        subprocess.run(
            ["git", "-C", tmpdir, "config", "user.name", commit_user],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", tmpdir, "config", "user.email", commit_email],
            check=True, capture_output=True, text=True,
        )

        # Stage and commit
        rel_path = str(dest_file.relative_to(tmpdir))
        subprocess.run(
            ["git", "-C", tmpdir, "add", rel_path],
            check=True, capture_output=True, text=True,
        )

        commit_msg = "[ICDEV] Deliver {} artifact: {}".format(
            artifact_type, filename)
        subprocess.run(
            ["git", "-C", tmpdir, "commit", "-m", commit_msg],
            check=True, capture_output=True, text=True,
        )

        # Get commit SHA
        result = subprocess.run(
            ["git", "-C", tmpdir, "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        commit_sha = result.stdout.strip()

        # Push
        logger.info("Pushing commit %s to %s/%s", commit_sha[:8], repo_url, branch)
        subprocess.run(
            ["git", "-C", tmpdir, "push", "origin", branch],
            check=True, capture_output=True, text=True, timeout=120,
        )

        return {
            "method": "git",
            "repo_url": repo_url,
            "branch": branch,
            "commit_sha": commit_sha,
            "file_path": rel_path,
            "pushed_at": _utcnow(),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================================
# SFTP Delivery
# ============================================================================

def _deliver_sftp(config: dict, artifact_path: str,
                  artifact_type: str) -> dict:
    """Upload an artifact via SFTP.

    Config keys:
        sftp_host (str):    Remote hostname (required).
        sftp_port (int):    Port, defaults to 22.
        sftp_user (str):    Username, defaults to "icdev".
        sftp_key_path (str): Path to SSH private key (optional).
        path_prefix (str):  Remote directory prefix.

    Returns:
        dict with sftp_host, remote_path, uploaded_at.
    """
    sftp_host = config.get("sftp_host")
    if not sftp_host:
        raise ValueError(
            "artifact_config.sftp_host is required for SFTP delivery")

    sftp_port = int(config.get("sftp_port", 22))
    sftp_user = config.get("sftp_user", "icdev")
    sftp_key_path = config.get("sftp_key_path")
    path_prefix = config.get("path_prefix", "/upload/artifacts").rstrip("/")

    filename = Path(artifact_path).name
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    remote_dir = "{}/{}".format(path_prefix, artifact_type)
    remote_path = "{}/{}-{}".format(remote_dir, timestamp, filename)

    if HAS_PARAMIKO:
        return _sftp_paramiko(
            sftp_host, sftp_port, sftp_user, sftp_key_path,
            artifact_path, remote_dir, remote_path)
    else:
        return _sftp_subprocess(
            sftp_host, sftp_port, sftp_user, sftp_key_path,
            artifact_path, remote_path)


def _sftp_paramiko(host, port, user, key_path, local_path,
                   remote_dir, remote_path):
    """SFTP upload using paramiko library."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": host,
        "port": port,
        "username": user,
    }
    if key_path:
        connect_kwargs["key_filename"] = key_path

    try:
        logger.info("Connecting to %s@%s:%d via paramiko", user, host, port)
        ssh.connect(**connect_kwargs)
        sftp = ssh.open_sftp()

        # Ensure remote directory exists
        _sftp_makedirs(sftp, remote_dir)

        logger.info("Uploading %s -> %s:%s", local_path, host, remote_path)
        sftp.put(str(local_path), remote_path)

        stat = sftp.stat(remote_path)
        sftp.close()

        return {
            "method": "sftp",
            "sftp_host": host,
            "remote_path": remote_path,
            "size_bytes": stat.st_size,
            "uploaded_at": _utcnow(),
        }
    finally:
        ssh.close()


def _sftp_makedirs(sftp, remote_dir):
    """Recursively create remote directories via SFTP."""
    parts = remote_dir.strip("/").split("/")
    current = ""
    for part in parts:
        current = current + "/" + part
        try:
            sftp.stat(current)
        except IOError:
            try:
                sftp.mkdir(current)
            except IOError:
                pass  # May already exist in a race


def _sftp_subprocess(host, port, user, key_path, local_path, remote_path):
    """SFTP upload using subprocess (fallback when paramiko unavailable)."""
    cmd = ["sftp", "-P", str(port)]
    if key_path:
        cmd.extend(["-i", key_path])
    cmd.append("{}@{}".format(user, host))

    sftp_commands = "put {} {}\nquit\n".format(local_path, remote_path)

    logger.info("Uploading %s -> %s:%s via subprocess sftp",
                local_path, host, remote_path)
    result = subprocess.run(
        cmd, input=sftp_commands, capture_output=True,
        text=True, timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "SFTP upload failed (rc={}): {}".format(
                result.returncode, result.stderr.strip()))

    return {
        "method": "sftp",
        "sftp_host": host,
        "remote_path": remote_path,
        "uploaded_at": _utcnow(),
    }


# ============================================================================
# Public API
# ============================================================================

def deliver_artifact(tenant_id: str, artifact_path: str,
                     artifact_type: str, metadata: dict = None) -> dict:
    """Deliver a compliance artifact to the tenant's configured storage.

    Args:
        tenant_id:      Platform tenant identifier.
        artifact_path:  Local filesystem path to the artifact file.
        artifact_type:  Type label (ssp, poam, stig, sbom, oscal, etc.).
        metadata:       Optional dict of extra metadata to record.

    Returns:
        dict with delivery status, method, destination details, and timing.

    Raises:
        ValueError:  Tenant not found, no config, or unsupported type.
        RuntimeError: Delivery transport failure.
        FileNotFoundError: artifact_path does not exist.
    """
    artifact_file = Path(artifact_path)
    if not artifact_file.exists():
        raise FileNotFoundError(
            "Artifact file not found: {}".format(artifact_path))

    config = _load_tenant_artifact_config(tenant_id)
    delivery_type = config["type"].lower()

    delivery_id = "dlv-" + uuid.uuid4().hex[:12]
    started_at = _utcnow()

    try:
        if delivery_type == "s3":
            result = _deliver_s3(config, str(artifact_file), artifact_type)
        elif delivery_type == "git":
            result = _deliver_git(config, str(artifact_file), artifact_type)
        elif delivery_type == "sftp":
            result = _deliver_sftp(config, str(artifact_file), artifact_type)
        else:
            raise ValueError(
                "Unsupported delivery type '{}'. "
                "Supported: s3, git, sftp.".format(delivery_type))

        result.update({
            "delivery_id": delivery_id,
            "tenant_id": tenant_id,
            "artifact_type": artifact_type,
            "artifact_file": str(artifact_file.name),
            "artifact_size_bytes": artifact_file.stat().st_size,
            "status": "delivered",
            "started_at": started_at,
            "completed_at": _utcnow(),
        })
        if metadata:
            result["metadata"] = metadata

        _record_delivery(
            tenant_id, artifact_type,
            destination=delivery_type,
            status="delivered",
            details={
                "delivery_id": delivery_id,
                "artifact_file": str(artifact_file.name),
            },
        )

        logger.info("Delivery %s complete: %s -> %s (%s)",
                     delivery_id, artifact_type, delivery_type,
                     result.get("completed_at"))
        return result

    except Exception as exc:
        _record_delivery(
            tenant_id, artifact_type,
            destination=delivery_type,
            status="failed",
            details={
                "delivery_id": delivery_id,
                "error": str(exc),
            },
        )
        logger.error("Delivery %s failed: %s", delivery_id, exc)
        raise


def get_delivery_history(tenant_id: str, limit: int = 50) -> list:
    """Retrieve recent artifact delivery records for a tenant.

    Reads from the audit_platform table filtered by event_type and tenant_id.

    Args:
        tenant_id: Platform tenant identifier.
        limit:     Maximum number of records to return.

    Returns:
        List of dicts with delivery details, ordered newest-first.
    """
    conn = get_platform_connection()
    try:
        rows = conn.execute(
            """SELECT id, tenant_id, event_type, action, details,
                      recorded_at
               FROM audit_platform
               WHERE tenant_id = ? AND event_type = 'artifact.delivered'
               ORDER BY recorded_at DESC
               LIMIT ?""",
            (tenant_id, limit),
        ).fetchall()

        results = []
        for row in rows:
            entry = {
                "id": row[0] if isinstance(row, (list, tuple)) else row["id"],
                "tenant_id": row[1] if isinstance(row, (list, tuple)) else row["tenant_id"],
                "event_type": row[2] if isinstance(row, (list, tuple)) else row["event_type"],
                "action": row[3] if isinstance(row, (list, tuple)) else row["action"],
                "recorded_at": row[5] if isinstance(row, (list, tuple)) else row["recorded_at"],
            }
            raw_details = row[4] if isinstance(row, (list, tuple)) else row["details"]
            if raw_details and isinstance(raw_details, str):
                try:
                    entry["details"] = json.loads(raw_details)
                except json.JSONDecodeError:
                    entry["details"] = raw_details
            else:
                entry["details"] = raw_details or {}
            results.append(entry)

        return results
    finally:
        conn.close()


# ============================================================================
# CLI
# ============================================================================

def main():
    """CLI entry point for artifact delivery."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI -- ICDEV Artifact Delivery Engine",
    )
    parser.add_argument("--tenant-id", required=True,
                        help="Target tenant ID")
    parser.add_argument("--artifact", type=str,
                        help="Path to artifact file to deliver")
    parser.add_argument("--type", type=str, dest="artifact_type",
                        help="Artifact type (ssp, poam, stig, sbom, oscal)")
    parser.add_argument("--history", action="store_true",
                        help="Show delivery history instead of delivering")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max history records to return (default 50)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output as JSON")

    args = parser.parse_args()

    try:
        if args.history:
            records = get_delivery_history(args.tenant_id, limit=args.limit)
            if args.as_json:
                print(json.dumps(records, indent=2, default=str))
            else:
                if not records:
                    print("No delivery history for tenant {}.".format(
                        args.tenant_id))
                else:
                    for rec in records:
                        print("-" * 60)
                        print("  ID:        {}".format(rec["id"]))
                        print("  Action:    {}".format(rec["action"]))
                        print("  Recorded:  {}".format(rec["recorded_at"]))
                        details = rec.get("details", {})
                        if isinstance(details, dict):
                            for k, v in details.items():
                                print("  {}:  {}".format(k, v))
                    print("\nTotal: {}".format(len(records)))
        else:
            if not args.artifact or not args.artifact_type:
                parser.error(
                    "--artifact and --type are required for delivery")
            result = deliver_artifact(
                tenant_id=args.tenant_id,
                artifact_path=args.artifact,
                artifact_type=args.artifact_type,
            )
            if args.as_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print("[{}] {}".format(
                    result["status"].upper(), result.get("delivery_id")))
                for k, v in result.items():
                    if k not in ("status", "delivery_id"):
                        print("  {}: {}".format(k, v))

    except (ValueError, FileNotFoundError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print("FATAL: {}".format(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
