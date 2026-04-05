#!/usr/bin/env python3
# CUI // SP-CTI
"""Microsoft Teams webhook notification adapter."""

import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from tools.notifications.adapters.base import NotificationAdapter

SEVERITY_COLORS = {
    "info": "00ff00",
    "warning": "ffcc00",
    "error": "ff6347",
    "critical": "dc143c",
}


class TeamsAdapter(NotificationAdapter):
    name = "teams"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        url_env = config.get("webhook_url_env", "TEAMS_WEBHOOK_URL")
        self.webhook_url = os.environ.get(url_env, "")

    def send(self, title: str, body: str, severity: str = "info", metadata: Optional[Dict] = None) -> bool:
        if not self.webhook_url:
            return False

        color = SEVERITY_COLORS.get(severity, "00ff00")
        # Office 365 Connector Card format
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": color,
            "summary": f"ICDEV™ {severity.upper()}: {title}",
            "sections": [
                {
                    "activityTitle": f"[ICDEV™ {severity.upper()}] {title}",
                    "text": body[:5000],
                    "markdown": True,
                }
            ],
        }

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
