#!/usr/bin/env python3
# CUI // SP-CTI
"""LocalSyncProvider — local filesystem sync backend (stdlib only, air-gap safe).

D-SYNC-1: Always available as fallback. Zero external dependencies.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional

from tools.filesync.providers.base import SyncTargetProvider


class LocalSyncProvider(SyncTargetProvider):
    """Local filesystem sync provider."""

    @property
    def provider_name(self) -> str:
        return "local"

    def list_files(self, base_path: str) -> List[Dict]:
        """Walk directory tree, return file metadata."""
        base = Path(base_path)
        if not base.is_dir():
            return []
        results = []
        for root, _dirs, files in os.walk(base):
            for fname in files:
                full = Path(root) / fname
                try:
                    stat = full.stat()
                    rel = full.relative_to(base).as_posix()
                    results.append(
                        {
                            "relative_path": rel,
                            "size": stat.st_size,
                            "mtime_epoch": stat.st_mtime,
                        }
                    )
                except (OSError, ValueError):
                    continue
        return results

    def read_file(self, base_path: str, relative_path: str) -> Optional[bytes]:
        """Read file from local filesystem."""
        fpath = Path(base_path) / relative_path
        if not fpath.is_file():
            return None
        try:
            return fpath.read_bytes()
        except OSError:
            return None

    def write_file(self, base_path: str, relative_path: str, data: bytes) -> bool:
        """Write file to local filesystem, creating parent dirs."""
        fpath = Path(base_path) / relative_path
        try:
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_bytes(data)
            return True
        except OSError:
            return False

    def delete_file(self, base_path: str, relative_path: str) -> bool:
        """Delete a file from local filesystem."""
        fpath = Path(base_path) / relative_path
        try:
            fpath.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def get_file_info(self, base_path: str, relative_path: str) -> Optional[Dict]:
        """Get file size and mtime."""
        fpath = Path(base_path) / relative_path
        if not fpath.is_file():
            return None
        try:
            stat = fpath.stat()
            return {"size": stat.st_size, "mtime_epoch": stat.st_mtime}
        except OSError:
            return None

    def check_availability(self) -> bool:
        """Local filesystem is always available."""
        return True

    def rename_file(self, base_path: str, old_path: str, new_path: str) -> bool:
        """Native rename via os.rename (faster than read+write+delete)."""
        src = Path(base_path) / old_path
        dst = Path(base_path) / new_path
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return True
        except OSError:
            return False

    def mkdir(self, base_path: str, relative_dir: str) -> bool:
        """Create directory on local filesystem."""
        dpath = Path(base_path) / relative_dir
        try:
            dpath.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False
