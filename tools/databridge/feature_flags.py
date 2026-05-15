# CUI // SP-CTI
"""DataBridge integration feature flags.

Single source of truth for optional external integrations (GNS3, LocalStack,
Docker). All adapters and connectors import from here — no ad-hoc os.getenv()
calls scattered across the codebase.

Design rules:
  - All flags default to False so air-gap environments require zero config.
  - Operators explicitly opt in by setting the flag to 'true' in .env.
  - Flags are checked at adapter init time, not per-call, so the check is O(1).
  - Adapters return a typed disabled response (not an exception) when off.

Usage::

    from tools.databridge.feature_flags import IntegrationFeatureFlags

    flags = IntegrationFeatureFlags()
    status = flags.gns3()
    if not status.enabled:
        return status.as_disabled_response()

    # --- or at adapter init ---
    self._flag = IntegrationFeatureFlags.gns3()
    # then in each method:
    if not self._flag.enabled:
        return self._flag.as_disabled_response()
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class IntegrationStatus(Enum):
    ENABLED = "enabled"
    DISABLED_BY_CONFIG = "disabled_by_config"


@dataclass(frozen=True)
class FeatureStatus:
    """Result of a feature flag check."""

    enabled: bool
    status: IntegrationStatus
    reason: str

    def as_disabled_response(self) -> dict:
        """Return a dict callers can return directly when the integration is off."""
        return {
            "status": "disabled",
            "enabled": False,
            "reason": self.reason,
        }


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


class IntegrationFeatureFlags:
    """Reads ICDEV integration feature flags from environment variables.

    All flags are staticmethod so callers can use them without instantiation:
        IntegrationFeatureFlags.gns3()
        IntegrationFeatureFlags.localstack()
        IntegrationFeatureFlags.docker()
    """

    # ------------------------------------------------------------------
    # GNS3 — network simulation
    # ------------------------------------------------------------------

    @staticmethod
    def gns3() -> FeatureStatus:
        """Returns enabled if GNS3_ENABLED=true in environment.

        Required env vars when enabled:
          GNS3_ENABLED=true
          GNS3_HOST=http://localhost:3080   (or GNS3 server address)
          GNS3_USERNAME=admin               (optional)
          GNS3_PASSWORD=...                 (optional)
        """
        raw = os.getenv("GNS3_ENABLED", "false")
        if _truthy(raw):
            return FeatureStatus(
                enabled=True,
                status=IntegrationStatus.ENABLED,
                reason="",
            )
        return FeatureStatus(
            enabled=False,
            status=IntegrationStatus.DISABLED_BY_CONFIG,
            reason=(
                "GNS3 integration is disabled. "
                "Set GNS3_ENABLED=true in .env to enable. "
                "Safe to leave disabled on air-gap environments without a GNS3 server."
            ),
        )

    @staticmethod
    def gns3_url() -> str:
        """Return the configured GNS3 server URL (from env or default)."""
        return os.getenv("GNS3_HOST", "http://localhost:3080").rstrip("/")

    @staticmethod
    def gns3_credentials() -> tuple[str, str, str]:
        """Return (username, password, token) from environment."""
        return (
            os.getenv("GNS3_USERNAME", ""),
            os.getenv("GNS3_PASSWORD", ""),
            os.getenv("GNS3_TOKEN", ""),
        )

    @staticmethod
    def gns3_timeout() -> float:
        try:
            return float(os.getenv("GNS3_TIMEOUT", "10"))
        except ValueError:
            return 10.0

    # ------------------------------------------------------------------
    # LocalStack — AWS service emulation
    # ------------------------------------------------------------------

    @staticmethod
    def localstack() -> FeatureStatus:
        """Returns enabled if LOCALSTACK_ENABLED=true in environment.

        Required env vars when enabled:
          LOCALSTACK_ENABLED=true
          LOCALSTACK_ENDPOINT=http://localhost:4566   (or compose service name)
          LOCALSTACK_REGION=us-east-1                 (optional, default us-east-1)
        """
        raw = os.getenv("LOCALSTACK_ENABLED", "false")
        if _truthy(raw):
            return FeatureStatus(
                enabled=True,
                status=IntegrationStatus.ENABLED,
                reason="",
            )
        return FeatureStatus(
            enabled=False,
            status=IntegrationStatus.DISABLED_BY_CONFIG,
            reason=(
                "LocalStack integration is disabled. "
                "Set LOCALSTACK_ENABLED=true in .env to enable. "
                "Run: docker compose --profile localstack up -d"
            ),
        )

    @staticmethod
    def localstack_endpoint() -> str:
        """Return the configured LocalStack endpoint URL."""
        return os.getenv("LOCALSTACK_ENDPOINT", "http://localhost:4566").rstrip("/")

    @staticmethod
    def localstack_region() -> str:
        return os.getenv("LOCALSTACK_REGION", "us-east-1")

    @staticmethod
    def localstack_credentials() -> tuple[str, str]:
        """Return (access_key_id, secret_access_key) for LocalStack.

        LocalStack accepts any non-empty credential values. 'test'/'test'
        is the conventional dummy pair used in development.
        """
        return (
            os.getenv("AWS_ACCESS_KEY_ID", "test"),
            os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        )

    @staticmethod
    def localstack_timeout() -> float:
        try:
            return float(os.getenv("LOCALSTACK_TIMEOUT", "15"))
        except ValueError:
            return 15.0

    # ------------------------------------------------------------------
    # Docker — container runtime
    # ------------------------------------------------------------------

    @staticmethod
    def docker() -> FeatureStatus:
        """Returns enabled if DOCKER_ENABLED is not explicitly set to false.

        Docker defaults to enabled because it is typically available in
        non-air-gap environments. Set DOCKER_ENABLED=false to disable.
        """
        raw = os.getenv("DOCKER_ENABLED", "true")
        if _truthy(raw):
            return FeatureStatus(
                enabled=True,
                status=IntegrationStatus.ENABLED,
                reason="",
            )
        return FeatureStatus(
            enabled=False,
            status=IntegrationStatus.DISABLED_BY_CONFIG,
            reason=(
                "Docker integration is disabled (DOCKER_ENABLED=false). "
                "Remove or set DOCKER_ENABLED=true to re-enable."
            ),
        )

    @staticmethod
    def docker_socket() -> str:
        """Return the Docker socket path or host from environment."""
        return os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
