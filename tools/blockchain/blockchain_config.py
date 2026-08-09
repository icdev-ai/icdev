#!/usr/bin/env python3

# CUI // SP-CTI
"""Blockchain configuration loader for ICDEV GovChain integration.

Loads args/blockchain_config.yaml and provides air-gap graceful degradation.
When blockchain is disabled, all ledger operations are no-ops that queue
intent to govchain_pending_operations.

Usage:
    from tools.blockchain.blockchain_config import get_config

    cfg = get_config()
    if cfg.is_enabled():
        client = cfg.get_fabric_client()
    else:
        print("Blockchain disabled — operating in air-gap mode")
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("blockchain.config")

HAS_YAML = False
try:
    import yaml

    HAS_YAML = True
except ImportError:
    pass

HAS_FABRIC = False
try:
    from hfc.fabric import Client as FabricClient

    HAS_FABRIC = True
except ImportError:
    pass


class NoOpFabricClient:
    """Mock Fabric client for air-gapped or disabled mode."""

    def __init__(self, queue_to_db: bool = True):
        self.queue_to_db = queue_to_db

    def _queue(self, operation_type: str, payload_hash: str):
        """Log intent to govchain_pending_operations."""
        if not self.queue_to_db:
            logger.info(f"[NO-OP] Would anchor: {operation_type} = {payload_hash}")
            return
        try:
            from tools.db.storage import get_connection

            conn = get_connection()
            conn.execute(
                "INSERT INTO govchain_pending_operations (operation_type, payload_hash, status) VALUES (%s, %s, %s)",
                (operation_type, payload_hash, "pending"),
            )
            conn.commit()
            conn.close()
            logger.info(f"[QUEUED] {operation_type} = {payload_hash}")
        except Exception as e:
            logger.warning(f"Failed to queue blockchain operation: {e}")

    def chaincode_invoke(self, channel: str, chaincode: str, fcn: str, args: list, cc_pattern: str = None):
        self._queue(f"invoke:{chaincode}:{fcn}", hashlib.sha256(str(args).encode()).hexdigest())
        return {"status": "queued", "tx_id": None}

    def chaincode_query(self, channel: str, chaincode: str, fcn: str, args: list):
        return {"status": "queued", "result": None}


class BlockchainConfig:
    """Parsed blockchain configuration with air-gap support."""

    def __init__(self, raw: dict):
        self.raw = raw or {}
        self._enabled = self._compute_enabled()

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
        return self._enabled and HAS_FABRIC

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

    def get_fabric_client(self):
        if self.is_enabled():
            # Real Fabric client instantiation
            try:
                return FabricClient()
            except Exception as e:
                logger.warning(f"Fabric client failed: {e}, falling back to no-op")
                return NoOpFabricClient()
        return NoOpFabricClient(queue_to_db=self.is_air_gapped())


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
    """Reset cached config (useful for testing)."""
    global _CONFIG
    _CONFIG = None


def main():
    parser = argparse.ArgumentParser(description="Blockchain Config Loader")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--test", action="store_true", help="Test config loading")
    args = parser.parse_args()

    if args.test:
        cfg = get_config()
        info = {
            "enabled": cfg.is_enabled(),
            "air_gapped": cfg.is_air_gapped(),
            "channel": cfg.channel(),
            "orderer": cfg.orderer(),
            "has_fabric_sdk": HAS_FABRIC,
            "has_yaml": HAS_YAML,
        }
        print(json.dumps(info, indent=2) if args.json else info)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
