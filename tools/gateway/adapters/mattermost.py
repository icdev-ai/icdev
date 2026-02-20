#!/usr/bin/env python3
# CUI // SP-CTI
"""Mattermost adapter for the Remote Command Gateway (air-gapped).

Receives Mattermost outgoing webhook payloads, normalizes into
CommandEnvelope, and sends replies via the Mattermost REST API.

This adapter is designed for air-gapped environments where
Telegram/Slack/Teams are not available. Mattermost runs on-prem
within the enclave — no data leaves the network.

Requires environment variables:
    MATTERMOST_URL: Base URL (e.g., https://mattermost.enclave.mil)
    MATTERMOST_TOKEN: Bot access token

Decision D134: Air-gapped environments use internal chat + optional Mattermost.
Decision D140: Mattermost uses REST API (no WebSocket), consistent with D20.
"""

import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.gateway.adapters.base import BaseChannelAdapter
from tools.gateway.event_envelope import CommandEnvelope, parse_command_text

logger = logging.getLogger("icdev.gateway.adapters.mattermost")


class MattermostAdapter(BaseChannelAdapter):
    """Adapter for Mattermost (on-prem, air-gapped safe)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("mattermost", config)
        self.base_url = os.environ.get("MATTERMOST_URL", "").rstrip("/")
        self.token = os.environ.get("MATTERMOST_TOKEN", "")
        self.webhook_token = os.environ.get("MATTERMOST_WEBHOOK_TOKEN", "")

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify Mattermost outgoing webhook token.

        Mattermost outgoing webhooks include a token field in the payload
        that must match the configured webhook token.
        """
        if not self.webhook_token:
            return True

        if not signature:
            return False

        return hmac.compare_digest(signature, self.webhook_token)

    def parse_webhook(self, request_data: Dict[str, Any],
                      headers: Dict[str, str]) -> Optional[CommandEnvelope]:
        """Parse a Mattermost outgoing webhook payload into a CommandEnvelope.

        Mattermost outgoing webhook format:
        {
            "token": "...",
            "team_id": "...",
            "channel_id": "...",
            "channel_name": "...",
            "user_id": "...",
            "user_name": "...",
            "text": "/icdev-status proj-123",
            "trigger_word": "/icdev-status",
            "post_id": "...",
            "timestamp": 1234567890
        }
        """
        text = request_data.get("text", "").strip()
        if not text:
            return None

        # Only process ICDEV commands
        if not (text.startswith("/icdev") or text.startswith("/bind")
                or text.startswith("icdev-")):
            return None

        command, args = parse_command_text(text)
        if not command:
            return None

        ts = request_data.get("timestamp")
        if ts:
            try:
                timestamp = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except (ValueError, TypeError):
                timestamp = datetime.now(timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()

        envelope = CommandEnvelope(
            channel="mattermost",
            channel_user_id=request_data.get("user_id", ""),
            channel_user_name=request_data.get("user_name", ""),
            channel_message_id=request_data.get("post_id", ""),
            channel_thread_id=request_data.get("channel_id", ""),
            raw_text=text,
            command=command,
            args=args,
            project_id=args.get("project_id", ""),
            timestamp=timestamp,
            signature=headers.get("X-Mattermost-Token", request_data.get("token", "")),
        )

        return envelope

    def send_message(self, channel_user_id: str, text: str,
                     thread_id: str = "") -> bool:
        """Send a message via Mattermost REST API.

        Uses POST /api/v4/posts to create a new post in the channel.

        Args:
            channel_user_id: Mattermost user ID (not used directly).
            text: Message text (Mattermost markdown).
            thread_id: Channel ID to post in.
        """
        if not self.base_url or not self.token:
            logger.error("MATTERMOST_URL or MATTERMOST_TOKEN not set")
            return False

        url = f"{self.base_url}/api/v4/posts"

        payload = json.dumps({
            "channel_id": thread_id,
            "message": text,
        }).encode("utf-8")

        try:
            req = Request(url, data=payload, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            })
            with urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201)
        except (URLError, Exception) as e:
            logger.error("Mattermost send failed: %s", e)
            return False
