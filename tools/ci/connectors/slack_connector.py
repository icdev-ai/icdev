# [TEMPLATE: CUI // SP-CTI]
# ICDEV Slack Connector — built-in Slack integration (D136, D137)

"""
Built-in Slack connector for ICDEV CI/CD.

Handles:
- Inbound: Slack Events API (event_callback, url_verification)
- Outbound: chat.postMessage with threading (D137)
- Signature: HMAC-SHA256 request signing verification

Uses only `requests` (existing ICDEV dependency) + stdlib `hmac`/`hashlib`.
No `slack_sdk` dependency required.

Architecture Decisions:
    D136: Slack is a built-in connector with enable/disable toggle
    D137: All Slack responses always use threads

Usage:
    from tools.ci.connectors.slack_connector import SlackConnector
    connector = SlackConnector(config)
    envelope = connector.parse_inbound(payload)
    connector.send_message(channel_id, "Hello", thread_id=thread_ts)
"""

import hashlib
import hmac
import os
import time
from typing import Optional

from tools.ci.connectors.base_connector import ChatConnectorAdapter
from tools.ci.core.event_envelope import EventEnvelope, BOT_IDENTIFIER


SLACK_API_BASE = "https://slack.com/api"


class SlackConnector(ChatConnectorAdapter):
    """Built-in Slack connector."""

    connector_name = "slack"

    def __init__(self, config: dict = None):
        config = config or {}
        self._bot_token = self._resolve_secret(
            config.get("bot_token_ref", ""),
            env_var="SLACK_BOT_TOKEN",
        )
        self._signing_secret = self._resolve_secret(
            config.get("signing_secret_ref", ""),
            env_var="SLACK_SIGNING_SECRET",
        )
        self._default_channel = config.get("default_channel", "")
        self._thread_mode = config.get("thread_mode", "always")
        self._enabled = config.get("enabled", False)

    def is_enabled(self) -> bool:
        return self._enabled

    def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verify Slack request signing (HMAC-SHA256).

        Slack signs requests with: v0=HMAC_SHA256(signing_secret, "v0:{timestamp}:{body}")
        """
        if not self._signing_secret:
            return False

        # Extract timestamp from signature header
        # Signature format: v0=<hex>
        # Also need X-Slack-Request-Timestamp (passed via signature as "ts:sig")
        parts = signature.split(":", 1) if ":" in signature else ["", signature]
        timestamp = parts[0] if len(parts) > 1 else str(int(time.time()))
        sig_value = parts[-1]

        # Check timestamp freshness (5 minute window)
        try:
            ts = int(timestamp)
            if abs(time.time() - ts) > 300:
                return False
        except (ValueError, TypeError):
            return False

        sig_basestring = f"v0:{timestamp}:{raw_body.decode('utf-8', errors='replace')}"
        computed = "v0=" + hmac.new(
            self._signing_secret.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed, sig_value)

    def parse_inbound(self, raw_payload: dict) -> Optional[EventEnvelope]:
        """Parse Slack Events API payload into EventEnvelope.

        Handles:
        - url_verification (challenge response) → returns None
        - event_callback → message, app_mention
        """
        payload_type = raw_payload.get("type", "")

        # URL verification challenge — handled by webhook route directly
        if payload_type == "url_verification":
            return None

        if payload_type != "event_callback":
            return None

        event = raw_payload.get("event", {})
        event_type = event.get("type", "")

        # Only handle message events and app_mentions
        if event_type not in ("message", "app_mention"):
            return None

        # Ignore bot messages
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return None

        text = event.get("text", "")
        if BOT_IDENTIFIER in text:
            return None

        event.get("channel", "")
        event.get("thread_ts", event.get("ts", ""))
        event.get("user", "")

        return EventEnvelope.from_slack_event(raw_payload)

    def send_message(
        self, channel_id: str, text: str, thread_id: str = None,
    ) -> bool:
        """Send message to Slack channel/thread.

        D137: Always threaded — thread_id is required for responses.
        """
        if not self._bot_token:
            print("Warning: Slack bot token not configured")
            return False

        # Ensure bot identifier
        if BOT_IDENTIFIER not in text:
            text = f"{BOT_IDENTIFIER} {text}"

        try:
            import requests

            payload = {
                "channel": channel_id or self._default_channel,
                "text": text,
            }

            # D137: Always use threads for responses
            if thread_id:
                payload["thread_ts"] = thread_id

            response = requests.post(
                f"{SLACK_API_BASE}/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {self._bot_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10,
            )

            result = response.json()
            if result.get("ok"):
                return True
            else:
                print(f"Warning: Slack API error: {result.get('error', 'unknown')}")
                return False

        except Exception as e:
            print(f"Warning: Failed to send Slack message: {e}")
            return False

    def _resolve_secret(self, ref: str, env_var: str = "") -> str:
        """Resolve a secret reference (AWS Secrets Manager or env var)."""
        # Try env var first (development / air-gapped)
        if env_var:
            val = os.environ.get(env_var, "")
            if val:
                return val

        # Try AWS Secrets Manager
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
