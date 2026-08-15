#!/usr/bin/env python3

# CUI // SP-CTI
"""Blockchain configuration loader for ICDEV GovChain integration.

Loads args/blockchain_config.yaml and resolves which anchor transport to use.
When no transport is healthy, ledger operations degrade to queueing intent in
govchain_pending_operations — retained, never dropped.

Transports (D-GC-1) live in ``tools/blockchain/transports/`` and are selected by
``tools/blockchain/transport_registry.py`` in priority order. ``HAS_FABRIC`` is
retained for backward compatibility but is NO LONGER the switch: fabric-sdk-py
(``hfc``) is in neither requirements.txt nor pyproject.toml, so it was
permanently False and routed every anchor in the platform to a no-op. A missing
optional dependency is now one unhealthy backend among several.

Usage:
    from tools.blockchain.blockchain_config import get_config

    cfg = get_config()
    client = cfg.get_fabric_client()   # best healthy transport, else no-op
    if cfg.is_enabled():
        ...                            # a real transport is up
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.blockchain.transports.noop import NoOpTransport  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("blockchain.config")

HAS_YAML = False
try:
    import yaml

    HAS_YAML = True
except ImportError:
    pass

#: Retained so existing callers/tests importing it keep working, and so
#: ``--test`` can still report whether the SDK is present. It is NOT consulted
#: when choosing a transport — ``FabricSdkTransport.health()`` is.
#: find_spec rather than an import: this is a presence question, and importing
#: an optional SDK at module scope to answer it makes every importer pay for it.
try:
    from importlib.util import find_spec

    HAS_FABRIC = find_spec("hfc") is not None and find_spec("hfc.fabric") is not None
except Exception:  # noqa: BLE001 — a broken hfc install is "not usable", not fatal
    HAS_FABRIC = False


class NoOpFabricClient(NoOpTransport):
    """Backward-compatible alias for :class:`NoOpTransport`.

    Kept because ``NoOpFabricClient(queue_to_db=...)`` is referenced by existing
    callers and tests. New code should use the transport directly.
    """

    def __init__(self, queue_to_db: bool = True):
        super().__init__(queue_to_db=queue_to_db, healthy=False)


class BlockchainConfig:
    """Parsed blockchain configuration with air-gap support."""

    def __init__(self, raw: dict):
        self.raw = raw or {}
        self._enabled = self._compute_enabled()
        self._registry = None

    def _compute_enabled(self) -> bool:
        # Environment override takes precedence
        env = os.environ.get("ICDEV_BLOCKCHAIN_ENABLED", "").lower()
        if env in ("false", "0", "no", "off"):
            return False
        if env in ("true", "1", "yes", "on"):
            return True

        # Config file settings
        air_gap = self.raw.get("air_gapped", {})
        if air_gap.get("enabled", False):
            return False

        fabric = self.raw.get("fabric", {})
        if not fabric.get("channel_default"):
            return False

        return True

    def is_enabled(self) -> bool:
        """True when the config permits anchoring AND a transport is healthy.

        Previously ``self._enabled and HAS_FABRIC``, which was permanently False
        because ``hfc`` is an undeclared dependency. Now it asks the registry,
        so a ``peer`` binary on PATH is sufficient and no Python SDK is needed.
        """
        return self._enabled and self.active_transport() is not None

    def is_air_gapped(self) -> bool:
        return self.raw.get("air_gapped", {}).get("enabled", False)

    def channel(self) -> str:
        return self.raw.get("fabric", {}).get("channel_default", "govchain-channel")

    def orderer(self) -> str:
        return self.raw.get("fabric", {}).get("orderer_default", "orderer.govchain.example.com:7050")

    def anchor_interval_seconds(self) -> int:
        return self.raw.get("provenance", {}).get("anchor_interval_seconds", 300)

    def anchor_batch_size(self) -> int:
        return self.raw.get("provenance", {}).get("anchor_batch_size", 100)

    def required_confirmations(self) -> int:
        return self.raw.get("verification", {}).get("required_confirmations", 3)

    def max_anchor_age_hours(self) -> int:
        return self.raw.get("verification", {}).get("max_anchor_age_hours", 24)

    def hsm_required(self) -> bool:
        crypto = self.raw.get("crypto", {})
        hsm = crypto.get("hsm", {})
        return hsm.get("required_for_il5_plus", False)

    # -- transports (D-GC-1) -------------------------------------------------

    def transport_registry(self):
        """The registry of anchor transports built from this config."""
        if self._registry is None:
            from tools.blockchain.transport_registry import build_registry_from_config

            self._registry = build_registry_from_config(self.raw)
        return self._registry

    def active_transport(self, force: bool = False):
        """The best healthy transport, or ``None`` when every backend is down.

        ``None`` is a real answer: the caller must queue to
        ``govchain_pending_operations`` rather than drop the anchor.
        """
        if not self._enabled:
            return None
        return self.transport_registry().best_healthy(force=force)

    def transport_health(self, force: bool = True) -> dict:
        """Per-transport health report, for ``--doctor`` and the dashboard."""
        report = self.transport_registry().to_dict(force=force)
        report["config_enabled"] = self._enabled
        report["has_fabric_sdk"] = HAS_FABRIC
        return report

    def get_fabric_client(self):
        """Return the best healthy transport, else a queueing no-op.

        Never returns ``None`` — every caller in ``tools/blockchain/`` treats
        this as a client object, and a ``None`` here would surface as an
        AttributeError inside an ``except Exception`` and vanish.
        """
        transport = self.active_transport()
        if transport is not None:
            return transport
        return NoOpTransport(queue_to_db=self.is_air_gapped(), healthy=False)


_CONFIG: Optional[BlockchainConfig] = None


def get_config(config_path: Optional[Path] = None) -> BlockchainConfig:
    """Load and cache blockchain configuration."""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    path = config_path or BASE_DIR / "args" / "blockchain_config.yaml"
    raw = {}
    if HAS_YAML and path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Failed to load blockchain config: {e}")

    _CONFIG = BlockchainConfig(raw)
    return _CONFIG


def reset_config():
    """Reset cached config and the transport registry (useful for testing)."""
    global _CONFIG
    _CONFIG = None
    try:
        from tools.blockchain.transport_registry import reset_transport_registry

        reset_transport_registry()
    except Exception:  # noqa: BLE001 — reset must never be the thing that fails
        pass


def main():
    parser = argparse.ArgumentParser(description="Blockchain Config Loader")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--test", action="store_true", help="Test config loading")
    parser.add_argument("--doctor", action="store_true", help="Probe every anchor transport")
    args = parser.parse_args()

    if args.test:
        cfg = get_config()
        transport = cfg.active_transport()
        info = {
            "enabled": cfg.is_enabled(),
            "air_gapped": cfg.is_air_gapped(),
            "channel": cfg.channel(),
            "orderer": cfg.orderer(),
            "active_transport": transport.name if transport else None,
            "has_fabric_sdk": HAS_FABRIC,
            "has_yaml": HAS_YAML,
        }
        print(json.dumps(info, indent=2) if args.json else info)
        return

    if args.doctor:
        report = get_config().transport_health()
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
