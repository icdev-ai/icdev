#!/usr/bin/env python3
# CUI // SP-CTI
"""Retrain trigger — monitor example count, auto-trigger retraining (D-FT-17).

Checks whether datasets have accumulated enough new examples since last
training job. When new_examples >= threshold (default 50) and cooldown
has elapsed (default 24h), queues a new training job.

Designed to run as a heartbeat daemon check (D162 pattern) or standalone.

Usage:
    python tools/finetune/retrain_trigger.py --check --json
    python tools/finetune/retrain_trigger.py --check --dataset-id "ds-xxx" --json
    python tools/finetune/retrain_trigger.py --trigger --dataset-id "ds-xxx" --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _load_retrain_config() -> Dict[str, Any]:
    """Load retrain thresholds from config."""
    config_path = BASE_DIR / "args" / "finetune_config.yaml"
    if config_path.exists():
        try:
            import yaml

            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            retrain = cfg.get("retrain", {})
            return {
                "new_example_threshold": retrain.get("new_example_threshold", 50),
                "cooldown_hours": retrain.get("cooldown_hours", 24),
                "auto_trigger": retrain.get("auto_trigger", True),
            }
        except (ImportError, Exception):
            pass
    return {"new_example_threshold": 50, "cooldown_hours": 24, "auto_trigger": True}


def check_retrain_needed(
    dataset_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Check if any datasets need retraining (D-FT-17).

    For each ready dataset, compares:
    - Number of approved examples added since last training job completion
    - Against configured threshold (default 50)
    - Respects cooldown period (default 24h)

    Returns list of datasets needing retrain with details.
    """
    config = _load_retrain_config()
    threshold = config["new_example_threshold"]
    cooldown_hours = config["cooldown_hours"]

    conn = _get_db(db_path)
    try:
        # Get datasets to check
        if dataset_id:
            datasets = conn.execute(
                "SELECT * FROM ft_datasets WHERE id = %s AND status IN ('ready', 'labeling')",
                (dataset_id,),
            ).fetchall()
        else:
            datasets = conn.execute(
                "SELECT * FROM ft_datasets WHERE status IN ('ready', 'labeling')",
            ).fetchall()

        if not datasets:
            return {
                "success": True,
                "datasets_checked": 0,
                "datasets_needing_retrain": [],
                "threshold": threshold,
                "cooldown_hours": cooldown_hours,
            }

        needing_retrain: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        cooldown_cutoff = (now - timedelta(hours=cooldown_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        for ds in datasets:
            ds_id = ds["id"]

            # Find last completed training job for this dataset
            last_job = conn.execute(
                """SELECT completed_at FROM ft_training_jobs
                   WHERE dataset_id = %s AND status = 'completed'
                   ORDER BY completed_at DESC LIMIT 1""",
                (ds_id,),
            ).fetchone()

            last_trained_at = last_job["completed_at"] if last_job else None

            # Check cooldown
            if last_trained_at and last_trained_at > cooldown_cutoff:
                continue  # Still in cooldown period

            # Count new approved examples since last training
            if last_trained_at:
                new_count = conn.execute(
                    """SELECT COUNT(*) FROM ft_dataset_examples
                       WHERE dataset_id = %s AND approved = 1
                         AND created_at > %s""",
                    (ds_id, last_trained_at),
                ).fetchone()[0]
            else:
                # Never trained — count all approved examples
                new_count = conn.execute(
                    """SELECT COUNT(*) FROM ft_dataset_examples
                       WHERE dataset_id = %s AND approved = 1""",
                    (ds_id,),
                ).fetchone()[0]

            # Check any active training jobs (don't re-trigger)
            active_job = conn.execute(
                """SELECT id FROM ft_training_jobs
                   WHERE dataset_id = %s AND status NOT IN ('completed', 'failed', 'canceled')
                   LIMIT 1""",
                (ds_id,),
            ).fetchone()

            if active_job:
                continue  # Training already in progress

            if new_count >= threshold:
                needing_retrain.append(
                    {
                        "dataset_id": ds_id,
                        "dataset_name": ds["name"],
                        "new_examples": new_count,
                        "threshold": threshold,
                        "last_trained_at": last_trained_at,
                        "purpose": ds["purpose"],
                        "base_model": ds["base_model"],
                    }
                )

        return {
            "success": True,
            "datasets_checked": len(datasets),
            "datasets_needing_retrain": needing_retrain,
            "threshold": threshold,
            "cooldown_hours": cooldown_hours,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def trigger_retrain(
    dataset_id: str,
    activated_by: str = "retrain_trigger",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Trigger a retraining job for a dataset (D-FT-17).

    Verifies the dataset needs retraining, then calls training_engine.
    """
    # Verify retrain is needed
    check = check_retrain_needed(dataset_id, db_path)
    if not check.get("success"):
        return check

    matching = [d for d in check.get("datasets_needing_retrain", []) if d["dataset_id"] == dataset_id]
    if not matching:
        return {
            "success": False,
            "error": f"Dataset {dataset_id} does not meet retrain threshold",
            "check_result": check,
        }

    ds_info = matching[0]

    # Start training job via training_engine
    try:
        from tools.finetune.training_engine import create_training_job

        result = create_training_job(
            dataset_id=dataset_id,
            provider="unsloth_local",
            base_model=ds_info.get("base_model", "qwen3:latest"),
            created_by=activated_by,
            tenant_id=tenant_id,
            project_id=project_id,
            db_path=db_path,
        )
        if result.get("success"):
            result["trigger_reason"] = (
                f"Auto-retrain: {ds_info['new_examples']} new examples (threshold: {ds_info['threshold']})"
            )
        return result
    except (ImportError, Exception) as e:
        return {"success": False, "error": f"Failed to start training: {e}"}


def heartbeat_check_retrain(
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Heartbeat daemon integration (D162 pattern).

    Returns check result compatible with heartbeat_daemon format.
    """
    config = _load_retrain_config()
    if not config.get("auto_trigger", True):
        return {
            "check_type": "retrain_trigger",
            "status": "ok",
            "issues": 0,
            "details": "Auto-retrain disabled in config",
        }

    result = check_retrain_needed(db_path=db_path)
    if not result.get("success"):
        return {
            "check_type": "retrain_trigger",
            "status": "warning",
            "issues": 1,
            "details": f"Check failed: {result.get('error', 'Unknown')}",
        }

    needing = result.get("datasets_needing_retrain", [])
    if not needing:
        return {
            "check_type": "retrain_trigger",
            "status": "ok",
            "issues": 0,
            "details": f"Checked {result['datasets_checked']} datasets, none need retrain",
        }

    details = [f"{d['dataset_name']}: {d['new_examples']} new examples" for d in needing]
    return {
        "check_type": "retrain_trigger",
        "status": "warning",
        "issues": len(needing),
        "details": f"{len(needing)} datasets need retraining: {'; '.join(details)}",
        "datasets": needing,
    }


# -- CLI -----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning retrain trigger")
    parser.add_argument("--check", action="store_true", help="Check if retrain needed")
    parser.add_argument("--trigger", action="store_true", help="Trigger retraining")
    parser.add_argument("--heartbeat", action="store_true", help="Heartbeat check")

    parser.add_argument("--dataset-id", type=str, default="")
    parser.add_argument("--activated-by", type=str, default="retrain_trigger")
    parser.add_argument("--tenant-id", type=str, default="")
    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.check:
        result = check_retrain_needed(
            dataset_id=args.dataset_id,
        )
    elif args.trigger and args.dataset_id:
        result = trigger_retrain(
            dataset_id=args.dataset_id,
            activated_by=args.activated_by,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    elif args.heartbeat:
        result = heartbeat_check_retrain()
    else:
        result = {"success": False, "error": "Specify --check, --trigger, or --heartbeat"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
