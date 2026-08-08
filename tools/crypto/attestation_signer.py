#!/usr/bin/env python3
# CUI // SP-CTI
"""Attestation signer for SLSA provenance and compliance artifacts.

Wraps tools.crypto.key_manager with artifact-specific canonicalization.
Produces ECDSA-P256 signatures over canonicalized JSON (sorted keys, no whitespace).

Usage:
    from tools.crypto.attestation_signer import sign_artifact, verify_artifact

    sig = sign_artifact({"provenance": {...}}, key_path="/keys/icdev.pem")
    ok = verify_artifact({"provenance": {...}}, sig, key_path="/keys/icdev.pem")
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.crypto.key_manager import sign_payload, verify_payload


def _canonicalize(obj: dict) -> bytes:
    """Canonicalize dict to deterministic JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _artifact_hash(obj: dict) -> str:
    """Return SHA-256 hex digest of canonicalized artifact."""
    return hashlib.sha256(_canonicalize(obj)).hexdigest()


def sign_artifact(artifact: dict, key_path: Optional[str] = None) -> dict:
    """Sign a compliance artifact (SLSA provenance, VEX, etc.).

    Returns:
        {
            "algorithm": "ECDSA-P256-SHA256" | ...,
            "value": base64 signature,
            "public_key_fp": ...,
            "signed_at": ISO-8601,
            "artifact_hash": SHA-256 of canonicalized artifact,
        }
    """
    payload = _canonicalize(artifact)
    sig = sign_payload(payload, key_path=key_path)
    sig["artifact_hash"] = _artifact_hash(artifact)
    return sig


def verify_artifact(
    artifact: dict,
    signature: dict,
    key_path: Optional[str] = None,
    public_key_pem=None,
) -> bool:
    """Verify an artifact signature.

    Args:
        artifact: the artifact dict (must match canonicalization)
        signature: dict from sign_artifact() with 'value', 'public_key_fp', 'algorithm'
        key_path: optional PRIVATE key path override
        public_key_pem: PEM of the public key alone — the path a consumer who
            does not hold the private key must use
    """
    # Integrity check: verify artifact hash matches
    expected_hash = signature.get("artifact_hash")
    if expected_hash and _artifact_hash(artifact) != expected_hash:
        return False

    payload = _canonicalize(artifact)
    return verify_payload(
        payload,
        signature.get("value", ""),
        signature.get("public_key_fp", ""),
        algorithm=signature.get("algorithm"),
        key_path=key_path,
        public_key_pem=public_key_pem,
    )


def main():
    parser = argparse.ArgumentParser(description="Attestation Signer")
    parser.add_argument("--sign", help="JSON artifact to sign")
    parser.add_argument("--verify", help="JSON artifact to verify")
    parser.add_argument("--signature", help="JSON signature block")
    parser.add_argument("--key-path", help="Signing key path override")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.sign:
        artifact = json.loads(args.sign)
        sig = sign_artifact(artifact, key_path=args.key_path)
        print(json.dumps(sig, indent=2) if args.json else sig)
        return

    if args.verify and args.signature:
        artifact = json.loads(args.verify)
        sig = json.loads(args.signature)
        ok = verify_artifact(artifact, sig, key_path=args.key_path)
        print(json.dumps({"verified": ok}) if args.json else ok)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
