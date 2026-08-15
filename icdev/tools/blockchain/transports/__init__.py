# CUI // SP-CTI
"""Pluggable anchor transports for GovChain (D-GC-1).

Every backend that can carry a Merkle root to a Fabric channel implements
:class:`~tools.blockchain.transports.base.AnchorTransport`. The registry in
``tools/blockchain/transport_registry.py`` picks the best HEALTHY one; when
none is healthy the caller queues to ``govchain_pending_operations`` instead
of dropping the anchor.

Nothing in this package imports ``hfc`` at module scope — fabric-sdk-py is an
undeclared ICDEV dependency and must stay optional.
"""

from tools.blockchain.transports.base import (  # noqa: F401
    AnchorTransport,
    TransportHealth,
    HEALTHY_STATUSES,
    STATUS_DEGRADED,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    STATUS_UNREACHABLE,
)

__all__ = [
    "AnchorTransport",
    "TransportHealth",
    "HEALTHY_STATUSES",
    "STATUS_OK",
    "STATUS_DEGRADED",
    "STATUS_UNREACHABLE",
    "STATUS_UNAVAILABLE",
]
