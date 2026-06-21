#!/usr/bin/env python3
# CUI // SP-CTI
"""MatterMost notification adapter — sends outbound ICDEV notifications to a
MatterMost channel.

Delegates to MattermostConnector.write("send_post") when available; falls back
to direct REST API call (POST /api/v4/posts).

Env vars:
    MATTERMOST_TOKEN    — personal access token
    MATTERMOST_URL      — base URL (https://mattermost.example.com)
    MATTERMOST_CHANNEL_ID — target channel ID
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.notifications.adapters.base import NotificationAdapter  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.notifications.mattermost")
except Exception:
    logger = get_logger("icdev.notifications.mattermost")


class MattermostNotificationAdapter(NotificationAdapter):
    """Outbound notification adapter for MatterMost."""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})
        self.token = os.environ.get("MATTERMOST_TOKEN", "")
        self.base_url = os.environ.get("MATTERMOST_URL", "").rstrip("/")
        self.channel_id = os.environ.get("MATTERMOST_CHANNEL_ID", "")

    def send(self, message: str, subject: str = "", **kwargs) -> bool:
        text = f"**{subject}**\n{message}" if subject else message
        channel_id = kwargs.get("channel_id", self.channel_id)

        # Delegate to DataBridge connector
        try:
            from tools.databridge.connectors.mattermost_connector import MattermostConnector
            from tools.databridge.connector import ConnectorRequest
            c = MattermostConnector()
            if c.connect({}):
                resp = c.write(
                    ConnectorRequest(table_name="send_post"),
                    {"message": text, "channel_id": channel_id},
                )
                if resp.status in ("ok", "success"):
                    return True
        except Exception as exc:
            logger.debug("MattermostConnector.write failed, falling back: %s", exc)

        # Fallback: direct REST API
        if not self.token or not self.base_url or not channel_id:
            logger.warning("MatterMost: missing credentials or channel_id")
            return False

        payload = json.dumps({"channel_id": channel_id, "message": text}).encode("utf-8")
        try:
            req = Request(
                f"{self.base_url}/api/v4/posts",
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:  # nosec B310 -- configured endpoint only
                return resp.status in (200, 201)
        except (URLError, Exception) as exc:
            logger.error("MatterMost notification failed: %s", exc)
            return False

    def health_check(self) -> Dict[str, Any]:
        configured = bool(self.token and self.base_url and self.channel_id)
        return {"adapter": "mattermost", "configured": configured, "status": "ok" if configured else "unconfigured"}
