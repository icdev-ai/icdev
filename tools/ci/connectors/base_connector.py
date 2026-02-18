# CUI // SP-CTI
# ICDEV Chat Connector ABC — base class for all chat platform connectors (D66, D136)

"""
Abstract base class for chat platform connectors.

All built-in and marketplace chat connectors must implement this ABC.
Provides a consistent interface for inbound event parsing, outbound messaging,
and webhook signature verification.

Architecture Decisions:
    D66: Provider abstraction pattern (ABC + implementations)
    D136: Slack and Mattermost are built-in connectors with enable/disable toggles;
          additional platforms via marketplace plugins

Usage:
    class MyConnector(ChatConnectorAdapter):
        connector_name = "my_platform"
        ...
"""

from abc import ABC, abstractmethod
from typing import Optional

from tools.ci.core.event_envelope import EventEnvelope


class ChatConnectorAdapter(ABC):
    """Abstract base class for all chat platform connectors."""

    @property
    @abstractmethod
    def connector_name(self) -> str:
        """Unique connector name (e.g., 'slack', 'mattermost')."""
        ...

    @abstractmethod
    def parse_inbound(self, raw_payload: dict) -> Optional[EventEnvelope]:
        """Parse an inbound webhook payload into an EventEnvelope.

        Returns None if the event should be ignored (e.g., bot message, challenge).
        """
        ...

    @abstractmethod
    def send_message(
        self, channel_id: str, text: str, thread_id: str = None,
    ) -> bool:
        """Send a message to a channel/thread.

        Args:
            channel_id: Platform-specific channel ID.
            text: Message text.
            thread_id: Thread ID for threaded replies (D137: always use threads).

        Returns:
            True if message sent successfully, False otherwise.
        """
        ...

    @abstractmethod
    def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verify webhook request signature.

        Args:
            raw_body: Raw request body bytes.
            signature: Signature header value from the request.

        Returns:
            True if signature is valid.
        """
        ...

    def get_webhook_route(self) -> str:
        """Return the webhook route path for this connector."""
        return f"/chat/{self.connector_name}"

    def is_enabled(self) -> bool:
        """Check if this connector is enabled in config."""
        return True
