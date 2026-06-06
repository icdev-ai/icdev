#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — FORGE Academy Oracle (forge_academy_oracle).

Queries oracle_predictions for rows associated with the FORGE Academy lens
and promotes high-confidence predictions (confidence >= 0.7) to kanban_tasks
with status='suggested' so the Kanban scheduler can surface them.

Cycle:
  query   → fetch oracle_predictions where lens='forge_academy' or name~'academy'
  filter  → retain rows with confidence >= 0.7 and outcome IS NULL or pending
  promote → insert into kanban_tasks (status='suggested') for each qualifying row
  return  → {predictions_found, promoted, errors}

COOLDOWN_HOURS = 4 (enforced by Genesis daemon).

Usage:
    python tools/genesis/reflexes/forge_academy_oracle.py [--dry-run] [--json]
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

COOLDOWN_HOURS = 4
CONFIDENCE_THRESHOLD = 0.7

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_predictions(conn: Any) -> List[Dict]:
    """Return oracle_predictions rows for the FORGE Academy lens."""
    rows = conn.execute(
        """
        SELECT id, lens_id, lens_name, subject_type, subject_id,
               prediction_type, prediction_text, confidence, severity, outcome
        FROM oracle_predictions
        WHERE (
            lens_id = 'forge_academy'
            OR lower(lens_name) LIKE '%academy%'
        )
        AND confidence >= ?
        AND (outcome IS NULL OR outcome = 'pending')
        ORDER BY confidence DESC, created_at DESC
        """,
        (CONFIDENCE_THRESHOLD,),
    ).fetchall()
    return [dict(r) for r in rows]


def _already_promoted(conn: Any, prediction_id: str) -> bool:
    """Return True if a kanban task was already created for this prediction."""
    row = conn.execute(
        "SELECT id FROM kanban_tasks WHERE id LIKE ? LIMIT 1",
        (f"fa-oracle-{prediction_id[:8]}%",),
    ).fetchone()
    return row is not None


def _promote(conn: Any, pred: Dict, dry_run: bool) -> str | None:
    """Insert a suggested kanban_task for the given prediction. Returns task_id or None."""
    task_id = f"fa-oracle-{pred['id'][:8]}-{_uid()}"
    severity = pred.get("severity", "medium")
    priority = "high" if severity in ("critical", "high") else "medium"
    title = (
        f"[FORGE Academy] {pred['prediction_type'].replace('_', ' ').title()}: "
        f"{pred['subject_id']}"
    )
    description = (
        f"Oracle prediction (confidence={pred['confidence']:.2f}, "
        f"severity={severity}) from lens '{pred['lens_name']}'.\n\n"
        f"{pred['prediction_text']}\n\n"
        f"Subject: {pred['subject_type']}:{pred['subject_id']}\n"
        f"Prediction ID: {pred['id']}"
    )
    now = _utcnow_iso()
    if dry_run:
        print(f"  [forge_academy_oracle] DRY-RUN would promote prediction {pred['id']!r} → {task_id}")
        return task_id
    try:
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, status, "
            " executor_type, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, title, description,
                "chore", priority, "suggested",
                "claude_cli", now, now,
            ),
        )
        conn.commit()
        return task_id
    except Exception as exc:
        print(f"  [forge_academy_oracle] WARNING: promote failed for {pred['id']!r}: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def oracle_sweep(dry_run: bool = False) -> Dict[str, Any]:
    """Query and promote FORGE Academy oracle predictions."""
    if get_connection is None:
        return {"predictions_found": 0, "promoted": 0, "errors": ["get_connection unavailable"]}

    errors: List[str] = []
    promoted_ids: List[str] = []
    predictions: List[Dict] = []

    conn = None
    try:
        conn = get_connection()
        predictions = _fetch_predictions(conn)
    except Exception as exc:
        errors.append(f"fetch_predictions: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    predictions_found = len(predictions)
    print(f"[forge_academy_oracle] {predictions_found} qualifying predictions found")

    for pred in predictions:
        conn = None
        try:
            conn = get_connection()
            if _already_promoted(conn, pred["id"]):
                print(f"  [forge_academy_oracle] skip {pred['id']!r} — already promoted")
                continue
            task_id = _promote(conn, pred, dry_run=dry_run)
            if task_id:
                promoted_ids.append(task_id)
                print(f"  [forge_academy_oracle] promoted {pred['id']!r} → {task_id}")
        except Exception as exc:
            errors.append(f"promote {pred['id']}: {exc}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    return {
        "predictions_found": predictions_found,
        "promoted": len(promoted_ids),
        "promoted_ids": promoted_ids,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Reflex entry point
# ---------------------------------------------------------------------------

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Genesis reflex entry point — called by daemon every 4 h (COOLDOWN_HOURS)."""
    dry_run = bool(config.get("dry_run", False))
    start = time.time()
    print(f"[forge_academy_oracle] run start  dry_run={dry_run}")

    result = oracle_sweep(dry_run=dry_run)
    result["duration_ms"] = round((time.time() - start) * 1000)
    print(
        f"[forge_academy_oracle] run complete  "
        f"found={result['predictions_found']} promoted={result['promoted']} "
        f"errors={len(result['errors'])} ({result['duration_ms']} ms)"
    )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FORGE Academy Oracle Genesis Reflex")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    out = oracle_sweep(dry_run=args.dry_run)
    if args.as_json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Predictions found : {out['predictions_found']}")
        print(f"Promoted          : {out['promoted']}")
        if out["errors"]:
            print(f"Errors            : {out['errors']}")
