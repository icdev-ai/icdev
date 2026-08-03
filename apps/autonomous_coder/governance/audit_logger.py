# CUI // SP-CTI
"""Audit Logger — immutable append-only log of all agent decisions and actions."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.apps.autonomous_coder.governance.audit_logger")


_DB_PATH = Path(__file__).parent.parent / "data" / "audit.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            agent       TEXT NOT NULL,
            event       TEXT NOT NULL,
            payload     TEXT,
            created_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


class AuditLogger:
    """Thread-safe append-only audit log for one coder session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._entries: list[dict[str, Any]] = []

    def log(self, agent: str, event: str, payload: Any = None) -> None:
        entry = {
            "session_id": self.session_id,
            "agent": agent,
            "event": event,
            "payload": json.dumps(payload, default=str) if payload is not None else None,
            "created_at": _now(),
        }
        self._entries.append(entry)
        with _lock:
            try:
                conn = _get_conn()
                conn.execute(
                    "INSERT INTO audit_log (session_id, agent, event, payload, created_at) "
                    "VALUES (:session_id, :agent, :event, :payload, :created_at)",
                    entry,
                )
                conn.commit()
                conn.close()
            except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                # never block execution due to logging failure
                logger.warning("log: best-effort INSERT into audit_log failed (non-blocking): %s", exc)

    def get_log(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def summary(self) -> str:
        lines = [f"[{e['created_at'][11:19]}] {e['agent']}: {e['event']}" for e in self._entries]
        return "\n".join(lines)
