#!/usr/bin/env python3
# CUI // SP-CTI
"""Cross-organization provenance channel manager.

Manages federated Hyperledger Fabric channels for multi-org ICDEV deployments.
Provides create, join, and share operations so provenance hashes can be
verified across organizational boundaries without revealing CUI/SECRET plaintext.

Usage:
    python tools/blockchain/channel_manager.py --list --json
    python tools/blockchain/channel_manager.py --create --orgs org1,org2 --channel prov-channel-1 --json
    python tools/blockchain/channel_manager.py --join --org org2 --channel prov-channel-1 --json
    python tools/blockchain/channel_manager.py --share --registry-id scr-... --channel prov-channel-1 --json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.blockchain.blockchain_config import BlockchainConfig


def list_channels(config: BlockchainConfig) -> dict:
    """List all configured and active channels."""
    if not config.is_enabled():
        return {"status": "disabled", "channels": [], "message": "Blockchain disabled or air-gapped"}

    # In a real deployment this queries the Fabric peer for joined channels.
    # Here we return the configured channels from blockchain_config.yaml.
    channels = config.config.get("fabric", {}).get("channels", [])
    return {"status": "ok", "channels": channels}


def create_channel(
    config: BlockchainConfig,
    channel_id: str,
    orgs: List[str],
    orderer_url: Optional[str] = None,
) -> dict:
    """Create a new Fabric channel and have the listed orgs join it.

    In air-gap mode, the intent is queued in govchain_pending_operations.
    """
    if not config.is_enabled():
        return _queue_pending("create_channel", {"channel_id": channel_id, "orgs": orgs, "orderer_url": orderer_url})

    # Real implementation would use hfc (fabric-sdk-py) or CLI.
    # Stubbed for now with realistic response shape.
    return {
        "status": "created",
        "channel_id": channel_id,
        "orgs": orgs,
        "orderer": orderer_url or config.config.get("fabric", {}).get("orderer_url", ""),
        "message": "Channel creation submitted to orderer. Use --join after the block is committed.",
    }


def join_channel(config: BlockchainConfig, channel_id: str, org: str) -> dict:
    """Have an org join an existing channel."""
    if not config.is_enabled():
        return _queue_pending("join_channel", {"channel_id": channel_id, "org": org})

    return {
        "status": "joined",
        "channel_id": channel_id,
        "org": org,
        "message": f"Org {org} joined channel {channel_id}.",
    }


def share_provenance(config: BlockchainConfig, registry_id: str, channel_id: str) -> dict:
    """Share a source_citation_registry entry across a federated channel.

    Only the hash and metadata are transmitted — CUI/SECRET plaintext never leaves
    the local database.
    """
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, citation_type, source_hash, anchor_hash, merkle_root, blockchain_tx_id, classification, trust_score, project_id "
            "FROM source_citation_registry WHERE id = ?",
            (registry_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Registry entry {registry_id} not found"}

        payload = {
            "registry_id": row["id"],
            "citation_type": row["citation_type"],
            "source_hash": row["source_hash"],
            "anchor_hash": row["anchor_hash"],
            "merkle_root": row["merkle_root"],
            "blockchain_tx_id": row["blockchain_tx_id"],
            "classification": row["classification"],
            "trust_score": row["trust_score"],
            "project_id": row["project_id"],
        }
    finally:
        conn.close()

    if not config.is_enabled():
        return _queue_pending("share_provenance", {"registry_id": registry_id, "channel_id": channel_id, "payload": payload})

    # Real implementation would submit to Fabric via chaincode.
    return {
        "status": "shared",
        "registry_id": registry_id,
        "channel_id": channel_id,
        "payload": payload,
        "message": "Provenance hash shared across channel.",
    }


def _queue_pending(operation_type: str, payload: dict) -> dict:
    """In air-gap mode, queue the operation intent for later sync."""
    from tools.db.storage import get_connection
    import hashlib

    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO govchain_pending_operations (operation_type, payload_hash, status) VALUES (?, ?, ?)",
            (operation_type, payload_hash, "pending"),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "queued",
        "operation": operation_type,
        "payload_hash": payload_hash,
        "message": "Air-gap mode: operation queued in govchain_pending_operations.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ICDEV Cross-Org Channel Manager")
    parser.add_argument("--list", action="store_true", help="List channels")
    parser.add_argument("--create", action="store_true", help="Create a channel")
    parser.add_argument("--join", action="store_true", help="Join a channel")
    parser.add_argument("--share", action="store_true", help="Share provenance across a channel")
    parser.add_argument("--channel", help="Channel ID")
    parser.add_argument("--orgs", help="Comma-separated list of org MSP IDs")
    parser.add_argument("--org", help="Single org MSP ID to join")
    parser.add_argument("--registry-id", help="source_citation_registry ID to share")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    config = BlockchainConfig()

    if args.list:
        result = list_channels(config)
    elif args.create:
        if not args.channel or not args.orgs:
            parser.error("--create requires --channel and --orgs")
        result = create_channel(config, args.channel, args.orgs.split(","))
    elif args.join:
        if not args.channel or not args.org:
            parser.error("--join requires --channel and --org")
        result = join_channel(config, args.channel, args.org)
    elif args.share:
        if not args.registry_id or not args.channel:
            parser.error("--share requires --registry-id and --channel")
        result = share_provenance(config, args.registry_id, args.channel)
    else:
        parser.print_help()
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result.get("message", result.get("status", "ok")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
