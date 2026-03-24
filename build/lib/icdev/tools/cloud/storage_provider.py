#!/usr/bin/env python3
# CUI // SP-CTI
"""Storage Provider — cloud-agnostic object storage.

ABC + 6 implementations: AWS S3, Azure Blob, GCS, OCI Object Storage, IBM Cloud Object Storage, Local filesystem.
Pattern: tools/llm/provider.py (D66 provider ABC).
"""

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional


class StorageProvider(ABC):
    """Abstract base class for object storage."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier."""

    @abstractmethod
    def upload(self, bucket: str, key: str, data: bytes) -> bool:
        """Upload data to storage."""

    @abstractmethod
    def download(self, bucket: str, key: str) -> Optional[bytes]:
        """Download data from storage."""

    @abstractmethod
    def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        """List object keys in a bucket/prefix."""

    @abstractmethod
    def delete(self, bucket: str, key: str) -> bool:
        """Delete an object from storage."""

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if storage provider is available."""


# ============================================================
# AWS S3
# ============================================================
try:
    import boto3 as _boto3_s3
    _HAS_BOTO3_S3 = True
except ImportError:
    _HAS_BOTO3_S3 = False


class AWSS3Provider(StorageProvider):
    """AWS S3 / S3 GovCloud implementation."""

    def __init__(self, region: str = "us-gov-west-1"):
        self._region = region
        self._client = None

    @property
    def provider_name(self) -> str:
        return "aws_s3"

    def _get_client(self):
        if self._client is None and _HAS_BOTO3_S3:
            self._client = _boto3_s3.client("s3", region_name=self._region)
        return self._client

    def upload(self, bucket: str, key: str, data: bytes) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.put_object(Bucket=bucket, Key=key, Body=data)
            return True
        except Exception:
            return False

    def download(self, bucket: str, key: str) -> Optional[bytes]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.get_object(Bucket=bucket, Key=key)
            return resp["Body"].read()
        except Exception:
            return None

    def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        client = self._get_client()
        if not client:
            return []
        try:
            resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
            return [obj["Key"] for obj in resp.get("Contents", [])]
        except Exception:
            return []

    def delete(self, bucket: str, key: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.delete_object(Bucket=bucket, Key=key)
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        if not _HAS_BOTO3_S3:
            return False
        try:
            client = self._get_client()
            client.list_buckets()
            return True
        except Exception:
            return False


# ============================================================
# Azure Blob Storage
# ============================================================
try:
    from azure.storage.blob import BlobServiceClient
    from azure.identity import DefaultAzureCredential as _AzureCredBlob
    _HAS_AZURE_BLOB = True
except ImportError:
    _HAS_AZURE_BLOB = False


class AzureBlobProvider(StorageProvider):
    """Azure Blob Storage implementation."""

    def __init__(self, account_url: str = ""):
        self._account_url = account_url or os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "azure_blob"

    def _get_client(self):
        if self._client is None and _HAS_AZURE_BLOB and self._account_url:
            credential = _AzureCredBlob()
            self._client = BlobServiceClient(account_url=self._account_url, credential=credential)
        return self._client

    def upload(self, bucket: str, key: str, data: bytes) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            blob = client.get_blob_client(container=bucket, blob=key)
            blob.upload_blob(data, overwrite=True)
            return True
        except Exception:
            return False

    def download(self, bucket: str, key: str) -> Optional[bytes]:
        client = self._get_client()
        if not client:
            return None
        try:
            blob = client.get_blob_client(container=bucket, blob=key)
            return blob.download_blob().readall()
        except Exception:
            return None

    def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        client = self._get_client()
        if not client:
            return []
        try:
            container = client.get_container_client(bucket)
            return [b.name for b in container.list_blobs(name_starts_with=prefix)]
        except Exception:
            return []

    def delete(self, bucket: str, key: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            blob = client.get_blob_client(container=bucket, blob=key)
            blob.delete_blob()
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        return _HAS_AZURE_BLOB and bool(self._account_url)


# ============================================================
# GCP Cloud Storage
# ============================================================
try:
    from google.cloud import storage as _gcs
    _HAS_GCS = True
except ImportError:
    _HAS_GCS = False


class GCSProvider(StorageProvider):
    """Google Cloud Storage implementation."""

    def __init__(self, project_id: str = ""):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "gcp_gcs"

    def _get_client(self):
        if self._client is None and _HAS_GCS:
            self._client = _gcs.Client(project=self._project_id)
        return self._client

    def upload(self, bucket: str, key: str, data: bytes) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            b = client.bucket(bucket)
            blob = b.blob(key)
            blob.upload_from_string(data)
            return True
        except Exception:
            return False

    def download(self, bucket: str, key: str) -> Optional[bytes]:
        client = self._get_client()
        if not client:
            return None
        try:
            b = client.bucket(bucket)
            blob = b.blob(key)
            return blob.download_as_bytes()
        except Exception:
            return None

    def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        client = self._get_client()
        if not client:
            return []
        try:
            b = client.bucket(bucket)
            return [blob.name for blob in b.list_blobs(prefix=prefix, max_results=1000)]
        except Exception:
            return []

    def delete(self, bucket: str, key: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            b = client.bucket(bucket)
            blob = b.blob(key)
            blob.delete()
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        return _HAS_GCS and bool(self._project_id)


# ============================================================
# OCI Object Storage
# ============================================================
try:
    import oci as _oci_storage
    _HAS_OCI_STORAGE = True
except ImportError:
    _HAS_OCI_STORAGE = False


class OCIObjectStorageProvider(StorageProvider):
    """Oracle Cloud Infrastructure Object Storage implementation."""

    def __init__(self, namespace: str = "", compartment_id: str = ""):
        self._namespace = namespace or os.environ.get("OCI_NAMESPACE", "")
        self._compartment_id = compartment_id or os.environ.get("OCI_COMPARTMENT_OCID", "")

    @property
    def provider_name(self) -> str:
        return "oci_object_storage"

    def upload(self, bucket: str, key: str, data: bytes) -> bool:
        return False  # Requires full OCI config

    def download(self, bucket: str, key: str) -> Optional[bytes]:
        return None

    def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        return []

    def delete(self, bucket: str, key: str) -> bool:
        return False

    def check_availability(self) -> bool:
        return _HAS_OCI_STORAGE and bool(self._namespace)


# ============================================================
# IBM Cloud Object Storage (D237)
# ============================================================
try:
    import ibm_boto3
    from ibm_botocore.client import Config as _IBMBotoConfig
    _HAS_IBM_COS = True
except ImportError:
    _HAS_IBM_COS = False


class IBMStorageProvider(StorageProvider):
    """IBM Cloud Object Storage (S3-compatible) implementation (D237)."""

    def __init__(self, api_key: str = "", instance_id: str = "",
                 region: str = "us-south"):
        self._api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY", "")
        self._instance_id = instance_id or os.environ.get("IBM_COS_INSTANCE_ID", "")
        self._region = region
        self._client = None

    @property
    def provider_name(self) -> str:
        return "ibm_cos"

    def _get_client(self):
        if self._client is None and _HAS_IBM_COS and self._api_key:
            self._client = ibm_boto3.client(
                "s3",
                ibm_api_key_id=self._api_key,
                ibm_service_instance_id=self._instance_id,
                config=_IBMBotoConfig(signature_version="oauth"),
                endpoint_url=f"https://s3.{self._region}.cloud-object-storage.appdomain.cloud",
            )
        return self._client

    def upload(self, bucket: str, key: str, data: bytes) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.put_object(Bucket=bucket, Key=key, Body=data)
            return True
        except Exception:
            return False

    def download(self, bucket: str, key: str) -> Optional[bytes]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.get_object(Bucket=bucket, Key=key)
            return resp["Body"].read()
        except Exception:
            return None

    def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        client = self._get_client()
        if not client:
            return []
        try:
            resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
            return [o["Key"] for o in resp.get("Contents", [])]
        except Exception:
            return []

    def delete(self, bucket: str, key: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.delete_object(Bucket=bucket, Key=key)
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        return _HAS_IBM_COS and bool(self._api_key) and bool(self._instance_id)


# ============================================================
# Local Filesystem — stdlib only, air-gap safe (D224)
# ============================================================
class LocalStorageProvider(StorageProvider):
    """Local filesystem storage provider (stdlib only, air-gap safe)."""

    def __init__(self, base_dir: Optional[str] = None):
        root = Path(__file__).resolve().parent.parent.parent
        self._base = Path(base_dir) if base_dir else root / "data" / "storage"

    @property
    def provider_name(self) -> str:
        return "local"

    def upload(self, bucket: str, key: str, data: bytes) -> bool:
        try:
            path = self._base / bucket / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return True
        except Exception:
            return False

    def download(self, bucket: str, key: str) -> Optional[bytes]:
        try:
            path = self._base / bucket / key
            if path.exists():
                return path.read_bytes()
            return None
        except Exception:
            return None

    def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        try:
            bucket_path = self._base / bucket
            if not bucket_path.exists():
                return []
            results = []
            for p in bucket_path.rglob("*"):
                if p.is_file():
                    rel = str(p.relative_to(bucket_path)).replace("\\", "/")
                    if rel.startswith(prefix):
                        results.append(rel)
            return results
        except Exception:
            return []

    def delete(self, bucket: str, key: str) -> bool:
        try:
            path = self._base / bucket / key
            if path.exists():
                path.unlink()
                return True
            return False
        except Exception:
            return False

    def check_availability(self) -> bool:
        return True  # Local always available
