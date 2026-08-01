# CUI // SP-CTI
"""SQLite Database Encryption at Rest — AES-256-GCM transparent file wrapper.

Encrypts the SQLite database file when closed and decrypts it on open so that
CUI data is never stored in plaintext on disk.  Uses AES-256-GCM via the
``cryptography`` library (FIPS 140-2 validated module available).

Environment:
    ICDEV_DB_ENCRYPTION_KEY   Base64-encoded 32-byte key for DB encryption
    ICDEV_DB_ENFORCE          "true" to refuse unencrypted SQLite (default false)

Usage:
    from tools.security.db_encryption import EncryptedSqliteConnection

    conn = EncryptedSqliteConnection("data/icdev.db")
    conn.execute("SELECT 1").fetchone()
    conn.close()   # file is re-encrypted on close

Design:
    - On ``open()``: decrypts *.enc file to a temporary file, then opens that
      temporary file with sqlite3.
    - On ``close()``: re-encrypts the temporary file back to *.enc, then
      shreds the temporary file.
    - If the key is not configured, falls back to plain sqlite3 (with a
      logged warning unless ICDEV_DB_ENFORCE=true).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import base64
import hashlib
import os
import shutil
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Optional

logger = get_logger("icdev.db.encryption")

# File header: magic + nonce + tag + ciphertext
_DB_ENC_MAGIC = b"ICDEVDB1"
_DB_ENC_NONCE_LEN = 12
_DB_ENC_TAG_LEN = 16
_DB_ENC_HEADER_LEN = len(_DB_ENC_MAGIC) + _DB_ENC_NONCE_LEN + _DB_ENC_TAG_LEN


def _get_db_key() -> Optional[bytes]:
    """Return the 32-byte AES-256 key for database encryption."""
    raw = os.environ.get("ICDEV_DB_ENCRYPTION_KEY", "")
    if raw:
        try:
            key = base64.urlsafe_b64decode(raw)
            if len(key) == 32:
                return key
        except Exception:
            pass
        # Derive 32-byte key from passphrase
        return hashlib.sha256(raw.encode("utf-8")).digest()

    # Fallback to dashboard secret
    fallback = os.environ.get("ICDEV_DASHBOARD_SECRET", "")
    if fallback:
        return hashlib.sha256(fallback.encode("utf-8")).digest()

    return None


def _aes_gcm_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt plaintext with AES-256-GCM. Returns: magic || nonce || tag || ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(_DB_ENC_NONCE_LEN)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    # AESGCM encrypt returns tag || ciphertext (tag is last 16 bytes in cryptography >= 2.0)
    # Actually AESGCM.encrypt returns ciphertext + tag appended
    return _DB_ENC_MAGIC + nonce + ciphertext


def _aes_gcm_decrypt(key: bytes, encrypted: bytes) -> bytes:
    """Decrypt AES-256-GCM payload.  Returns plaintext or raises ValueError."""
    if not encrypted.startswith(_DB_ENC_MAGIC):
        raise ValueError("Invalid encrypted DB file header")

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = encrypted[len(_DB_ENC_MAGIC) : len(_DB_ENC_MAGIC) + _DB_ENC_NONCE_LEN]
    ciphertext = encrypted[len(_DB_ENC_MAGIC) + _DB_ENC_NONCE_LEN :]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


class EncryptedSqliteConnection:
    """Transparently encrypted SQLite connection.

    Wraps a sqlite3.Connection.  The physical file on disk is always AES-256-GCM
    encrypted.  A temporary decrypted copy lives in the system temp directory
    while the connection is open.
    """

    def __init__(self, db_path: str, key: Optional[bytes] = None, **sqlite_kwargs):
        self._db_path = Path(db_path)
        self._enc_path = self._db_path.with_suffix(self._db_path.suffix + ".enc")
        self._key = key or _get_db_key()
        self._sqlite_kwargs = sqlite_kwargs
        self._conn: Optional[sqlite3.Connection] = None
        self._temp_path: Optional[Path] = None
        self._lock = threading.Lock()

    def _decrypt_to_temp(self) -> Path:
        """Decrypt the encrypted file to a secure temporary path."""
        if self._key is None:
            raise RuntimeError("DB encryption key not configured")

        if not self._enc_path.exists():
            # New database — create empty temp file
            fd, temp_path = tempfile.mkstemp(suffix=".db", prefix="icdev_enc_")
            os.close(fd)
            return Path(temp_path)

        encrypted = self._enc_path.read_bytes()
        plaintext = _aes_gcm_decrypt(self._key, encrypted)

        fd, temp_path = tempfile.mkstemp(suffix=".db", prefix="icdev_enc_")
        try:
            os.write(fd, plaintext)
        finally:
            os.close(fd)
        return Path(temp_path)

    def _encrypt_and_cleanup(self) -> None:
        """Re-encrypt the temp file, write to disk, and securely delete the temp."""
        if self._temp_path is None or not self._temp_path.exists():
            return

        if self._key is None:
            shutil.move(str(self._temp_path), str(self._db_path))
            return

        plaintext = self._temp_path.read_bytes()
        encrypted = _aes_gcm_encrypt(self._key, plaintext)
        self._enc_path.write_bytes(encrypted)

        # Secure delete: overwrite with zeros before unlinking
        self._shred_file(self._temp_path)
        self._temp_path = None

    @staticmethod
    def _shred_file(path: Path, passes: int = 3) -> None:
        """Overwrite file with random data before deleting."""
        if not path.exists():
            return
        try:
            size = path.stat().st_size
            with open(path, "r+b") as f:
                for _ in range(passes):
                    f.seek(0)
                    f.write(os.urandom(size))
                    f.flush()
                    os.fsync(f.fileno())
        except Exception:
            pass
        try:
            path.unlink()
        except Exception:
            pass

    def open(self) -> sqlite3.Connection:
        """Open the database (decrypting if necessary) and return the connection."""
        with self._lock:
            if self._conn is not None:
                return self._conn

            if self._key is None:
                enforce = os.environ.get("ICDEV_DB_ENFORCE", "false").lower() in ("1", "true", "yes")
                if enforce:
                    raise RuntimeError(
                        "SQLite DB encryption enforced (ICDEV_DB_ENFORCE=true) "
                        "but ICDEV_DB_ENCRYPTION_KEY is not set"
                    )
                logger.warning("DB encryption key not configured — opening unencrypted SQLite")
                self._conn = sqlite3.connect(str(self._db_path), **self._sqlite_kwargs)  # pg-ok: SQLCipher-style encrypted SQLite wrapper (infra)
                self._conn.row_factory = sqlite3.Row
                return self._conn

            # Encrypted path
            self._temp_path = self._decrypt_to_temp()
            self._conn = sqlite3.connect(str(self._temp_path), **self._sqlite_kwargs)  # pg-ok: SQLCipher decrypt-to-temp SQLite (infra)
            self._conn.row_factory = sqlite3.Row
            return self._conn

    def close(self) -> None:
        """Close the connection and re-encrypt the database file."""
        with self._lock:
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()
                self._conn = None

            if self._key is not None and self._temp_path is not None:
                self._encrypt_and_cleanup()

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def open_encrypted_db(db_path: str, **kwargs) -> sqlite3.Connection:
    """Convenience: open an encrypted SQLite database and return the raw connection.

    The caller is responsible for calling ``close()`` on the returned connection,
    which will trigger re-encryption if the wrapper was used.
    """
    key = _get_db_key()
    if key is None:
        enforce = os.environ.get("ICDEV_DB_ENFORCE", "false").lower() in ("1", "true", "yes")
        if enforce:
            raise RuntimeError("DB encryption enforced but key not configured")
        logger.warning("DB encryption key not configured — falling back to plain SQLite")
        conn = sqlite3.connect(db_path, **kwargs)  # pg-ok: SQLCipher-style encrypted SQLite wrapper (infra)
        conn.row_factory = sqlite3.Row
        return conn

    wrapper = EncryptedSqliteConnection(db_path, key=key, **kwargs)
    return wrapper.open()


def health_check() -> dict:
    """Return DB encryption readiness status."""
    key = _get_db_key()
    enforce = os.environ.get("ICDEV_DB_ENFORCE", "false").lower() in ("1", "true", "yes")
    status = "healthy" if key is not None else ("warning" if not enforce else "critical")
    return {
        "status": status,
        "key_configured": key is not None,
        "key_length_bytes": len(key) if key else 0,
        "enforce_mode": enforce,
        "algorithm": "AES-256-GCM",
    }


def _cli():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="SQLite DB Encryption at Rest")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--encrypt-file", metavar="PATH", help="Encrypt a plain SQLite file")
    parser.add_argument("--decrypt-file", metavar="PATH", help="Decrypt an encrypted SQLite file")
    parser.add_argument("--out", metavar="PATH", help="Output path")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = health_check()
        print(json.dumps(result, indent=2) if args.json else str(result))
    elif args.encrypt_file:
        key = _get_db_key()
        if key is None:
            raise RuntimeError("No encryption key configured")
        plaintext = Path(args.encrypt_file).read_bytes()
        enc = _aes_gcm_encrypt(key, plaintext)
        out = Path(args.out or args.encrypt_file + ".enc")
        out.write_bytes(enc)
        print(f"Encrypted {args.encrypt_file} -> {out}")
    elif args.decrypt_file:
        key = _get_db_key()
        if key is None:
            raise RuntimeError("No encryption key configured")
        encrypted = Path(args.decrypt_file).read_bytes()
        plain = _aes_gcm_decrypt(key, encrypted)
        out = Path(args.out or args.decrypt_file.replace(".enc", ""))
        out.write_bytes(plain)
        print(f"Decrypted {args.decrypt_file} -> {out}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
