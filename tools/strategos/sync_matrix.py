# CUI // SP-CTI
"""Synchronization Matrix — Time-phased operational effects synchronization.

Rows: functional warfighting areas (Maneuver, Fires, ISR, C2, CSS, etc.)
Columns: time blocks relative to H-Hour (H-4, H-3, H-2, H-1, H, H+1, H+2...)
Cells: free-text tasks/effects synchronized at that time block for that WFA.

Doctrinal basis: FM 3-0, JP 5-0 — Operations Order Annex C (Synchronization Matrix).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.sync_matrix")

DEFAULT_ROW_LABELS = [
    "Maneuver",
    "Fires",
    "ISR / Collection",
    "C2 / Command",
    "CSS / Sustainment",
    "Air Defense",
    "SIGINT / EW",
    "Cyber / Info Ops",
    "Aviation",
    "Special Operations",
]

DEFAULT_TIME_BLOCKS = [
    "H-72", "H-48", "H-24", "H-12",
    "H-4", "H-3", "H-2", "H-1",
    "H-Hour",
    "H+1", "H+2", "H+4", "H+8", "H+12", "H+24",
]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_matrix(
    operation_name: str,
    theater: str = "unspecified",
    time_blocks: list[str] | None = None,
    row_labels: list[str] | None = None,
    created_by: str = "analyst",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        matrix_id = str(uuid.uuid4())
        now = _now_utc()
        tb = time_blocks or DEFAULT_TIME_BLOCKS
        rl = row_labels or DEFAULT_ROW_LABELS
        conn.execute(
            "INSERT INTO sg_sync_matrices "
            "(id, operation_name, theater, time_blocks, row_labels, cells, "
            " created_by, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, '{}', %s, %s, %s)",
            (matrix_id, operation_name, theater,
             json.dumps(tb), json.dumps(rl), created_by, now, now),
        )
        conn.commit()
        return {
            "matrix_id": matrix_id,
            "operation_name": operation_name,
            "theater": theater,
            "time_blocks": tb,
            "row_labels": rl,
        }
    finally:
        conn.close()


def get_matrix(matrix_id: str) -> dict | None:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT id, operation_name, theater, time_blocks, row_labels, cells, "  # nosec B608
            f"phase, created_by, created_at, updated_at "
            f"FROM sg_sync_matrices WHERE id = {ph}",
            (matrix_id,),
        ).fetchone()
        if not row:
            return None
        cols = ("id", "operation_name", "theater", "time_blocks", "row_labels",
                "cells", "phase", "created_by", "created_at", "updated_at")
        d = dict(zip(cols, row))
        for key in ("time_blocks", "row_labels", "cells"):
            try:
                d[key] = json.loads(d[key] or "[]" if key != "cells" else d[key] or "{}")
            except Exception:
                d[key] = [] if key != "cells" else {}
        return d
    finally:
        conn.close()


def update_cell(matrix_id: str, row_idx: int, col_idx: int, text: str) -> bool:
    """Update a single cell in the matrix."""
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT cells FROM sg_sync_matrices WHERE id = {ph}", (matrix_id,)  # nosec B608
        ).fetchone()
        if not row:
            return False
        try:
            cells = json.loads(row[0] or "{}")
        except Exception:
            cells = {}
        key = f"{row_idx}:{col_idx}"
        if text.strip():
            cells[key] = text.strip()
        else:
            cells.pop(key, None)
        now = _now_utc()
        conn.execute(
            f"UPDATE sg_sync_matrices SET cells = {ph}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            (json.dumps(cells), now, matrix_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("update_cell failed: %s", exc)
        return False
    finally:
        conn.close()


def bulk_update_cells(matrix_id: str, cells: dict[str, str]) -> bool:
    """Replace entire cells dict (used for bulk save from UI)."""
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        now = _now_utc()
        conn.execute(
            f"UPDATE sg_sync_matrices SET cells = {ph}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            (json.dumps(cells), now, matrix_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("bulk_update_cells failed: %s", exc)
        return False
    finally:
        conn.close()


def list_matrices(theater: str = "", limit: int = 20) -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if theater:
            rows = conn.execute(
                f"SELECT id, operation_name, theater, phase, created_by, created_at "  # nosec B608
                f"FROM sg_sync_matrices WHERE theater = {ph} ORDER BY created_at DESC LIMIT {ph}",
                (theater, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, operation_name, theater, phase, created_by, created_at "  # nosec B608
                "FROM sg_sync_matrices ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        cols = ("id", "operation_name", "theater", "phase", "created_by", "created_at")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def delete_matrix(matrix_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(f"DELETE FROM sg_sync_matrices WHERE id = {ph}", (matrix_id,))  # nosec B608
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_matrix failed: %s", exc)
        return False
    finally:
        conn.close()
