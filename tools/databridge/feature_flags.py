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
    # AWS service emulation (floci) — DELEGATES to tools/cloud/emulator.py
    # ------------------------------------------------------------------
    #
    # These four keep their `localstack_*` names because callers import them by
    # name (localstack_connector, infra_canvas adapters, dockerfile_generator),
    # and they keep the FeatureStatus shape. What changed is that they no
    # longer ANSWER the question -- tools/cloud/emulator.py is the one switch
    # (flx-seam-01). A second reader of LOCALSTACK_ENABLED here is how this
    # module and detect_mode() came to disagree about whether an emulator was
    # in play; do not reintroduce one.

    @staticmethod
    def localstack() -> FeatureStatus:
        """Is the AWS emulator switched on? Delegates to ``emulator.enabled()``.

        ``FLOCI_ENABLED``, default false, with ``LOCALSTACK_ENABLED`` honoured
        as a deprecated alias.
        """
        from tools.cloud import emulator  # noqa: PLC0415

        if emulator.enabled():
            return FeatureStatus(
                enabled=True,
                status=IntegrationStatus.ENABLED,
                reason="",
            )
        return FeatureStatus(
            enabled=False,
            status=IntegrationStatus.DISABLED_BY_CONFIG,
            reason=(
                "AWS emulator (floci) integration is disabled. "
                "Set FLOCI_ENABLED=true in .env to enable "
                "(LOCALSTACK_ENABLED is honoured as a deprecated alias), and "
                f"point FLOCI_ENDPOINT at a running emulator "
                f"(default {emulator.DEFAULT_ENDPOINT}). "
                # docker-compose.yml declares this profile as of flx-compose-01.
                # Naming a profile that does not exist is what the previous
                # wording did ("--profile localstack"), so this line is only
                # correct while that service is declared -- pinned by
                # tests/cloud/test_emulator_seam.py.
                "Start one with `docker compose --profile floci up -d`. "
                "Safe to leave disabled on air-gap environments."
            ),
        )

    @staticmethod
    def localstack_endpoint() -> str:
        """Emulator endpoint URL. Delegates to ``emulator.endpoint()``."""
        from tools.cloud import emulator  # noqa: PLC0415

        return emulator.endpoint()

    @staticmethod
    def localstack_region() -> str:
        """Emulator region. Delegates to ``emulator.region()``.

        NOTE the default moved from ``us-east-1`` to ``us-gov-west-1`` with the
        seam — ICDEV's target partition. ``FLOCI_REGION`` (or the deprecated
        ``LOCALSTACK_REGION``) still overrides it.
        """
        from tools.cloud import emulator  # noqa: PLC0415

        return emulator.region()

    @staticmethod
    def localstack_credentials() -> tuple[str, str]:
        """Dummy AWS credentials. Delegates to ``emulator.credentials()``."""
        from tools.cloud import emulator  # noqa: PLC0415

        return emulator.credentials()

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
