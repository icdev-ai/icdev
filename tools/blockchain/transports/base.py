#!/usr/bin/env python3
# CUI // SP-CTI
"""Base ABC for GovChain anchor transports (D-GC-1).

Modelled on ``tools/platform_connectors/base.py`` (``PlatformConnector`` ABC +
``priority`` + ``health()``) — the same primitive, because the problem is the
same: several backends can serve one logical channel and the caller wants the
best reachable one without knowing which.

Every transport implements:
    chaincode_invoke(channel, chaincode, fcn, args) -> dict   — write path
    chaincode_query(channel, chaincode, fcn, args)  -> dict   — read path
    health()                                        -> TransportHealth

The two chaincode methods keep the exact signature ``NoOpFabricClient`` already
exposed, so ``ChainAnchor`` and ``channel_manager`` are unchanged callers.

Result contract
---------------
``chaincode_invoke`` returns a dict whose ``status`` is one of:

    ``anchored``  the transport got a commitment from the network. ``tx_id`` may
                  still be ``None`` if the backend could not report one — an
                  anchor without a tx id is weak evidence, so the flag
                  ``tx_id_confirmed`` says which it was.
    ``queued``    the transport did not reach the network and recorded intent.
    ``failed``    the transport reached a decision that the write did not land.

``failed`` and ``queued`` both mean "not on chain". Callers must not treat a
returned dict as success without reading ``status`` — that is how an anchor
gets silently dropped.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# -- health vocabulary -------------------------------------------------------
#: The backend answered and can carry an anchor.
STATUS_OK = "ok"
#: The backend answered but something is off (e.g. binary present, peer slow).
STATUS_DEGRADED = "degraded"
#: The backend is configured but did not answer.
STATUS_UNREACHABLE = "unreachable"
#: The backend is not installed / not configured at all (e.g. hfc absent).
STATUS_UNAVAILABLE = "unavailable"

#: Only these two count as "can carry an anchor right now".
HEALTHY_STATUSES = frozenset({STATUS_OK, STATUS_DEGRADED})

# -- invoke result vocabulary ------------------------------------------------
RESULT_ANCHORED = "anchored"
RESULT_QUEUED = "queued"
RESULT_FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TransportHealth:
    """Health probe result for a single anchor transport."""

    transport_name: str
    backend: str
    status: str
    latency_ms: float | None = None
    message: str = ""
    checked_at: str = field(default_factory=_now)

    @property
    def healthy(self) -> bool:
        return self.status in HEALTHY_STATUSES

    def to_dict(self) -> dict:
        return {
            "transport_name": self.transport_name,
            "backend": self.backend,
            "status": self.status,
            "healthy": self.healthy,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "checked_at": self.checked_at,
        }


class AnchorTransport(ABC):
    """Abstract base for every GovChain anchor transport.

    Subclasses set:
        name     — unique slug, e.g. "peer_cli" or "peer_cli:peer0.gov1:7051"
        backend  — logical family, e.g. "peer_cli" | "fabric_sdk" | "noop"
        priority — lower = preferred (0 = highest priority)
    """

    name: str = "unnamed"
    backend: str = "unknown"
    priority: int = 100

    @abstractmethod
    def chaincode_invoke(
        self,
        channel: str,
        chaincode: str,
        fcn: str,
        args: list,
        **kwargs: Any,
    ) -> dict:
        """Submit a transaction. See module docstring for the result contract."""

    @abstractmethod
    def chaincode_query(
        self,
        channel: str,
        chaincode: str,
        fcn: str,
        args: list,
        **kwargs: Any,
    ) -> dict:
        """Evaluate a read-only chaincode function."""

    @abstractmethod
    def health(self) -> TransportHealth:
        """Probe this backend. Must never raise — report the failure instead."""

    # -- helpers used by subclasses -----------------------------------------

    def _health(
        self,
        status: str,
        message: str = "",
        latency_ms: float | None = None,
    ) -> TransportHealth:
        return TransportHealth(
            transport_name=self.name,
            backend=self.backend,
            status=status,
            latency_ms=latency_ms,
            message=message,
        )

    def _anchored(self, tx_id: str | None, **extra: Any) -> dict:
        result = {
            "status": RESULT_ANCHORED,
            "tx_id": tx_id,
            "tx_id_confirmed": tx_id is not None,
            "transport": self.name,
        }
        result.update(extra)
        return result

    def _queued(self, reason: str = "", **extra: Any) -> dict:
        result = {
            "status": RESULT_QUEUED,
            "tx_id": None,
            "transport": self.name,
            "reason": reason,
        }
        result.update(extra)
        return result

    def _failed(self, reason: str, **extra: Any) -> dict:
        result = {
            "status": RESULT_FAILED,
            "tx_id": None,
            "transport": self.name,
            "reason": reason,
        }
        result.update(extra)
        return result

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"<{type(self).__name__} name={self.name!r} priority={self.priority}>"
