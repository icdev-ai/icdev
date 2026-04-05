#!/usr/bin/env python3
# CUI // SP-CTI
"""Base notification adapter — ABC for all delivery backends."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class NotificationAdapter(ABC):
    """Abstract base for notification delivery adapters."""

    name: str = "base"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.enabled = config.get("enabled", False)

    @abstractmethod
    def send(self, title: str, body: str, severity: str = "info", metadata: Optional[Dict] = None) -> bool:
        """Send a notification.

        Args:
            title: Notification title/subject.
            body: Notification body (plain text or markdown).
            severity: One of: info, warning, error, critical.
            metadata: Optional extra data for the notification.

        Returns:
            True if delivered successfully, False otherwise.
        """

    def health_check(self) -> Dict[str, Any]:
        """Check adapter health/connectivity."""
        return {
            "adapter": self.name,
            "enabled": self.enabled,
            "status": "ok" if self.enabled else "disabled",
        }
