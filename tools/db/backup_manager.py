#!/usr/bin/env python3
# CUI // SP-CTI
"""Backup and restore manager for ICDEV databases (SQLite and PostgreSQL).

Provides WAL-safe online backup via sqlite3 backup() API, SHA-256 integrity
verification, optional AES-256-CBC encryption, per-tenant backup, and
retention-based pruning.

ADR D152: Backup uses sqlite3.backup() for online consistency, pg_dump/psql
for PostgreSQL, SHA-256 sidecar checksums, optional AES-256-CBC encryption
via cryptography package with PBKDF2 key derivation (600K iterations).
Audit trail entries logged best-effort via tools.audit.audit_logger.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _load_config() -> dict:
    """Load backup configuration from args/db_config.yaml with fallback defaults."""
    config_path = BASE_DIR / "args" / "db_config.yaml"
    defaults = {
        "backup": {
            "enabled": True,
            "backup_dir": "data/backups",
            "retention_days": 30,
            "max_backups": 50,
            "compression": False,
            "encryption": {
                "enabled": False,
                "algorithm": "AES-256-CBC",
                "pbkdf2_iterations": 600000,
            },
            "databases": {
                "icdev": {"path": "data/icdev.db", "schedule": "daily"},
                "platform": {"path": "data/platform.db", "schedule": "daily"},
                "memory": {"path": "data/memory.db", "schedule": "weekly"},
                "activity": {"path": "data/activity.db", "schedule": "weekly"},
            },
            "tenants": {
                "backup_on_provision": True,
                "backup_on_migrate": True,
            },
        }
    }
    try:
        import yaml

        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            if loaded and isinstance(loaded, dict):
                return loaded
    except ImportError:
        pass
    except Exception:
        pass
    return defaults


def _iso_timestamp() -> str:
    """Return current UTC timestamp in ISO 8601 format (filename-safe)."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _iso_timestamp_full() -> str:
    """Return current UTC timestamp in full ISO 8601 format for metadata."""
    return datetime.now(timezone.utc).isoformat()


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest for a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _write_sha256_sidecar(file_path: Path, checksum: str) -> Path:
    """Write a .sha256 sidecar file next to the backup."""
    sidecar = file_path.parent / (file_path.name + ".sha256")
    with open(sidecar, "w", encoding="utf-8") as f:
        f.write(f"{checksum}  {file_path.name}\n")
    return sidecar


def _write_meta_json(
    file_path: Path,
    db_name: str,
    db_path: str,
    checksum: str,
    engine: str,
    encrypted: bool = False,
) -> Path:
    """Write a .meta.json sidecar with backup metadata."""
    meta_path = file_path.parent / (file_path.name + ".meta.json")
    meta = {
        "db_name": db_name,
        "db_path": str(db_path),
        "backup_path": str(file_path),
        "checksum_sha256": checksum,
        "size_bytes": file_path.stat().st_size,
        "created_at": _iso_timestamp_full(),
        "engine": engine,
        "encrypted": encrypted,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta_path


def _log_audit(event_type: str, action: str, details: dict = None) -> None:
    """Best-effort audit trail logging."""
    try:
        from tools.audit.audit_logger import log_event

        log_event(
            event_type=event_type,
            actor="backup-manager",
            action=action,
            details=details,
            classification="CUI",
        )
    except Exception:
        pass


class BackupManager:
    """Manages backup, restore, verify, encrypt, and prune operations for
    ICDEV SQLite and PostgreSQL databases.

    All paths are constructed with pathlib.Path and ``/`` operators.
    Thread-safe: no global mutable state; configuration is read-only after init.
    """

    def __init__(self, config: dict = None):
        self._config = config or _load_config()
        self._backup_cfg = self._config.get("backup", {})
        self._backup_dir = BASE_DIR / self._backup_cfg.get(
            "backup_dir", "data/backups"
        )
        self._retention_days = self._backup_cfg.get("retention_days", 30)
        self._max_backups = self._backup_cfg.get("max_backups", 50)
        self._databases = self._backup_cfg.get("databases", {})
        self._encryption_cfg = self._backup_cfg.get("encryption", {})
        self._pbkdf2_iterations = self._encryption_cfg.get(
            "pbkdf2_iterations", 600000
        )

    # ------------------------------------------------------------------
    # SQLite backup / restore
    # ------------------------------------------------------------------

    def backup_sqlite(
        self, db_path: Path, output_dir: Path = None
    ) -> dict:
        """Create a WAL-safe online backup of a SQLite database.

        Uses the ``sqlite3.Connection.backup()`` API for consistency.

        Args:
            db_path: Path to the source SQLite database.
            output_dir: Directory to write the backup into. Defaults to the
                configured backup directory.

        Returns:
            dict with backup metadata (db_name, backup_path, checksum, etc.).
        """
        db_path = Path(db_path).resolve()
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}")

        dest_dir = Path(output_dir) if output_dir else self._backup_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        db_name = db_path.stem
        timestamp = _iso_timestamp()
        backup_name = f"{db_name}_{timestamp}.db.bak"
        backup_path = dest_dir / backup_name

        # Online backup via sqlite3 backup() API — WAL-safe
        src_conn = sqlite3.connect(str(db_path))
        dst_conn = sqlite3.connect(str(backup_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        # Sidecar files
        checksum = _compute_sha256(backup_path)
        _write_sha256_sidecar(backup_path, checksum)
        _write_meta_json(
            backup_path,
            db_name=db_name,
            db_path=str(db_path),
            checksum=checksum,
            engine="sqlite",
        )

        _log_audit(
            "config_changed",
            f"SQLite backup created: {backup_name}",
            {"db_name": db_name, "backup_path": str(backup_path)},
        )

        return {
            "db_name": db_name,
            "db_path": str(db_path),
            "backup_path": str(backup_path),
            "checksum_sha256": checksum,
            "size_bytes": backup_path.stat().st_size,
            "created_at": _iso_timestamp_full(),
            "engine": "sqlite",
            "encrypted": False,
        }

    def backup_postgresql(
        self, db_url: str, output_dir: Path = None
    ) -> dict:
        """Create a pg_dump backup of a PostgreSQL database.

        Args:
            db_url: PostgreSQL connection URL
                (e.g. ``postgresql://user:pass@host:5432/dbname``).
            output_dir: Directory to write the backup into.

        Returns:
            dict with backup metadata.
        """
        dest_dir = Path(output_dir) if output_dir else self._backup_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Extract database name from URL
        db_name = db_url.rstrip("/").rsplit("/", 1)[-1]
        if "?" in db_name:
            db_name = db_name.split("?")[0]

        timestamp = _iso_timestamp()
        backup_name = f"{db_name}_{timestamp}.sql.bak"
        backup_path = dest_dir / backup_name

        cmd = ["pg_dump", "--no-owner", "--no-acl", "-f", str(backup_path), db_url]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        checksum = _compute_sha256(backup_path)
        _write_sha256_sidecar(backup_path, checksum)
        _write_meta_json(
            backup_path,
            db_name=db_name,
            db_path=db_url,
            checksum=checksum,
            engine="postgresql",
        )

        _log_audit(
            "config_changed",
            f"PostgreSQL backup created: {backup_name}",
            {"db_name": db_name, "backup_path": str(backup_path)},
        )

        return {
            "db_name": db_name,
            "db_path": db_url,
            "backup_path": str(backup_path),
            "checksum_sha256": checksum,
            "size_bytes": backup_path.stat().st_size,
            "created_at": _iso_timestamp_full(),
            "engine": "postgresql",
            "encrypted": False,
        }

    def restore_sqlite(
        self, backup_path: Path, db_path: Path
    ) -> dict:
        """Restore a SQLite database from a backup file.

        Copies the backup to the target path and runs ``PRAGMA integrity_check``
        to verify the restored database.

        Args:
            backup_path: Path to the ``.db.bak`` backup file.
            db_path: Destination path for the restored database.

        Returns:
            dict with restore result including integrity status.
        """
        backup_path = Path(backup_path).resolve()
        db_path = Path(db_path).resolve()

        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        # Ensure parent directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy backup to target using shutil.copy2 (preserves metadata)
        shutil.copy2(str(backup_path), str(db_path))

        # Verify integrity of restored database
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            integrity_ok = result is not None and result[0] == "ok"
        finally:
            conn.close()

        _log_audit(
            "config_changed",
            f"SQLite restore completed: {db_path.name}",
            {
                "backup_path": str(backup_path),
                "db_path": str(db_path),
                "integrity_ok": integrity_ok,
            },
        )

        return {
            "backup_path": str(backup_path),
            "db_path": str(db_path),
            "integrity_ok": integrity_ok,
            "restored_at": _iso_timestamp_full(),
            "engine": "sqlite",
        }

    def restore_postgresql(
        self, backup_path: Path, db_url: str
    ) -> dict:
        """Restore a PostgreSQL database from a pg_dump backup.

        Args:
            backup_path: Path to the ``.sql.bak`` backup file.
            db_url: PostgreSQL connection URL for the target database.

        Returns:
            dict with restore result.
        """
        backup_path = Path(backup_path).resolve()
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        cmd = ["psql", "-f", str(backup_path), db_url]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        _log_audit(
            "config_changed",
            f"PostgreSQL restore completed from {backup_path.name}",
            {"backup_path": str(backup_path), "db_url": db_url},
        )

        return {
            "backup_path": str(backup_path),
            "db_url": db_url,
            "restored_at": _iso_timestamp_full(),
            "engine": "postgresql",
            "stdout": result.stdout,
        }

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, backup_path: Path) -> dict:
        """Verify a backup file's integrity.

        For SQLite backups, runs ``PRAGMA integrity_check``.
        For all backups, validates the SHA-256 checksum against the sidecar.

        Args:
            backup_path: Path to the backup file.

        Returns:
            dict with verification results.
        """
        backup_path = Path(backup_path).resolve()
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        result = {
            "backup_path": str(backup_path),
            "verified_at": _iso_timestamp_full(),
            "checksum_valid": None,
            "integrity_valid": None,
            "errors": [],
        }

        # SHA-256 sidecar validation
        sha_sidecar = backup_path.parent / (backup_path.name + ".sha256")
        if sha_sidecar.exists():
            with open(sha_sidecar, "r", encoding="utf-8") as f:
                content = f.read().strip()
            expected_checksum = content.split()[0] if content else ""
            actual_checksum = _compute_sha256(backup_path)
            result["checksum_valid"] = actual_checksum == expected_checksum
            result["expected_checksum"] = expected_checksum
            result["actual_checksum"] = actual_checksum
            if not result["checksum_valid"]:
                result["errors"].append("SHA-256 checksum mismatch")
        else:
            result["errors"].append("SHA-256 sidecar file not found")

        # SQLite integrity check (for .db.bak files)
        if backup_path.name.endswith(".db.bak"):
            try:
                conn = sqlite3.connect(str(backup_path))
                cursor = conn.execute("PRAGMA integrity_check")
                row = cursor.fetchone()
                result["integrity_valid"] = row is not None and row[0] == "ok"
                if not result["integrity_valid"]:
                    result["errors"].append(
                        f"SQLite integrity check failed: {row}"
                    )
                conn.close()
            except sqlite3.Error as exc:
                result["integrity_valid"] = False
                result["errors"].append(f"SQLite error: {exc}")

        result["valid"] = (
            result.get("checksum_valid", False) is not False
            and result.get("integrity_valid") is not False
            and len(result["errors"]) == 0
        )

        return result

    # ------------------------------------------------------------------
    # Encryption / Decryption
    # ------------------------------------------------------------------

    def encrypt(self, file_path: Path, passphrase: str) -> Path:
        """Encrypt a file with AES-256-CBC using PBKDF2-derived key.

        Requires the ``cryptography`` package. The encrypted file is written
        with an ``.enc`` suffix appended.

        Args:
            file_path: Path to the file to encrypt.
            passphrase: Passphrase for key derivation.

        Returns:
            Path to the encrypted file.

        Raises:
            ImportError: If the ``cryptography`` package is not installed.
            FileNotFoundError: If the source file does not exist.
        """
        try:
            from cryptography.hazmat.primitives.ciphers import (
                Cipher,
                algorithms,
                modes,
            )
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
        except ImportError:
            raise ImportError(
                "The 'cryptography' package is required for encryption. "
                "Install it with: pip install cryptography"
            )

        file_path = Path(file_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        salt = os.urandom(16)
        iv = os.urandom(16)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self._pbkdf2_iterations,
        )
        key = kdf.derive(passphrase.encode("utf-8"))

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()

        with open(file_path, "rb") as f:
            plaintext = f.read()

        # PKCS7 padding
        pad_len = 16 - (len(plaintext) % 16)
        plaintext += bytes([pad_len]) * pad_len

        ciphertext = encryptor.update(plaintext) + encryptor.finalize()

        enc_path = file_path.parent / (file_path.name + ".enc")
        with open(enc_path, "wb") as f:
            # Header: 16-byte salt + 16-byte IV + ciphertext
            f.write(salt)
            f.write(iv)
            f.write(ciphertext)

        # Update meta.json if it exists
        meta_path = file_path.parent / (file_path.name + ".meta.json")
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["encrypted"] = True
            meta["encrypted_path"] = str(enc_path)
            meta["encrypted_checksum_sha256"] = _compute_sha256(enc_path)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

        return enc_path

    def decrypt(self, file_path: Path, passphrase: str) -> Path:
        """Decrypt an AES-256-CBC encrypted file.

        Requires the ``cryptography`` package. The decrypted file is written
        without the ``.enc`` suffix.

        Args:
            file_path: Path to the ``.enc`` encrypted file.
            passphrase: Passphrase used during encryption.

        Returns:
            Path to the decrypted file.

        Raises:
            ImportError: If the ``cryptography`` package is not installed.
            FileNotFoundError: If the encrypted file does not exist.
        """
        try:
            from cryptography.hazmat.primitives.ciphers import (
                Cipher,
                algorithms,
                modes,
            )
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
        except ImportError:
            raise ImportError(
                "The 'cryptography' package is required for decryption. "
                "Install it with: pip install cryptography"
            )

        file_path = Path(file_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Encrypted file not found: {file_path}")

        with open(file_path, "rb") as f:
            salt = f.read(16)
            iv = f.read(16)
            ciphertext = f.read()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self._pbkdf2_iterations,
        )
        key = kdf.derive(passphrase.encode("utf-8"))

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

        # Remove PKCS7 padding
        pad_len = padded_plaintext[-1]
        plaintext = padded_plaintext[:-pad_len]

        # Write decrypted file (strip .enc suffix)
        if file_path.name.endswith(".enc"):
            dec_name = file_path.name[:-4]
        else:
            dec_name = file_path.name + ".dec"
        dec_path = file_path.parent / dec_name

        with open(dec_path, "wb") as f:
            f.write(plaintext)

        return dec_path

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def backup_all(self, output_dir: Path = None) -> list:
        """Backup all configured databases (icdev, platform, memory, activity).

        Args:
            output_dir: Optional override for the backup directory.

        Returns:
            List of backup metadata dicts.
        """
        results = []
        for db_key, db_info in self._databases.items():
            db_rel_path = db_info.get("path", f"data/{db_key}.db")
            db_path = BASE_DIR / db_rel_path
            if db_path.exists():
                try:
                    result = self.backup_sqlite(db_path, output_dir)
                    result["db_key"] = db_key
                    results.append(result)
                except Exception as exc:
                    results.append({
                        "db_key": db_key,
                        "db_path": str(db_path),
                        "error": str(exc),
                    })
            else:
                results.append({
                    "db_key": db_key,
                    "db_path": str(db_path),
                    "error": "Database file not found",
                })
        return results

    def backup_tenants(
        self, slug: str = None, output_dir: Path = None
    ) -> list:
        """Backup per-tenant databases from data/tenants/.

        Args:
            slug: Optional tenant slug to backup a single tenant.
                If None, all tenants are backed up.
            output_dir: Optional override for the backup directory.

        Returns:
            List of backup metadata dicts.
        """
        tenants_dir = BASE_DIR / "data" / "tenants"
        results = []

        if not tenants_dir.exists():
            return [{"error": "Tenants directory not found", "path": str(tenants_dir)}]

        if slug:
            # Backup a single tenant
            tenant_db = tenants_dir / f"{slug}.db"
            if tenant_db.exists():
                try:
                    dest = Path(output_dir) if output_dir else self._backup_dir / "tenants"
                    result = self.backup_sqlite(tenant_db, dest)
                    result["tenant_slug"] = slug
                    results.append(result)
                except Exception as exc:
                    results.append({
                        "tenant_slug": slug,
                        "error": str(exc),
                    })
            else:
                results.append({
                    "tenant_slug": slug,
                    "error": f"Tenant database not found: {tenant_db}",
                })
        else:
            # Backup all tenants
            for tenant_file in sorted(tenants_dir.glob("*.db")):
                tenant_slug = tenant_file.stem
                try:
                    dest = Path(output_dir) if output_dir else self._backup_dir / "tenants"
                    result = self.backup_sqlite(tenant_file, dest)
                    result["tenant_slug"] = tenant_slug
                    results.append(result)
                except Exception as exc:
                    results.append({
                        "tenant_slug": tenant_slug,
                        "error": str(exc),
                    })

        return results

    # ------------------------------------------------------------------
    # Listing and pruning
    # ------------------------------------------------------------------

    def list_backups(self, backup_dir: Path = None) -> list:
        """Scan for .meta.json files and return a list of backup records.

        Args:
            backup_dir: Directory to scan. Defaults to the configured
                backup directory (searched recursively).

        Returns:
            List of metadata dicts sorted by created_at descending.
        """
        scan_dir = Path(backup_dir) if backup_dir else self._backup_dir
        if not scan_dir.exists():
            return []

        records = []
        for meta_file in scan_dir.rglob("*.meta.json"):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                meta["meta_path"] = str(meta_file)
                records.append(meta)
            except (json.JSONDecodeError, OSError):
                continue

        # Sort by created_at descending
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return records

    def prune_old_backups(
        self, backup_dir: Path = None, retention_days: int = None
    ) -> dict:
        """Remove backups older than the retention period.

        Args:
            backup_dir: Directory to prune. Defaults to configured backup dir.
            retention_days: Days to retain. Defaults to configured value.

        Returns:
            dict with pruned count and details.
        """
        scan_dir = Path(backup_dir) if backup_dir else self._backup_dir
        days = retention_days if retention_days is not None else self._retention_days

        if not scan_dir.exists():
            return {"pruned": 0, "errors": [], "message": "Backup directory not found"}

        now = datetime.now(timezone.utc)
        pruned = []
        errors = []

        for meta_file in scan_dir.rglob("*.meta.json"):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)

                created_str = meta.get("created_at", "")
                if not created_str:
                    continue

                # Parse ISO 8601 timestamp
                created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                age_days = (now - created_at).days

                if age_days > days:
                    # Remove backup file
                    backup_file = Path(meta.get("backup_path", ""))
                    if backup_file.exists():
                        backup_file.unlink()

                    # Remove SHA-256 sidecar
                    sha_file = backup_file.parent / (backup_file.name + ".sha256")
                    if sha_file.exists():
                        sha_file.unlink()

                    # Remove encrypted file if present
                    enc_path_str = meta.get("encrypted_path")
                    if enc_path_str:
                        enc_file = Path(enc_path_str)
                        if enc_file.exists():
                            enc_file.unlink()

                    # Remove meta file
                    meta_file.unlink()

                    pruned.append({
                        "db_name": meta.get("db_name"),
                        "backup_path": meta.get("backup_path"),
                        "age_days": age_days,
                    })

            except Exception as exc:
                errors.append({
                    "meta_file": str(meta_file),
                    "error": str(exc),
                })

        _log_audit(
            "config_changed",
            f"Pruned {len(pruned)} old backups (retention: {days} days)",
            {"pruned_count": len(pruned), "retention_days": days},
        )

        return {
            "pruned": len(pruned),
            "retention_days": days,
            "pruned_backups": pruned,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Convenience: resolve named database
    # ------------------------------------------------------------------

    def resolve_db_path(self, db_name: str) -> Path:
        """Resolve a configured database name to its absolute path.

        Args:
            db_name: Key from the databases config (e.g. ``icdev``, ``memory``).

        Returns:
            Absolute Path to the database file.

        Raises:
            ValueError: If the database name is not configured.
        """
        db_info = self._databases.get(db_name)
        if not db_info:
            raise ValueError(
                f"Unknown database '{db_name}'. "
                f"Configured: {list(self._databases.keys())}"
            )
        return BASE_DIR / db_info["path"]
