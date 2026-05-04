#!/usr/bin/env python3
# CUI // SP-CTI
"""SyncTargetProvider ABC — abstract interface for sync backends (D-SYNC-1).

Pattern: tools/cloud/storage_provider.py (D66 provider ABC).
Implementations: LocalSyncProvider, SFTPSyncProvider, CloudSyncProvider.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class SyncTargetProvider(ABC):
    """Abstract base class for file sync target backends."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier (e.g., 'local', 'sftp', 's3')."""

    @abstractmethod
    def list_files(self, base_path: str) -> List[Dict]:
        """List files recursively under base_path.

        Returns list of dicts: {relative_path, size, mtime_epoch}.
        Directories are not included — only files.
        Paths use forward slashes regardless of OS.
        """

    @abstractmethod
    def read_file(self, base_path: str, relative_path: str) -> Optional[bytes]:
        """Read file contents. Returns None if file does not exist."""

    @abstractmethod
    def write_file(self, base_path: str, relative_path: str, data: bytes) -> bool:
        """Write file contents. Creates parent directories if needed. Returns True on success."""

    @abstractmethod
    def delete_file(self, base_path: str, relative_path: str) -> bool:
        """Delete a file. Returns True on success, False if not found or error."""

    @abstractmethod
    def get_file_info(self, base_path: str, relative_path: str) -> Optional[Dict]:
        """Return {size, mtime_epoch} for a file, or None if it doesn't exist."""

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if the provider is available and ready."""

    def rename_file(self, base_path: str, old_path: str, new_path: str) -> bool:
        """Rename a file. Default: read + write + delete (override for native rename)."""
        data = self.read_file(base_path, old_path)
        if data is None:
            return False
        if not self.write_file(base_path, new_path, data):
            return False
        return self.delete_file(base_path, old_path)

    def mkdir(self, base_path: str, relative_dir: str) -> bool:
        """Create a directory. Default: no-op (providers that need it should override)."""
        return True
