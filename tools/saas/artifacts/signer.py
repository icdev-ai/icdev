#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV SaaS Phase 5 -- Artifact Signer.

CUI // SP-CTI

SHA-256 hash and optional RSA digital signature for compliance artifacts.
Provides tamper-evidence for delivered SSP, POAM, SBOM, OSCAL documents
so recipients can verify integrity and authenticity.

Hash-only mode is always available (stdlib).  RSA signing requires the
``cryptography`` library -- if missing, sign_artifact() and
verify_signature() raise RuntimeError with install instructions.

Usage (library):
    from tools.saas.artifacts.signer import hash_artifact, sign_artifact

    info = hash_artifact(".tmp/ssp.json")
    sig  = sign_artifact(".tmp/ssp.json", private_key_path="/keys/icdev.pem")
    ok   = verify_signature(".tmp/ssp.json", sig["signature"], "/keys/icdev.pub")

Usage (CLI):
    python tools/saas/artifacts/signer.py --hash .tmp/ssp.json
    python tools/saas/artifacts/signer.py --sign .tmp/ssp.json --key /keys/icdev.pem
    python tools/saas/artifacts/signer.py --verify .tmp/ssp.json \\
        --signature <base64> --pubkey /keys/icdev.pub
"""

import argparse
import base64
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("saas.artifacts.signer")

# ---------------------------------------------------------------------------
# Optional dependency: cryptography (for RSA signing)
# ---------------------------------------------------------------------------
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logger.debug(
        "cryptography library not available -- RSA signing disabled. "
        "Hash-only mode remains functional."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(file_path: str) -> str:
    """Compute SHA-256 hex digest of a file, reading in 64 KB chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ============================================================================
# Public API
# ============================================================================

def hash_artifact(file_path: str) -> dict:
    """Compute SHA-256 hash of an artifact file.

    Args:
        file_path: Absolute or relative path to the artifact.

    Returns:
        dict with keys: sha256, size_bytes, hashed_at.

    Raises:
        FileNotFoundError: If file_path does not exist.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(
            "Artifact file not found: {}".format(file_path))

    digest = _sha256_file(str(p))
    size = p.stat().st_size

    return {
        "sha256": digest,
        "size_bytes": size,
        "hashed_at": _utcnow(),
    }


def sign_artifact(file_path: str, private_key_path: str = None) -> dict:
    """Hash and optionally RSA-sign an artifact file.

    If ``private_key_path`` is None or the cryptography library is
    unavailable, returns hash-only (signature will be None).

    Args:
        file_path:        Path to the artifact file.
        private_key_path: Path to PEM-encoded RSA private key (optional).

    Returns:
        dict with keys: sha256, signature (base64 or None),
        algorithm, size_bytes, signed_at.

    Raises:
        FileNotFoundError: If file_path does not exist.
        RuntimeError:      If private_key_path is given but cryptography
                           is not installed.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(
            "Artifact file not found: {}".format(file_path))

    digest = _sha256_file(str(p))
    size = p.stat().st_size

    if private_key_path is None:
        return {
            "sha256": digest,
            "signature": None,
            "algorithm": "SHA-256",
            "size_bytes": size,
            "signed_at": _utcnow(),
        }

    if not HAS_CRYPTO:
        raise RuntimeError(
            "cryptography library is required for RSA signing. "
            "Install with: pip install cryptography")

    key_path = Path(private_key_path)
    if not key_path.exists():
        raise FileNotFoundError(
            "Private key not found: {}".format(private_key_path))

    with open(str(key_path), "rb") as kf:
        private_key = serialization.load_pem_private_key(
            kf.read(), password=None)

    # Read file content for signing
    with open(str(p), "rb") as f:
        file_data = f.read()

    signature_bytes = private_key.sign(
        file_data,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signature_b64 = base64.b64encode(signature_bytes).decode("ascii")

    return {
        "sha256": digest,
        "signature": signature_b64,
        "algorithm": "RSA-SHA256",
        "size_bytes": size,
        "signed_at": _utcnow(),
    }


def verify_signature(file_path: str, signature_b64: str,
                     public_key_path: str) -> bool:
    """Verify an RSA-SHA256 signature against a file.

    Args:
        file_path:       Path to the artifact file.
        signature_b64:   Base64-encoded RSA signature.
        public_key_path: Path to PEM-encoded RSA public key.

    Returns:
        True if signature is valid, False otherwise.

    Raises:
        RuntimeError:      If cryptography library is not installed.
        FileNotFoundError: If file_path or public_key_path does not exist.
    """
    if not HAS_CRYPTO:
        raise RuntimeError(
            "cryptography library is required for signature verification. "
            "Install with: pip install cryptography")

    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(
            "Artifact file not found: {}".format(file_path))

    pub_path = Path(public_key_path)
    if not pub_path.exists():
        raise FileNotFoundError(
            "Public key not found: {}".format(public_key_path))

    with open(str(pub_path), "rb") as kf:
        public_key = serialization.load_pem_public_key(kf.read())

    with open(str(p), "rb") as f:
        file_data = f.read()

    signature_bytes = base64.b64decode(signature_b64)

    try:
        public_key.verify(
            signature_bytes,
            file_data,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


# ============================================================================
# CLI
# ============================================================================

def main():
    """CLI entry point for artifact hashing and signing."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI -- ICDEV Artifact Signer",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--hash", dest="hash_file", type=str,
                        help="Compute SHA-256 hash of a file")
    action.add_argument("--sign", dest="sign_file", type=str,
                        help="Hash and sign a file")
    action.add_argument("--verify", dest="verify_file", type=str,
                        help="Verify a signature against a file")

    parser.add_argument("--key", type=str,
                        help="Path to RSA private key (for --sign)")
    parser.add_argument("--signature", type=str,
                        help="Base64 signature string (for --verify)")
    parser.add_argument("--pubkey", type=str,
                        help="Path to RSA public key (for --verify)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output as JSON")

    args = parser.parse_args()

    try:
        if args.hash_file:
            result = hash_artifact(args.hash_file)
            if args.as_json:
                print(json.dumps(result, indent=2))
            else:
                print("SHA-256:    {}".format(result["sha256"]))
                print("Size:       {} bytes".format(result["size_bytes"]))
                print("Hashed at:  {}".format(result["hashed_at"]))

        elif args.sign_file:
            result = sign_artifact(args.sign_file, private_key_path=args.key)
            if args.as_json:
                print(json.dumps(result, indent=2))
            else:
                print("SHA-256:    {}".format(result["sha256"]))
                print("Algorithm:  {}".format(result["algorithm"]))
                print("Size:       {} bytes".format(result["size_bytes"]))
                if result["signature"]:
                    print("Signature:  {}".format(result["signature"]))
                else:
                    print("Signature:  (none -- hash only)")
                print("Signed at:  {}".format(result["signed_at"]))

        elif args.verify_file:
            if not args.signature or not args.pubkey:
                parser.error(
                    "--verify requires --signature and --pubkey")
            valid = verify_signature(
                args.verify_file, args.signature, args.pubkey)
            if args.as_json:
                print(json.dumps({"valid": valid}))
            else:
                print("Valid: {}".format(valid))
            if not valid:
                sys.exit(1)

    except (FileNotFoundError, RuntimeError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print("FATAL: {}".format(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
