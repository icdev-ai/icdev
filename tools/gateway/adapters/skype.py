#!/usr/bin/env python3
# CUI // SP-CTI
"""Skype gateway adapter — receives Bot Framework webhook Activities from Skype
and routes ICDEV commands through the security chain.

Inbound: POST /gateway/skype (Bot Framework Activity, Authorization: Bearer JWT)
Outbound: SkypeConnector.write() → fallback to BotFrameworkBaseAdapter.send_message()

Inbound messages are ALSO written to skype_inbox DB table so the Skype listener
can process them asynchronously and create Kanban tasks.

Commands: /icdev-* slash commands, !icdev prefix

Requires environment variables:
    SKYPE_APP_ID      — Bot application (client) ID
    SKYPE_APP_SECRET  — Bot application client secret

Decision D133: Channel adapters follow ABC pattern.
Decision D142: Skype uses BotFrameworkBaseAdapter (same as Teams).
Decision D144: Skype has no polling API — inbound messages go through skype_inbox.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.gateway.adapters.botframework_base import BotFrameworkBaseAdapter  # noqa: E402
from tools.gateway.event_envelope import CommandEnvelope  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.gateway.adapters.skype")
except Exception:
    logger = get_logger("icdev.gateway.adapters.skype")


class SkypeAdapter(BotFrameworkBaseAdapter):
    """Gateway adapter for Skype via Bot Framework."""

    _app_id_env = "SKYPE_APP_ID"
    _app_secret_env = "SKYPE_APP_SECRET"
    _token_url = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
    _token_scope = "https://api.botframework.com/.default"

    def __init__(self, config: Dict[str, Any]):
        super().__init__("skype", config)

    def parse_webhook(self, request_data: Dict[str, Any], headers: Dict[str, str]) -> Optional[CommandEnvelope]:
        """Parse Bot Framework Activity and write to skype_inbox for async processing."""
        envelope = super().parse_webhook(request_data, headers)

        # Always write to skype_inbox (even non-command messages) for the listener
        self._write_to_inbox(request_data)

        return envelope

    def _write_to_inbox(self, activity: Dict[str, Any]) -> None:
        """Persist Skype Activity to local skype_inbox table."""
        try:
            from tools.db.storage import get_connection

            activity_id = activity.get("id", "")
            if not activity_id:
                return

            text = (activity.get("text") or "").strip()
            conversation_id = (activity.get("conversation") or {}).get("id", "")
            service_url = (activity.get("serviceUrl") or "").rstrip("/")
            sender_id = (activity.get("from") or {}).get("id", "")

            conn = get_connection()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO skype_inbox "
                    "(activity_id, message_json, conversation_id, service_url, "
                    "sender_id, text, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        activity_id,
                        json.dumps(activity),
                        conversation_id,
                        service_url,
                        sender_id,
                        text[:500],
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("skype_inbox write failed: %s", exc)

    def send_message(self, channel_user_id: str, text: str, thread_id: str = "") -> bool:
        """Send reply — tries SkypeConnector first, falls back to Bot Connector API."""
        conversation_id = thread_id or self._last_conversation_id
        service_url = self._last_service_url

        try:
            from tools.databridge.connectors.skype_connector import SkypeConnector
            from tools.databridge.connector import ConnectorRequest
            c = SkypeConnector()
            if c.connect({}):
                resp = c.write(
                    ConnectorRequest(table_name="send_message"),
                    {"text": text, "conversation_id": conversation_id, "service_url": service_url},
                )
                if resp.status in ("ok", "success"):
                    return True
        except Exception as exc:
            logger.debug("SkypeConnector.write failed, falling back: %s", exc)

        return super().send_message(channel_user_id, text, conversation_id or thread_id)
