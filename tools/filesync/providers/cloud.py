#!/usr/bin/env python3
# CUI // SP-CTI
"""Cloud sync provider — wraps existing StorageProvider ABC (D-SYNC-1).

Pattern: tools/cloud/storage_provider.py (D66 provider ABC).
Supports: AWS S3, Azure Blob, GCP, OCI, IBM COS, Local via existing providers.
"""

import time
from typing import Dict, List, Optional

from tools.filesync.providers.base import SyncTargetProvider

# Graceful import of cloud storage providers
try:
    from tools.cloud.storage_provider import (
        AWSS3Provider,
        AzureBlobProvider,
        GCSProvider,
        IBMStorageProvider,
        LocalStorageProvider,
        OCIObjectStorageProvider,
    )

    _HAS_CLOUD = True
except ImportError:
    _HAS_CLOUD = False


# Map provider names to classes
_PROVIDER_MAP = {}
if _HAS_CLOUD:
    _PROVIDER_MAP = {
        "s3": AWSS3Provider,
        "aws_s3": AWSS3Provider,
        "azure_blob": AzureBlobProvider,
        "azure": AzureBlobProvider,
        "gcs": GCSProvider,
        "gcp": GCSProvider,
        "oci": OCIObjectStorageProvider,
        "ibm": IBMStorageProvider,
        "ibm_cos": IBMStorageProvider,
        "local_storage": LocalStorageProvider,
    }


class CloudSyncProvider(SyncTargetProvider):
    """Cloud file sync provider wrapping existing StorageProvider.

    The cloud provider treats:
      - base_path as the bucket/container name
      - relative_path as the object key within the bucket

    Cloud storage is flat (no real directories), so list_files returns
    all objects with the given prefix. mtime is approximated from
    list/head metadata when available.
    """

    def __init__(self, cloud_provider: str = "s3", region: str = "", provider_instance=None, **kwargs):
        """Initialize cloud sync provider.

        Args:
            cloud_provider: Provider name ('s3', 'azure_blob', 'gcs', 'oci', 'ibm').
            region: Cloud region (passed to underlying provider).
            provider_instance: Pre-built StorageProvider instance (overrides cloud_provider).
            **kwargs: Additional kwargs passed to underlying provider constructor.
        """
        self._cloud_provider_name = cloud_provider
        self._region = region

        if provider_instance is not None:
            self._provider = provider_instance
        elif _HAS_CLOUD and cloud_provider in _PROVIDER_MAP:
            ctor_kwargs = {}
            if region:
                ctor_kwargs["region"] = region
            ctor_kwargs.update(kwargs)
            try:
                self._provider = _PROVIDER_MAP[cloud_provider](**ctor_kwargs)
            except Exception:
                self._provider = None
        else:
            self._provider = None

    @property
    def provider_name(self) -> str:
        return f"cloud_{self._cloud_provider_name}"

    def list_files(self, base_path: str) -> List[Dict]:
        """List objects in bucket. base_path = bucket name.

        Returns list of {relative_path, size, mtime_epoch}.
        Cloud storage doesn't always provide size/mtime in list operations,
        so we return what's available and use 0 as defaults.
        """
        if not self._provider:
            return []
        try:
            keys = self._provider.list_objects(base_path, prefix="")
            files = []
            for key in keys:
                if not key or key.endswith("/"):
                    continue  # Skip directory markers
                files.append(
                    {
                        "relative_path": key.replace("\\", "/"),
                        "size": 0,  # Cloud list typically doesn't include size
                        "mtime_epoch": 0.0,
                    }
                )
            return files
        except Exception:
            return []

    def read_file(self, base_path: str, relative_path: str) -> Optional[bytes]:
        """Download object from cloud storage. base_path = bucket."""
        if not self._provider:
            return None
        try:
            return self._provider.download(base_path, relative_path)
        except Exception:
            return None

    def write_file(self, base_path: str, relative_path: str, data: bytes) -> bool:
        """Upload object to cloud storage. base_path = bucket."""
        if not self._provider:
            return False
        try:
            return self._provider.upload(base_path, relative_path, data)
        except Exception:
            return False

    def delete_file(self, base_path: str, relative_path: str) -> bool:
        """Delete object from cloud storage."""
        if not self._provider:
            return False
        try:
            return self._provider.delete(base_path, relative_path)
        except Exception:
            return False

    def get_file_info(self, base_path: str, relative_path: str) -> Optional[Dict]:
        """Get object info. Downloads the object to determine size.

        Cloud providers don't expose HEAD-like operations via the existing ABC,
        so we do a download to check existence and get size. For large files,
        consider using provider-specific HEAD operations.
        """
        if not self._provider:
            return None
        try:
            data = self._provider.download(base_path, relative_path)
            if data is None:
                return None
            return {
                "size": len(data),
                "mtime_epoch": time.time(),  # Cloud doesn't expose mtime via ABC
            }
        except Exception:
            return None

    def check_availability(self) -> bool:
        """Check if cloud provider is available."""
        if not self._provider:
            return False
        try:
            return self._provider.check_availability()
        except Exception:
            return False

    # rename_file: use default read+write+delete from base class (no native cloud rename)
    # mkdir: no-op for cloud storage (flat namespace)
