#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Usage Rollup Reflex — daily aggregation of usage_events into usage_daily_rollup.

Runs at 00:05 UTC. Reads yesterday's usage_events rows and upserts one rollup
row per (tenant_id, event_type, model, rollup_date) bucket. Safe to re-run
(idempotent UPSERT). Never reads or writes usage_events (append-only guard).
"""
from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

IMPLEMENTATION_STATUS = "full"


def _rollup_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Aggregate yesterday's usage_events into usage_daily_rollup."""
    run_id = f"usage-rollup-{uuid.uuid4().hex[:12]}"
    start_wall = time.time()

    if get_connection is None:
        return {"success": False, "metric_value": 0, "run_id": run_id,
                "details": {"error": "get_connection unavailable"}}

    now_utc = datetime.now(timezone.utc)
    yesterday = now_utc - timedelta(days=1)
    target_date = _rollup_date(yesterday)
    date_start = f"{target_date}T00:00:00"
    date_end = f"{target_date}T23:59:59"

    rows_upserted = 0
    try:
        with get_connection() as conn:
            # Aggregate: group usage_events for target date
            rows = conn.execute(
                """
                SELECT tenant_id,
                       event_type,
                       COALESCE(model, '') AS model,
                       SUM(quantity)       AS total_quantity
                FROM   usage_events
                WHERE  recorded_at >= %s AND recorded_at <= %s
                GROUP  BY tenant_id, event_type, COALESCE(model, '')
                """,
                (date_start, date_end),
            ).fetchall()

            for row in rows:
                conn.execute(
                    """
                    INSERT INTO usage_daily_rollup
                        (tenant_id, event_type, model, rollup_date, total_quantity)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, event_type, model, rollup_date)
                    DO UPDATE SET total_quantity = excluded.total_quantity
                    """,
                    (row[0], row[1], row[2], target_date, row[3]),
                )
                rows_upserted += 1

            conn.commit()
    except Exception as exc:
        elapsed_ms = int((time.time() - start_wall) * 1000)
        return {
            "success": False,
            "metric_value": 0,
            "run_id": run_id,
            "elapsed_ms": elapsed_ms,
            "details": {"error": str(exc), "target_date": target_date},
        }

    elapsed_ms = int((time.time() - start_wall) * 1000)
    return {
        "success": True,
        "metric_value": rows_upserted,
        "run_id": run_id,
        "elapsed_ms": elapsed_ms,
        "details": {
            "target_date": target_date,
            "rows_upserted": rows_upserted,
        },
    }
