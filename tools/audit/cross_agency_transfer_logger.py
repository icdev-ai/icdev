#!/usr/bin/env python3
# CUI // SP-CTI
"""Cross-Agency Data Transfer Audit Logger.

Append-only audit trail for all cross-agency data transfer events, satisfying
NIST 800-53 AU-2 (Audit Events) and AU-9 (Protection of Audit Information).

Inserts only — never UPDATE or DELETE (NIST AU).
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

_TABLE = "cross_agency_transfers"

_VALID_EVENT_TYPES = (
    "initiated",
    "completed",
    "failed",
    "rejected",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn) -> bool:
    # Backend-aware, translation-independent existence probe (pgrt-sweep-06).
    return table_exists(conn, _TABLE)


class CrossAgencyTransferLogger:
    """Append-only logger for cross-agency data transfer events (NIST AU-2, AU-9)."""

    def log_initiated(
        self,
        transfer_id: str,
        source_agency: str,
        target_agency: str,
        data_type: str,
        actor: str,
        data_classification: str = "CUI",
        project_id: str | None = None,
        details: dict | None = None,
    ) -> str:
        """Record that a cross-agency transfer has been initiated."""
        return self._insert(
            transfer_id=transfer_id,
            event_type="initiated",
            source_agency=source_agency,
            target_agency=target_agency,
            data_type=data_type,
            actor=actor,
            data_classification=data_classification,
            project_id=project_id,
            details=details,
        )

    def log_completed(
        self,
        transfer_id: str,
        source_agency: str,
        target_agency: str,
        actor: str,
        bytes_transferred: int | None = None,
        checksum: str | None = None,
        duration_ms: int | None = None,
        data_classification: str = "CUI",
    ) -> str:
        """Record successful completion of a cross-agency transfer."""
        return self._insert(
            transfer_id=transfer_id,
            event_type="completed",
            source_agency=source_agency,
            target_agency=target_agency,
            actor=actor,
            bytes_transferred=bytes_transferred,
            checksum=checksum,
            duration_ms=duration_ms,
            data_classification=data_classification,
        )

    def log_failed(
        self,
        transfer_id: str,
        source_agency: str,
        target_agency: str,
        actor: str,
        rejection_reason: str | None = None,
        error_code: str | None = None,
        data_classification: str = "CUI",
    ) -> str:
        """Record a failed cross-agency transfer."""
        return self._insert(
            transfer_id=transfer_id,
            event_type="failed",
            source_agency=source_agency,
            target_agency=target_agency,
            actor=actor,
            rejection_reason=rejection_reason,
            error_code=error_code,
            data_classification=data_classification,
        )

    def log_rejected(
        self,
        transfer_id: str,
        source_agency: str,
        target_agency: str,
        reviewed_by: str,
        rejection_reason: str,
        data_classification: str = "CUI",
    ) -> str:
        """Record that a cross-agency transfer was rejected by a reviewer."""
        return self._insert(
            transfer_id=transfer_id,
            event_type="rejected",
            source_agency=source_agency,
            target_agency=target_agency,
            actor=reviewed_by,
            rejection_reason=rejection_reason,
            data_classification=data_classification,
        )

    def _insert(self, **kwargs) -> str:
        event_id = str(uuid.uuid4())
        occurred_at = _now()
        details = kwargs.get("details")
        try:
            conn = get_connection()
            if not _table_exists(conn):
                log.warning("%s table missing — skipping audit log", _TABLE)
                return ""
            conn.execute(
                """
                INSERT INTO cross_agency_transfers
                    (id, transfer_id, event_type, source_agency, target_agency,
                     data_type, data_classification, actor, project_id,
                     bytes_transferred, checksum, duration_ms,
                     rejection_reason, error_code, details, occurred_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event_id,
                    kwargs.get("transfer_id", ""),
                    kwargs.get("event_type"),
                    kwargs.get("source_agency", ""),
                    kwargs.get("target_agency", ""),
                    kwargs.get("data_type"),
                    kwargs.get("data_classification", "CUI"),
                    kwargs.get("actor", ""),
                    kwargs.get("project_id"),
                    kwargs.get("bytes_transferred"),
                    kwargs.get("checksum"),
                    kwargs.get("duration_ms"),
                    kwargs.get("rejection_reason"),
                    kwargs.get("error_code"),
                    json.dumps(details) if details else None,
                    occurred_at,
                ),
            )
            conn.commit()
            # Mirror to main audit_trail (NIST AU-2 dual-write)
            _mirror_to_audit_trail(conn, kwargs, event_id, occurred_at)
        except Exception:
            log.exception("CrossAgencyTransferLogger insert failed — degrading gracefully")
            return ""
        return event_id


def _mirror_to_audit_trail(conn, kwargs: dict, event_id: str, occurred_at: str) -> None:
    """Write a summary entry to the main audit_trail for AU-2 completeness.

    Writes directly to the audit_trail table using the caller's connection so
    both the cross_agency_transfers insert and this mirror are visible in the
    same transaction/connection (required for test isolation).
    """
    event_map = {
        "initiated": "cross_agency_transfer_initiated",
        "completed": "cross_agency_transfer_completed",
        "failed": "cross_agency_transfer_failed",
        "rejected": "cross_agency_transfer_rejected",
    }
    event_type = event_map.get(kwargs.get("event_type", ""), "")
    if not event_type:
        return
    try:
        action = (
            f"Cross-agency transfer {kwargs.get('event_type')}: "
            f"{kwargs.get('source_agency')} → {kwargs.get('target_agency')}"
        )
        details = json.dumps(
            {
                "transfer_id": kwargs.get("transfer_id"),
                "data_type": kwargs.get("data_type"),
                "event_log_id": event_id,
            }
        )
        # ``audit_trail.id`` is an autoincrementing integer key. Supplying a
        # uuid string made every mirror INSERT raise "datatype mismatch",
        # which the except below swallowed — the AU-2 dual-write never landed.
        # Omit it, matching tools/audit/audit_logger.py.
        conn.execute(
            """
            INSERT INTO audit_trail
                (event_type, actor, action, project_id, details,
                 classification, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_type,
                kwargs.get("actor", "system"),
                action,
                kwargs.get("project_id"),
                details,
                kwargs.get("data_classification", "CUI"),
                occurred_at,
            ),
        )
        conn.commit()
    except Exception:
        log.warning("audit_trail mirror failed — cross_agency_transfers row still committed")


def query_by_transfer_id(transfer_id: str) -> list[dict]:
    """Return all events for a given transfer_id, newest first."""
    conn = get_connection()
    if not _table_exists(conn):
        return []
    rows = conn.execute(
        "SELECT * FROM cross_agency_transfers WHERE transfer_id=%s ORDER BY occurred_at DESC LIMIT 50",
        (transfer_id,),
    ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cross-Agency Transfer Audit Logger CLI")
    parser.add_argument("--query", action="store_true")
    parser.add_argument("--transfer-id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.query:
        result = query_by_transfer_id(args.transfer_id)
        print(json.dumps(result, indent=2))
