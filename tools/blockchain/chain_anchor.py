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
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

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

# The citation type whose leaf this module re-derives rather than trusts
# (trust-anchor-02). Named here as a plain constant so the sweep and the verify
# branch cannot disagree; the vocabulary itself lives in
# tools/provenance/citation_types.py.
TRUST_VALIDATION_TYPE = "trust_validation"


class ChainAnchor:
    """Service for anchoring audit/provenance batches to the blockchain."""

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DB_PATH
        self._cfg = None
        self._client = None

    def _ensure_config(self):
        """Resolve config + transport WITHOUT the auto-flush side effect.

        Split out of ``_lazy_init`` so ``flush_pending()`` can initialise
        itself: calling ``_lazy_init`` there would re-enter ``flush_pending``,
        drain the queue in the nested call, and leave the outer call reporting
        ``flushed: 0`` against an empty queue.
        """
        if self._cfg is None and HAS_DEPS:
            self._cfg = get_config()
            self._client = self._cfg.get_fabric_client()

    def _lazy_init(self):
        first_init = self._cfg is None
        self._ensure_config()
        # Auto-flush pending ops when a transport is now reachable
        if first_init and self._cfg is not None and self._cfg.is_enabled():
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
            except Exception as e:
                logger.warning(f"Anchor raised, queuing: {e}")
                return self._queue_operation("anchor_merkle_root", merkle_root, metadata)

            # A transport reports failure by RETURNING it, not by raising.
            # Treating any returned dict as success is how an anchor gets
            # silently dropped — queue it instead.
            if (result or {}).get("status") != "anchored":
                logger.warning(
                    "Anchor not accepted by transport (%s), queuing: %s",
                    (result or {}).get("transport"),
                    (result or {}).get("reason") or (result or {}).get("status"),
                )
                return self._queue_operation("anchor_merkle_root", merkle_root, metadata)

            logger.info(f"Anchored Merkle root {merkle_root[:16]}... tx={result.get('tx_id')}")
            return {
                "status": "anchored",
                "merkle_root": merkle_root,
                "tx_id": result.get("tx_id"),
                "tx_id_confirmed": result.get("tx_id_confirmed", result.get("tx_id") is not None),
                "transport": result.get("transport"),
                "channel": self._cfg.channel(),
            }
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

    @staticmethod
    def _trust_validation_leaf(row) -> tuple:
        """Re-derive a trust_validation row's leaf. Returns ``(leaf, reason)``.

        ``leaf`` is None when the row cannot be verified, and ``reason`` says
        which way it failed. A trust_validation row's ``source_hash`` is
        ``sha256(artifact_hash|findings_hash|delta_chain_hash|approver)`` and
        ``source_doc`` carries those four components, so unlike every other
        citation type the leaf is REPRODUCIBLE — and a reproducible hash that
        nobody reproduces is just a hash. Anchoring a stored value without
        re-deriving it would wrap tamper-evidence around an unverified number,
        which reads as proof and is not.
        """
        try:
            from tools.provenance.trust_validation import recompute_leaf
        except Exception as exc:  # noqa: BLE001
            return None, f"trust_validation module unavailable: {exc}"

        stored = row["source_hash"]
        derived = recompute_leaf(row)
        if derived is None:
            return None, "components missing or unparseable in source_doc"
        if derived != stored:
            return None, "stored leaf does not match its own components"
        return derived, ""

    def anchor_provenance(
        self,
        registry_ids: List[str],
        trust_validations: Optional[List[dict]] = None,
    ) -> dict:
        """Anchor provenance hashes from source_citation_registry.

        Args:
            registry_ids: rows to anchor. A row whose ``citation_type`` is
                ``trust_validation`` has its leaf re-derived from the components
                in ``source_doc`` and is REFUSED if the two disagree — see
                :meth:`_trust_validation_leaf`. Every other type contributes its
                ``source_hash`` opaquely, as before.
            trust_validations: TRUST validation records supplied directly, each
                a dict carrying ``artifact_hash``, ``findings_hash``,
                ``delta_chain_hash`` and ``approver`` (and optionally
                ``registry_id`` to back-fill). Their leaves join the SAME Merkle
                tree as the registry rows — one batch, one root, one chain
                write, which is what lets this ride the existing 30-minute
                govchain reflex instead of needing a schedule of its own.

        Refused rows are reported in ``rejected`` and are never silently dropped
        from a batch that reports success.
        """
        if not HAS_DEPS:
            return {"status": "disabled", "reason": "dependencies missing"}

        registry_ids = list(registry_ids or [])
        trust_validations = list(trust_validations or [])

        conn = self._get_db()
        try:
            rows = []
            if registry_ids:
                placeholders = ",".join(["%s"] * len(registry_ids))
                rows = conn.execute(
                    "SELECT id, source_hash, citation_type, source_doc "
                    f"FROM source_citation_registry WHERE id IN ({placeholders})",
                    tuple(registry_ids),
                ).fetchall()

            leaves: List[str] = []
            anchored_ids: List[str] = []
            rejected: List[dict] = []

            for r in rows:
                if r["citation_type"] == TRUST_VALIDATION_TYPE:
                    leaf, reason = self._trust_validation_leaf(r)
                    if leaf is None:
                        logger.warning(
                            "Refusing to anchor trust_validation %s: %s", r["id"], reason
                        )
                        rejected.append({"registry_id": r["id"], "reason": reason})
                        continue
                else:
                    leaf = r["source_hash"]
                leaves.append(leaf)
                anchored_ids.append(r["id"])

            # Directly-supplied records. Composed through the one recipe, so a
            # caller cannot hand us a leaf we did not derive ourselves.
            direct_registry_ids: List[str] = []
            if trust_validations:
                from tools.provenance.trust_validation import leaf_of
            for record in trust_validations:
                try:
                    leaf = leaf_of(record)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Refusing a supplied trust validation record: %s", exc)
                    rejected.append({
                        "registry_id": record.get("registry_id"),
                        "reason": f"cannot compose leaf: {exc}",
                    })
                    continue
                leaves.append(leaf)
                if record.get("registry_id"):
                    direct_registry_ids.append(record["registry_id"])

            if not leaves:
                return {
                    "status": "empty",
                    "registry_ids": registry_ids,
                    "rejected": rejected,
                }

            from tools.crypto.merkle_tree import MerkleTree

            tree = MerkleTree(leaves)
            merkle_root = tree.root()

            anchored_set = set(anchored_ids)
            metadata = {
                "source": "source_citation_registry",
                "batch_size": len(leaves),
                # Only the types that actually made it into the tree — a refused
                # row must not appear in the metadata submitted to the chain.
                "citation_types": sorted(
                    {r["citation_type"] for r in rows if r["id"] in anchored_set}
                    | ({TRUST_VALIDATION_TYPE} if trust_validations else set())
                ),
                "trust_validations": len(trust_validations),
                "rejected": len(rejected),
            }

            result = self.anchor_merkle_root(merkle_root, metadata)

            if HAS_REGISTRY and result.get("status") in ("anchored", "queued"):
                for reg_id in anchored_ids + direct_registry_ids:
                    try:
                        update_blockchain_anchor(reg_id, merkle_root, result.get("tx_id", "queued"))
                    except Exception:
                        pass

            result["rejected"] = rejected
            result["batch_size"] = len(leaves)
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
            # tx_id is explicit and None: a queued anchor has no transaction,
            # and a caller reading result["tx_id"] must not get a KeyError and
            # fall into an except-branch that discards the anchor.
            return {
                "status": "queued",
                "tx_id": None,
                "operation_type": operation_type,
                "payload_hash": payload_hash,
            }
        except Exception as e:
            logger.warning(f"Queue failed: {e}")
            return {"status": "error", "tx_id": None, "reason": str(e)}

    @staticmethod
    def _mark_pending_operation(conn, op_id, new_status: str, error_message: str = None) -> bool:
        """Update one govchain_pending_operations row's outcome.

        Writes ``submitted_at`` / ``error_message`` — the columns migration 149
        actually creates. The previous statement set ``updated_at``, which
        exists in neither the PostgreSQL nor the SQLite DDL, so every UPDATE
        raised, was swallowed, and the row stayed 'pending' forever: the flush
        reported success while draining nothing.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                "UPDATE govchain_pending_operations "
                "SET status=%s, submitted_at=%s, error_message=%s WHERE id=%s",
                (new_status, now, error_message, op_id),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[FLUSH] could not mark op {op_id} as {new_status}: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return False

    def periodic_anchor(self) -> dict:
        """Background scan for unanchored entries and anchor them.

        This is the ONLY scheduled entry point — ``genesis/reflexes/
        govchain_anchor.py`` shells out to ``--periodic`` every 30 minutes.
        TRUST validation records (trust-anchor-02) need no addition here: they
        are ``source_citation_registry`` rows like any other, so the registry
        sweep below already finds them and :meth:`anchor_provenance` verifies
        their leaves. Adding a second reflex for them would have created one
        more scheduled capability to go quietly inert.

        Returns summary of actions taken. ``trust_validations_rejected`` is
        surfaced rather than buried: a validation whose leaf did not verify is
        the single most important thing this sweep can find, and a summary that
        reported only ``provenance_batches: 1`` would read as complete success.
        """
        if not HAS_DEPS:
            return {"status": "disabled"}

        self._lazy_init()
        summary = {
            "audit_batches": 0,
            "provenance_batches": 0,
            "trust_validations_rejected": 0,
            "queued": 0,
            "errors": 0,
        }

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
                    summary["trust_validations_rejected"] += len(result.get("rejected") or [])
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

        self._ensure_config()
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

                error_message = None
                try:
                    result = self.anchor_merkle_root(payload_hash, metadata)
                    status = (result or {}).get("status")
                    # ONLY 'anchored' drains a row. 'queued' means the anchor
                    # did not reach the chain and _queue_operation just wrote a
                    # NEW pending row — marking this one flushed would both lie
                    # and grow the queue by one on every cycle.
                    if status == "anchored":
                        new_status = "flushed"
                    elif status == "queued":
                        new_status = "pending"  # leave it for the next attempt
                        error_message = "transport unavailable at flush time"
                    else:
                        new_status = "failed"
                        error_message = str((result or {}).get("reason") or status)[:500]
                except Exception as e:
                    logger.warning(f"[FLUSH] op {op_id} failed: {e}")
                    new_status = "failed"
                    error_message = str(e)[:500]

                self._mark_pending_operation(conn, op_id, new_status, error_message)

                if new_status == "flushed":
                    summary["flushed"] += 1
                elif new_status == "failed":
                    summary["failed"] += 1
                else:
                    summary["skipped"] += 1

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
