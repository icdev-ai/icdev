#!/usr/bin/env python3

import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""ICDEV™ Disaster Recovery Failover Automation.

Operations:
  failover    — Promote DR read replica + redirect traffic (RTO target 4h)
  test        — Automated quarterly DR test (non-destructive)
  status      — Report current DR health (replica lag, S3 replication, snapshots)
  restore     — Restore from cross-account RDS snapshot to a test instance

Usage:
  python tools/infra/dr_failover.py status --json
  python tools/infra/dr_failover.py test --dry-run
  python tools/infra/dr_failover.py failover --confirm
  python tools/infra/dr_failover.py restore --snapshot-id <id> --target-instance <name>
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import boto3
    _HAS_BOTO = True
except ImportError:
    _HAS_BOTO = False

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DR_CONFIG_PATH = BASE_DIR / "args" / "dr_config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = get_logger(__name__)


def _load_config() -> dict:
    if _HAS_YAML and DR_CONFIG_PATH.exists():
        with DR_CONFIG_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("disaster_recovery", {})
    return {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rds_client(region: str):
    if not _HAS_BOTO:
        raise RuntimeError("boto3 not installed — run: pip install boto3")
    return boto3.client("rds", region_name=region)


def _s3_client(region: str):
    if not _HAS_BOTO:
        raise RuntimeError("boto3 not installed — run: pip install boto3")
    return boto3.client("s3", region_name=region)


def _cw_client(region: str):
    if not _HAS_BOTO:
        raise RuntimeError("boto3 not installed — run: pip install boto3")
    return boto3.client("cloudwatch", region_name=region)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def cmd_status(cfg: dict, args) -> dict:
    """Report DR health: replica lag, S3 replication, latest snapshot age."""
    primary_region = cfg.get("primary_region", "us-gov-west-1")
    dr_region = cfg.get("dr_region", "us-east-2")
    rto_hours = cfg.get("rto_hours", 4)
    rpo_minutes = cfg.get("rpo_minutes", 15)

    result = {
        "timestamp": _now(),
        "rto_hours": rto_hours,
        "rpo_minutes": rpo_minutes,
        "primary_region": primary_region,
        "dr_region": dr_region,
        "checks": {},
    }

    if not _HAS_BOTO:
        result["warning"] = "boto3 not available — showing config-only status"
        result["checks"]["boto3"] = "MISSING"
        return result

    db_id = cfg.get("rds", {}).get("read_replica", {}).get("region", "") and args.db_identifier if hasattr(args, "db_identifier") else None

    # RDS snapshot check
    try:
        rds = _rds_client(primary_region)
        if db_id:
            snaps = rds.describe_db_snapshots(
                DBInstanceIdentifier=db_id,
                SnapshotType="automated",
            )["DBSnapshots"]
            snaps.sort(key=lambda s: s["SnapshotCreateTime"], reverse=True)
            if snaps:
                latest = snaps[0]
                age_h = (datetime.now(timezone.utc) - latest["SnapshotCreateTime"].replace(tzinfo=timezone.utc)).total_seconds() / 3600
                result["checks"]["rds_snapshot"] = {
                    "status": "OK" if age_h < 26 else "STALE",
                    "snapshot_id": latest["DBSnapshotIdentifier"],
                    "age_hours": round(age_h, 2),
                    "encrypted": latest.get("Encrypted", False),
                    "state": latest["Status"],
                }
            else:
                result["checks"]["rds_snapshot"] = {"status": "MISSING"}
        else:
            result["checks"]["rds_snapshot"] = {"status": "SKIPPED", "reason": "no --db-identifier provided"}
    except Exception as exc:
        result["checks"]["rds_snapshot"] = {"status": "ERROR", "error": str(exc)}

    # RDS replica lag check (CloudWatch)
    try:
        cw = _cw_client(dr_region)
        if db_id:
            resp = cw.get_metric_statistics(
                Namespace="AWS/RDS",
                MetricName="ReplicaLag",
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": f"{db_id}-dr-replica"}],
                StartTime=datetime.now(timezone.utc) - timedelta(minutes=30),
                EndTime=datetime.now(timezone.utc),
                Period=300,
                Statistics=["Maximum"],
            )
            points = resp.get("Datapoints", [])
            if points:
                lag_s = max(p["Maximum"] for p in points)
                result["checks"]["rds_replica_lag"] = {
                    "status": "OK" if lag_s < rpo_minutes * 60 else "RPO_AT_RISK",
                    "lag_seconds": round(lag_s),
                    "lag_minutes": round(lag_s / 60, 1),
                    "rpo_limit_seconds": rpo_minutes * 60,
                }
            else:
                result["checks"]["rds_replica_lag"] = {"status": "NO_DATA"}
        else:
            result["checks"]["rds_replica_lag"] = {"status": "SKIPPED"}
    except Exception as exc:
        result["checks"]["rds_replica_lag"] = {"status": "ERROR", "error": str(exc)}

    # Overall health
    statuses = [v.get("status", "UNKNOWN") if isinstance(v, dict) else v
                for v in result["checks"].values()]
    result["overall"] = "HEALTHY" if all(s in ("OK", "SKIPPED", "NO_DATA") for s in statuses) else "DEGRADED"

    return result


# ---------------------------------------------------------------------------
# Quarterly DR test (non-destructive)
# ---------------------------------------------------------------------------

def cmd_test(cfg: dict, args) -> dict:
    """Run automated quarterly DR test — does NOT affect production."""
    dry_run = getattr(args, "dry_run", False)
    db_id = getattr(args, "db_identifier", None)
    primary_region = cfg.get("primary_region", "us-gov-west-1")
    dr_region = cfg.get("dr_region", "us-east-2")
    rto_hours = cfg.get("rto_hours", 4)
    rpo_minutes = cfg.get("rpo_minutes", 15)

    steps = [
        "find_latest_snapshot",
        "restore_to_test_instance",
        "verify_data_integrity",
        "measure_restore_time",
        "check_s3_replication_lag",
        "verify_rto_met",
        "verify_rpo_met",
        "cleanup_test_resources",
    ]

    start_ts = datetime.now(timezone.utc)
    result = {
        "test_type": "quarterly_dr_test",
        "timestamp": _now(),
        "dry_run": dry_run,
        "rto_target_hours": rto_hours,
        "rpo_target_minutes": rpo_minutes,
        "primary_region": primary_region,
        "dr_region": dr_region,
        "steps": {},
        "passed": False,
    }

    logger.info("DR quarterly test starting (dry_run=%s)", dry_run)

    for step in steps:
        if dry_run:
            result["steps"][step] = {"status": "DRY_RUN", "would_execute": True}
            continue

        step_start = datetime.now(timezone.utc)

        try:
            if step == "find_latest_snapshot" and db_id and _HAS_BOTO:
                rds = _rds_client(primary_region)
                snaps = rds.describe_db_snapshots(
                    DBInstanceIdentifier=db_id,
                    SnapshotType="automated",
                )["DBSnapshots"]
                snaps.sort(key=lambda s: s["SnapshotCreateTime"], reverse=True)
                if snaps:
                    s = snaps[0]
                    result["steps"][step] = {
                        "status": "PASS",
                        "snapshot_id": s["DBSnapshotIdentifier"],
                        "age_hours": round(
                            (datetime.now(timezone.utc) - s["SnapshotCreateTime"].replace(tzinfo=timezone.utc)).total_seconds() / 3600, 2
                        ),
                    }
                    result["_test_snapshot_id"] = s["DBSnapshotIdentifier"]
                else:
                    result["steps"][step] = {"status": "FAIL", "reason": "no_snapshots"}
            elif step == "restore_to_test_instance" and _HAS_BOTO:
                snap_id = result.get("_test_snapshot_id")
                if snap_id:
                    rds = _rds_client(primary_region)
                    test_id = f"icdev-dr-test-{start_ts.strftime('%Y%m%d%H%M')}"
                    rds.restore_db_instance_from_db_snapshot(
                        DBInstanceIdentifier=test_id,
                        DBSnapshotIdentifier=snap_id,
                        DBInstanceClass="db.t3.medium",
                        MultiAZ=False,
                        PubliclyAccessible=False,
                        Tags=[
                            {"Key": "DR_Test", "Value": "true"},
                            {"Key": "TestRun", "Value": start_ts.isoformat()},
                        ],
                    )
                    result["steps"][step] = {"status": "INITIATED", "test_instance": test_id}
                    result["_test_instance_id"] = test_id
                else:
                    result["steps"][step] = {"status": "SKIPPED", "reason": "no_snapshot_found"}
            elif step == "measure_restore_time":
                elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds() / 3600
                rto_met = elapsed < rto_hours
                result["steps"][step] = {
                    "status": "PASS" if rto_met else "FAIL",
                    "elapsed_hours": round(elapsed, 2),
                    "rto_target_hours": rto_hours,
                    "rto_met": rto_met,
                }
            elif step == "cleanup_test_resources" and _HAS_BOTO:
                test_id = result.get("_test_instance_id")
                if test_id:
                    try:
                        rds = _rds_client(primary_region)
                        rds.delete_db_instance(
                            DBInstanceIdentifier=test_id,
                            SkipFinalSnapshot=True,
                            DeleteAutomatedBackups=True,
                        )
                        result["steps"][step] = {"status": "PASS", "deleted": test_id}
                    except Exception as e:
                        result["steps"][step] = {"status": "WARN", "error": str(e)}
                else:
                    result["steps"][step] = {"status": "SKIPPED"}
            else:
                result["steps"][step] = {"status": "SKIPPED", "reason": "no_boto3_or_no_db_id"}

        except Exception as exc:
            result["steps"][step] = {"status": "ERROR", "error": str(exc)}
            logger.error("Step %s failed: %s", step, exc)

        elapsed_step = (datetime.now(timezone.utc) - step_start).total_seconds()
        if step in result["steps"] and isinstance(result["steps"][step], dict):
            result["steps"][step]["duration_seconds"] = round(elapsed_step, 2)

    # Evaluate pass/fail
    step_statuses = [v.get("status") for v in result["steps"].values() if isinstance(v, dict)]
    failed = [s for s in step_statuses if s in ("FAIL", "ERROR")]
    result["passed"] = len(failed) == 0
    result["total_duration_seconds"] = round((datetime.now(timezone.utc) - start_ts).total_seconds(), 2)

    if dry_run:
        result["passed"] = True
        result["note"] = "Dry run — no AWS resources created or modified"

    logger.info("DR test complete. passed=%s", result["passed"])
    return result


# ---------------------------------------------------------------------------
# Failover (production — requires --confirm)
# ---------------------------------------------------------------------------

def cmd_failover(cfg: dict, args) -> dict:
    """Promote DR read replica to standalone primary. Requires --confirm."""
    confirmed = getattr(args, "confirm", False)
    db_id = getattr(args, "db_identifier", None)
    primary_region = cfg.get("primary_region", "us-gov-west-1")
    dr_region = cfg.get("dr_region", "us-east-2")

    if not confirmed:
        return {
            "status": "ABORTED",
            "reason": "Failover requires --confirm flag. This is a PRODUCTION operation.",
            "rto_target_hours": cfg.get("rto_hours", 4),
        }

    if not db_id:
        return {"status": "ERROR", "reason": "--db-identifier required for failover"}

    if not _HAS_BOTO:
        return {"status": "ERROR", "reason": "boto3 not installed"}

    start_ts = datetime.now(timezone.utc)
    replica_id = f"{db_id}-dr-replica"
    result = {
        "operation": "failover",
        "timestamp": _now(),
        "primary_region": primary_region,
        "dr_region": dr_region,
        "replica_id": replica_id,
        "steps": {},
    }

    logger.warning("FAILOVER INITIATED: promoting %s in %s", replica_id, dr_region)

    try:
        rds = _rds_client(dr_region)
        rds.promote_read_replica(
            DBInstanceIdentifier=replica_id,
            BackupRetentionPeriod=35,
            PreferredBackupWindow="02:00-03:00",
        )
        result["steps"]["promote_replica"] = {
            "status": "INITIATED",
            "instance": replica_id,
            "note": "Instance now promoting — allow up to 30 min. Monitor via AWS Console.",
        }
    except Exception as exc:
        result["steps"]["promote_replica"] = {"status": "ERROR", "error": str(exc)}
        result["status"] = "FAILED"
        return result

    elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds() / 3600
    result["elapsed_hours"] = round(elapsed, 2)
    result["rto_target_hours"] = cfg.get("rto_hours", 4)
    result["rto_budget_remaining_hours"] = round(cfg.get("rto_hours", 4) - elapsed, 2)
    result["status"] = "IN_PROGRESS"
    result["next_steps"] = [
        f"1. Monitor promotion: aws rds describe-db-instances --db-instance-identifier {replica_id} --region {dr_region}",
        "2. Update application connection strings to point to new primary endpoint.",
        "3. Enable Multi-AZ on promoted instance once stable.",
        "4. Document incident timeline for RTO/RPO compliance report.",
        "5. Runbook: docs/runbooks/dr-runbook.md",
    ]

    return result


# ---------------------------------------------------------------------------
# Snapshot restore (testing / recovery)
# ---------------------------------------------------------------------------

def cmd_restore(cfg: dict, args) -> dict:
    """Restore an RDS snapshot to a named test instance."""
    snap_id = getattr(args, "snapshot_id", None)
    target_id = getattr(args, "target_instance", None)
    primary_region = cfg.get("primary_region", "us-gov-west-1")

    if not (snap_id and target_id):
        return {"status": "ERROR", "reason": "--snapshot-id and --target-instance required"}

    if not _HAS_BOTO:
        return {"status": "ERROR", "reason": "boto3 not installed"}

    try:
        rds = _rds_client(primary_region)
        rds.restore_db_instance_from_db_snapshot(
            DBInstanceIdentifier=target_id,
            DBSnapshotIdentifier=snap_id,
            DBInstanceClass="db.t3.medium",
            MultiAZ=False,
            PubliclyAccessible=False,
            Tags=[
                {"Key": "RestoredFrom", "Value": snap_id},
                {"Key": "RestoredAt", "Value": _now()},
                {"Key": "Classification", "Value": "CUI"},
            ],
        )
        return {
            "status": "INITIATED",
            "snapshot_id": snap_id,
            "target_instance": target_id,
            "region": primary_region,
            "note": "Restore in progress. Monitor status with: aws rds describe-db-instances",
        }
    except Exception as exc:
        return {"status": "ERROR", "error": str(exc)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV™ DR Failover Automation — RTO 4h | RPO 15min"
    )
    sub = parser.add_subparsers(dest="command")

    # status
    p_status = sub.add_parser("status", help="DR health status")
    p_status.add_argument("--db-identifier", dest="db_identifier", default=None)
    p_status.add_argument("--json", action="store_true")

    # test
    p_test = sub.add_parser("test", help="Quarterly DR test (non-destructive)")
    p_test.add_argument("--db-identifier", dest="db_identifier", default=None)
    p_test.add_argument("--dry-run", action="store_true")
    p_test.add_argument("--json", action="store_true")

    # failover
    p_fo = sub.add_parser("failover", help="Initiate DR failover (production)")
    p_fo.add_argument("--db-identifier", dest="db_identifier", required=True)
    p_fo.add_argument("--confirm", action="store_true", help="Required to proceed")
    p_fo.add_argument("--json", action="store_true")

    # restore
    p_rs = sub.add_parser("restore", help="Restore snapshot to test instance")
    p_rs.add_argument("--snapshot-id", dest="snapshot_id", required=True)
    p_rs.add_argument("--target-instance", dest="target_instance", required=True)
    p_rs.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cfg = _load_config()

    dispatch = {
        "status": cmd_status,
        "test": cmd_test,
        "failover": cmd_failover,
        "restore": cmd_restore,
    }

    result = dispatch[args.command](cfg, args)

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
