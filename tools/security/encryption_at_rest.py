#!/usr/bin/env python3
# CUI // SP-CTI
"""Encryption at Rest — per-classification column encryption with HKDF key derivation.

Derives column-specific AES-256-GCM keys from a master secret using HKDF-SHA256.
Supports per-classification key namespaces (PUBLIC, CUI, SECRET), optional HSM
key wrapping via PKCS#11, and key rotation with append-only audit logging.

Environment:
    ICDEV_ENCRYPTION_MASTER_KEY   Base64-encoded 32-byte master key
    ICDEV_DASHBOARD_SECRET        Fallback secret for key derivation
    ICDEV_ENCRYPTION_HSM_LIB      Path to PKCS#11 shared library (optional)
    ICDEV_ENCRYPTION_HSM_SLOT     HSM slot index (default 0)
    ICDEV_ENCRYPTION_HSM_PIN      HSM user PIN (optional)
    ICDEV_ENCRYPTION_HSM_KEY_ID   HSM key object label/ID for wrapping (optional)
    ICDEV_ENCRYPTION_ENFORCE      "true" to raise on missing master key (default false)

Usage:
    from tools.security.encryption_at_rest import EncryptionAtRest

    ear = EncryptionAtRest()
    cipher = ear.encrypt("sensitive data", classification="CUI", column="ssn")
    plain = ear.decrypt(cipher, classification="CUI", column="ssn")

CLI:
    python tools/security/encryption_at_rest.py --health --json
    python tools/security/encryption_at_rest.py --rotate --classification CUI --json
    python tools/security/encryption_at_rest.py --status --json
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import argparse
import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional

from tools.db.storage import get_connection

logger = get_logger("icdev.encryption_at_rest")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Supported classification levels (ordered lowest to highest)
CLASSIFICATIONS = ["PUBLIC", "CUI", "SECRET"]

# Key derivation parameters
_HKDF_SALT_PREFIX = b"icdev-ear-v1"
_HKDF_INFO_TEMPLATE = b"col:%s:cls:%s"
_AES_KEY_LEN = 32
_AES_NONCE_LEN = 12
_AES_TAG_LEN = 16
_CIPHERTEXT_PREFIX = b"$ear$"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_master_key() -> Optional[bytes]:
    """Return the 32-byte master key for encryption at rest."""
    raw = os.environ.get("ICDEV_ENCRYPTION_MASTER_KEY", "")
    if raw:
        try:
            key = base64.urlsafe_b64decode(raw)
            if len(key) == _AES_KEY_LEN:
                return key
        except Exception:
            pass
        return hashlib.sha256(raw.encode("utf-8")).digest()

    fallback = os.environ.get("ICDEV_DASHBOARD_SECRET", "")
    if fallback:
        return hashlib.sha256(fallback.encode("utf-8")).digest()

    return None


def _derive_column_key(master_key: bytes, column: str, classification: str) -> bytes:
    """Derive a column-specific AES-256 key via HKDF-SHA256."""
    try:
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes

        info = _HKDF_INFO_TEMPLATE % (column.encode("utf-8"), classification.encode("utf-8"))
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=_AES_KEY_LEN,
            salt=_HKDF_SALT_PREFIX + classification.encode("utf-8"),
            info=info,
        )
        return hkdf.derive(master_key)
    except Exception:
        # Fallback deterministic derivation when cryptography HKDF unavailable
        h = hashlib.sha256()
        h.update(master_key)
        h.update(_HKDF_SALT_PREFIX)
        h.update(classification.encode("utf-8"))
        h.update(column.encode("utf-8"))
        return h.digest()


def _aes_gcm_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt with AES-256-GCM. Returns: prefix || nonce || tag || ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(_AES_NONCE_LEN)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return _CIPHERTEXT_PREFIX + nonce + ciphertext


def _aes_gcm_decrypt(key: bytes, encrypted: bytes) -> bytes:
    """Decrypt AES-256-GCM payload."""
    if not encrypted.startswith(_CIPHERTEXT_PREFIX):
        raise ValueError("Invalid ciphertext header")

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    offset = len(_CIPHERTEXT_PREFIX)
    nonce = encrypted[offset : offset + _AES_NONCE_LEN]
    ciphertext = encrypted[offset + _AES_NONCE_LEN :]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


class HSMWrapper:
    """Optional PKCS#11 HSM wrapper for key wrapping/unwrapping."""

    def __init__(self):
        self._lib_path = os.environ.get("ICDEV_ENCRYPTION_HSM_LIB", "")
        self._slot = int(os.environ.get("ICDEV_ENCRYPTION_HSM_SLOT", "0"))
        self._pin = os.environ.get("ICDEV_ENCRYPTION_HSM_PIN", "")
        self._key_id = os.environ.get("ICDEV_ENCRYPTION_HSM_KEY_ID", "")
        self._session = None
        self._available = False
        self._init()

    def _init(self):
        if not self._lib_path or not Path(self._lib_path).exists():
            return
        try:
            import PyKCS11

            self._pkcs11 = PyKCS11.PyKCS11Lib()
            self._pkcs11.load(self._lib_path)
            slots = self._pkcs11.getSlotList(tokenPresent=True)
            if not slots:
                return
            slot = slots[self._slot] if self._slot < len(slots) else slots[0]
            self._session = self._pkcs11.openSession(slot)
            if self._pin:
                self._session.login(self._pin)
            self._available = True
        except Exception as exc:
            logger.debug("HSM initialization failed: %s", exc)

    def is_available(self) -> bool:
        return self._available

    def wrap_key(self, key: bytes) -> Optional[bytes]:
        """Wrap a key using the HSM. Returns wrapped key or None."""
        if not self._available:
            return None
        try:
            import PyKCS11

            mechanism = PyKCS11.Mechanism(PyKCS11.CKM_AES_GCM, b"" + os.urandom(12))
            # Simplified: find AES key by label and use it to wrap
            objs = self._session.findObjects([
                (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                (PyKCS11.CKA_LABEL, self._key_id),
            ])
            if not objs:
                return None
            wrapped = self._session.wrapKey(objs[0], key, mechanism)
            return bytes(wrapped)
        except Exception as exc:
            logger.warning("HSM wrap_key failed: %s", exc)
            return None

    def unwrap_key(self, wrapped: bytes) -> Optional[bytes]:
        """Unwrap a key using the HSM."""
        if not self._available:
            return None
        try:
            import PyKCS11

            mechanism = PyKCS11.Mechanism(PyKCS11.CKM_AES_GCM, b"" + os.urandom(12))
            objs = self._session.findObjects([
                (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                (PyKCS11.CKA_LABEL, self._key_id),
            ])
            if not objs:
                return None
            template = {
                PyKCS11.CKA_CLASS: PyKCS11.CKO_SECRET_KEY,
                PyKCS11.CKA_KEY_TYPE: PyKCS11.CKK_AES,
                PyKCS11.CKA_ENCRYPT: True,
                PyKCS11.CKA_DECRYPT: True,
            }
            unwrapped = self._session.unwrapKey(objs[0], wrapped, mechanism, template)
            return bytes(unwrapped)
        except Exception as exc:
            logger.warning("HSM unwrap_key failed: %s", exc)
            return None


class EncryptionAtRest:
    """Per-classification encryption-at-rest engine.

    Derives column-specific keys from a master secret, supports optional HSM
    key wrapping, and logs all key lifecycle events to an append-only audit table.
    """

    def __init__(self, db_path: Optional[Path] = None, master_key: Optional[bytes] = None):
        self._db_path = db_path or DB_PATH
        self._master_key = master_key or _get_master_key()
        self._hsm = HSMWrapper()
        self._ensure_tables()

    def _get_db(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = get_connection(db_path=str(self._db_path))
        if hasattr(conn, "execute"):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass
        return conn

    def _ensure_tables(self):
        conn = self._get_db()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS encryption_keys (
                id              TEXT PRIMARY KEY,
                classification  TEXT NOT NULL CHECK(classification IN ('PUBLIC', 'CUI', 'SECRET')),
                column_name     TEXT NOT NULL,
                key_hash        TEXT NOT NULL,
                wrapped_key     BLOB,
                hsm_enabled     INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                rotated_at      TEXT,
                expires_at      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ek_cls_col ON encryption_keys(classification, column_name);
            CREATE INDEX IF NOT EXISTS idx_ek_created ON encryption_keys(created_at);

            CREATE TABLE IF NOT EXISTS encryption_key_log (
                id              TEXT PRIMARY KEY,
                classification  TEXT NOT NULL,
                column_name     TEXT,
                action          TEXT NOT NULL CHECK(action IN ('create', 'rotate', 'revoke', 'access', 'hsm_wrap', 'hsm_unwrap')),
                key_hash_prefix TEXT,
                reason          TEXT,
                performed_by    TEXT,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ekl_action ON encryption_key_log(action);
            CREATE INDEX IF NOT EXISTS idx_ekl_created ON encryption_key_log(created_at);

            CREATE TABLE IF NOT EXISTS encryption_key_history (
                id              TEXT PRIMARY KEY,
                classification  TEXT NOT NULL,
                column_name     TEXT NOT NULL,
                key_hash        TEXT NOT NULL,
                wrapped_key     BLOB,
                created_at      TEXT NOT NULL,
                retired_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ekh_cls_col ON encryption_key_history(classification, column_name);
            """
        )
        conn.commit()
        conn.close()

    def _log_event(
        self,
        conn,
        classification: str,
        action: str,
        column_name: str = "",
        key_hash_prefix: str = "",
        reason: str = "",
        performed_by: str = "",
    ):
        conn.execute(
            """INSERT INTO encryption_key_log
               (id, classification, column_name, action, key_hash_prefix, reason, performed_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                str(uuid.uuid4()),
                classification,
                column_name,
                action,
                key_hash_prefix,
                reason,
                performed_by or "encryption_at_rest",
                _now(),
            ),
        )

    def _column_key(self, classification: str, column: str) -> bytes:
        if self._master_key is None:
            raise RuntimeError("Encryption master key not configured")
        return _derive_column_key(self._master_key, column, classification)

    def encrypt(self, plaintext: str, classification: str, column: str = "default") -> str:
        """Encrypt plaintext with a classification/column-specific key.

        Returns a base64-encoded ciphertext string. If encryption key is not
        configured, returns plaintext unchanged (with a logged warning).
        """
        if not isinstance(plaintext, str):
            plaintext = str(plaintext)
        if not plaintext:
            return plaintext

        if self._master_key is None:
            enforce = os.environ.get("ICDEV_ENCRYPTION_ENFORCE", "false").lower() in ("1", "true", "yes")
            if enforce:
                raise RuntimeError("Encryption at rest enforced but ICDEV_ENCRYPTION_MASTER_KEY is not set")
            logger.warning("Encryption master key not configured — returning plaintext")
            return plaintext

        if classification not in CLASSIFICATIONS:
            raise ValueError(f"Invalid classification: {classification}. Must be one of {CLASSIFICATIONS}")

        key = self._column_key(classification, column)
        ciphertext = _aes_gcm_encrypt(key, plaintext.encode("utf-8"))

        # Ensure key record exists for rotation tracking
        self._ensure_key_record(classification, column, key)

        return base64.urlsafe_b64encode(ciphertext).decode("ascii")

    def decrypt(self, ciphertext: str, classification: str, column: str = "default") -> str:
        """Decrypt ciphertext with a classification/column-specific key.

        Returns plaintext. If the value is not encrypted, returns it unchanged.
        """
        if not isinstance(ciphertext, str):
            ciphertext = str(ciphertext)
        if not ciphertext:
            return ciphertext

        # Detect if it looks like base64-encoded ciphertext with our prefix
        try:
            raw = base64.urlsafe_b64decode(ciphertext)
        except Exception:
            return ciphertext

        if not raw.startswith(_CIPHERTEXT_PREFIX):
            return ciphertext

        if self._master_key is None:
            enforce = os.environ.get("ICDEV_ENCRYPTION_ENFORCE", "false").lower() in ("1", "true", "yes")
            if enforce:
                raise RuntimeError("Encryption at rest enforced but ICDEV_ENCRYPTION_MASTER_KEY is not set")
            logger.warning("Encryption master key not configured — returning ciphertext unchanged")
            return ciphertext

        key = self._column_key(classification, column)
        plaintext = _aes_gcm_decrypt(key, raw)
        return plaintext.decode("utf-8")

    def _ensure_key_record(self, classification: str, column: str, key: bytes):
        """Ensure a key record exists in encryption_keys."""
        key_hash = hashlib.sha256(key).hexdigest()
        conn = self._get_db()
        row = conn.execute(
            "SELECT id FROM encryption_keys WHERE classification = %s AND column_name = %s",
            (classification, column),
        ).fetchone()
        if not row:
            wrapped = None
            hsm_enabled = 0
            if self._hsm.is_available():
                wrapped = self._hsm.wrap_key(key)
                hsm_enabled = 1 if wrapped else 0
            conn.execute(
                """INSERT INTO encryption_keys
                   (id, classification, column_name, key_hash, wrapped_key, hsm_enabled, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (str(uuid.uuid4()), classification, column, key_hash, wrapped, hsm_enabled, _now()),
            )
            self._log_event(conn, classification, "create", column, key_hash[:16], "auto-created on first use")
        conn.commit()
        conn.close()

    def rotate_keys(self, classification: Optional[str] = None, reason: str = "scheduled") -> Dict:
        """Rotate encryption keys for a classification (or all) and archive old keys.

        Returns a summary of rotated keys. Requires a NEW master key to be set
        in the environment before calling — the old master key is still used for
        decryption of archived data.
        """
        conn = self._get_db()
        if classification:
            rows = conn.execute(
                "SELECT id, classification, column_name, key_hash FROM encryption_keys WHERE classification = %s",
                (classification,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, classification, column_name, key_hash FROM encryption_keys"
            ).fetchall()

        rotated = 0
        now = _now()
        for row in rows:
            # Move current key to history
            conn.execute(
                """INSERT INTO encryption_key_history
                   (id, classification, column_name, key_hash, wrapped_key, created_at, retired_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    str(uuid.uuid4()),
                    row["classification"],
                    row["column_name"],
                    row["key_hash"],
                    row.get("wrapped_key"),
                    row.get("created_at") or now,
                    now,
                ),
            )
            # Update key record with new hash (actual re-encryption of data must be done by caller)
            new_key = self._column_key(row["classification"], row["column_name"])
            new_hash = hashlib.sha256(new_key).hexdigest()
            wrapped = None
            hsm_enabled = 0
            if self._hsm.is_available():
                wrapped = self._hsm.wrap_key(new_key)
                hsm_enabled = 1 if wrapped else 0
            conn.execute(
                """UPDATE encryption_keys
                   SET key_hash = %s, wrapped_key = %s, hsm_enabled = %s, rotated_at = %s
                   WHERE id = %s""",
                (new_hash, wrapped, hsm_enabled, now, row["id"]),
            )
            self._log_event(
                conn,
                row["classification"],
                "rotate",
                row["column_name"],
                new_hash[:16],
                reason,
            )
            rotated += 1

        conn.commit()
        conn.close()
        return {
            "rotated": rotated,
            "classification": classification or "all",
            "reason": reason,
            "rotated_at": now,
        }

    def get_status(self) -> Dict:
        """Return encryption-at-rest status and key inventory."""
        conn = self._get_db()
        total_keys = conn.execute("SELECT COUNT(*) FROM encryption_keys").fetchone()[0]
        hsm_keys = conn.execute(
            "SELECT COUNT(*) FROM encryption_keys WHERE hsm_enabled = 1"
        ).fetchone()[0]
        by_classification = {}
        for cls in CLASSIFICATIONS:
            count = conn.execute(
                "SELECT COUNT(*) FROM encryption_keys WHERE classification = %s", (cls,)
            ).fetchone()[0]
            by_classification[cls] = count
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_rotations = conn.execute(
            "SELECT COUNT(*) FROM encryption_key_log WHERE action = 'rotate' AND created_at > %s",
            (cutoff,),
        ).fetchone()[0]
        conn.close()

        key = self._master_key
        return {
            "master_key_configured": key is not None,
            "key_length_bytes": len(key) if key else 0,
            "hsm_available": self._hsm.is_available(),
            "total_keys": total_keys,
            "hsm_wrapped_keys": hsm_keys,
            "by_classification": by_classification,
            "recent_rotations": recent_rotations,
        }

    def health_check(self) -> Dict:
        """Return health status for the encryption-at-rest subsystem."""
        status = self.get_status()
        enforce = os.environ.get("ICDEV_ENCRYPTION_ENFORCE", "false").lower() in ("1", "true", "yes")
        if status["master_key_configured"]:
            overall = "healthy"
        elif enforce:
            overall = "critical"
        else:
            overall = "warning"
        status["status"] = overall
        status["enforce_mode"] = enforce
        status["algorithm"] = "AES-256-GCM + HKDF-SHA256"
        return status

    def evaluate_gate(self, project_id: str = "") -> Dict:
        """Evaluate encryption-at-rest security gate."""
        health = self.health_check()
        blocking = []
        warnings = []

        if not health["master_key_configured"]:
            blocking.append("encryption_master_key_not_configured")

        if health["hsm_available"] and health["total_keys"] > 0 and health["hsm_wrapped_keys"] == 0:
            warnings.append("hsm_available_but_no_keys_wrapped")

        if health["total_keys"] == 0:
            warnings.append("no_column_keys_initialized")

        passed = len(blocking) == 0
        return {
            "gate": "encryption_at_rest",
            "passed": passed,
            "blocking": blocking,
            "warnings": warnings,
            "status": health,
            "project_id": project_id,
            "evaluated_at": _now(),
        }


def main():
    parser = argparse.ArgumentParser(description="Encryption at Rest — per-classification column encryption")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--status", action="store_true", help="Show key inventory status")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gate")
    parser.add_argument("--project-id", type=str, default="", help="Project identifier for gate")
    parser.add_argument("--rotate", action="store_true", help="Rotate keys")
    parser.add_argument("--classification", type=str, choices=CLASSIFICATIONS, help="Limit rotation to classification")
    parser.add_argument("--reason", type=str, default="manual", help="Rotation reason")
    parser.add_argument("--encrypt", metavar="TEXT", help="Encrypt text")
    parser.add_argument("--decrypt", metavar="TEXT", help="Decrypt text")
    parser.add_argument("--column", type=str, default="default", help="Column name for key derivation")
    parser.add_argument("--db-path", type=Path, help="Database path override")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    ear = EncryptionAtRest(db_path=args.db_path)

    if args.health:
        result = ear.health_check()
    elif args.status:
        result = ear.get_status()
    elif args.gate:
        result = ear.evaluate_gate(args.project_id)
    elif args.rotate:
        result = ear.rotate_keys(classification=args.classification, reason=args.reason)
    elif args.encrypt is not None:
        result = {"ciphertext": ear.encrypt(args.encrypt, args.classification or "CUI", args.column)}
    elif args.decrypt is not None:
        result = {"plaintext": ear.decrypt(args.decrypt, args.classification or "CUI", args.column)}
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result)

    if args.gate and not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
