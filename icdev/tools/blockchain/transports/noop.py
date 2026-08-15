#!/usr/bin/env python3
# CUI // SP-CTI
"""No-op anchor transport — the always-present last resort (D-GC-1).

Default posture is **unhealthy**. That is deliberate: a no-op is not a place
anchors go, it is the absence of one. Reporting it unhealthy is what makes the
registry fall through to ``govchain_pending_operations``, which is the correct
behaviour for an air-gapped or Fabric-less deployment — the anchor is retained,
not dropped, and ``ChainAnchor.flush_pending()`` replays it once a real
transport comes up.

Flipping it healthy (``fabric.transports.noop.healthy: true`` or
``ICDEV_BLOCKCHAIN_NOOP_HEALTHY=1``) turns it into an accepting simulation sink
that returns a deterministic synthetic tx id. Use it to exercise the drain path
without a Fabric network. A synthetic tx id is prefixed ``noop-`` and is NOT a
chain commitment — ``provenance_verifier`` must never treat one as proof.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from tools.blockchain.transports.base import (
    STATUS_OK,
    STATUS_UNAVAILABLE,
    AnchorTransport,
    TransportHealth,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("blockchain.transport.noop")

#: Marks a tx id as simulated. Anything reading a tx id for evidence must
#: reject this prefix.
SYNTHETIC_TX_PREFIX = "noop-"

_HEALTHY_ENV = "ICDEV_BLOCKCHAIN_NOOP_HEALTHY"


def _env_healthy() -> bool | None:
    """Read the env override. ``None`` means "not set — use the config value".

    Set, it WINS over ``fabric.transports.noop.healthy``, the same precedence
    ``ICDEV_BLOCKCHAIN_ENABLED`` already has over the YAML. Without that, the
    shipped ``healthy: false`` would make the env var a no-op.
    """
    raw = os.environ.get(_HEALTHY_ENV)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


class NoOpTransport(AnchorTransport):
    """Queue-only transport used when no real Fabric backend is reachable."""

    backend = "noop"

    def __init__(
        self,
        queue_to_db: bool = True,
        healthy: bool | None = None,
        priority: int = 90,
        name: str = "noop",
        queue_table: str = "govchain_pending_operations",
    ) -> None:
        self.queue_to_db = queue_to_db
        # Precedence: env override > explicit/config value > unhealthy default.
        env = _env_healthy()
        if env is not None:
            self._healthy = env
        else:
            self._healthy = False if healthy is None else bool(healthy)
        self.priority = priority
        self.name = name
        self.queue_table = queue_table

    # -- health --------------------------------------------------------------

    def health(self) -> TransportHealth:
        if self._healthy:
            return self._health(
                STATUS_OK,
                "no-op simulation sink is accepting anchors — tx ids are "
                f"synthetic ({SYNTHETIC_TX_PREFIX}...) and are not chain commitments",
            )
        return self._health(
            STATUS_UNAVAILABLE,
            "no-op transport is not a real anchoring backend; anchors are "
            f"queued to {self.queue_table}",
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
        payload_hash = self._payload_hash(args)
        if self._healthy:
            tx_id = SYNTHETIC_TX_PREFIX + hashlib.sha256(
                f"{channel}|{chaincode}|{fcn}|{payload_hash}".encode()
            ).hexdigest()[:32]
            logger.info("[NO-OP:accepted] %s:%s -> %s", chaincode, fcn, tx_id)
            return self._anchored(tx_id, synthetic=True, channel=channel)

        self._queue(f"invoke:{chaincode}:{fcn}", payload_hash)
        return self._queued(reason="no real anchor transport is healthy")

    def chaincode_query(
        self,
        channel: str,
        chaincode: str,
        fcn: str,
        args: list,
        **kwargs: Any,
    ) -> dict:
        # A no-op has no ledger to read, healthy or not. Never fabricate a
        # result — a caller that gets data back would treat it as on-chain.
        return {
            "status": "queued",
            "result": None,
            "transport": self.name,
            "reason": "no-op transport holds no ledger state",
        }

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _payload_hash(args: list) -> str:
        try:
            serialized = json.dumps(list(args), sort_keys=True, default=str)
        except Exception:  # noqa: BLE001 — fall back to repr, never fail an anchor
            serialized = str(args)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _queue(self, operation_type: str, payload_hash: str) -> None:
        """Record intent in the pending-operations queue."""
        if not self.queue_to_db:
            logger.info("[NO-OP] Would anchor: %s = %s", operation_type, payload_hash)
            return
        try:
            from tools.db.storage import get_connection

            conn = get_connection()
            try:
                conn.execute(
                    f"INSERT INTO {self.queue_table} "  # nosec B608 — table name is a config constant, never user input
                    "(operation_type, payload_hash, status) VALUES (%s, %s, %s)",
                    (operation_type, payload_hash, "pending"),
                )
                conn.commit()
            finally:
                conn.close()
            logger.info("[QUEUED] %s = %s", operation_type, payload_hash)
        except Exception as exc:  # noqa: BLE001 — queueing is best-effort here
            logger.warning("Failed to queue blockchain operation: %s", exc)
