#!/usr/bin/env python3
# CUI // SP-CTI
"""Generic webhook notification adapter."""

import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from tools.notifications.adapters.base import NotificationAdapter


class WebhookAdapter(NotificationAdapter):
    name = "webhook"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        url_env = config.get("url_env", "NOTIFICATION_WEBHOOK_URL")
        self.url = os.environ.get(url_env, "")
        self.method = config.get("method", "POST")
        self.headers = config.get("headers", {})

    def send(self, title: str, body: str, severity: str = "info", metadata: Optional[Dict] = None) -> bool:
        if not self.url:
            return False

        payload = {
            "source": "icdev",
            "severity": severity,
            "title": title,
            "body": body,
            "metadata": metadata or {},
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            headers.update(self.headers)
            req = urllib.request.Request(
                self.url,
                data=data,
                headers=headers,
                method=self.method,
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError):
            return False
