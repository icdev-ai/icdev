#!/usr/bin/env python3
# CUI // SP-CTI
"""Base channel adapter — ABC interface for all messaging channel adapters.

Every channel adapter (Telegram, Slack, Teams, Mattermost, internal)
implements this interface.  The gateway agent loads adapters dynamically
based on the channel config in args/remote_gateway_config.yaml.

Decision D133: Channel adapters are ABC + implementations (D66 pattern).
Decision D140: Mattermost uses REST API (no WebSocket), consistent with D20.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from tools.gateway.event_envelope import CommandEnvelope


class BaseChannelAdapter(ABC):
    """Abstract base class for messaging channel adapters."""

    def __init__(self, channel_name: str, config: Dict[str, Any]):
        self.channel_name = channel_name
        self.config = config
        self.enabled = config.get("enabled", False)
        self.requires_internet = config.get("requires_internet", False)
        self.min_il = config.get("min_il", "IL2")
        self.max_il = config.get("max_il", "IL4")
        self.webhook_path = config.get("webhook_path", "")

    @abstractmethod
    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify the webhook signature from the channel platform.

        Args:
            payload: Raw request body bytes.
            signature: Value from the channel's signature header.

        Returns:
            True if signature is valid (or verification not applicable).
        """

    @abstractmethod
    def parse_webhook(self, request_data: Dict[str, Any],
                      headers: Dict[str, str]) -> Optional[CommandEnvelope]:
        """Parse an inbound webhook payload into a CommandEnvelope.

        Args:
            request_data: Parsed JSON body of the webhook request.
            headers: HTTP headers from the webhook request.

        Returns:
            A CommandEnvelope if the payload is a valid command message,
            or None if it should be ignored (e.g., bot message, event
            type we don't handle).
        """

    @abstractmethod
    def send_message(self, channel_user_id: str, text: str,
                     thread_id: str = "") -> bool:
        """Send a reply message back to the user via the channel.

        Args:
            channel_user_id: Platform-specific user or chat identifier.
            text: Message text to send (may include markdown).
            thread_id: Thread/conversation ID for threaded replies.

        Returns:
            True if the message was sent successfully.
        """

    def send_confirmation(self, channel_user_id: str, command: str,
                          thread_id: str = "") -> bool:
        """Ask the user to confirm a command before execution.

        Default implementation sends a text prompt.  Channels that support
        interactive elements (buttons, etc.) can override this.

        Args:
            channel_user_id: Platform-specific user or chat identifier.
            command: The command that needs confirmation.
            thread_id: Thread/conversation ID.

        Returns:
            True if the confirmation prompt was sent.
        """
        text = (
            f"Confirm execution of `{command}`?\n"
            "Reply `yes` or `confirm` to proceed, `no` to cancel."
        )
        return self.send_message(channel_user_id, text, thread_id)

    def is_available(self, environment_mode: str) -> bool:
        """Check if this adapter should be loaded given the environment mode.

        Args:
            environment_mode: "connected" or "air_gapped".

        Returns:
            True if the adapter can run in this environment.
        """
        if not self.enabled:
            return False
        if environment_mode == "air_gapped" and self.requires_internet:
            return False
        return True

    def get_info(self) -> Dict[str, Any]:
        """Return adapter metadata for dashboard display."""
        return {
            "channel": self.channel_name,
            "enabled": self.enabled,
            "requires_internet": self.requires_internet,
            "min_il": self.min_il,
            "max_il": self.max_il,
            "webhook_path": self.webhook_path,
            "description": self.config.get("description", ""),
        }
