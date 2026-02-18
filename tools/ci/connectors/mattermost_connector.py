# CUI // SP-CTI
# ICDEV Mattermost Connector — built-in Mattermost integration (D136, D137)

"""
Built-in Mattermost connector for ICDEV CI/CD.

Mattermost is common in DoD environments (self-hosted, IL4/IL5 compatible).

Handles:
- Inbound: Mattermost outgoing webhook payloads
- Outbound: POST /api/v4/posts with threading (D137)
- Signature: HMAC-SHA256 webhook token verification

Uses only `requests` (existing ICDEV dependency) + stdlib `hmac`/`hashlib`.

Architecture Decisions:
    D136: Mattermost is a built-in connector with enable/disable toggle
    D137: All Mattermost responses always use threads (root_id)

Usage:
    from tools.ci.connectors.mattermost_connector import MattermostConnector
    connector = MattermostConnector(config)
    envelope = connector.parse_inbound(payload)
    connector.send_message(channel_id, "Hello", thread_id=root_id)
"""

import hashlib
import hmac
import os
from typing import Optional

from tools.ci.connectors.base_connector import ChatConnectorAdapter
from tools.ci.core.event_envelope import EventEnvelope, BOT_IDENTIFIER


class MattermostConnector(ChatConnectorAdapter):
    """Built-in Mattermost connector."""

    connector_name = "mattermost"

    def __init__(self, config: dict = None):
        config = config or {}
        self._server_url = config.get("server_url", "").rstrip("/")
        self._bot_token = self._resolve_secret(
            config.get("bot_token_ref", ""),
            env_var="MATTERMOST_BOT_TOKEN",
        )
        self._webhook_secret = self._resolve_secret(
            config.get("webhook_secret_ref", ""),
            env_var="MATTERMOST_WEBHOOK_SECRET",
        )
        self._default_channel = config.get("default_channel", "")
        self._thread_mode = config.get("thread_mode", "always")
        self._enabled = config.get("enabled", False)

    def is_enabled(self) -> bool:
        return self._enabled

    def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verify Mattermost outgoing webhook token (HMAC-SHA256)."""
        if not self._webhook_secret:
            return False

        computed = hmac.new(
            self._webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed, signature)

    def parse_inbound(self, raw_payload: dict) -> Optional[EventEnvelope]:
        """Parse Mattermost outgoing webhook payload into EventEnvelope.

        Mattermost outgoing webhooks send:
            channel_id, channel_name, team_id, team_domain,
            post_id, text, timestamp, token, trigger_word,
            user_id, user_name, root_id (thread parent)
        """
        text = raw_payload.get("text", "")

        # Ignore bot messages
        if BOT_IDENTIFIER in text:
            return None

        user_name = raw_payload.get("user_name", "")
        if not text or not user_name:
            return None

        return EventEnvelope.from_mattermost_event(raw_payload)

    def send_message(
        self, channel_id: str, text: str, thread_id: str = None,
    ) -> bool:
        """Send message to Mattermost channel/thread.

        D137: Always threaded — root_id is required for responses.
        """
        if not self._bot_token or not self._server_url:
            print("Warning: Mattermost bot token or server URL not configured")
            return False

        # Ensure bot identifier
        if BOT_IDENTIFIER not in text:
            text = f"{BOT_IDENTIFIER} {text}"

        try:
            import requests

            payload = {
                "channel_id": channel_id or self._default_channel,
                "message": text,
            }

            # D137: Always use threads for responses
            if thread_id:
                payload["root_id"] = thread_id

            response = requests.post(
                f"{self._server_url}/api/v4/posts",
                headers={
                    "Authorization": f"Bearer {self._bot_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )

            if response.status_code in (200, 201):
                return True
            else:
                print(
                    f"Warning: Mattermost API error: "
                    f"{response.status_code} {response.text[:200]}"
                )
                return False

        except Exception as e:
            print(f"Warning: Failed to send Mattermost message: {e}")
            return False

    def _resolve_secret(self, ref: str, env_var: str = "") -> str:
        """Resolve a secret reference (AWS Secrets Manager or env var)."""
        if env_var:
            val = os.environ.get(env_var, "")
            if val:
                return val

        if ref and ref.startswith("aws:secretsmanager:"):
            secret_name = ref.split(":", 2)[-1]
            try:
                import boto3
                client = boto3.client("secretsmanager", region_name="us-gov-west-1")
                response = client.get_secret_value(SecretId=secret_name)
                return response.get("SecretString", "")
            except Exception:
                pass

        return ""
