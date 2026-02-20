#!/usr/bin/env python3
# CUI // SP-CTI
"""Telegram Bot API adapter for the Remote Command Gateway.

Receives Telegram webhook updates, normalizes into CommandEnvelope,
and sends replies via the Telegram Bot API.

Requires environment variables:
    TELEGRAM_BOT_TOKEN: Bot token from @BotFather
    TELEGRAM_WEBHOOK_SECRET: Secret for webhook verification

Decision D133: Channel adapters are ABC + implementations.
Decision D134: Disabled in air-gapped environments (requires_internet: true).
"""

import hashlib
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

logger = logging.getLogger("icdev.gateway.adapters.telegram")

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramAdapter(BaseChannelAdapter):
    """Adapter for Telegram Bot API."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("telegram", config)
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify Telegram webhook secret token.

        Telegram uses a secret_token header (X-Telegram-Bot-Api-Secret-Token)
        that must match the configured webhook secret.
        """
        if not self.webhook_secret:
            return True  # Skip if not configured

        if not signature:
            return False

        return hmac.compare_digest(signature, self.webhook_secret)

    def parse_webhook(self, request_data: Dict[str, Any],
                      headers: Dict[str, str]) -> Optional[CommandEnvelope]:
        """Parse a Telegram Update object into a CommandEnvelope.

        Handles:
        - message.text for direct messages
        - message.text in group chats (bot must be mentioned or use /)
        """
        message = request_data.get("message", {})
        if not message:
            return None

        text = message.get("text", "").strip()
        if not text:
            return None

        # In group chats, only respond to commands (starting with /)
        chat_type = message.get("chat", {}).get("type", "private")
        if chat_type in ("group", "supergroup") and not text.startswith("/"):
            return None

        # Strip bot mention in group chats (e.g., /icdev-status@ICDEVBot)
        if "@" in text.split()[0]:
            first_word = text.split()[0]
            text = first_word.split("@")[0] + " " + " ".join(text.split()[1:])
            text = text.strip()

        # Only process ICDEV commands
        if not (text.startswith("/icdev") or text.startswith("/bind")
                or text.startswith("icdev-")):
            return None

        command, args = parse_command_text(text)
        if not command:
            return None

        from_user = message.get("from", {})
        chat_id = str(message.get("chat", {}).get("id", ""))

        envelope = CommandEnvelope(
            channel="telegram",
            channel_user_id=str(from_user.get("id", "")),
            channel_user_name=from_user.get("username", from_user.get("first_name", "")),
            channel_message_id=str(message.get("message_id", "")),
            channel_thread_id=chat_id,
            raw_text=text,
            command=command,
            args=args,
            project_id=args.get("project_id", ""),
            is_bot=from_user.get("is_bot", False),
            timestamp=datetime.fromtimestamp(
                message.get("date", 0), tz=timezone.utc
            ).isoformat() if message.get("date") else datetime.now(timezone.utc).isoformat(),
        )

        return envelope

    def send_message(self, channel_user_id: str, text: str,
                     thread_id: str = "") -> bool:
        """Send a message via Telegram Bot API.

        Args:
            channel_user_id: Not used directly — we send to chat_id (thread_id).
            text: Message text (Telegram supports Markdown).
            thread_id: Telegram chat ID to send to.
        """
        if not self.bot_token:
            logger.error("TELEGRAM_BOT_TOKEN not set")
            return False

        chat_id = thread_id or channel_user_id
        url = f"{TELEGRAM_API_BASE.format(token=self.bot_token)}/sendMessage"

        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }).encode("utf-8")

        try:
            req = Request(url, data=payload,
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except (URLError, Exception) as e:
            logger.error("Telegram send failed: %s", e)
            return False
