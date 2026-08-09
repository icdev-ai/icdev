#!/usr/bin/env python3

# CUI // SP-CTI
"""Chain Anchor Service — periodically anchors Merkle roots to Hyperledger Fabric.

Batches unanchored audit entries and provenance hashes into Merkle trees,
then submits the root hash to the GovChain channel. In air-gap mode,
operations are queued to govchain_pending_operations.

Usage:
    from tools.blockchain.chain_anchor import ChainAnchor

    anchor = ChainAnchor()
    result = anchor.anchor_audit_batch([1, 2, 3, 4, 5])
    anchor.periodic_anchor()  # background scan + anchor
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection

logger = get_logger("blockchain.chain_anchor")

# Optional imports (graceful degradation)
try:
    from tools.crypto.merkle_tree import build_audit_merkle_root
    from tools.blockchain.blockchain_config import get_config

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

try:
    from tools.provenance.registry import update_blockchain_anchor

    HAS_REGISTRY = True
except ImportError:
    HAS_REGISTRY = False

DB_PATH = BASE_DIR / "data" / "icdev.db"


class ChainAnchor:
    """Service for anchoring audit/provenance batches to the blockchain."""

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DB_PATH
        self._cfg = None
        self._client = None

    def _lazy_init(self):
        if self._cfg is None and HAS_DEPS:
            self._cfg = get_config()
            self._client = self._cfg.get_fabric_client()
            # Auto-flush pending ops when Fabric is now reachable
            if self._cfg.is_enabled():
                self.flush_pending()

    def _get_db(self):
        return get_connection(db_path=str(self.db_path))

    def anchor_merkle_root(self, merkle_root: str, metadata: dict) -> dict:
        """Submit a Merkle root hash to the Fabric channel."""
        self._lazy_init()
        if not HAS_DEPS:
            return {"status": "disabled", "reason": "dependencies missing"}

        if self._cfg.is_enabled():
            try:
                result = self._client.chaincode_invoke(
                    channel=self._cfg.channel(),
                    chaincode="AuditContract",
                    fcn="StoreMerkleRoot",
                    args=[merkle_root, json.dumps(metadata)],
                )
                logger.info(f"Anchored Merkle root {merkle_root[:16]}... tx={result.get('tx_id')}")
                return {
                    "status": "anchored",
                    "merkle_root": merkle_root,
                    "tx_id": result.get("tx_id"),
                    "channel": self._cfg.channel(),
                }
            except Exception as e:
                logger.warning(f"Anchor failed, queuing: {e}")
                return self._queue_operation("anchor_merkle_root", merkle_root, metadata)
        else:
            return self._queue_operation("anchor_merkle_root", merkle_root, metadata)

    def anchor_audit_batch(self, audit_ids: List[int]) -> dict:
        """Build a Merkle tree from audit entries and anchor the root."""
        if not HAS_DEPS:
            return {"status": "disabled", "reason": "dependencies missing"}

        conn = self._get_db()
        try:
            # Fetch audit entries
            placeholders = ",".join(["?"] * len(audit_ids))
            rows = conn.execute(
                f"SELECT id, project_id, event_type, actor, action, details, classification, created_at FROM audit_trail WHERE id IN ({placeholders}) ORDER BY id",
                tuple(audit_ids),
            ).fetchall()

            if not rows:
                return {"status": "empty", "audit_ids": audit_ids}

            merkle_root = build_audit_merkle_root([dict(r) for r in rows])
            metadata = {
                "source_table": "audit_trail",
                "batch_size": len(rows),
                "first_id": rows[0]["id"],
                "last_id": rows[-1]["id"],
            }

            result = self.anchor_merkle_root(merkle_root, metadata)

            # Update registry entries with anchor info
            if HAS_REGISTRY and result.get("status") in ("anchored", "queued"):
                for r in rows:
                    try:
                        reg = conn.execute(
                            "SELECT id FROM source_citation_registry WHERE source_table='audit_trail' AND source_record_id=%s",
                            (str(r["id"]),),
                        ).fetchone()
                        if reg:
                            update_blockchain_anchor(reg["id"], merkle_root, result.get("tx_id", "queued"))
                    except Exception:
                        pass

            return result
        finally:
            conn.close()

    def anchor_provenance(self, registry_ids: List[str]) -> dict:
        """Anchor provenance hashes from source_citation_registry."""
        if not HAS_DEPS:
            return {"status": "disabled", "reason": "dependencies missing"}

        conn = self._get_db()
        try:
            placeholders = ",".join(["?"] * len(registry_ids))
            rows = conn.execute(
                f"SELECT id, source_hash, citation_type, source_doc FROM source_citation_registry WHERE id IN ({placeholders})",
                tuple(registry_ids),
            ).fetchall()

            if not rows:
                return {"status": "empty", "registry_ids": registry_ids}

            leaves = [r["source_hash"] for r in rows]
            from tools.crypto.merkle_tree import MerkleTree

            tree = MerkleTree(leaves)
            merkle_root = tree.root()

            metadata = {
                "source": "source_citation_registry",
                "batch_size": len(rows),
                "citation_types": list(set(r["citation_type"] for r in rows)),
            }

            result = self.anchor_merkle_root(merkle_root, metadata)

            if HAS_REGISTRY and result.get("status") in ("anchored", "queued"):
                for r in rows:
                    try:
                        update_blockchain_anchor(r["id"], merkle_root, result.get("tx_id", "queued"))
                    except Exception:
                        pass

            return result
        finally:
            conn.close()

    def _queue_operation(self, operation_type: str, payload_hash: str, metadata: dict) -> dict:
        """Queue an operation for air-gapped or failed submission."""
        try:
            conn = self._get_db()
            conn.execute(
                "INSERT INTO govchain_pending_operations (operation_type, payload_hash, status) VALUES (%s, %s, %s)",
                (f"{operation_type}:{json.dumps(metadata)}", payload_hash, "pending"),
            )
            conn.commit()
            conn.close()
            logger.info(f"[QUEUED] {operation_type} = {payload_hash[:16]}...")
            return {"status": "queued", "operation_type": operation_type, "payload_hash": payload_hash}
        except Exception as e:
            logger.warning(f"Queue failed: {e}")
            return {"status": "error", "reason": str(e)}

    def periodic_anchor(self) -> dict:
        """Background scan for unanchored entries and anchor them.

        Returns summary of actions taken.
        """
        if not HAS_DEPS:
            return {"status": "disabled"}

        self._lazy_init()
        summary = {"audit_batches": 0, "provenance_batches": 0, "queued": 0, "errors": 0}

        conn = self._get_db()
        try:
            # Find unanchored audit entries (no hash or no signature)
            try:
                rows = conn.execute(
                    "SELECT id FROM audit_trail WHERE hash IS NULL OR signature IS NULL ORDER BY id LIMIT 100"
                ).fetchall()
                if rows:
                    ids = [r["id"] for r in rows]
                    result = self.anchor_audit_batch(ids)
                    if result.get("status") in ("anchored", "queued"):
                        summary["audit_batches"] += 1
            except Exception as e:
                logger.warning(f"Audit batch anchor failed: {e}")
                summary["errors"] += 1

            # Find unanchored registry entries (no merkle_root)
            try:
                rows = conn.execute(
                    "SELECT id FROM source_citation_registry WHERE merkle_root IS NULL LIMIT 100"
                ).fetchall()
                if rows:
                    ids = [r["id"] for r in rows]
                    result = self.anchor_provenance(ids)
                    if result.get("status") in ("anchored", "queued"):
                        summary["provenance_batches"] += 1
            except Exception as e:
                logger.warning(f"Provenance batch anchor failed: {e}")
                summary["errors"] += 1

        finally:
            conn.close()

        return summary


    def flush_pending(self) -> dict:
        """Flush queued pending operations to Fabric when peer becomes reachable.

        Reads all rows in govchain_pending_operations WHERE status='pending',
        re-submits each as a Merkle root anchor, and marks them 'flushed' or 'failed'.
        Safe to call repeatedly — idempotent on already-flushed rows.

        Returns:
            dict with flushed, failed, and skipped counts.
        """
        if not HAS_DEPS:
            return {"status": "disabled", "flushed": 0, "failed": 0, "skipped": 0}

        if not self._cfg or not self._cfg.is_enabled():
            return {"status": "fabric_unavailable", "flushed": 0, "failed": 0, "skipped": 0}

        summary = {"status": "ok", "flushed": 0, "failed": 0, "skipped": 0}
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT id, operation_type, payload_hash FROM govchain_pending_operations WHERE status='pending' ORDER BY id LIMIT 200"
            ).fetchall()

            if not rows:
                return summary

            logger.info(f"[FLUSH] {len(rows)} pending ops to submit")

            for row in rows:
                op_id = row["id"]
                payload_hash = row["payload_hash"]

                # operation_type may encode metadata as "op_name:{json}"
                metadata = {"source": "flush", "original_operation": row["operation_type"][:200]}

                try:
                    result = self.anchor_merkle_root(payload_hash, metadata)
                    new_status = "flushed" if result.get("status") in ("anchored", "queued") else "failed"
                except Exception as e:
                    logger.warning(f"[FLUSH] op {op_id} failed: {e}")
                    new_status = "failed"

                try:
                    conn.execute(
                        "UPDATE govchain_pending_operations SET status=%s, updated_at=%s WHERE id=%s",
                        (new_status, __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), op_id),
                    )
                    conn.commit()
                except Exception:
                    pass

                if new_status == "flushed":
                    summary["flushed"] += 1
                elif new_status == "failed":
                    summary["failed"] += 1

            logger.info(f"[FLUSH] done: {summary}")
            return summary
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="Chain Anchor Service")
    parser.add_argument("--anchor-audit", nargs="+", type=int, help="Anchor specific audit IDs")
    parser.add_argument("--anchor-provenance", nargs="+", help="Anchor specific registry IDs")
    parser.add_argument("--periodic", action="store_true", help="Run periodic anchor scan")
    parser.add_argument("--flush-pending", action="store_true", help="Flush queued pending ops to Fabric")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    anchor = ChainAnchor()

    if args.anchor_audit:
        result = anchor.anchor_audit_batch(args.anchor_audit)
        print(json.dumps(result, indent=2) if args.json else result)
        return

    if args.anchor_provenance:
        result = anchor.anchor_provenance(args.anchor_provenance)
        print(json.dumps(result, indent=2) if args.json else result)
        return

    if args.periodic:
        result = anchor.periodic_anchor()
        print(json.dumps(result, indent=2) if args.json else result)
        return

    if getattr(args, "flush_pending", False):
        result = anchor.flush_pending()
        print(json.dumps(result, indent=2) if args.json else result)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
