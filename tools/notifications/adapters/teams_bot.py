#!/usr/bin/env python3
# CUI // SP-CTI
"""Microsoft Teams Bot notification adapter — sends outbound ICDEV notifications
to a Teams channel or conversation via the Bot Framework Connector API.

Named `teams_bot` to avoid collision with the existing `teams.py` webhook adapter.

Delegates to TeamsConnector.write("send_message") when available; falls back to
direct Bot Connector API POST.

Env vars:
    TEAMS_APP_ID         — Azure AD application (client) ID
    TEAMS_APP_SECRET     — Azure AD client secret
    TEAMS_TENANT_ID      — Azure AD tenant ID (default: common)
    TEAMS_CONVERSATION_ID — target conversation or channel ID for notifications
    TEAMS_SERVICE_URL     — Bot Framework service URL for the target channel
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.notifications.adapters.base import NotificationAdapter  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.notifications.teams_bot")
except Exception:
    logger = logging.getLogger("icdev.notifications.teams_bot")


class TeamsBotNotificationAdapter(NotificationAdapter):
    """Outbound notification adapter for Microsoft Teams via Bot Connector API."""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})
        self.app_id = os.environ.get("TEAMS_APP_ID", "")
        self.app_secret = os.environ.get("TEAMS_APP_SECRET", "")
        self.tenant_id = os.environ.get("TEAMS_TENANT_ID", "common")
        self.conversation_id = os.environ.get("TEAMS_CONVERSATION_ID", "")
        self.service_url = os.environ.get("TEAMS_SERVICE_URL", "").rstrip("/")
        self._token_url = (
            f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        )
        self._token_scope = "https://smba.trafficmanager.net/.default"

    def _get_token(self) -> Optional[str]:
        try:
            from tools.gateway.adapters.botframework_base import _get_connector_token
            return _get_connector_token(
                self.app_id, self.app_secret, self._token_url, self._token_scope
            )
        except Exception as exc:
            logger.debug("BotFramework token helper unavailable: %s", exc)
        return None

    def send(self, message: str, subject: str = "", **kwargs) -> bool:
        text = f"**{subject}**\n\n{message}" if subject else message
        conversation_id = kwargs.get("conversation_id", self.conversation_id)
        service_url = kwargs.get("service_url", self.service_url)

        # Delegate to DataBridge connector
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

        # Fallback: direct Bot Connector API
        if not self.app_id or not conversation_id or not service_url:
            logger.warning("Teams Bot: missing credentials or conversation/service URL")
            return False

        token = self._get_token()
        if not token:
            logger.error("Teams Bot: could not obtain connector token")
            return False

        url = f"{service_url}/v3/conversations/{conversation_id}/activities"
        payload = json.dumps({
            "type": "message",
            "from": {"id": self.app_id},
            "text": text,
            "textFormat": "markdown",
        }).encode("utf-8")
        try:
            req = Request(
                url, data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:  # nosec B310 -- Bot Framework service URL
                return resp.status in (200, 201)
        except (URLError, Exception) as exc:
            logger.error("Teams Bot notification failed: %s", exc)
            return False

    def health_check(self) -> Dict[str, Any]:
        configured = bool(self.app_id and self.app_secret and self.conversation_id and self.service_url)
        return {"adapter": "teams_bot", "configured": configured, "status": "ok" if configured else "unconfigured"}
