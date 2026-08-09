#!/usr/bin/env python3
# CUI // SP-CTI
"""Fine-tuning training engine — orchestrates the full pipeline (D-FT-3).

Pipeline: dataset export → provider training → GGUF export → Ollama register → evaluate

Usage:
    python tools/finetune/training_engine.py --dataset-id "ds-xxx" --json
    python tools/finetune/training_engine.py --job-id "ft-xxx" --status --json
    python tools/finetune/training_engine.py --job-id "ft-xxx" --cancel --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
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
from tools.common.helpers import now_iso
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.finetune.provider import FineTuneRequest
from tools.finetune.provider_factory import get_provider
from tools.finetune.dataset_manager import export_jsonl, get_dataset

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _record_event(conn: sqlite3.Connection, job_id: str, event_type: str, details: str = "{}") -> None:
    """Record a training job event (append-only, D6)."""
    conn.execute(
        "INSERT INTO ft_training_job_events (job_id, event_type, details, created_at) VALUES (%s, %s, %s, %s)",
        (job_id, event_type, details, now_iso()),
    )


# ── Job Lifecycle ────────────────────────────────────────────────────


def create_training_job(
    dataset_id: str,
    provider_name: Optional[str] = None,
    base_model: str = "",
    lora_rank: int = 16,
    learning_rate: float = 2e-4,
    epochs: int = 3,
    batch_size: int = 2,
    max_seq_length: int = 2048,
    gpu_count: int = 1,
    distributed: bool = False,
    classification: str = "CUI",
    tenant_id: str = "",
    project_id: str = "",
    created_by: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create and start a training job."""
    conn = _get_db(db_path)
    try:
        # Validate dataset
        ds_result = get_dataset(dataset_id, db_path=db_path)
        if not ds_result.get("success"):
            return {"success": False, "error": f"Dataset not found: {dataset_id}"}
        ds = ds_result["dataset"]

        if ds["status"] != "ready":
            return {"success": False, "error": f"Dataset status must be 'ready', got '{ds['status']}'"}

        if not base_model:
            base_model = ds.get("base_model", "qwen3:latest")

        # Create job record
        job_id = f"ft-{uuid.uuid4().hex[:12]}"
        now = now_iso()

        hyperparams = json.dumps(
            {
                "lora_rank": lora_rank,
                "learning_rate": learning_rate,
                "epochs": epochs,
                "batch_size": batch_size,
                "max_seq_length": max_seq_length,
            }
        )

        output_dir = str(BASE_DIR / "data" / "finetune" / "jobs" / job_id)

        conn.execute(
            """INSERT INTO ft_training_jobs
               (id, dataset_id, provider, base_model, status, hyperparams,
                lora_rank, learning_rate, epochs, batch_size, max_seq_length,
                gpu_count, distributed, output_dir, classification,
                tenant_id, project_id, created_by, created_at)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                job_id,
                dataset_id,
                provider_name or "unsloth_local",
                base_model,
                hyperparams,
                lora_rank,
                learning_rate,
                epochs,
                batch_size,
                max_seq_length,
                gpu_count,
                1 if distributed else 0,
                output_dir,
                classification,
                tenant_id,
                project_id,
                created_by,
                now,
            ),
        )
        _record_event(conn, job_id, "created", json.dumps({"dataset_id": dataset_id, "base_model": base_model}))
        conn.commit()

        # Export dataset to JSONL
        export_path = str(Path(output_dir) / "training_data.jsonl")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        export_result = export_jsonl(dataset_id, export_path, approved_only=True, db_path=db_path)
        if not export_result.get("success"):
            conn.execute(
                "UPDATE ft_training_jobs SET status = 'failed', error_message = %s WHERE id = %s",
                (f"Dataset export failed: {export_result.get('error', '')}", job_id),
            )
            _record_event(conn, job_id, "failed", json.dumps({"reason": "dataset_export_failed"}))
            conn.commit()
            return {"success": False, "error": f"Dataset export failed: {export_result.get('error', '')}"}

        # Update status to preparing
        conn.execute(
            "UPDATE ft_training_jobs SET status = 'preparing', started_at = %s WHERE id = %s",
            (now, job_id),
        )
        _record_event(conn, job_id, "started")
        conn.commit()

        # Start training via provider
        provider = get_provider(provider_name)
        request = FineTuneRequest(
            dataset_path=export_path,
            base_model=base_model,
            lora_rank=lora_rank,
            learning_rate=learning_rate,
            epochs=epochs,
            batch_size=batch_size,
            max_seq_length=max_seq_length,
            output_dir=output_dir,
            gpu_count=gpu_count,
            distributed=distributed,
            job_id=job_id,
            classification=classification,
        )

        status = provider.start_training(request)

        if status.status == "failed":
            conn.execute(
                "UPDATE ft_training_jobs SET status = 'failed', error_message = %s WHERE id = %s",
                (status.error, job_id),
            )
            _record_event(conn, job_id, "failed", json.dumps({"error": status.error}))
            conn.commit()
            return {"success": False, "error": status.error, "job_id": job_id}

        conn.execute(
            "UPDATE ft_training_jobs SET status = 'training' WHERE id = %s",
            (job_id,),
        )
        conn.commit()

        return {
            "success": True,
            "job_id": job_id,
            "status": "training",
            "dataset_id": dataset_id,
            "provider": provider_name or "unsloth_local",
            "base_model": base_model,
            "output_dir": output_dir,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_job_status(
    job_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get current status of a training job."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM ft_training_jobs WHERE id = %s", (job_id,)).fetchone()
        if not row:
            return {"success": False, "error": f"Job not found: {job_id}"}

        job = dict(row)

        # For active jobs, poll the provider
        if job["status"] in ("training", "preparing", "exporting"):
            try:
                provider = get_provider(job["provider"])
                live_status = provider.get_status(job_id)
                if live_status.status != job["status"]:
                    # Update DB with latest status
                    conn.execute(
                        "UPDATE ft_training_jobs SET status = %s, loss_history = %s WHERE id = %s",
                        (live_status.status, json.dumps(live_status.loss_history), job_id),
                    )
                    conn.commit()
                    job["status"] = live_status.status

                job["live_progress_pct"] = live_status.progress_pct
                job["live_current_step"] = live_status.current_step
                job["live_current_loss"] = live_status.current_loss
            except Exception:
                pass

        # Get event history
        events = conn.execute(
            "SELECT * FROM ft_training_job_events WHERE job_id = %s ORDER BY created_at DESC LIMIT 20",
            (job_id,),
        ).fetchall()
        job["events"] = [dict(e) for e in events]

        return {"success": True, "job": job}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def cancel_job(
    job_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Cancel a running training job."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM ft_training_jobs WHERE id = %s", (job_id,)).fetchone()
        if not row:
            return {"success": False, "error": f"Job not found: {job_id}"}

        if row["status"] in ("completed", "failed", "canceled"):
            return {"success": False, "error": f"Job already in terminal state: {row['status']}"}

        provider = get_provider(row["provider"])
        canceled = provider.cancel_training(job_id)

        conn.execute(
            "UPDATE ft_training_jobs SET status = 'canceled', completed_at = %s WHERE id = %s",
            (now_iso(), job_id),
        )
        _record_event(conn, job_id, "canceled")
        conn.commit()

        return {"success": True, "job_id": job_id, "canceled": canceled}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def list_jobs(
    tenant_id: str = "",
    project_id: str = "",
    status: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """List training jobs."""
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM ft_training_jobs WHERE 1=1"
        params: List[Any] = []
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return {"success": True, "jobs": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning training engine")
    parser.add_argument("--dataset-id", type=str, help="Start training on dataset")
    parser.add_argument("--job-id", type=str, help="Job ID for status/cancel")
    parser.add_argument("--status", action="store_true", help="Get job status")
    parser.add_argument("--cancel", action="store_true", help="Cancel job")
    parser.add_argument("--list", action="store_true", help="List jobs")
    parser.add_argument("--provider", type=str, default=None)
    parser.add_argument("--base-model", type=str, default="")
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--tenant-id", type=str, default="")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.dataset_id and not args.status and not args.cancel:
        result = create_training_job(
            dataset_id=args.dataset_id,
            provider_name=args.provider,
            base_model=args.base_model,
            lora_rank=args.lora_rank,
            learning_rate=args.learning_rate,
            epochs=args.epochs,
            batch_size=args.batch_size,
            project_id=args.project_id,
            tenant_id=args.tenant_id,
        )
    elif args.job_id and args.status:
        result = get_job_status(args.job_id)
    elif args.job_id and args.cancel:
        result = cancel_job(args.job_id)
    elif args.list:
        result = list_jobs(tenant_id=args.tenant_id, project_id=args.project_id)
    else:
        result = {"success": False, "error": "Specify --dataset-id to train or --job-id with --status/--cancel"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
