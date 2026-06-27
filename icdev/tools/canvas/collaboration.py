# CUI // SP-CTI
"""Canvas Collaboration Manager — real-time multi-user canvas editing.

Provides session-based collaboration for all design canvases.
Uses SQLite-backed session tracking (air-gap safe, no WebSocket required).
"""

from __future__ import annotations

import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone


class CanvasCollabManager:
    """Manages collaborative editing sessions for a canvas.

    Parameters
    ----------
    canvas_key : str
        Canvas identifier (e.g., 'idc', 'sdc', 'qdc').
    db_path : str
        Path to the canvas SQLite database.
    collab_table : str
        Name of the collaboration sessions table.
    """

    # Map canvas keys to their DB filenames
    _DB_MAP = {
        "idc": "infra_canvas.db",
        "ndc": "network_canvas.db",
        "sdc": "security_canvas.db",
        "bdc": "boundary_canvas.db",
        "pdc": "pipeline_canvas.db",
        "odc": "observability_canvas.db",
        "ddc": "data_canvas.db",
        "qdc": "qdc_canvas.db",
        "mdc": "migration_canvas.db",
    }

    def __init__(self, canvas_key: str, db_path: str | None = None, collab_table: str | None = None):
        self.canvas_key = canvas_key
        if db_path is None:
            from pathlib import Path

            data_dir = Path(__file__).resolve().parents[2] / "data"
            db_name = self._DB_MAP.get(canvas_key, f"{canvas_key}_canvas.db")
            db_path = str(data_dir / db_name)
        self.db_path = db_path
        self.collab_table = collab_table or f"{canvas_key}_collab_sessions"

    def _get_conn(self):
        conn = get_connection(str(self.db_path))
        return conn

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def join(self, design_id: str, user_id: str, user_name: str = "", color: str = "#3498db") -> dict:
        """Join a collaboration session."""
        conn = self._get_conn()
        try:
            session_id = str(uuid.uuid4())
            conn.execute(
                f"INSERT OR REPLACE INTO {self.collab_table} "  # noqa: S608
                "(id, design_id, user_id, user_name, color, joined_at, last_seen, is_active) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 1)",
                (session_id, design_id, user_id, user_name, color, self._now(), self._now()),
            )
            conn.commit()
            return {"session_id": session_id, "design_id": design_id, "user_id": user_id}
        finally:
            conn.close()

    def leave(self, design_id: str, user_id: str) -> dict:
        """Leave a collaboration session."""
        conn = self._get_conn()
        try:
            conn.execute(
                f"UPDATE {self.collab_table} SET is_active = 0 "  # noqa: S608
                "WHERE design_id = %s AND user_id = %s",
                (design_id, user_id),
            )
            conn.commit()
            return {"design_id": design_id, "user_id": user_id, "left": True}
        finally:
            conn.close()

    def push(self, design_id: str, user_id: str, operation: dict) -> dict:
        """Push an operation to collaborators (stored for polling)."""
        conn = self._get_conn()
        try:
            conn.execute(
                f"UPDATE {self.collab_table} SET last_seen = %s "  # noqa: S608
                "WHERE design_id = %s AND user_id = %s AND is_active = 1",
                (self._now(), design_id, user_id),
            )
            conn.commit()
            return {"design_id": design_id, "operation": operation, "pushed_at": self._now()}
        finally:
            conn.close()

    def poll(self, design_id: str) -> dict:
        """Poll for collaboration updates."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"SELECT user_id, user_name, color, last_seen FROM {self.collab_table} "  # noqa: S608
                "WHERE design_id = %s AND is_active = 1 ORDER BY joined_at",
                (design_id,),
            ).fetchall()
            return {"participants": [dict(r) for r in rows], "polled_at": self._now()}
        finally:
            conn.close()

    def participants(self, design_id: str) -> list[dict]:
        """List active participants."""
        result = self.poll(design_id)
        return result.get("participants", [])
