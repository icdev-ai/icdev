# CUI // SP-CTI
"""AADC Activity Feed Emitter.

Writes one row to aadc_design_events on each significant canvas action.
Uses get_connection() so it works with both SQLite and PostgreSQL backends.
"""
from __future__ import annotations

import json

from typing import Any, Dict, Optional


def emit_event(
    design_id: str,
    event_type: str,
    actor: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Insert one row into aadc_design_events.

    Returns True on success, False if the table doesn't exist or write fails
    (non-fatal — callers should not block on this).
    """
    try:
        from tools.agentic_ai_canvas.db.init_db import get_connection
    except ImportError:
        return False

    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO aadc_design_events (design_id, event_type, actor, metadata_json) "
            "VALUES (?, ?, ?, ?)",
            (
                design_id,
                event_type,
                actor,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        return True
    except Exception:
        # Table may not exist in SQLite mode (migration is PostgreSQL-only DDL).
        # Silently skip — event feed is non-critical.
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
