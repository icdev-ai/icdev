# CUI // SP-CTI
"""Shared event utility for ICDEV hooks — stores events + optional SIEM forwarding."""

import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def get_db_path() -> Path:
    return DB_PATH


def get_session_id() -> str:
    return os.environ.get("CLAUDE_SESSION_ID", str(uuid.uuid4()))


def compute_hmac(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def store_event(session_id: str, hook_type: str, tool_name: str = None,
                payload: dict = None, classification: str = "CUI") -> int:
    """Store hook event in SQLite. Returns event ID or -1 on failure."""
    payload_str = json.dumps(payload) if payload else None
    # HMAC signing for tamper detection
    secret = os.environ.get("ICDEV_HOOK_HMAC_SECRET", "icdev-default-hmac-key")
    signature = compute_hmac(payload_str or "", secret)

    event_id = -1
    try:
        if not DB_PATH.exists():
            return -1  # DB not initialized yet — graceful skip
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            """INSERT INTO hook_events
               (session_id, hook_type, tool_name, payload, classification, signature)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, hook_type, tool_name, payload_str, classification, signature)
        )
        conn.commit()
        event_id = c.lastrowid
        conn.close()
    except sqlite3.OperationalError:
        return -1  # Table missing or DB locked — graceful skip

    # Best-effort forward to dashboard SSE
    forward_to_dashboard({
        "id": event_id,
        "session_id": session_id,
        "hook_type": hook_type,
        "tool_name": tool_name,
        "payload": payload,
        "classification": classification,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return event_id


def forward_to_dashboard(event_data: dict):
    """Best-effort HTTP POST to dashboard SSE ingest endpoint."""
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:5000/api/events/ingest",
            data=json.dumps(event_data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass  # Best-effort — dashboard may not be running
