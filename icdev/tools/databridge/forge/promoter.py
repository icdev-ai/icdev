# CUI // SP-CTI
"""Promoter — Sandbox → Production promotion workflow (D-CF-6).

Moves forge connectors through the status state machine:
  sandboxed → promoted → published → deprecated
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import sqlite3
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = get_logger("databridge.forge.promoter")

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "icdev.db"


def promote_connector(
    connector_id: str,
    promoted_by: str,
    review_notes: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Promote a sandboxed connector to production registry.

    Moves status: sandboxed → promoted.
    Registers the connector in the in-memory registry via load_forge_connectors().
    """
    db = db_path or str(DB_PATH)
    try:
        conn = _get_conn(db)
        row = conn.execute(
            "SELECT id, connector_name, status FROM db_forge_connectors WHERE id = ?",
            (connector_id,),
        ).fetchone()

        if not row:
            conn.close()
            return {"status": "error", "error": f"Connector {connector_id} not found"}

        if row["status"] != "sandboxed":
            conn.close()
            return {
                "status": "error",
                "error": f"Cannot promote: current status is '{row['status']}', expected 'sandboxed'",
            }

        now = datetime.now(timezone.utc).isoformat()

        # Update status
        conn.execute(
            """UPDATE db_forge_connectors
               SET status = 'promoted', promoted_by = ?, promoted_at = ?, updated_at = ?
               WHERE id = ?""",
            (promoted_by, now, now, connector_id),
        )

        # Record promotion (append-only)
        conn.execute(
            """INSERT INTO db_forge_promotions
               (connector_id, from_status, to_status, promoted_by, review_notes, promoted_at)
               VALUES (?, 'sandboxed', 'promoted', ?, ?, ?)""",
            (connector_id, promoted_by, review_notes[:5000], now),
        )

        # Audit trail
        conn.execute(
            """INSERT INTO audit_trail (id, event_type, actor, action, details, created_at)
               VALUES (?, 'connector_forge_promoted', ?, 'promote', ?, ?)""",
            (f"audit-{uuid.uuid4().hex[:12]}", promoted_by, f"Promoted {row['connector_name']} ({connector_id})", now),
        )

        conn.commit()
        conn.close()

        # Load into in-memory registry
        try:
            from tools.databridge.registry import load_forge_connectors

            loaded = load_forge_connectors(db_path=db)
            logger.info("Loaded %d forge connectors after promotion", loaded)
        except Exception as exc:
            logger.warning("Registry load after promotion failed: %s", exc)

        return {
            "status": "ok",
            "connector_id": connector_id,
            "connector_name": row["connector_name"],
            "new_status": "promoted",
            "promoted_by": promoted_by,
        }

    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def deprecate_connector(
    connector_id: str,
    deprecated_by: str,
    reason: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Deprecate a promoted or published connector."""
    db = db_path or str(DB_PATH)
    try:
        conn = _get_conn(db)
        row = conn.execute(
            "SELECT id, connector_name, status FROM db_forge_connectors WHERE id = ?",
            (connector_id,),
        ).fetchone()

        if not row:
            conn.close()
            return {"status": "error", "error": f"Connector {connector_id} not found"}

        if row["status"] not in ("promoted", "published"):
            conn.close()
            return {
                "status": "error",
                "error": f"Cannot deprecate: status is '{row['status']}'",
            }

        now = datetime.now(timezone.utc).isoformat()
        old_status = row["status"]

        conn.execute(
            "UPDATE db_forge_connectors SET status = 'deprecated', updated_at = ? WHERE id = ?",
            (now, connector_id),
        )

        conn.execute(
            """INSERT INTO db_forge_promotions
               (connector_id, from_status, to_status, promoted_by, review_notes, promoted_at)
               VALUES (?, ?, 'deprecated', ?, ?, ?)""",
            (connector_id, old_status, deprecated_by, reason[:5000], now),
        )

        conn.commit()
        conn.close()

        return {
            "status": "ok",
            "connector_id": connector_id,
            "new_status": "deprecated",
            "previous_status": old_status,
        }

    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _get_conn(db_path: str) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
