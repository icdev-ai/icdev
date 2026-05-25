# CUI // SP-CTI
"""CUI Field-Level Encryption — AES-128-CBC via Fernet (cryptography).

Encrypts sensitive CUI data before it is written to the database and decrypts
on read.  Uses a master key loaded from ICDEV_CUI_MASTER_KEY, or falls back
to HKDF-SHA256 key derivation from ICDEV_DASHBOARD_SECRET.

Environment:
    ICDEV_CUI_MASTER_KEY    Base64-encoded 32-byte key (preferred)
    ICDEV_DASHBOARD_SECRET  Fallback secret for key derivation
    ICDEV_CUI_ENFORCE       "true" to raise on missing key (default false)

Usage:
    from tools.security.cui_crypto import encrypt_cui, decrypt_cui, is_encrypted

    cipher = encrypt_cui("SECRET DATA")
    plain  = decrypt_cui(cipher)

    # JSON objects
    cipher_json = encrypt_cui_json({"ssn": "123-45-6789"})
    plain_json  = decrypt_cui_json(cipher_json)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import base64
import hashlib
import json
import os
from typing import Any, Optional

logger = get_logger("icdev.cui.crypto")

# Fernet prefix so we can detect already-encrypted values
_FERNET_PREFIX = b"gAAAA"
_FERNET_TAG = "[CUI_ENCRYPTED]"


def _derive_key(secret: str) -> bytes:
    """Derive a 32-byte Fernet key from a secret string using HKDF-SHA256."""
    try:
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"icdev-cui-v1",
            info=b"cui-field-encryption",
        )
        return hkdf.derive(secret.encode("utf-8"))
    except Exception:
        # Fallback: simple SHA-256 hash (deterministic, not HKDF-grade but
        # air-gap safe when cryptography HKDF is unavailable)
        return hashlib.sha256((secret + "icdev-cui-v1").encode("utf-8")).digest()


def _get_master_key() -> Optional[bytes]:
    """Return the 32-byte master key for CUI encryption."""
    raw = os.environ.get("ICDEV_CUI_MASTER_KEY", "")
    if raw:
        try:
            key = base64.urlsafe_b64decode(raw)
            if len(key) == 32:
                return key
        except Exception:
            pass
        # If not 32 raw bytes, treat as passphrase and derive
        return _derive_key(raw)

    fallback = os.environ.get("ICDEV_DASHBOARD_SECRET", "")
    if fallback:
        return _derive_key(fallback)

    return None


def _get_fernet():
    """Return a Fernet instance or None if no key is configured."""
    key = _get_master_key()
    if key is None:
        return None
    from cryptography.fernet import Fernet

    b64_key = base64.urlsafe_b64encode(key)
    return Fernet(b64_key)


def _fernet_instance():
    """Return Fernet instance, raising if CUI enforcement is on and key missing."""
    f = _get_fernet()
    if f is None:
        enforce = os.environ.get("ICDEV_CUI_ENFORCE", "false").lower() in ("1", "true", "yes")
        if enforce:
            raise RuntimeError("CUI encryption enforced but ICDEV_CUI_MASTER_KEY is not set")
    return f


def is_encrypted(value: Any) -> bool:
    """Check if a value appears to be already encrypted by this module."""
    if isinstance(value, str):
        return value.startswith(_FERNET_TAG) or value.startswith("gAAAA")
    if isinstance(value, bytes):
        return value.startswith(_FERNET_PREFIX)
    return False


def encrypt_cui(plaintext: str) -> str:
    """Encrypt a string containing CUI data.

    Returns the ciphertext with a CUI tag prepended.  If encryption key is not
    configured, returns the plaintext unchanged (with a logged warning).
    """
    if not isinstance(plaintext, str):
        plaintext = str(plaintext)
    if not plaintext:
        return plaintext
    if is_encrypted(plaintext):
        return plaintext

    f = _fernet_instance()
    if f is None:
        logger.warning("CUI encryption skipped — no master key configured")
        return plaintext

    token = f.encrypt(plaintext.encode("utf-8"))
    return f"{_FERNET_TAG}{token.decode('ascii')}"


def decrypt_cui(ciphertext: str) -> str:
    """Decrypt a string encrypted by encrypt_cui().

    Returns the original plaintext.  If the value is not encrypted, returns
    it unchanged.
    """
    if not isinstance(ciphertext, str):
        ciphertext = str(ciphertext)
    if not ciphertext:
        return ciphertext
    if not ciphertext.startswith(_FERNET_TAG):
        # Also handle bare Fernet tokens (legacy or external)
        if ciphertext.startswith("gAAAA"):
            token = ciphertext.encode("ascii")
        else:
            return ciphertext
    else:
        token = ciphertext[len(_FERNET_TAG) :].encode("ascii")

    f = _fernet_instance()
    if f is None:
        logger.warning("CUI decryption skipped — no master key configured")
        return ciphertext

    try:
        return f.decrypt(token).decode("utf-8")
    except Exception as exc:
        logger.error("CUI decryption failed: %s", exc)
        raise ValueError(f"CUI decryption failed: {exc}") from exc


def encrypt_cui_json(data: dict) -> str:
    """Encrypt a JSON-serializable dict as a single encrypted string."""
    if not isinstance(data, dict):
        raise TypeError("encrypt_cui_json expects a dict")
    return encrypt_cui(json.dumps(data, separators=(",", ":")))


def decrypt_cui_json(ciphertext: str) -> dict:
    """Decrypt a string produced by encrypt_cui_json() back to a dict."""
    plain = decrypt_cui(ciphertext)
    return json.loads(plain)


def rotate_cui_key(old_key: bytes, new_key: bytes, ciphertext: str) -> str:
    """Re-encrypt a ciphertext with a new master key.

    Args:
        old_key: 32-byte key used for the original encryption.
        new_key: 32-byte key to use for re-encryption.
        ciphertext: Value produced by encrypt_cui().

    Returns:
        New ciphertext encrypted with new_key.
    """
    from cryptography.fernet import Fernet

    old_f = Fernet(base64.urlsafe_b64encode(old_key))
    new_f = Fernet(base64.urlsafe_b64encode(new_key))

    if ciphertext.startswith(_FERNET_TAG):
        token = ciphertext[len(_FERNET_TAG) :].encode("ascii")
    else:
        token = ciphertext.encode("ascii")

    plain = old_f.decrypt(token)
    new_token = new_f.encrypt(plain)
    return f"{_FERNET_TAG}{new_token.decode('ascii')}"


# ---------------------------------------------------------------------------
# CLI / health check
# ---------------------------------------------------------------------------
def health_check() -> dict:
    """Return encryption readiness status."""
    key = _get_master_key()
    f = _get_fernet()
    enforce = os.environ.get("ICDEV_CUI_ENFORCE", "false").lower() in ("1", "true", "yes")
    status = "healthy" if f is not None else ("warning" if not enforce else "critical")
    return {
        "status": status,
        "key_configured": key is not None,
        "key_length_bytes": len(key) if key else 0,
        "enforce_mode": enforce,
        "algorithm": "Fernet (AES-128-CBC + HMAC-SHA256)",
    }


def _cli():
    import argparse

    parser = argparse.ArgumentParser(description="CUI Field-Level Encryption")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--encrypt", metavar="TEXT", help="Encrypt text")
    parser.add_argument("--decrypt", metavar="TEXT", help="Decrypt text")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = health_check()
        print(json.dumps(result, indent=2) if args.json else str(result))
    elif args.encrypt:
        print(encrypt_cui(args.encrypt))
    elif args.decrypt:
        print(decrypt_cui(args.decrypt))
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
