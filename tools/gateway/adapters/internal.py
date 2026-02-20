#!/usr/bin/env python3
# CUI // SP-CTI
"""Internal Chat adapter — bridges ICDEV's existing /chat page to the gateway.

This adapter is always available in both connected and air-gapped
environments. It normalizes messages from the internal chat system
into CommandEnvelope objects.

Decision D134: Air-gapped environments use internal chat + optional Mattermost.
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.gateway.adapters.base import BaseChannelAdapter
from tools.gateway.event_envelope import CommandEnvelope, parse_command_text

logger = logging.getLogger("icdev.gateway.adapters.internal")


class InternalChatAdapter(BaseChannelAdapter):
    """Adapter for ICDEV's built-in /chat page.

    Since internal chat is within the same application, signature
    verification is implicit (same-process trust). Identity is
    resolved from the Flask session or request context.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("internal_chat", config)

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Internal chat — no external signature needed (same-process trust)."""
        return True

    def parse_webhook(self, request_data: Dict[str, Any],
                      headers: Dict[str, str]) -> Optional[CommandEnvelope]:
        """Parse an internal chat message into a CommandEnvelope.

        Expected request_data format:
        {
            "user_id": "user-123",
            "user_name": "John Doe",
            "message": "/icdev-status proj-123",
            "session_id": "chat-session-abc"
        }
        """
        message = request_data.get("message", "").strip()
        if not message:
            return None

        # Only process messages that look like commands
        if not (message.startswith("/") or message.startswith("icdev-")):
            return None

        command, args = parse_command_text(message)
        if not command:
            return None

        user_id = request_data.get("user_id", "")
        user_name = request_data.get("user_name", "")

        envelope = CommandEnvelope(
            channel="internal_chat",
            channel_user_id=user_id,
            channel_user_name=user_name,
            channel_message_id=request_data.get("message_id", ""),
            channel_thread_id=request_data.get("session_id", ""),
            raw_text=message,
            command=command,
            args=args,
            project_id=args.get("project_id", ""),
        )

        return envelope

    def send_message(self, channel_user_id: str, text: str,
                     thread_id: str = "") -> bool:
        """Send a response back to the internal chat.

        For internal chat, responses are stored in the chat session's
        database and delivered via the existing SSE/polling mechanism.
        This method stores the response for the chat UI to pick up.
        """
        import sqlite3

        db_path = BASE_DIR / "data" / "icdev.db"
        try:
            conn = sqlite3.connect(str(db_path))
            # Store as a chat turn in the agent_chat_turns table if it exists
            conn.execute(
                "INSERT OR IGNORE INTO agent_chat_turns "
                "(session_id, role, content, created_at) "
                "VALUES (?, 'agent', ?, datetime('now'))",
                (thread_id, text)
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error("Failed to store internal chat response: %s", e)
            return False
