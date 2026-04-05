#!/usr/bin/env python3
# CUI // SP-CTI
"""Slack webhook notification adapter."""

import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from tools.notifications.adapters.base import NotificationAdapter

SEVERITY_COLORS = {
    "info": "#36a64f",
    "warning": "#daa520",
    "error": "#ff6347",
    "critical": "#dc143c",
}


class SlackAdapter(NotificationAdapter):
    name = "slack"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        url_env = config.get("webhook_url_env", "SLACK_WEBHOOK_URL")
        self.webhook_url = os.environ.get(url_env, "")
        self.channel = config.get("channel", "")

    def send(self, title: str, body: str, severity: str = "info", metadata: Optional[Dict] = None) -> bool:
        if not self.webhook_url:
            return False

        color = SEVERITY_COLORS.get(severity, "#36a64f")
        payload = {
            "attachments": [
                {
                    "color": color,
                    "title": f"[ICDEV™ {severity.upper()}] {title}",
                    "text": body[:3000],
                    "footer": "ICDEV™ Notification Gateway",
                }
            ]
        }
        if self.channel:
            payload["channel"] = self.channel

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def health_check(self) -> Dict[str, Any]:
        return {
            "adapter": self.name,
            "enabled": self.enabled,
            "webhook_configured": bool(self.webhook_url),
            "status": "ok" if self.enabled and self.webhook_url else "misconfigured",
        }
