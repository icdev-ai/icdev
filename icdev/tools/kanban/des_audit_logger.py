#!/usr/bin/env python3
# CUI // SP-CTI
"""DES Execution Audit Logger (nwa-des-04).

Append-only audit trail for DES dispatch, completion, verification, and
gate_override events. Inserts only — never UPDATE/DELETE (NIST AU).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402

log = get_logger(__name__)

_TABLE = "des_execution_events"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn) -> bool:
    # Backend-aware probe — works on PG + SQLite without translate_sql.
    return table_exists(conn, _TABLE)


class DESAuditLogger:
    """Append-only logger for DES execution events."""

    def log_dispatch(
        self,
        task_id: str,
        skill: str,
        inputs: dict,
        executor: str,
        source: str,
        queued_at: str | None = None,
    ) -> str:
        return self._insert(
            task_id=task_id,
            event_type="dispatch",
            skill_invoked=skill,
            inputs_json=json.dumps(inputs),
            executor=executor,
            dispatch_source=source,
            queued_at=queued_at,
        )

    def log_completion(
        self,
        task_id: str,
        status: str,
        outputs: dict,
        duration_ms: int,
    ) -> str:
        return self._insert(
            task_id=task_id,
            event_type="completion",
            task_status=status,
            outputs_json=json.dumps(outputs),
            duration_ms=duration_ms,
        )

    def log_verification(self, task_id: str, signals: dict) -> str:
        return self._insert(
            task_id=task_id,
            event_type="verification",
            verification_signals=json.dumps(signals),
        )

    def log_gate_override(
        self, task_id: str, reason: str, operator: str
    ) -> str:
        return self._insert(
            task_id=task_id,
            event_type="gate_override",
            inputs_json=json.dumps({"reason": reason, "operator": operator}),
        )

    def _insert(self, **kwargs) -> str:
        event_id = str(uuid.uuid4())
        occurred_at = _now()
        try:
            conn = get_connection()
            if not _table_exists(conn):
                log.warning("des_execution_events table missing — skipping audit log")
                return ""
            conn.execute(
                """
                INSERT INTO des_execution_events
                    (id, task_id, event_type, skill_invoked,
                     inputs_json, outputs_json, executor,
                     dispatch_source, task_status, duration_ms,
                     verification_signals, queued_at, occurred_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event_id,
                    kwargs.get("task_id", ""),
                    kwargs.get("event_type"),
                    kwargs.get("skill_invoked"),
                    kwargs.get("inputs_json"),
                    kwargs.get("outputs_json"),
                    kwargs.get("executor"),
                    kwargs.get("dispatch_source"),
                    kwargs.get("task_status"),
                    kwargs.get("duration_ms"),
                    kwargs.get("verification_signals"),
                    kwargs.get("queued_at"),
                    occurred_at,
                ),
            )
            conn.commit()
        except Exception:
            log.exception("DESAuditLogger insert failed — degrading gracefully")
            return ""
        return event_id


def _query(task_id: str) -> list[dict]:
    conn = get_connection()
    if not _table_exists(conn):
        return []
    rows = conn.execute(
        "SELECT * FROM des_execution_events WHERE task_id=%s ORDER BY occurred_at DESC LIMIT 20",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DES Audit Logger CLI")
    parser.add_argument("--query", action="store_true")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.query:
        result = _query(args.task_id)
        print(json.dumps(result, indent=2))
