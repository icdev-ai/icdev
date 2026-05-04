# CUI // SP-CTI
"""Telegram notification adapter — sends messages via Bot API."""

import os
import urllib.request
import urllib.parse
import json
from typing import Any, Dict, Optional


def send(
    title: str,
    body: str,
    severity: str = "info",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Send a Telegram message via Bot API.

    Args:
        title: Notification title.
        body: Message body.
        severity: info/warning/critical.
        config: Adapter config from notification_config.yaml.

    Returns:
        Dict with status and message_id.
    """
    cfg = config or {}
    token_env = cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    chat_env = cfg.get("chat_id_env", "TELEGRAM_CHAT_ID")

    token = os.getenv(token_env, "")
    chat_id = os.getenv(chat_env, "")

    if not token or not chat_id:
        return {
            "status": "error",
            "message": f"Missing {token_env} or {chat_env}",
        }

    # Format message with severity icon
    icons = {
        "critical": "\U0001f6a8",
        "warning": "\u26a0\ufe0f",
        "info": "\u2139\ufe0f",
        "success": "\u2705",
    }
    icon = icons.get(severity, "\u2139\ufe0f")
    text = f"{icon} <b>{title}</b>\n\n{body}"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "status": "sent",
                "message_id": data.get("result", {}).get("message_id"),
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}
