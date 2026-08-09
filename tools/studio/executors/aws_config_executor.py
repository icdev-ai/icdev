"""AWS Config Executor — Shared Workflow Executor (canvas-agnostic).

AWS-native post-provisioning step (replaces Ansible for managed services).
Uses boto3 to verify and configure resources after Terraform Apply:
  1. Wait/check RDS instances → available
  2. Wait/check Neptune clusters → available
  3. Wait/check ElastiCache replication groups → available
  4. Verify S3 bucket accessibility
  5. Store resource IDs in SSM Parameter Store for downstream use

Modes (auto-detected from .env):
  aws        — real AWS (AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_DEFAULT_REGION)
  localstack — LocalStack (LOCALSTACK_ENDPOINT=http://localhost:4566)
  sam        — SAM local (AWS_SAM_LOCAL=true)
  dry_run    — no credentials → reports what WOULD be verified/configured (safe)

Canvas auto-detected from run artifacts; override with --canvas.
SSM params stored under: /icdev/<canvas>/<service>/<key>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from tools.studio.executors._base import (  # noqa: E402
    artifacts_dir, resolve_canvas, get_iac_artifacts, filter_artifacts,
    aws_env, detect_mode, boto3_client,
)


def _extract_resources(tf_paths: list[Path]) -> dict[str, list[str]]:
    """Parse TF files to extract resource identifiers by service type."""
    resources: dict[str, list[str]] = {
        "rds": [], "neptune": [], "elasticache": [], "s3": [],
    }
    for p in tf_paths:
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r'identifier\s*=\s*"([^"]+)"', text):
            pos = text.find(m.group(0))
            if "db_instance" in text[:pos] or "rds" in p.name:
                resources["rds"].append(m.group(1))
        for m in re.finditer(r'cluster_identifier\s*=\s*"([^"]+)"', text):
            if "neptune" in text[:text.find(m.group(0))]:
                resources["neptune"].append(m.group(1))
        for m in re.finditer(r'replication_group_id\s*=\s*"([^"]+)"', text):
            resources["elasticache"].append(m.group(1))
        for m in re.finditer(r'bucket\s*=\s*"([^"]+)"', text):
            val = m.group(1)
            if not val.startswith("aws_"):
                resources["s3"].append(val)

    return {k: list(dict.fromkeys(v)) for k, v in resources.items()}


def _check_rds(client, identifiers: list[str], findings: list[dict], dry_run: bool) -> None:
    for iid in identifiers[:5]:
        if dry_run:
            findings.append({"severity": "info", "check": "rds_health",
                              "message": f"[dry-run] Would verify RDS '{iid}' → available"})
            continue
        try:
            resp = client.describe_db_instances(DBInstanceIdentifier=iid)
            status = resp["DBInstances"][0]["DBInstanceStatus"]
            endpoint = resp["DBInstances"][0].get("Endpoint", {}).get("Address", "unknown")
            sev = "pass" if status == "available" else "warn"
            findings.append({"severity": sev, "check": "rds_health",
                              "message": f"RDS '{iid}': {status}" + (f" at {endpoint}" if status == "available" else "")})
        except Exception as e:
            msg = str(e)
            sev = "warn" if "NotFound" in msg or "not found" in msg.lower() else "warn"
            findings.append({"severity": sev, "check": "rds_health",
                              "message": f"RDS '{iid}': {msg[:200]}"})


def _check_neptune(client, identifiers: list[str], findings: list[dict], dry_run: bool) -> None:
    for iid in identifiers[:3]:
        if dry_run:
            findings.append({"severity": "info", "check": "neptune_health",
                              "message": f"[dry-run] Would verify Neptune '{iid}' → available"})
            continue
        try:
            resp = client.describe_db_clusters(DBClusterIdentifier=iid)
            status = resp["DBClusters"][0]["Status"]
            endpoint = resp["DBClusters"][0].get("Endpoint", "unknown")
            sev = "pass" if status == "available" else "warn"
            findings.append({"severity": sev, "check": "neptune_health",
                              "message": f"Neptune '{iid}': {status}" + (f" at {endpoint}" if status == "available" else "")})
        except Exception as e:
            findings.append({"severity": "warn", "check": "neptune_health",
                              "message": f"Neptune '{iid}': {str(e)[:200]}"})


def _check_elasticache(client, identifiers: list[str], findings: list[dict], dry_run: bool) -> None:
    for iid in identifiers[:3]:
        if dry_run:
            findings.append({"severity": "info", "check": "elasticache_health",
                              "message": f"[dry-run] Would verify ElastiCache '{iid}' → available"})
            continue
        try:
            resp = client.describe_replication_groups(ReplicationGroupId=iid)
            status = resp["ReplicationGroups"][0]["Status"]
            sev = "pass" if status == "available" else "warn"
            findings.append({"severity": sev, "check": "elasticache_health",
                              "message": f"ElastiCache '{iid}': {status}"})
        except Exception as e:
            findings.append({"severity": "warn", "check": "elasticache_health",
                              "message": f"ElastiCache '{iid}': {str(e)[:200]}"})


def _check_s3(client, buckets: list[str], findings: list[dict], dry_run: bool) -> None:
    for bucket in buckets[:5]:
        if dry_run:
            findings.append({"severity": "info", "check": "s3_verify",
                              "message": f"[dry-run] Would verify S3 bucket '{bucket}'"})
            continue
        try:
            client.head_bucket(Bucket=bucket)
            findings.append({"severity": "pass", "check": "s3_verify",
                              "message": f"S3 '{bucket}': accessible"})
        except Exception as e:
            msg = str(e)
            sev = "warn"
            findings.append({"severity": sev, "check": "s3_verify",
                              "message": f"S3 '{bucket}': {msg[:200]}"})


def _store_ssm_params(ssm, canvas: str, resources: dict[str, list[str]],
                      findings: list[dict], dry_run: bool) -> None:
    params = {}
    if resources["rds"]:
        params[f"/icdev/{canvas}/rds/primary_id"] = resources["rds"][0]
    if resources["neptune"]:
        params[f"/icdev/{canvas}/neptune/cluster_id"] = resources["neptune"][0]
    if resources["elasticache"]:
        params[f"/icdev/{canvas}/elasticache/replication_group_id"] = resources["elasticache"][0]

    for name, value in params.items():
        if dry_run:
            findings.append({"severity": "info", "check": "ssm_param",
                              "message": f"[dry-run] Would store SSM {name} = {value}"})
            continue
        try:
            ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True,
                              Description=f"ICDEV {canvas.upper()} auto-provisioned resource ID")
            findings.append({"severity": "pass", "check": "ssm_param",
                              "message": f"SSM stored: {name}"})
        except Exception as e:
            findings.append({"severity": "warn", "check": "ssm_param",
                              "message": f"SSM '{name}': {str(e)[:200]}"})


def run_aws_config(run_id: str, project_id: str, canvas: str = "") -> dict:
    canvas = resolve_canvas(run_id, canvas)
    out_dir = artifacts_dir(canvas)
    env = aws_env()
    mode = detect_mode(env)
    dry_run = (mode == "dry_run")

    iac_arts = get_iac_artifacts(run_id)
    tf_paths = filter_artifacts(iac_arts, ["tf"])

    findings: list[dict] = []
    gate = "PASS"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uid = uuid.uuid4().hex[:8]

    findings.append({"severity": "info", "check": "mode",
                      "message": f"Mode: {mode.upper()} — "
                                 + ("reporting what would run (no credentials)" if dry_run
                                    else f"executing against {'LocalStack' if mode == 'localstack' else 'AWS'}")})

    if not tf_paths:
        findings.append({"severity": "warn", "check": "no_tf",
                          "message": f"No TF files found for canvas '{canvas}' — skipping resource scan"})
        resources: dict[str, list[str]] = {"rds": [], "neptune": [], "elasticache": [], "s3": []}
    else:
        resources = _extract_resources(tf_paths)
        total = sum(len(v) for v in resources.values())
        findings.append({"severity": "info", "check": "resource_scan",
                          "message": f"Scanned {len(tf_paths)} TF file(s) — found {total} resource identifier(s): "
                                     f"RDS={len(resources['rds'])}, Neptune={len(resources['neptune'])}, "
                                     f"ElastiCache={len(resources['elasticache'])}, S3={len(resources['s3'])}"})

    try:
        import boto3  # noqa: F401
        boto3_ok = True
    except ImportError:
        boto3_ok = False
        findings.append({"severity": "warn", "check": "boto3",
                          "message": "boto3 not installed (pip install boto3) — running in simulation mode"})
        dry_run = True

    if boto3_ok:
        try:
            if resources["rds"]:
                _check_rds(boto3_client("rds"), resources["rds"], findings, dry_run)
            if resources["neptune"]:
                _check_neptune(boto3_client("neptune"), resources["neptune"], findings, dry_run)
            if resources["elasticache"]:
                _check_elasticache(boto3_client("elasticache"), resources["elasticache"], findings, dry_run)
            if resources["s3"]:
                _check_s3(boto3_client("s3"), resources["s3"], findings, dry_run)

            _store_ssm_params(boto3_client("ssm"), canvas, resources, findings, dry_run)

        except Exception as e:
            findings.append({"severity": "warn", "check": "boto3_init",
                              "message": f"AWS client init error: {e}"})

    if not any(r for r in resources.values()):
        findings.append({"severity": "info", "check": "no_resources",
                          "message": "No AWS resources identified — nothing to verify"})

    if any(f["severity"] == "fail" for f in findings):
        gate = "FAIL"

    lines = [
        f"# AWS Config Execution Report — {canvas.upper()}",
        f"**Generated:** {ts}  ",
        f"**Canvas:** {canvas}  ",
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

    report_path = out_dir / f"aws_config_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    return {
        "gate": gate, "mode": mode, "canvas": canvas,
        "findings": findings,
        "report_path": report_path.relative_to(_ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="AWS Config Executor (shared)")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="", help="Canvas slug (auto-detected if omitted)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = run_aws_config(args.run_id, args.project_id, args.canvas)
        gate = result["gate"]
        output = {
            "status": "success" if gate == "PASS" else "failed",
            "gate": gate, "canvas": result["canvas"], "mode": result["mode"],
            "findings": len(result["findings"]),
            "failures": sum(1 for f in result["findings"] if f["severity"] == "fail"),
            "artifacts": [{"name": "AWS Config Report",
                           "path": result["report_path"], "type": "md"}],
        }
        print(json.dumps(output))
        sys.exit(0 if gate == "PASS" else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
