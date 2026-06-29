# CUI // SP-CTI
"""Slack OAuth connector — recent mentions and DM summaries."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode

from tools.logging.icdev_logger import get_logger
from tools.second_brain.connectors.base import BaseConnector

logger = get_logger(__name__)

_AUTH_URL = "https://slack.com/oauth/v2/authorize"
_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
_API = "https://slack.com/api"
_SCOPES = "channels:history,im:history,users:read,users.profile:read"


class SlackConnector(BaseConnector):
    service = "slack"

    def verify(self, credentials: dict) -> bool:
        token = credentials.get("access_token") or credentials.get("bot_token")
        if not token:
            return False
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{_API}/auth.test",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
                return bool(data.get("ok"))
        except Exception as exc:
            logger.debug("[slack] verify failed: %s", exc)
            return False

    def get_oauth_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id = os.environ.get("SLACK_CLIENT_ID", "")
        params = {
            "client_id": client_id,
            "scope": _SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        import urllib.request
        client_id = os.environ.get("SLACK_CLIENT_ID", "")
        client_secret = os.environ.get("SLACK_CLIENT_SECRET", "")
        body = urlencode({
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }).encode()
        req = urllib.request.Request(f"{_API}/oauth.v2.access", data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def get_todays_items(self, user_id: str) -> list[dict[str, Any]]:
        """Return recent Slack mentions (last 24 h)."""
        token = self._get_token(user_id)
        if not token:
            return []
        try:
            import time, urllib.request
            oldest = str(int(time.time()) - 86400)
            req = urllib.request.Request(
                f"{_API}/search.messages?query=<@me>&oldest={oldest}&count=10",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            items = []
            for msg in (data.get("messages", {}).get("matches") or []):
                items.append({
                    "source": "slack",
                    "type": "mention",
                    "channel": msg.get("channel", {}).get("name", ""),
                    "text": (msg.get("text") or "")[:200],
                    "user": msg.get("username", ""),
                    "ts": msg.get("ts", ""),
                })
            return items
        except Exception as exc:
            logger.warning("[slack] get_todays_items failed: %s", exc)
            return []

    def post_dm(self, user_id: str, slack_user_id: str, text: str) -> bool:
        """Post a DM to *slack_user_id*. Returns True on success."""
        token = self._get_token(user_id)
        if not token:
            return False
        try:
            import urllib.request
            body = json.dumps({"channel": slack_user_id, "text": text}).encode()
            req = urllib.request.Request(f"{_API}/chat.postMessage", data=body)
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return bool(data.get("ok"))
        except Exception as exc:
            logger.warning("[slack] post_dm failed: %s", exc)
            return False

    def sync_to_context(self, user_id: str) -> dict[str, Any]:
        items = self.get_todays_items(user_id)
        return {"service": "slack", "items_count": len(items), "items": items}

    def _get_token(self, user_id: str) -> str | None:
        try:
            from tools.second_brain.integrations import get_decrypted_token
            return get_decrypted_token(user_id, "slack")
        except Exception:
            return None
