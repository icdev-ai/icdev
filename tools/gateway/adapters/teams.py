#!/usr/bin/env python3
# CUI // SP-CTI
"""Microsoft Teams gateway adapter — receives Bot Framework webhook commands
and sends replies via Graph API or Bot Connector.

Inbound: POST /gateway/teams (Bot Framework Activity, Authorization: Bearer JWT)
Outbound: TeamsConnector.write() → fallback to BotFrameworkBaseAdapter.send_message()

Commands: /icdev-* slash commands, icdev-* prefixed text, !icdev prefix

Requires environment variables:
    TEAMS_APP_ID        — Azure AD application (client) ID
    TEAMS_APP_SECRET    — Azure AD client secret
    TEAMS_TENANT_ID     — Azure AD tenant ID (default: common)

Decision D133: Channel adapters follow ABC pattern.
Decision D142: Teams uses BotFrameworkBaseAdapter.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.gateway.adapters.botframework_base import BotFrameworkBaseAdapter  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.gateway.adapters.teams")
except Exception:
    logger = get_logger("icdev.gateway.adapters.teams")


class TeamsAdapter(BotFrameworkBaseAdapter):
    """Gateway adapter for Microsoft Teams via Bot Framework."""

    _app_id_env = "TEAMS_APP_ID"
    _app_secret_env = "TEAMS_APP_SECRET"
    _token_scope = "https://smba.trafficmanager.net/.default"

    def __init__(self, config: Dict[str, Any]):
        super().__init__("teams", config)
        self.tenant_id = os.environ.get("TEAMS_TENANT_ID", "common")
        self._token_url = (
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        )

    def send_message(self, channel_user_id: str, text: str, thread_id: str = "") -> bool:
        """Send reply — tries TeamsConnector first, falls back to Bot Connector API."""
        conversation_id = thread_id or self._last_conversation_id

        # Delegate to DataBridge connector for IQE observability
        try:
            from tools.databridge.connectors.teams_connector import TeamsConnector
            from tools.databridge.connector import ConnectorRequest
            c = TeamsConnector()
            if c.connect({}):
                resp = c.write(
                    ConnectorRequest(table_name="send_message"),
                    {"text": text, "conversation_id": conversation_id},
                )
                if resp.status in ("ok", "success"):
                    return True
        except Exception as exc:
            logger.debug("TeamsConnector.write failed, falling back: %s", exc)

        # Fallback: direct Bot Connector API call
        return super().send_message(channel_user_id, text, conversation_id or thread_id)
