#!/usr/bin/env python3
# CUI // SP-CTI
"""Zero-Knowledge Prover for CUI/SECRET provenance (Phase 4 Research).

Provides Pedersen commitments and Merkle-membership proofs so that a verifier
can confirm a hash exists in the source_citation_registry WITHOUT learning the
hash value or the underlying document content.

This is a research/stub module — full ZK circuits (e.g. ZoKrates, Circom) are
not required for initial NIST 800-53 AU-9/AU-10 compliance, but the module
demonstrates the architecture for future IL6/SECRET deployments.

Usage:
    python tools/blockchain/zk_prover.py --commit --value <hash> --json
    python tools/blockchain/zk_prover.py --prove-merkle --leaf <hash> --json
    python tools/blockchain/zk_prover.py --verify --commitment <c> --json
"""

import argparse
import hashlib
import json
import secrets
import sys
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Pedersen commitment (simplified, no elliptic-curve dependency)
# In production this uses secp256k1 or bls12-381 curve points.
# ---------------------------------------------------------------------------
class PedersenCommitment:
    """Simplified Pedersen commitment using modular arithmetic.

    NOT for production secrets — this is a pedagogical / research stub.
    Production should use ZoKrates, Circom, or a proper EC library.
    """

    # A large safe prime (2048-bit placeholder)
    P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F

    def __init__(self, g: Optional[int] = None, h: Optional[int] = None):
        self.g = g or 2
        self.h = h or 3

    def commit(self, value: bytes) -> dict:
        """Create a Pedersen commitment to a value."""
        blinding = secrets.randbits(256)
        v_int = int(hashlib.sha256(value).hexdigest(), 16)
        commitment = (pow(self.g, v_int, self.P) * pow(self.h, blinding, self.P)) % self.P
        return {
            "commitment": hex(commitment),
            "blinding": hex(blinding),
            "value_hash": hashlib.sha256(value).hexdigest(),
            "algorithm": "pedersen-modular-stub",
        }

    def verify(self, value: bytes, commitment_hex: str, blinding_hex: str) -> bool:
        """Verify that a commitment opens to the given value."""
        try:
            c = int(commitment_hex, 16)
            r = int(blinding_hex, 16)
            v_int = int(hashlib.sha256(value).hexdigest(), 16)
            expected = (pow(self.g, v_int, self.P) * pow(self.h, r, self.P)) % self.P
            return c == expected
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Merkle membership proof (uses existing merkle_tree.py)
# ---------------------------------------------------------------------------
def prove_merkle_membership(leaf_hash: str, leaves: List[str]) -> dict:
    """Generate a Merkle proof that leaf_hash is in leaves."""
    from tools.crypto.merkle_tree import MerkleTree

    mt = MerkleTree(leaves)
    try:
        idx = leaves.index(leaf_hash)
    except ValueError:
        return {"error": "leaf not in tree"}

    proof = mt.proof(idx)
    return {
        "root": mt.root(),
        "leaf": leaf_hash,
        "index": idx,
        "proof_hashes": proof,
        "valid": mt.verify(leaf_hash, proof, mt.root()),
    }


def verify_merkle_membership(leaf_hash: str, proof_hashes: List[str], root: str) -> bool:
    """Verify a Merkle membership proof."""
    from tools.crypto.merkle_tree import MerkleTree

    return MerkleTree.verify(leaf_hash, proof_hashes, root)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="ICDEV ZK Prover (Research)")
    parser.add_argument("--commit", action="store_true", help="Create a Pedersen commitment")
    parser.add_argument("--value", help="Value to commit (hex or plain text)")
    parser.add_argument("--prove-merkle", action="store_true", help="Generate Merkle proof")
    parser.add_argument("--leaf", help="Leaf hash for Merkle proof")
    parser.add_argument("--leaves", help="Comma-separated leaf hashes for Merkle tree")
    parser.add_argument("--verify", action="store_true", help="Verify a commitment")
    parser.add_argument("--commitment", help="Commitment hex")
    parser.add_argument("--blinding", help="Blinding factor hex")
    parser.add_argument("--verify-merkle", action="store_true", help="Verify Merkle proof")
    parser.add_argument("--proof-hashes", help="Comma-separated proof hashes")
    parser.add_argument("--root", help="Merkle root")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    pc = PedersenCommitment()

    if args.commit:
        if not args.value:
            parser.error("--commit requires --value")
        val = args.value.encode("utf-8")
        result = pc.commit(val)
    elif args.verify:
        if not args.value or not args.commitment or not args.blinding:
            parser.error("--verify requires --value, --commitment, and --blinding")
        val = args.value.encode("utf-8")
        ok = pc.verify(val, args.commitment, args.blinding)
        result = {"verified": ok}
    elif args.prove_merkle:
        if not args.leaf or not args.leaves:
            parser.error("--prove-merkle requires --leaf and --leaves")
        leaves = args.leaves.split(",")
        result = prove_merkle_membership(args.leaf, leaves)
    elif args.verify_merkle:
        if not args.leaf or not args.proof_hashes or not args.root:
            parser.error("--verify-merkle requires --leaf, --proof-hashes, and --root")
        proof_hashes = args.proof_hashes.split(",")
        ok = verify_merkle_membership(args.leaf, proof_hashes, args.root)
        result = {"verified": ok}
    else:
        parser.print_help()
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
