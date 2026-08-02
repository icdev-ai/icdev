# [TEMPLATE: CUI // SP-CTI]
"""Shared event utility for ICDEV™ hooks — stores events + optional SIEM forwarding."""

import hashlib
import hmac
import json
import os
import sqlite3
import time
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


# Circuit breaker for the dashboard forward. When nothing is listening, the
# connect does not fail fast on Windows — it sits in SYN_SENT until the timeout
# expires. Paying that on every tool call is what made this hook the single
# slowest thing in a build. Once a connect fails we skip the forward entirely
# for _FORWARD_BREAKER_TTL_SEC rather than re-paying it.
_FORWARD_BREAKER_PATH = BASE_DIR / ".tmp" / "dashboard_unreachable"
_FORWARD_BREAKER_TTL_SEC = 60
_FORWARD_CONNECT_TIMEOUT_SEC = 0.25


def _forward_breaker_open() -> bool:
    """True when a recent forward failed and we should not retry yet."""
    try:
        age = time.time() - _FORWARD_BREAKER_PATH.stat().st_mtime
    except OSError:
        return False  # no sentinel (or unreadable) — allow the attempt
    return age < _FORWARD_BREAKER_TTL_SEC


def _forward_breaker_trip() -> None:
    try:
        _FORWARD_BREAKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FORWARD_BREAKER_PATH.write_text(
            datetime.now(timezone.utc).isoformat(), encoding="utf-8"
        )
    except OSError:
        pass


def _forward_breaker_reset() -> None:
    try:
        _FORWARD_BREAKER_PATH.unlink()
    except OSError:
        pass  # already absent — the common case


def forward_to_dashboard(event_data: dict):
    """Fire-and-forget POST to the dashboard SSE ingest endpoint.

    Deliberately does NOT read the response. This runs inside PostToolUse, i.e.
    on the critical path of every single tool call, and the hook has no use for
    the reply — ``/api/events/ingest`` returns 200 with no body the caller needs.
    Waiting for it cost 380-840 ms per tool call against a live dashboard and a
    full 2 s timeout against a dead one. Writing the request and closing costs
    under 15 ms, and the server still persists the event and broadcasts it,
    because it has a complete Content-Length-framed request before we hang up.
    """
    if _forward_breaker_open():
        return
    sock = None
    try:
        import socket

        port = int(os.environ.get("ICDEV_DASHBOARD_PORT", "5050"))
        body = json.dumps(event_data).encode()
        request = (
            f"POST /api/events/ingest HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode() + body

        sock = socket.create_connection(
            ("127.0.0.1", port), timeout=_FORWARD_CONNECT_TIMEOUT_SEC
        )
        sock.sendall(request)
        # Half-close so the server sees EOF immediately instead of waiting on
        # a keep-alive it will never get.
        sock.shutdown(socket.SHUT_WR)
        _forward_breaker_reset()
    except Exception:
        # Best-effort — dashboard may not be running. Stop retrying for a while.
        _forward_breaker_trip()
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
