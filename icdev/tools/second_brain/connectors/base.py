# CUI // SP-CTI
"""Base connector interface for Second Brain external integrations."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseConnector(ABC):
    service: str = ""

    @abstractmethod
    def verify(self, credentials: dict) -> bool:
        """Test credentials. Returns True if valid."""

    @abstractmethod
    def get_todays_items(self, user_id: str) -> list[dict[str, Any]]:
        """Return today's relevant items for the daily briefing."""

    @abstractmethod
    def sync_to_context(self, user_id: str) -> dict[str, Any]:
        """Pull latest data and store in memory_entries / profile context."""

    def get_oauth_authorize_url(self, state: str, redirect_uri: str) -> str:
        """Return the OAuth authorization URL. Override for OAuth services."""
        raise NotImplementedError(f"{self.service} does not support OAuth")

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """Exchange authorization code for tokens. Override for OAuth services."""
        raise NotImplementedError(f"{self.service} does not support OAuth code exchange")
