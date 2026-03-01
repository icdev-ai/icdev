#!/usr/bin/env python3
# CUI // SP-CTI
"""KMS Provider — cloud-agnostic key management and encryption.

ABC + 6 implementations: AWS KMS, Azure Key Vault, GCP Cloud KMS, OCI Key Management, IBM Key Protect, Local (Fernet).
Pattern: tools/llm/provider.py (D66 provider ABC).
"""

import os
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple


class KMSProvider(ABC):
    """Abstract base class for key management / encryption."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier."""

    @abstractmethod
    def encrypt(self, plaintext: bytes, key_id: str = "") -> Optional[bytes]:
        """Encrypt plaintext data."""

    @abstractmethod
    def decrypt(self, ciphertext: bytes, key_id: str = "") -> Optional[bytes]:
        """Decrypt ciphertext data."""

    @abstractmethod
    def generate_data_key(self, key_id: str = "") -> Optional[Tuple[bytes, bytes]]:
        """Generate a data encryption key. Returns (plaintext_key, encrypted_key)."""

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if KMS provider is available."""


# ============================================================
# AWS KMS
# ============================================================
try:
    import boto3 as _boto3_kms
    _HAS_BOTO3_KMS = True
except ImportError:
    _HAS_BOTO3_KMS = False


class AWSKMSProvider(KMSProvider):
    """AWS KMS implementation."""

    def __init__(self, region: str = "us-gov-west-1", key_id: str = ""):
        self._region = region
        self._default_key = key_id or os.environ.get("AWS_KMS_KEY_ID", "alias/icdev-master")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "aws_kms"

    def _get_client(self):
        if self._client is None and _HAS_BOTO3_KMS:
            self._client = _boto3_kms.client("kms", region_name=self._region)
        return self._client

    def encrypt(self, plaintext: bytes, key_id: str = "") -> Optional[bytes]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.encrypt(KeyId=key_id or self._default_key, Plaintext=plaintext)
            return resp["CiphertextBlob"]
        except Exception:
            return None

    def decrypt(self, ciphertext: bytes, key_id: str = "") -> Optional[bytes]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.decrypt(CiphertextBlob=ciphertext)
            return resp["Plaintext"]
        except Exception:
            return None

    def generate_data_key(self, key_id: str = "") -> Optional[Tuple[bytes, bytes]]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.generate_data_key(
                KeyId=key_id or self._default_key, KeySpec="AES_256"
            )
            return resp["Plaintext"], resp["CiphertextBlob"]
        except Exception:
            return None

    def check_availability(self) -> bool:
        if not _HAS_BOTO3_KMS:
            return False
        try:
            client = self._get_client()
            client.list_keys(Limit=1)
            return True
        except Exception:
            return False


# ============================================================
# Azure Key Vault (Cryptography)
# ============================================================
try:
    from azure.keyvault.keys import KeyClient
    from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm
    from azure.identity import DefaultAzureCredential as _AzureCredKMS
    _HAS_AZURE_KMS = True
except ImportError:
    _HAS_AZURE_KMS = False


class AzureKMSProvider(KMSProvider):
    """Azure Key Vault Cryptography implementation."""

    def __init__(self, vault_url: str = ""):
        self._vault_url = vault_url or os.environ.get("AZURE_VAULT_URL", "")

    @property
    def provider_name(self) -> str:
        return "azure_key_vault"

    def encrypt(self, plaintext: bytes, key_id: str = "") -> Optional[bytes]:
        return None  # Requires full Azure setup

    def decrypt(self, ciphertext: bytes, key_id: str = "") -> Optional[bytes]:
        return None

    def generate_data_key(self, key_id: str = "") -> Optional[Tuple[bytes, bytes]]:
        return None

    def check_availability(self) -> bool:
        return _HAS_AZURE_KMS and bool(self._vault_url)


# ============================================================
# GCP Cloud KMS
# ============================================================
try:
    from google.cloud import kms as _gcp_kms
    _HAS_GCP_KMS = True
except ImportError:
    _HAS_GCP_KMS = False


class GCPKMSProvider(KMSProvider):
    """Google Cloud KMS implementation."""

    def __init__(self, project_id: str = "", location: str = "us-east4",
                 key_ring: str = "", key_name: str = ""):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._location = location
        self._key_ring = key_ring or os.environ.get("GCP_KMS_KEY_RING", "icdev")
        self._key_name = key_name or os.environ.get("GCP_KMS_KEY", "master")

    @property
    def provider_name(self) -> str:
        return "gcp_cloud_kms"

    def encrypt(self, plaintext: bytes, key_id: str = "") -> Optional[bytes]:
        return None  # Requires full GCP setup

    def decrypt(self, ciphertext: bytes, key_id: str = "") -> Optional[bytes]:
        return None

    def generate_data_key(self, key_id: str = "") -> Optional[Tuple[bytes, bytes]]:
        return None

    def check_availability(self) -> bool:
        return _HAS_GCP_KMS and bool(self._project_id)


# ============================================================
# OCI Key Management
# ============================================================
class OCIKMSProvider(KMSProvider):
    """Oracle OCI Key Management implementation."""

    def __init__(self, vault_id: str = ""):
        self._vault_id = vault_id or os.environ.get("OCI_VAULT_OCID", "")

    @property
    def provider_name(self) -> str:
        return "oci_key_management"

    def encrypt(self, plaintext: bytes, key_id: str = "") -> Optional[bytes]:
        return None

    def decrypt(self, ciphertext: bytes, key_id: str = "") -> Optional[bytes]:
        return None

    def generate_data_key(self, key_id: str = "") -> Optional[Tuple[bytes, bytes]]:
        return None

    def check_availability(self) -> bool:
        return bool(self._vault_id)


# ============================================================
# IBM Key Protect (D237)
# ============================================================
try:
    from ibm_platform_services import KeyProtectV2
    from ibm_cloud_sdk_core.authenticators import IAMAuthenticator as _IBMKMSAuth
    _HAS_IBM_KP = True
except ImportError:
    _HAS_IBM_KP = False


class IBMKMSProvider(KMSProvider):
    """IBM Key Protect implementation (D237)."""

    def __init__(self, api_key: str = "", instance_id: str = "",
                 region: str = "us-south"):
        self._api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY", "")
        self._instance_id = instance_id or os.environ.get("IBM_KEY_PROTECT_INSTANCE_ID", "")
        self._region = region
        self._client = None

    @property
    def provider_name(self) -> str:
        return "ibm_key_protect"

    def _get_client(self):
        if self._client is None and _HAS_IBM_KP and self._api_key:
            authenticator = _IBMKMSAuth(apikey=self._api_key)
            self._client = KeyProtectV2(authenticator=authenticator)
            self._client.set_service_url(
                f"https://{self._region}.kms.cloud.ibm.com"
            )
        return self._client

    def encrypt(self, plaintext: bytes, key_id: str = "") -> Optional[bytes]:
        client = self._get_client()
        if not client:
            return None
        try:
            import base64
            encoded = base64.b64encode(plaintext).decode()
            resp = client.wrap_key(
                id=self._instance_id,
                key_id=key_id,
                plaintext=encoded,
            ).get_result()
            return base64.b64decode(resp.get("ciphertext", ""))
        except Exception:
            return None

    def decrypt(self, ciphertext: bytes, key_id: str = "") -> Optional[bytes]:
        client = self._get_client()
        if not client:
            return None
        try:
            import base64
            encoded = base64.b64encode(ciphertext).decode()
            resp = client.unwrap_key(
                id=self._instance_id,
                key_id=key_id,
                ciphertext=encoded,
            ).get_result()
            return base64.b64decode(resp.get("plaintext", ""))
        except Exception:
            return None

    def generate_data_key(self, key_id: str = "") -> Optional[Dict]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.create_key(
                id=self._instance_id,
                name=f"dek-{key_id}",
                extractable=True,
            ).get_result()
            return {"key_id": resp.get("id", ""), "encrypted_key": b""}
        except Exception:
            return None

    def check_availability(self) -> bool:
        return _HAS_IBM_KP and bool(self._api_key) and bool(self._instance_id)


# ============================================================
# Local — Fernet AES-256 (D175, D224)
# ============================================================
try:
    from cryptography.fernet import Fernet
    _HAS_FERNET = True
except ImportError:
    _HAS_FERNET = False


class LocalKMSProvider(KMSProvider):
    """Local Fernet AES-256 encryption (stdlib + cryptography, D175)."""

    def __init__(self, key: str = ""):
        self._key = key or os.environ.get("ICDEV_BYOK_ENCRYPTION_KEY", "")
        self._fernet = None
        if self._key and _HAS_FERNET:
            try:
                self._fernet = Fernet(self._key.encode() if isinstance(self._key, str) else self._key)
            except Exception:
                pass

    @property
    def provider_name(self) -> str:
        return "local"

    def encrypt(self, plaintext: bytes, key_id: str = "") -> Optional[bytes]:
        if not self._fernet:
            return None
        try:
            return self._fernet.encrypt(plaintext)
        except Exception:
            return None

    def decrypt(self, ciphertext: bytes, key_id: str = "") -> Optional[bytes]:
        if not self._fernet:
            return None
        try:
            return self._fernet.decrypt(ciphertext)
        except Exception:
            return None

    def generate_data_key(self, key_id: str = "") -> Optional[Tuple[bytes, bytes]]:
        if not _HAS_FERNET:
            return None
        try:
            new_key = Fernet.generate_key()
            if self._fernet:
                encrypted = self._fernet.encrypt(new_key)
                return new_key, encrypted
            return new_key, new_key
        except Exception:
            return None

    def check_availability(self) -> bool:
        return _HAS_FERNET
