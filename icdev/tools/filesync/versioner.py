#!/usr/bin/env python3
# CUI // SP-CTI
"""File version control — snapshot previous versions before overwrite (D-SYNC-16).

Keeps N previous versions of each file in a .versions/ directory alongside the
sync destination. Version metadata tracked in sync_file_versions DB table.

Pattern: tools/mbse/sync_engine.py for SHA-256 content hashing.
"""

import hashlib
import os
import shutil
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from typing import Dict, List, Optional


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id():
    return f"fver-{uuid.uuid4().hex[:12]}"


def _ensure_versions_table(conn):
    """Create sync_file_versions table if missing."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_file_versions (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            version_number INTEGER NOT NULL DEFAULT 1,
            content_hash TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            version_path TEXT,
            action TEXT NOT NULL DEFAULT 'auto',
            created_by TEXT DEFAULT 'filesync-engine',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES sync_jobs(id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sfv_job_path
        ON sync_file_versions (job_id, relative_path, version_number)
    """)
    conn.commit()


class FileVersioner:
    """Manages file version snapshots for sync jobs.

    Before a file is overwritten during sync, the current version is copied
    to a .versions/ directory with a version number suffix. Metadata is
    stored in sync_file_versions for browsing and restore.

    Args:
        db_path: Path to icdev.db.
        max_versions: Maximum versions to keep per file (0 = unlimited).
        versions_dir_name: Name of the versions subdirectory (default: .versions).
    """

    def __init__(self, db_path: str, max_versions: int = 10, versions_dir_name: str = ".versions"):
        self._db_path = str(db_path)
        self._max_versions = max_versions
        self._versions_dir = versions_dir_name
        self._initialized = False

    def _get_db(self):
        conn = get_connection(db_path=str(self._db_path))
        if not self._initialized:
            _ensure_versions_table(conn)
            self._initialized = True
        return conn

    def snapshot_before_overwrite(self, job_id: str, dest_base: str, relative_path: str) -> Optional[Dict]:
        """Create a versioned snapshot of a file before it's overwritten.

        Args:
            job_id: Sync job ID.
            dest_base: Destination root path.
            relative_path: File path relative to dest_base.

        Returns:
            Version info dict, or None if file doesn't exist (new file).
        """
        full_path = os.path.join(dest_base, relative_path)
        if not os.path.isfile(full_path):
            return None  # New file, nothing to version

        # Read current file
        try:
            with open(full_path, "rb") as f:
                data = f.read()
        except (OSError, IOError):
            return None

        content_hash = hashlib.sha256(data).hexdigest()
        file_size = len(data)

        # Get next version number
        conn = self._get_db()
        try:
            row = conn.execute(
                """SELECT MAX(version_number) as max_ver FROM sync_file_versions
                   WHERE job_id=%s AND relative_path=%s""",
                (job_id, relative_path),
            ).fetchone()
            next_ver = (row["max_ver"] or 0) + 1

            # Check if this exact hash already versioned (skip duplicate)
            existing = conn.execute(
                """SELECT id FROM sync_file_versions
                   WHERE job_id=%s AND relative_path=%s AND content_hash=%s
                   ORDER BY version_number DESC LIMIT 1""",
                (job_id, relative_path, content_hash),
            ).fetchone()
            if existing:
                return None  # Same content, no new version needed
        finally:
            conn.close()

        # Copy file to .versions/ directory
        ver_dir = os.path.join(dest_base, self._versions_dir, relative_path)
        os.makedirs(
            os.path.dirname(ver_dir)
            if "/" in relative_path or "\\" in relative_path
            else os.path.join(dest_base, self._versions_dir),
            exist_ok=True,
        )

        # Version filename: original.ext -> original.ext.v3
        ver_filename = f"{relative_path}.v{next_ver}"
        ver_path = os.path.join(dest_base, self._versions_dir, ver_filename)
        os.makedirs(os.path.dirname(ver_path), exist_ok=True)

        try:
            shutil.copy2(full_path, ver_path)
        except (OSError, IOError) as e:
            return {"error": str(e)}

        # Record in DB
        ver_id = _gen_id()
        conn = self._get_db()
        try:
            conn.execute(
                """INSERT INTO sync_file_versions
                   (id, job_id, relative_path, version_number, content_hash,
                    file_size, version_path, action, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'auto', %s)""",
                (ver_id, job_id, relative_path, next_ver, content_hash, file_size, ver_path, _now()),
            )
            conn.commit()

            # Prune old versions if max_versions > 0
            if self._max_versions > 0:
                self._prune_versions(conn, job_id, relative_path)
        finally:
            conn.close()

        return {
            "id": ver_id,
            "job_id": job_id,
            "relative_path": relative_path,
            "version_number": next_ver,
            "content_hash": content_hash,
            "file_size": file_size,
            "version_path": ver_path,
        }

    def _prune_versions(self, conn, job_id: str, relative_path: str):
        """Remove oldest versions beyond max_versions limit."""
        rows = conn.execute(
            """SELECT id, version_path, version_number FROM sync_file_versions
               WHERE job_id=%s AND relative_path=%s
               ORDER BY version_number DESC""",
            (job_id, relative_path),
        ).fetchall()

        if len(rows) <= self._max_versions:
            return

        to_remove = rows[self._max_versions :]
        for row in to_remove:
            # Delete version file
            vpath = row["version_path"]
            if vpath and os.path.exists(vpath):
                try:
                    os.remove(vpath)
                except OSError:
                    pass
            # Delete DB record
            conn.execute("DELETE FROM sync_file_versions WHERE id=%s", (row["id"],))
        conn.commit()

    def list_versions(self, job_id: str, relative_path: str = None, limit: int = 50) -> List[Dict]:
        """List file versions for a job.

        Args:
            job_id: Sync job ID.
            relative_path: Optional filter by file path.
            limit: Max results.

        Returns:
            List of version info dicts.
        """
        conn = self._get_db()
        try:
            if relative_path:
                rows = conn.execute(
                    """SELECT * FROM sync_file_versions
                       WHERE job_id=%s AND relative_path=%s
                       ORDER BY version_number DESC LIMIT %s""",
                    (job_id, relative_path, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM sync_file_versions
                       WHERE job_id=%s
                       ORDER BY created_at DESC LIMIT %s""",
                    (job_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def restore_version(self, version_id: str, dest_base: str) -> Dict:
        """Restore a specific version of a file.

        Args:
            version_id: Version record ID.
            dest_base: Destination root path.

        Returns:
            Restore result dict.
        """
        conn = self._get_db()
        try:
            row = conn.execute("SELECT * FROM sync_file_versions WHERE id=%s", (version_id,)).fetchone()
            if not row:
                return {"status": "error", "error": f"Version not found: {version_id}"}
            ver = dict(row)
        finally:
            conn.close()

        ver_path = ver["version_path"]
        if not ver_path or not os.path.exists(ver_path):
            return {"status": "error", "error": f"Version file missing: {ver_path}"}

        target_path = os.path.join(dest_base, ver["relative_path"])

        # Snapshot the current file before restoring (create a restore-point)
        self.snapshot_before_overwrite(ver["job_id"], dest_base, ver["relative_path"])

        # Copy version file back
        try:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(ver_path, target_path)
        except (OSError, IOError) as e:
            return {"status": "error", "error": str(e)}

        # Record restore event
        conn = self._get_db()
        try:
            restore_id = _gen_id()
            conn.execute(
                """INSERT INTO sync_file_versions
                   (id, job_id, relative_path, version_number, content_hash,
                    file_size, version_path, action, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'restore', %s)""",
                (
                    restore_id,
                    ver["job_id"],
                    ver["relative_path"],
                    ver["version_number"],
                    ver["content_hash"],
                    ver["file_size"],
                    ver_path,
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "restored",
            "version_id": version_id,
            "relative_path": ver["relative_path"],
            "version_number": ver["version_number"],
            "restored_to": target_path,
        }

    def get_version_stats(self, job_id: str) -> Dict:
        """Get version statistics for a job."""
        conn = self._get_db()
        try:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM sync_file_versions WHERE job_id=%s",
                (job_id,),
            ).fetchone()["cnt"]
            files = conn.execute(
                "SELECT COUNT(DISTINCT relative_path) as cnt FROM sync_file_versions WHERE job_id=%s",
                (job_id,),
            ).fetchone()["cnt"]
            total_size = conn.execute(
                "SELECT COALESCE(SUM(file_size), 0) as s FROM sync_file_versions WHERE job_id=%s",
                (job_id,),
            ).fetchone()["s"]
        finally:
            conn.close()
        return {
            "job_id": job_id,
            "total_versions": total,
            "versioned_files": files,
            "total_version_size_bytes": total_size,
        }
