#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Cryptographic key manager for audit signing and attestation.

Supports ECDSA-P256 (FIPS 186-5 primary), Ed25519 (secondary),
and HMAC-SHA256 fallback for dev/air-gapped environments.

Key loading:
  1. ICDEV_AUDIT_SIGNING_KEY_PATH  → PEM private key file
  2. ICDEV_AUDIT_HMAC_SECRET      → HMAC fallback (dev/air-gap)
  3. None                           → warning, no signing (audit logging continues)

HSM integration via args/blockchain_config.yaml crypto.hsm settings.

Usage:
    from tools.crypto.key_manager import sign_payload, verify_payload

    sig = sign_payload(b"hello world")
    ok = verify_payload(b"hello world", sig["value"], sig["public_key_fp"])
"""

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = get_logger("crypto.key_manager")

# Optional cryptography library (required for ECDSA/Ed25519)
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519
    from cryptography.hazmat.backends import default_backend

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logger.debug("cryptography library not available — ECDSA/Ed25519 disabled. HMAC fallback only.")

# HSM integration (optional)
try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ENV_KEY_PATH = "ICDEV_AUDIT_SIGNING_KEY_PATH"
ENV_HMAC_SECRET = "ICDEV_AUDIT_HMAC_SECRET"
GENESIS_HASH = "0" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_key_config() -> dict:
    """Load HSM/crypto config from args/blockchain_config.yaml if present."""
    config_path = BASE_DIR / "args" / "blockchain_config.yaml"
    if not HAS_YAML or not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f).get("crypto", {})
    except Exception:
        return {}


def _load_private_key(path: str) -> Optional[object]:
    """Load a PEM private key (ECDSA or Ed25519 or RSA)."""
    if not HAS_CRYPTO:
        return None
    key_path = Path(path)
    if not key_path.exists():
        return None
    try:
        pem = key_path.read_bytes()
        # Try ECDSA first (our primary)
        try:
            key = serialization.load_pem_private_key(pem, password=None, backend=default_backend())
            if isinstance(key, (ec.EllipticCurvePrivateKey, ed25519.Ed25519PrivateKey)):
                return key
        except Exception:
            pass
        # Try unencrypted PKCS#8 / PKCS#1
        key = serialization.load_pem_private_key(pem, password=None, backend=default_backend())
        return key
    except Exception as e:
        logger.warning(f"Failed to load private key from {path}: {e}")
        return None


def _get_public_key_fp(private_key) -> str:
    """Return SHA-256 fingerprint of the public key PEM."""
    if not HAS_CRYPTO or private_key is None:
        return ""
    try:
        pub = private_key.public_key()
        pub_pem = pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return hashlib.sha256(pub_pem).hexdigest()
    except Exception:
        return ""


def _hmac_sign(payload: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), payload, hashlib.sha256).digest()).decode()


def _hmac_verify(payload: bytes, signature_b64: str, secret: str) -> bool:
    expected = _hmac_sign(payload, secret)
    return hmac.compare_digest(expected, signature_b64)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def sign_payload(payload: bytes, key_path: Optional[str] = None) -> dict:
    """Sign a payload and return a signature dict.

    Returns:
        {
            "algorithm": "ECDSA-P256-SHA256" | "Ed25519" | "HMAC-SHA256",
            "value": base64-encoded signature,
            "public_key_fp": sha256-of-public-key-pem (empty for HMAC),
            "signed_at": ISO-8601 timestamp,
        }
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    # 1. Try ECDSA/Ed25519 from key path or env
    path = key_path or os.environ.get(ENV_KEY_PATH)
    if path and HAS_CRYPTO:
        priv = _load_private_key(path)
        if priv:
            try:
                if isinstance(priv, ec.EllipticCurvePrivateKey):
                    sig = priv.sign(payload, ec.ECDSA(hashes.SHA256()))
                    return {
                        "algorithm": "ECDSA-P256-SHA256",
                        "value": base64.b64encode(sig).decode(),
                        "public_key_fp": _get_public_key_fp(priv),
                        "signed_at": now,
                    }
                elif isinstance(priv, ed25519.Ed25519PrivateKey):
                    sig = priv.sign(payload)
                    return {
                        "algorithm": "Ed25519",
                        "value": base64.b64encode(sig).decode(),
                        "public_key_fp": _get_public_key_fp(priv),
                        "signed_at": now,
                    }
            except Exception as e:
                logger.warning(f"Asymmetric signing failed: {e}, falling back to HMAC")

    # 2. HMAC fallback
    secret = os.environ.get(ENV_HMAC_SECRET)
    if secret:
        return {
            "algorithm": "HMAC-SHA256",
            "value": _hmac_sign(payload, secret),
            "public_key_fp": "",
            "signed_at": now,
        }

    # 3. No signing available — return empty signature block (audit logging must never break)
    logger.warning("No signing key or HMAC secret configured. Audit entries will be unsigned.")
    return {
        "algorithm": "none",
        "value": "",
        "public_key_fp": "",
        "signed_at": now,
    }


def verify_payload(payload: bytes, signature_b64: str, public_key_fp: str, algorithm: Optional[str] = None, key_path: Optional[str] = None) -> bool:
    """Verify a payload signature.

    Args:
        payload: the original payload bytes
        signature_b64: base64-encoded signature from sign_payload()
        public_key_fp: expected public key fingerprint (for asymmetric)
        algorithm: optional algorithm hint
        key_path: path to public key PEM for asymmetric verification
    """
    if not signature_b64:
        return False

    # HMAC verification
    if algorithm == "HMAC-SHA256" or (algorithm is None and not public_key_fp):
        secret = os.environ.get(ENV_HMAC_SECRET)
        if secret:
            return _hmac_verify(payload, signature_b64, secret)
        return False

    if algorithm == "none":
        return False

    # Asymmetric verification
    if not HAS_CRYPTO:
        return False

    path = key_path or os.environ.get(ENV_KEY_PATH)
    if not path:
        return False

    priv = _load_private_key(path)
    if not priv:
        return False

    # Verify public key fingerprint matches
    actual_fp = _get_public_key_fp(priv)
    if public_key_fp and actual_fp != public_key_fp:
        logger.warning("Public key fingerprint mismatch")
        return False

    pub = priv.public_key()
    sig = base64.b64decode(signature_b64)

    try:
        if isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(sig, payload, ec.ECDSA(hashes.SHA256()))
            return True
        elif isinstance(pub, ed25519.Ed25519PublicKey):
            pub.verify(sig, payload)
            return True
    except Exception:
        return False
    return False


def has_signing_key() -> bool:
    """Return True if any signing method is available."""
    if os.environ.get(ENV_KEY_PATH):
        return True
    if os.environ.get(ENV_HMAC_SECRET):
        return True
    return False


def generate_keypair(output_dir: Path, key_type: str = "ecdsa-p256") -> dict:
    """Generate a new signing keypair and write to disk.

    Returns dict with paths and fingerprint."""
    if not HAS_CRYPTO:
        raise RuntimeError("cryptography library required. Install: pip install cryptography")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if key_type == "ecdsa-p256":
        priv = ec.generate_private_key(ec.SECP256R1(), default_backend())
        algo = "ECDSA-P256-SHA256"
    elif key_type == "ed25519":
        priv = ed25519.Ed25519PrivateKey.generate()
        algo = "Ed25519"
    else:
        raise ValueError(f"Unsupported key_type: {key_type}")

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path = output_dir / f"icdev_audit_{key_type}.pem"
    pub_path = output_dir / f"icdev_audit_{key_type}.pub"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)

    fp = hashlib.sha256(pub_pem).hexdigest()
    return {
        "algorithm": algo,
        "private_key": str(priv_path),
        "public_key": str(pub_path),
        "public_key_fp": fp,
    }


def main():
    parser = argparse.ArgumentParser(description="ICDEV Crypto Key Manager")
    parser.add_argument("--generate-keys", action="store_true", help="Generate a new keypair")
    parser.add_argument("--output-dir", default="data/keys", help="Key output directory")
    parser.add_argument("--key-type", default="ecdsa-p256", choices=["ecdsa-p256", "ed25519"])
    parser.add_argument("--sign", help="Sign a string payload")
    parser.add_argument("--verify", help="Verify a string payload")
    parser.add_argument("--signature", help="Base64 signature for --verify")
    parser.add_argument("--pubkey-fp", help="Public key fingerprint for --verify")
    parser.add_argument("--algorithm", help="Algorithm hint")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.generate_keys:
        info = generate_keypair(args.output_dir, args.key_type)
        print(json.dumps(info, indent=2) if args.json else info)
        return

    if args.sign:
        sig = sign_payload(args.sign.encode())
        print(json.dumps(sig, indent=2) if args.json else sig)
        return

    if args.verify and args.signature:
        ok = verify_payload(
            args.verify.encode(),
            args.signature,
            args.pubkey_fp or "",
            algorithm=args.algorithm,
        )
        print(json.dumps({"verified": ok}) if args.json else ok)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
