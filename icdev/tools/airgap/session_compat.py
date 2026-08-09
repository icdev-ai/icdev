#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Session compatibility for non-Claude-Code environments.

Provides session management, transcript capture, and event correlation
that normally depend on Claude Code's lifecycle management.

Usage::

    from tools.airgap.session_compat import SessionManager

    session = SessionManager.start()
    session.log_prompt("user", "Build a login page")
    session.log_tool_use("Edit", {"file": "auth.py"}, duration_ms=150)
    session.end()
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.airgap.session_compat")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


class SessionManager:
    """Lightweight session manager that mimics Claude Code session lifecycle.

    Provides:
    - Unique session IDs compatible with hook_events table
    - Prompt and tool-use logging to activity_log
    - Transcript capture to .tmp/ for debugging
    - Session start/end events for dashboard SSE
    """

    _instances: Dict[str, "SessionManager"] = {}

    def __init__(self, session_id: Optional[str] = None, source: str = "airgap"):
        self.session_id = session_id or f"{source}-{uuid.uuid4().hex[:12]}"
        self.source = source
        self.started_at = datetime.now(timezone.utc)
        self.ended_at: Optional[datetime] = None
        self.events: List[Dict[str, Any]] = []
        self._transcript_lines: List[str] = []

        # Set env vars so hooks and other tools see the session
        os.environ["CLAUDE_SESSION_ID"] = self.session_id
        os.environ["ICDEV_SESSION_ID"] = self.session_id
        if not os.environ.get("CLAUDE_PROJECT_DIR"):
            os.environ["CLAUDE_PROJECT_DIR"] = str(BASE_DIR)

        SessionManager._instances[self.session_id] = self

    @classmethod
    def start(cls, source: str = "airgap") -> "SessionManager":
        """Create and start a new session."""
        session = cls(source=source)
        session._store_event(
            "session_start",
            {
                "source": source,
                "started_at": session.started_at.isoformat(),
            },
        )
        logger.info("Session started: %s (source=%s)", session.session_id, source)
        return session

    @classmethod
    def get_current(cls) -> Optional["SessionManager"]:
        """Get the most recently started session."""
        sid = os.environ.get("ICDEV_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
        if sid and sid in cls._instances:
            return cls._instances[sid]
        return None

    def end(self) -> Dict[str, Any]:
        """End the session and log final event."""
        self.ended_at = datetime.now(timezone.utc)
        duration_s = (self.ended_at - self.started_at).total_seconds()

        self._store_event(
            "session_end",
            {
                "duration_seconds": round(duration_s, 1),
                "event_count": len(self.events),
            },
        )

        # Write transcript to .tmp/
        self._write_transcript()

        logger.info(
            "Session ended: %s (duration=%.1fs, events=%d)",
            self.session_id,
            duration_s,
            len(self.events),
        )

        return {
            "session_id": self.session_id,
            "duration_seconds": round(duration_s, 1),
            "event_count": len(self.events),
        }

    def log_prompt(self, role: str, content: str) -> None:
        """Log a user or assistant prompt."""
        self._transcript_lines.append(f"[{role}] {content[:500]}")
        self._store_event(
            "prompt",
            {
                "role": role,
                "content_length": len(content),
                "content_preview": content[:200],
            },
        )

    def log_tool_use(
        self,
        tool_name: str,
        tool_input: Optional[Dict] = None,
        duration_ms: int = 0,
        success: bool = True,
    ) -> None:
        """Log a tool invocation."""
        self._transcript_lines.append(f"[tool:{tool_name}] {'OK' if success else 'FAIL'} ({duration_ms}ms)")
        self._store_event(
            "tool_use",
            {
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                "success": success,
            },
        )

    def _store_event(self, event_type: str, payload: Dict) -> None:
        """Store event in memory and in DB."""
        event = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        self.events.append(event)

        # Best-effort DB storage
        try:
            from tools.airgap.hook_compat import store_event

            store_event(
                self.session_id,
                event_type,
                payload=payload,
            )
        except Exception:
            pass

    def _write_transcript(self) -> None:
        """Write transcript to .tmp/ for debugging."""
        tmp_dir = BASE_DIR / ".tmp"
        tmp_dir.mkdir(exist_ok=True)

        ts = self.started_at.strftime("%Y%m%d_%H%M%S")
        path = tmp_dir / f"transcript_{ts}_{self.session_id[:8]}.txt"
        try:
            path.write_text(
                "\n".join(self._transcript_lines),
                encoding="utf-8", newline="",
            )
        except Exception as exc:
            logger.debug("Transcript write failed: %s", exc)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize session state."""
        return {
            "session_id": self.session_id,
            "source": self.source,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "event_count": len(self.events),
        }


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    session = SessionManager.start(source="cli")
    print(json.dumps(session.to_dict(), indent=2))

    session.log_prompt("user", "Test prompt from CLI")
    session.log_tool_use("Bash", {"command": "echo hello"}, duration_ms=50)

    result = session.end()
    print(json.dumps(result, indent=2))
