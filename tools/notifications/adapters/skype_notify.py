#!/usr/bin/env python3
# CUI // SP-CTI
"""Skype notification adapter — sends outbound ICDEV notifications to a Skype
conversation via the Bot Framework Connector API.

Named `skype_notify` to coexist with any future `skype.py` gateway module.

Delegates to SkypeConnector.write("send_message") when available; falls back to
direct Bot Connector API POST.

Env vars:
    SKYPE_APP_ID           — Bot application (client) ID
    SKYPE_APP_SECRET       — Bot application client secret
    SKYPE_CONVERSATION_ID  — target Skype conversation ID for notifications
    SKYPE_SERVICE_URL      — Bot Framework service URL (default: https://smba.trafficmanager.net/apis)
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
    logger = get_logger("icdev.notifications.skype")
except Exception:
    logger = get_logger("icdev.notifications.skype")

_MSA_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_MSA_SCOPE = "https://api.botframework.com/.default"
_DEFAULT_SERVICE_URL = "https://smba.trafficmanager.net/apis"


class SkypeNotificationAdapter(NotificationAdapter):
    """Outbound notification adapter for Skype via Bot Connector API."""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})
        self.app_id = os.environ.get("SKYPE_APP_ID", "")
        self.app_secret = os.environ.get("SKYPE_APP_SECRET", "")
        self.conversation_id = os.environ.get("SKYPE_CONVERSATION_ID", "")
        self.service_url = os.environ.get("SKYPE_SERVICE_URL", _DEFAULT_SERVICE_URL).rstrip("/")

    def _get_token(self) -> Optional[str]:
        try:
            from tools.gateway.adapters.botframework_base import _get_connector_token
            return _get_connector_token(self.app_id, self.app_secret, _MSA_TOKEN_URL, _MSA_SCOPE)
        except Exception as exc:
            logger.debug("BotFramework token helper unavailable: %s", exc)
        return None

    def send(self, message: str, subject: str = "", **kwargs) -> bool:
        text = f"**{subject}**\n\n{message}" if subject else message
        conversation_id = kwargs.get("conversation_id", self.conversation_id)
        service_url = kwargs.get("service_url", self.service_url)

        # Delegate to DataBridge connector
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

        # Fallback: direct Bot Connector API
        if not self.app_id or not conversation_id:
            logger.warning("Skype: missing app credentials or conversation_id")
            return False

        token = self._get_token()
        if not token:
            logger.error("Skype: could not obtain MSA connector token")
            return False

        url = f"{service_url}/v3/conversations/{conversation_id}/activities"
        payload = json.dumps({
            "type": "message",
            "from": {"id": self.app_id},
            "text": text,
        }).encode("utf-8")
        try:
            req = Request(
                url, data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:  # nosec B310 -- Bot Framework service URL only
                return resp.status in (200, 201)
        except (URLError, Exception) as exc:
            logger.error("Skype notification failed: %s", exc)
            return False

    def health_check(self) -> Dict[str, Any]:
        configured = bool(self.app_id and self.app_secret and self.conversation_id)
        return {"adapter": "skype", "configured": configured, "status": "ok" if configured else "unconfigured"}
