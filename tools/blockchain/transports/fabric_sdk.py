#!/usr/bin/env python3
# CUI // SP-CTI
"""fabric-sdk-py (``hfc``) anchor transport — optional, never assumed.

``hfc`` is in NEITHER ``requirements.txt`` NOR ``pyproject.toml``. It is not an
ICDEV dependency and this module does not make it one: the import happens
inside :meth:`health` and :meth:`_client`, never at module scope, so importing
this file on a stock install is free and cannot fail.

When ``hfc`` is absent the transport reports ``unavailable`` with that as the
reason, the registry skips it, and anchors queue. That is the whole point of
the registry — the SDK's absence stops being a permanently-False module
constant that silently routes everything to a no-op, and becomes one unhealthy
backend among several.

A network profile (``fabric.transports.fabric_sdk.net_profile``) is required:
``hfc.fabric.Client`` cannot be constructed without one. No profile configured
is also ``unavailable``, reported distinctly from "SDK not installed" so an
operator can tell which thing to fix.
"""

from __future__ import annotations

import time
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from tools.blockchain.transports.base import (
    STATUS_OK,
    STATUS_UNAVAILABLE,
    STATUS_UNREACHABLE,
    AnchorTransport,
    TransportHealth,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("blockchain.transport.fabric_sdk")

SDK_MISSING_MESSAGE = (
    "fabric-sdk-py (hfc) is not installed — it is an undeclared ICDEV "
    "dependency and is never required; use the peer_cli transport instead"
)


class FabricSdkTransport(AnchorTransport):
    """Anchors through ``hfc.fabric.Client`` when the SDK is actually present."""

    backend = "fabric_sdk"

    def __init__(
        self,
        net_profile: str | None = None,
        org_name: str | None = None,
        user_name: str | None = None,
        peers: list | None = None,
        priority: int = 10,
        name: str = "fabric_sdk",
        timeout_seconds: int = 60,
    ) -> None:
        self.net_profile = net_profile
        self.org_name = org_name
        self.user_name = user_name
        self.peers = list(peers or [])
        self.priority = priority
        self.name = name
        self.timeout_seconds = int(timeout_seconds or 60)
        self._cached_client: Any = None

    # -- health --------------------------------------------------------------

    def health(self) -> TransportHealth:
        started = time.monotonic()
        try:
            installed = find_spec("hfc") is not None and find_spec("hfc.fabric") is not None
        except Exception:  # noqa: BLE001 — a broken hfc install is "not usable"
            installed = False
        if not installed:
            return self._health(STATUS_UNAVAILABLE, SDK_MISSING_MESSAGE)

        if not self.net_profile:
            return self._health(
                STATUS_UNAVAILABLE,
                "fabric-sdk-py is installed but no network profile is configured "
                "(fabric.transports.fabric_sdk.net_profile)",
            )
        if not Path(self.net_profile).exists():
            return self._health(
                STATUS_UNAVAILABLE,
                f"network profile not found: {self.net_profile}",
            )

        try:
            self._client()
        except Exception as exc:  # noqa: BLE001 — health() must never raise
            return self._health(
                STATUS_UNREACHABLE,
                f"fabric-sdk-py client could not be constructed: {exc}",
                latency_ms=(time.monotonic() - started) * 1000,
            )

        return self._health(
            STATUS_OK,
            f"fabric-sdk-py client ready ({self.net_profile})",
            latency_ms=(time.monotonic() - started) * 1000,
        )

    # -- chaincode -----------------------------------------------------------

    def chaincode_invoke(
        self,
        channel: str,
        chaincode: str,
        fcn: str,
        args: list,
        **kwargs: Any,
    ) -> dict:
        try:
            client = self._client()
        except Exception as exc:  # noqa: BLE001
            return self._failed(f"fabric-sdk-py unavailable: {exc}")

        try:
            user = client.get_user(self.org_name, self.user_name)
            response = client.chaincode_invoke(
                requestor=user,
                channel_name=channel,
                peers=self.peers or None,
                cc_name=chaincode,
                fcn=fcn,
                args=[str(a) for a in (args or [])],
                wait_for_event=True,
            )
        except Exception as exc:  # noqa: BLE001 — a failed submit must queue, not raise
            logger.warning("fabric-sdk-py invoke %s:%s failed: %s", chaincode, fcn, exc)
            return self._failed(f"fabric-sdk-py invoke failed: {exc}")

        return self._anchored(self._extract_tx_id(response), channel=channel, raw=str(response)[:300])

    def chaincode_query(
        self,
        channel: str,
        chaincode: str,
        fcn: str,
        args: list,
        **kwargs: Any,
    ) -> dict:
        try:
            client = self._client()
            user = client.get_user(self.org_name, self.user_name)
            response = client.chaincode_query(
                requestor=user,
                channel_name=channel,
                peers=self.peers or None,
                cc_name=chaincode,
                fcn=fcn,
                args=[str(a) for a in (args or [])],
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "result": None, "transport": self.name,
                    "reason": f"fabric-sdk-py query failed: {exc}"}
        return {"status": "ok", "result": response, "transport": self.name}

    # -- internals -----------------------------------------------------------

    def _client(self) -> Any:
        if self._cached_client is not None:
            return self._cached_client
        from hfc.fabric import Client as FabricClient  # nosec — optional dependency

        if not self.net_profile:
            raise RuntimeError("no network profile configured")
        self._cached_client = FabricClient(net_profile=self.net_profile)
        return self._cached_client

    @staticmethod
    def _extract_tx_id(response: Any) -> str | None:
        """Pull a tx id out of whatever shape the SDK returned.

        The SDK's return type varies by version and by ``wait_for_event``; an
        unrecognised shape yields ``None`` rather than a fabricated id, and the
        caller sees ``tx_id_confirmed: False``.
        """
        if isinstance(response, dict):
            for key in ("tx_id", "txid", "transaction_id"):
                value = response.get(key)
                if value:
                    return str(value)
            return None
        return getattr(response, "tx_id", None) or None
