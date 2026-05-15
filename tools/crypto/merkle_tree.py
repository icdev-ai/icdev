#!/usr/bin/env python3
# CUI // SP-CTI
"""Merkle Tree builder for batching audit entries into tamper-evident roots.

Deterministic SHA-256 leaf sorting, Bitcoin-style odd-leaf duplication.
Provides root computation, inclusion proofs, and verification.

Usage:
    from tools.crypto.merkle_tree import MerkleTree, build_audit_merkle_root

    tree = MerkleTree(["leaf1", "leaf2", "leaf3"])
    root = tree.root()
    proof = tree.proof(1)  # proof for leaf2
    assert tree.verify("leaf2", proof, root)
"""

import argparse
import hashlib
import json
from typing import List, Tuple


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _hex(data: bytes) -> str:
    return data.hex()


class MerkleTree:
    """Deterministic Merkle tree using SHA-256."""

    def __init__(self, leaves: List[str], hash_algo: str = "sha256"):
        if hash_algo != "sha256":
            raise ValueError("Only sha256 is supported (FIPS 180-4 compliant)")
        # Deterministic sorting for reproducible roots
        self.leaves = sorted(leaves)
        self._tree: List[List[bytes]] = self._build(self.leaves)

    def _build(self, leaves: List[str]) -> List[List[bytes]]:
        """Build tree bottom-up. Duplicate last leaf if odd count."""
        if not leaves:
            return [[_sha256(b"")]]
        # Hash leaves
        level = [_sha256(leaf.encode("utf-8")) for leaf in leaves]
        if len(level) % 2 == 1:
            level.append(level[-1])
        tree = [level]
        while len(level) > 1:
            next_level = []
            for i in range(0, len(level), 2):
                left, right = level[i], level[i + 1]
                # Sort pair lexicographically for deterministic hashing
                pair = sorted([left, right])
                next_level.append(_sha256(pair[0] + pair[1]))
            if len(next_level) % 2 == 1 and len(next_level) > 1:
                next_level.append(next_level[-1])
            tree.append(next_level)
            level = next_level
        return tree

    def root(self) -> str:
        """Return hex-encoded Merkle root."""
        return _hex(self._tree[-1][0])

    def proof(self, leaf_index: int) -> List[Tuple[str, str]]:
        """Return Merkle proof as list of (sibling_hash, direction).

        direction: 'L' if sibling is on the left, 'R' if on the right.
        """
        if not self.leaves:
            return []
        # Map original leaf to position in sorted leaves
        proof = []
        idx = leaf_index
        # Handle duplication: if odd, duplicated leaf shares its own hash
        for level in self._tree[:-1]:
            if idx % 2 == 0:
                sibling_idx = idx + 1
                if sibling_idx >= len(level):
                    sibling_idx = idx  # duplicated last leaf
                direction = "R"
            else:
                sibling_idx = idx - 1
                direction = "L"
            sibling_hash = _hex(level[sibling_idx])
            proof.append((sibling_hash, direction))
            idx //= 2
        return proof

    def verify(self, leaf: str, proof: List[Tuple[str, str]], root: str) -> bool:
        """Verify a leaf against a root using a proof."""
        current = _sha256(leaf.encode("utf-8"))
        for sibling_hex, direction in proof:
            sibling = bytes.fromhex(sibling_hex)
            if direction == "L":
                pair = sorted([sibling, current])
            else:
                pair = sorted([current, sibling])
            current = _sha256(pair[0] + pair[1])
        return _hex(current) == root


def build_audit_merkle_root(audit_entries: List[dict]) -> str:
    """Build a Merkle root from a list of audit entry dicts.

    Each entry is canonicalized as: id|project_id|event_type|actor|action|details|classification|created_at
    """
    leaves = []
    for entry in audit_entries:
        canonical = "|".join(
            str(entry.get(k, ""))
            for k in ("id", "project_id", "event_type", "actor", "action", "details", "classification", "created_at")
        )
        leaves.append(canonical)
    if not leaves:
        return _hex(_sha256(b""))
    tree = MerkleTree(leaves)
    return tree.root()


def main():
    parser = argparse.ArgumentParser(description="Merkle tree operations")
    parser.add_argument("--leaves", nargs="+", help="Leaf strings")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--verify", help="Leaf to verify")
    parser.add_argument("--root", help="Expected root for verification")
    parser.add_argument("--proof", help="JSON proof array [(hash, dir), ...]")
    args = parser.parse_args()

    if args.verify and args.root and args.proof:
        tree = MerkleTree([])  # dummy for method access
        ok = tree.verify(args.verify, json.loads(args.proof), args.root)
        print(json.dumps({"verified": ok}) if args.json else ok)
        return

    if args.leaves:
        tree = MerkleTree(args.leaves)
        root = tree.root()
        proofs = {leaf: tree.proof(i) for i, leaf in enumerate(tree.leaves)}
        if args.json:
            print(json.dumps({"root": root, "leaves": tree.leaves, "proofs": proofs}, indent=2))
        else:
            print(f"Root: {root}")
            for i, leaf in enumerate(tree.leaves):
                print(f"  Leaf {i}: {leaf[:60]}...")
                print(f"    Proof: {tree.proof(i)}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
