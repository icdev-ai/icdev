# CUI // SP-CTI
"""Abstract base class for external step adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ExternalStepAdapter(ABC):

    @abstractmethod
    def send(self, step: dict) -> str:
        """Execute the external action.

        Returns an external_ref string (ticket_id, message_id, page_url, etc.)
        that identifies the created external artifact.
        """

    @abstractmethod
    def poll_status(self, step: dict) -> bool:
        """Check if the external action has been completed.

        Returns True if the step is done and the instance can advance.
        """

    def build_payload(self, step: dict) -> dict:
        """Build the system-specific payload dict for this step.

        Override in subclasses for custom payload construction.
        """
        payload_raw = step.get("payload_json") or "{}"
        import json
        try:
            return json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except Exception:
            return {}
