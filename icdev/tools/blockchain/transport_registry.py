#!/usr/bin/env python3
# CUI // SP-CTI
"""Anchor transport registry — picks the best HEALTHY GovChain backend (D-GC-1).

The shape is ``tools/platform_connectors/connector_registry.py``: adapters are
registered with a ``priority``, probed with ``health()``, and tried in ascending
priority order. That primitive is exactly what peer failover needs — register
one :class:`PeerCliTransport` per peer endpoint and the registry moves to the
next one when the first is unreachable.

What this replaces
------------------
``blockchain_config.get_fabric_client()`` used to branch on ``HAS_FABRIC``, a
module constant set by ``import hfc``. Since ``hfc`` is in neither
``requirements.txt`` nor ``pyproject.toml``, it was permanently ``False`` and
every anchor in the platform reached ``NoOpFabricClient``. A missing optional
dependency is now one unhealthy backend among several rather than a global
kill switch.

Health results are cached for ``ttl_seconds`` (default 60) because
``is_enabled()`` is on the dashboard's render path and a health probe can spawn
a subprocess. ``reset_health_cache()`` clears it; ``best_healthy(force=True)``
bypasses it for one call.

Usage:
    from tools.blockchain.transport_registry import build_registry_from_config

    registry = build_registry_from_config(raw_yaml)
    transport = registry.best_healthy()      # None => queue, do not drop
    report = registry.doctor()               # every backend's health
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.blockchain.transports.base import (  # noqa: E402
    AnchorTransport,
    TransportHealth,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("blockchain.transport_registry")

DEFAULT_HEALTH_TTL_SECONDS = 60

#: Default priorities when the config does not name one. Lower = preferred.
#: The SDK first when it is genuinely installed, the CLI next (the transport a
#: real Fabric deployment always has), the no-op last.
_DEFAULT_PRIORITIES = {"fabric_sdk": 10, "peer_cli": 20, "noop": 90}


class AnchorTransportRegistry:
    """Holds anchor transports in priority order and routes to the healthy one."""

    def __init__(self, ttl_seconds: int = DEFAULT_HEALTH_TTL_SECONDS) -> None:
        self._transports: list[AnchorTransport] = []
        self._ttl = max(0, int(ttl_seconds))
        self._health_cache: dict[str, tuple[float, TransportHealth]] = {}
        self._lock = threading.Lock()

    # -- registration --------------------------------------------------------

    def register(self, transport: AnchorTransport) -> None:
        if any(t.name == transport.name for t in self._transports):
            return
        self._transports.append(transport)
        self._transports.sort(key=lambda t: getattr(t, "priority", 100))
        logger.debug(
            "blockchain: registered transport %s (backend=%s, priority=%s)",
            transport.name, transport.backend, getattr(transport, "priority", 100),
        )

    def transports(self) -> list[AnchorTransport]:
        return list(self._transports)

    def get(self, name: str) -> Optional[AnchorTransport]:
        return next((t for t in self._transports if t.name == name), None)

    # -- health --------------------------------------------------------------

    def health_of(self, transport: AnchorTransport, force: bool = False) -> TransportHealth:
        """Probe one transport, honouring the TTL cache. Never raises."""
        now = time.monotonic()
        if not force and self._ttl:
            cached = self._health_cache.get(transport.name)
            if cached and (now - cached[0]) < self._ttl:
                return cached[1]

        try:
            result = transport.health()
        except Exception as exc:  # noqa: BLE001 — a raising probe is unhealthy, not fatal
            logger.warning("blockchain: health probe %s raised: %s", transport.name, exc)
            result = TransportHealth(
                transport_name=transport.name,
                backend=transport.backend,
                status="unreachable",
                message=f"health probe raised: {exc}",
            )

        with self._lock:
            self._health_cache[transport.name] = (now, result)
        return result

    def reset_health_cache(self) -> None:
        with self._lock:
            self._health_cache.clear()

    def doctor(self, force: bool = True) -> list[TransportHealth]:
        """Probe every registered transport, in priority order."""
        return [self.health_of(t, force=force) for t in self._transports]

    # -- routing -------------------------------------------------------------

    def best_healthy(self, force: bool = False) -> Optional[AnchorTransport]:
        """Return the highest-priority healthy transport, or ``None``.

        ``None`` is a real answer, not an error: it means every backend is
        down and the caller must queue to ``govchain_pending_operations``.
        """
        for transport in self._transports:
            if self.health_of(transport, force=force).healthy:
                return transport
        return None

    def to_dict(self, force: bool = True) -> dict:
        healths = self.doctor(force=force)
        active = next((h for h in healths if h.healthy), None)
        return {
            "transports": [h.to_dict() for h in healths],
            "active_transport": active.transport_name if active else None,
            "any_healthy": active is not None,
            "count": len(self._transports),
        }


# ---------------------------------------------------------------------------
# Construction from args/blockchain_config.yaml
# ---------------------------------------------------------------------------


def _transport_cfg(raw: dict, key: str) -> dict:
    return ((raw.get("fabric") or {}).get("transports") or {}).get(key) or {}


def _enabled(cfg: dict, default: bool = True) -> bool:
    return bool(cfg.get("enabled", default))


def _priority(cfg: dict, key: str) -> int:
    return int(cfg.get("priority", _DEFAULT_PRIORITIES.get(key, 100)))


def build_registry_from_config(raw: dict) -> AnchorTransportRegistry:
    """Build a registry from a parsed ``blockchain_config.yaml`` mapping.

    One unimportable or unconstructable transport must not take the registry
    down with it — that failure shape is exactly what this module exists to
    fix. The no-op is always registered last so ``best_healthy()`` can still
    return something once an operator flips it healthy.
    """
    fabric = raw.get("fabric") or {}
    registry = AnchorTransportRegistry(
        ttl_seconds=int(fabric.get("transport_health_ttl_seconds", DEFAULT_HEALTH_TTL_SECONDS))
    )

    # -- fabric-sdk-py (optional, never required) ---------------------------
    sdk_cfg = _transport_cfg(raw, "fabric_sdk")
    if _enabled(sdk_cfg):
        try:
            from tools.blockchain.transports.fabric_sdk import FabricSdkTransport

            registry.register(FabricSdkTransport(
                net_profile=sdk_cfg.get("net_profile"),
                org_name=sdk_cfg.get("org_name"),
                user_name=sdk_cfg.get("user_name"),
                peers=sdk_cfg.get("peers"),
                priority=_priority(sdk_cfg, "fabric_sdk"),
                timeout_seconds=int(fabric.get("cli_timeout_seconds", 60)),
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("blockchain: fabric_sdk transport unavailable: %s", exc)

    # -- peer CLI (one transport per endpoint => failover) ------------------
    cli_cfg = _transport_cfg(raw, "peer_cli")
    if _enabled(cli_cfg):
        try:
            from tools.blockchain.transports.peer_cli import PeerCliTransport

            base_priority = _priority(cli_cfg, "peer_cli")
            tls = dict(fabric.get("tls") or {})
            tls.update(cli_cfg.get("tls") or {})
            endpoints = list(cli_cfg.get("peers") or [])
            if not endpoints:
                # No explicit endpoints: one transport using whatever the
                # ambient CORE_PEER_* environment already points at.
                endpoints = [None]

            for offset, endpoint in enumerate(endpoints):
                address = endpoint.get("address") if isinstance(endpoint, dict) else endpoint
                entry = endpoint if isinstance(endpoint, dict) else {}
                registry.register(PeerCliTransport(
                    cli_path=cli_cfg.get("cli_path") or fabric.get("cli_path") or "peer",
                    timeout_seconds=int(
                        cli_cfg.get("cli_timeout_seconds")
                        or fabric.get("cli_timeout_seconds", 60)
                    ),
                    peer_address=address,
                    orderer=entry.get("orderer") or cli_cfg.get("orderer") or fabric.get("orderer_default"),
                    tls=tls,
                    # Each endpoint gets its own slot so failover is ordered.
                    priority=int(entry.get("priority", base_priority + offset)),
                    env=entry.get("env") or cli_cfg.get("env"),
                ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("blockchain: peer_cli transport unavailable: %s", exc)

    # -- no-op (always last) ------------------------------------------------
    noop_cfg = _transport_cfg(raw, "noop")
    if _enabled(noop_cfg):
        try:
            from tools.blockchain.transports.noop import NoOpTransport

            air_gapped = bool((raw.get("air_gapped") or {}).get("enabled", False))
            registry.register(NoOpTransport(
                # Queue to the DB whenever the deployment says it is air-gapped;
                # otherwise the ChainAnchor caller owns the queue write.
                queue_to_db=bool(noop_cfg.get("queue_to_db", air_gapped)),
                healthy=noop_cfg.get("healthy"),
                priority=_priority(noop_cfg, "noop"),
                queue_table=(raw.get("air_gapped") or {}).get(
                    "queue_table", "govchain_pending_operations"
                ),
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning("blockchain: noop transport unavailable: %s", exc)

    return registry


_REGISTRY: Optional[AnchorTransportRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_transport_registry(raw: Optional[dict] = None) -> AnchorTransportRegistry:
    """Return the process-wide registry, building it on first use."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                if raw is None:
                    from tools.blockchain.blockchain_config import get_config

                    raw = get_config().raw
                _REGISTRY = build_registry_from_config(raw or {})
    return _REGISTRY


def reset_transport_registry() -> None:
    """Drop the cached registry (tests, and after a config reload)."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None


def main() -> None:
    parser = argparse.ArgumentParser(description="GovChain Anchor Transport Registry")
    parser.add_argument("--doctor", action="store_true", help="Probe every transport")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.doctor:
        report = get_transport_registry().to_dict()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Active transport: {report['active_transport'] or '(none — anchors queue)'}")
            for entry in report["transports"]:
                mark = "OK " if entry["healthy"] else "-- "
                print(f"  {mark}{entry['transport_name']:<28} {entry['status']:<12} {entry['message']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
