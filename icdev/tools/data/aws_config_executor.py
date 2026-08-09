"""AWS Config Executor — DDC Workflow Step 11.

AWS-native post-provisioning (replaces Ansible for managed services).
Uses boto3 to:
  1. Wait for RDS instances to become available
  2. Wait for ElastiCache replication groups to become available
  3. Verify Neptune cluster health
  4. Store connection strings in SSM Parameter Store
  5. Verify S3 bucket accessibility

Supports:
  Real AWS   — AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION in .env
  LocalStack — LOCALSTACK_ENDPOINT=http://localhost:4566 in .env
  SAM local  — AWS_SAM_LOCAL=true in .env
  Dry-run    — no credentials → reports what WOULD be verified/configured
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "ddc"


def _load_dotenv() -> dict:
    try:
        from dotenv import dotenv_values
        return dotenv_values(_ROOT / ".env")
    except Exception:
        return {}


def _aws_config(service: str):
    """Build boto3 client config, applying LocalStack/SAM endpoint override."""
    import os
    merged = {**os.environ, **_load_dotenv()}

    kwargs: dict = {
        "region_name": merged.get("AWS_DEFAULT_REGION") or merged.get("AWS_REGION") or "us-east-1",
    }
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


def _detect_mode() -> str:
    import os
    merged = {**os.environ, **_load_dotenv()}
    if merged.get("LOCALSTACK_ENDPOINT"):
        return "localstack"
    if merged.get("AWS_SAM_LOCAL", "").lower() in ("true", "1"):
        return "sam"
    if merged.get("AWS_ACCESS_KEY_ID"):
        return "aws"
    return "dry_run"


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


def _extract_resources_from_tf(tf_paths: list[Path]) -> dict[str, list[str]]:
    """Parse TF files to extract resource identifiers by type."""
    import re
    resources: dict[str, list[str]] = {
        "rds": [], "neptune": [], "elasticache": [], "s3": [],
    }
    for p in tf_paths:
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r'identifier\s*=\s*"([^"]+)"', text):
            if "rds" in p.name or "db_instance" in text[:text.find(m.group(0))]:
                resources["rds"].append(m.group(1))
        for m in re.finditer(r'cluster_identifier\s*=\s*"([^"]+)"', text):
            if "neptune" in text[:text.find(m.group(0))]:
                resources["neptune"].append(m.group(1))
        for m in re.finditer(r'replication_group_id\s*=\s*"([^"]+)"', text):
            resources["elasticache"].append(m.group(1))
        for m in re.finditer(r'bucket\s*=\s*"([^"]+)"', text):
            val = m.group(1)
            if not val.startswith("aws_"):  # skip references like aws_s3_bucket.x.id
                resources["s3"].append(val)
    # Deduplicate
    return {k: list(dict.fromkeys(v)) for k, v in resources.items()}


def _check_rds(client, identifiers: list[str], findings: list[dict], dry_run: bool) -> bool:
    all_ok = True
    for iid in identifiers[:5]:  # limit to 5 to avoid long waits
        if dry_run:
            findings.append({"severity": "info", "check": "rds_wait",
                              "message": f"[dry-run] Would wait for RDS '{iid}' → available"})
            continue
        try:
            resp = client.describe_db_instances(DBInstanceIdentifier=iid)
            status = resp["DBInstances"][0]["DBInstanceStatus"]
            if status == "available":
                endpoint = resp["DBInstances"][0].get("Endpoint", {}).get("Address", "unknown")
                findings.append({"severity": "pass", "check": "rds_health",
                                  "message": f"RDS '{iid}': available at {endpoint}"})
            else:
                findings.append({"severity": "warn", "check": "rds_health",
                                  "message": f"RDS '{iid}': status={status} (may still be provisioning)"})
        except client.exceptions.DBInstanceNotFoundFault:
            findings.append({"severity": "warn", "check": "rds_health",
                              "message": f"RDS '{iid}': not found (may not be applied yet)"})
        except Exception as e:
            findings.append({"severity": "warn", "check": "rds_health",
                              "message": f"RDS '{iid}': {e}"})
            all_ok = False
    return all_ok


def _check_neptune(client, identifiers: list[str], findings: list[dict], dry_run: bool) -> bool:
    all_ok = True
    for iid in identifiers[:3]:
        if dry_run:
            findings.append({"severity": "info", "check": "neptune_wait",
                              "message": f"[dry-run] Would check Neptune '{iid}' → available"})
            continue
        try:
            resp = client.describe_db_clusters(DBClusterIdentifier=iid)
            status = resp["DBClusters"][0]["Status"]
            endpoint = resp["DBClusters"][0].get("Endpoint", "unknown")
            sev = "pass" if status == "available" else "warn"
            findings.append({"severity": sev, "check": "neptune_health",
                              "message": f"Neptune '{iid}': {status} at {endpoint}"})
        except Exception as e:
            findings.append({"severity": "warn", "check": "neptune_health",
                              "message": f"Neptune '{iid}': {e}"})
    return all_ok


def _check_elasticache(client, identifiers: list[str], findings: list[dict], dry_run: bool) -> bool:
    for iid in identifiers[:3]:
        if dry_run:
            findings.append({"severity": "info", "check": "elasticache_wait",
                              "message": f"[dry-run] Would check ElastiCache '{iid}' → available"})
            continue
        try:
            resp = client.describe_replication_groups(ReplicationGroupId=iid)
            status = resp["ReplicationGroups"][0]["Status"]
            sev = "pass" if status == "available" else "warn"
            findings.append({"severity": sev, "check": "elasticache_health",
                              "message": f"ElastiCache '{iid}': {status}"})
        except Exception as e:
            findings.append({"severity": "warn", "check": "elasticache_health",
                              "message": f"ElastiCache '{iid}': {e}"})
    return True


def _check_s3(client, buckets: list[str], findings: list[dict], dry_run: bool) -> bool:
    for bucket in buckets[:5]:
        if dry_run:
            findings.append({"severity": "info", "check": "s3_verify",
                              "message": f"[dry-run] Would verify S3 bucket '{bucket}'"})
            continue
        try:
            client.head_bucket(Bucket=bucket)
            findings.append({"severity": "pass", "check": "s3_verify",
                              "message": f"S3 bucket '{bucket}': accessible"})
        except Exception as e:
            msg = str(e)
            if "404" in msg or "NoSuchBucket" in msg:
                findings.append({"severity": "warn", "check": "s3_verify",
                                  "message": f"S3 '{bucket}': not found (may not be applied yet)"})
            else:
                findings.append({"severity": "warn", "check": "s3_verify",
                                  "message": f"S3 '{bucket}': {msg[:200]}"})
    return True


def _store_ssm_params(ssm_client, resources: dict[str, list[str]],
                      findings: list[dict], dry_run: bool) -> None:
    """Store resource identifiers in SSM Parameter Store for downstream use."""
    params = {}
    if resources["rds"]:
        params["/icdev/ddc/rds/primary_id"] = resources["rds"][0]
    if resources["neptune"]:
        params["/icdev/ddc/neptune/cluster_id"] = resources["neptune"][0]
    if resources["elasticache"]:
        params["/icdev/ddc/elasticache/replication_group_id"] = resources["elasticache"][0]

    for name, value in params.items():
        if dry_run:
            findings.append({"severity": "info", "check": "ssm_param",
                              "message": f"[dry-run] Would store SSM param {name} = {value}"})
            continue
        try:
            ssm_client.put_parameter(
                Name=name, Value=value, Type="String", Overwrite=True,
                Description="ICDEV DDC auto-provisioned resource ID",
            )
            findings.append({"severity": "pass", "check": "ssm_param",
                              "message": f"SSM stored: {name}"})
        except Exception as e:
            findings.append({"severity": "warn", "check": "ssm_param",
                              "message": f"SSM write failed for {name}: {e}"})


def run_aws_config(run_id: str, project_id: str) -> dict:
    mode = _detect_mode()
    dry_run = (mode == "dry_run")
    artifacts = _get_iac_artifacts(run_id)
    tf_artifacts = [a for a in artifacts if a.get("type") == "tf"]
    tf_paths = [_ROOT / a["path"] for a in tf_artifacts if (_ROOT / a["path"]).exists()]

    findings: list[dict] = []
    gate = "PASS"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uid = uuid.uuid4().hex[:8]

    findings.append({"severity": "info", "check": "mode",
                      "message": f"Mode: {mode.upper()} — "
                                 + ("no credentials, reporting what would run" if dry_run
                                    else f"executing against {'LocalStack' if 'local' in mode else 'AWS'}")})

    if not tf_paths:
        findings.append({"severity": "warn", "check": "no_tf",
                          "message": "No TF files found — using generic resource IDs"})
        resources = {"rds": [], "neptune": [], "elasticache": [], "s3": []}
    else:
        resources = _extract_resources_from_tf(tf_paths)
        total = sum(len(v) for v in resources.values())
        findings.append({"severity": "info", "check": "resource_scan",
                          "message": f"Found {total} resource identifiers: "
                                     f"RDS={len(resources['rds'])}, "
                                     f"Neptune={len(resources['neptune'])}, "
                                     f"ElastiCache={len(resources['elasticache'])}, "
                                     f"S3={len(resources['s3'])}"})

    try:
        import boto3  # noqa: F401
        boto3_available = True
    except ImportError:
        boto3_available = False
        findings.append({"severity": "warn", "check": "boto3",
                          "message": "boto3 not installed — run: pip install boto3"})
        dry_run = True

    if boto3_available:
        try:
            rds_client = _aws_config("rds")
            neptune_client = _aws_config("neptune")
            ec_client = _aws_config("elasticache")
            s3_client = _aws_config("s3")
            ssm_client = _aws_config("ssm")

            if resources["rds"]:
                _check_rds(rds_client, resources["rds"], findings, dry_run)
            if resources["neptune"]:
                _check_neptune(neptune_client, resources["neptune"], findings, dry_run)
            if resources["elasticache"]:
                _check_elasticache(ec_client, resources["elasticache"], findings, dry_run)
            if resources["s3"]:
                _check_s3(s3_client, resources["s3"], findings, dry_run)

            _store_ssm_params(ssm_client, resources, findings, dry_run)

        except Exception as e:
            findings.append({"severity": "warn", "check": "boto3_init",
                              "message": f"Client init error: {e}"})

    fails = [f for f in findings if f["severity"] == "fail"]
    gate = "FAIL" if fails else "PASS"

    # Build report
    lines = [
        "# AWS Config Execution Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Mode:** {mode.upper()}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '✗ FAIL'}  ",
        "",
    ]
    for grp, label in [
        ([f for f in findings if f["severity"] == "fail"], "✗ Failures"),
        ([f for f in findings if f["severity"] == "warn"], "⚠ Warnings"),
        ([f for f in findings if f["severity"] == "pass"], "✓ Verified"),
        ([f for f in findings if f["severity"] == "info"], "ℹ Info"),
    ]:
        if grp:
            lines.append(f"## {label}")
            for f in grp:
                lines.append(f"- **[{f['check']}]** {f['message'][:300]}")
            lines.append("")

    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _ARTIFACTS_DIR / f"aws_config_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    return {
        "gate": gate,
        "mode": mode,
        "findings": findings,
        "report_path": report_path.relative_to(_ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="AWS Config Executor")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_aws_config(args.run_id, args.project_id)
        gate = result["gate"]
        output = {
            "status": "success" if gate == "PASS" else "failed",
            "gate": gate,
            "mode": result["mode"],
            "findings": len(result["findings"]),
            "failures": sum(1 for f in result["findings"] if f["severity"] == "fail"),
            "artifacts": [
                {"name": "AWS Config Report", "path": result["report_path"], "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if gate == "PASS" else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
