#!/usr/bin/env python3
# CUI // SP-CTI
"""Secrets Provider — cloud-agnostic secrets management.

ABC + 6 implementations: AWS, Azure, GCP, OCI, IBM, Local.
Pattern: tools/llm/provider.py (D66 provider ABC).
Each implementation ~40-60 lines with try/except ImportError.
"""

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional


class SecretsProvider(ABC):
    """Abstract base class for secrets management."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier."""

    @abstractmethod
    def get_secret(self, secret_name: str) -> Optional[str]:
        """Retrieve a secret value by name."""

    @abstractmethod
    def put_secret(self, secret_name: str, secret_value: str) -> bool:
        """Store or update a secret."""

    @abstractmethod
    def list_secrets(self) -> List[str]:
        """List available secret names."""

    @abstractmethod
    def delete_secret(self, secret_name: str) -> bool:
        """Delete a secret by name."""

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if the secrets provider is available."""


# ============================================================
# AWS Secrets Manager
# ============================================================
try:
    import boto3
    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False


class AWSSecretsProvider(SecretsProvider):
    """AWS Secrets Manager implementation."""

    def __init__(self, region: str = "us-gov-west-1"):
        self._region = region
        self._client = None

    @property
    def provider_name(self) -> str:
        return "aws_secrets_manager"

    def _get_client(self):
        if self._client is None and _HAS_BOTO3:
            self._client = boto3.client("secretsmanager", region_name=self._region)
        return self._client

    def get_secret(self, secret_name: str) -> Optional[str]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.get_secret_value(SecretId=secret_name)
            return resp.get("SecretString")
        except Exception:
            return None

    def put_secret(self, secret_name: str, secret_value: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            try:
                client.create_secret(Name=secret_name, SecretString=secret_value)
            except client.exceptions.ResourceExistsException:
                client.update_secret(SecretId=secret_name, SecretString=secret_value)
            return True
        except Exception:
            return False

    def list_secrets(self) -> List[str]:
        client = self._get_client()
        if not client:
            return []
        try:
            resp = client.list_secrets(MaxResults=100)
            return [s["Name"] for s in resp.get("SecretList", [])]
        except Exception:
            return []

    def delete_secret(self, secret_name: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        if not _HAS_BOTO3:
            return False
        try:
            client = self._get_client()
            client.list_secrets(MaxResults=1)
            return True
        except Exception:
            return False


# ============================================================
# Azure Key Vault
# ============================================================
try:
    from azure.keyvault.secrets import SecretClient
    from azure.identity import DefaultAzureCredential
    _HAS_AZURE = True
except ImportError:
    _HAS_AZURE = False


class AzureSecretsProvider(SecretsProvider):
    """Azure Key Vault Secrets implementation."""

    def __init__(self, vault_url: str = ""):
        self._vault_url = vault_url or os.environ.get("AZURE_VAULT_URL", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "azure_key_vault"

    def _get_client(self):
        if self._client is None and _HAS_AZURE and self._vault_url:
            credential = DefaultAzureCredential()
            self._client = SecretClient(vault_url=self._vault_url, credential=credential)
        return self._client

    def get_secret(self, secret_name: str) -> Optional[str]:
        client = self._get_client()
        if not client:
            return None
        try:
            return client.get_secret(secret_name).value
        except Exception:
            return None

    def put_secret(self, secret_name: str, secret_value: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.set_secret(secret_name, secret_value)
            return True
        except Exception:
            return False

    def list_secrets(self) -> List[str]:
        client = self._get_client()
        if not client:
            return []
        try:
            return [s.name for s in client.list_properties_of_secrets()]
        except Exception:
            return []

    def delete_secret(self, secret_name: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.begin_delete_secret(secret_name)
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        if not _HAS_AZURE or not self._vault_url:
            return False
        try:
            client = self._get_client()
            list(client.list_properties_of_secrets())
            return True
        except Exception:
            return False


# ============================================================
# GCP Secret Manager
# ============================================================
try:
    from google.cloud import secretmanager
    _HAS_GCP = True
except ImportError:
    _HAS_GCP = False


class GCPSecretsProvider(SecretsProvider):
    """Google Cloud Secret Manager implementation."""

    def __init__(self, project_id: str = ""):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "gcp_secret_manager"

    def _get_client(self):
        if self._client is None and _HAS_GCP:
            self._client = secretmanager.SecretManagerServiceClient()
        return self._client

    def get_secret(self, secret_name: str) -> Optional[str]:
        client = self._get_client()
        if not client or not self._project_id:
            return None
        try:
            name = f"projects/{self._project_id}/secrets/{secret_name}/versions/latest"
            resp = client.access_secret_version(request={"name": name})
            return resp.payload.data.decode("utf-8")
        except Exception:
            return None

    def put_secret(self, secret_name: str, secret_value: str) -> bool:
        client = self._get_client()
        if not client or not self._project_id:
            return False
        try:
            parent = f"projects/{self._project_id}"
            try:
                client.create_secret(
                    request={"parent": parent, "secret_id": secret_name,
                             "secret": {"replication": {"automatic": {}}}}
                )
            except Exception:
                pass  # Secret may already exist
            name = f"projects/{self._project_id}/secrets/{secret_name}"
            client.add_secret_version(
                request={"parent": name, "payload": {"data": secret_value.encode("utf-8")}}
            )
            return True
        except Exception:
            return False

    def list_secrets(self) -> List[str]:
        client = self._get_client()
        if not client or not self._project_id:
            return []
        try:
            parent = f"projects/{self._project_id}"
            return [s.name.split("/")[-1] for s in client.list_secrets(request={"parent": parent})]
        except Exception:
            return []

    def delete_secret(self, secret_name: str) -> bool:
        client = self._get_client()
        if not client or not self._project_id:
            return False
        try:
            name = f"projects/{self._project_id}/secrets/{secret_name}"
            client.delete_secret(request={"name": name})
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        if not _HAS_GCP or not self._project_id:
            return False
        try:
            client = self._get_client()
            parent = f"projects/{self._project_id}"
            list(client.list_secrets(request={"parent": parent}))
            return True
        except Exception:
            return False


# ============================================================
# OCI Vault
# ============================================================
try:
    import oci
    _HAS_OCI = True
except ImportError:
    _HAS_OCI = False


class OCISecretsProvider(SecretsProvider):
    """Oracle Cloud Infrastructure Vault Secrets implementation."""

    def __init__(self, compartment_id: str = "", vault_id: str = ""):
        self._compartment_id = compartment_id or os.environ.get("OCI_COMPARTMENT_OCID", "")
        self._vault_id = vault_id or os.environ.get("OCI_VAULT_OCID", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "oci_vault"

    def _get_client(self):
        if self._client is None and _HAS_OCI:
            config = oci.config.from_file()
            self._client = oci.secrets.SecretsClient(config)
        return self._client

    def get_secret(self, secret_name: str) -> Optional[str]:
        # OCI uses secret OCID, not name — simplified implementation
        return None

    def put_secret(self, secret_name: str, secret_value: str) -> bool:
        return False

    def list_secrets(self) -> List[str]:
        return []

    def delete_secret(self, secret_name: str) -> bool:
        return False

    def check_availability(self) -> bool:
        return _HAS_OCI and bool(self._compartment_id)


# ============================================================
# IBM Cloud Secrets Manager (D237)
# ============================================================
try:
    from ibm_platform_services import SecretsManagerV2
    from ibm_cloud_sdk_core.authenticators import IAMAuthenticator as _IBMSecretsAuth
    _HAS_IBM_SECRETS = True
except ImportError:
    _HAS_IBM_SECRETS = False


class IBMSecretsProvider(SecretsProvider):
    """IBM Cloud Secrets Manager implementation (D237)."""

    def __init__(self, api_key: str = "", region: str = "us-south",
                 instance_id: str = ""):
        self._api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY", "")
        self._region = region
        self._instance_id = instance_id or os.environ.get("IBM_SECRETS_MANAGER_ID", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "ibm_secrets_manager"

    def _get_client(self):
        if self._client is None and _HAS_IBM_SECRETS and self._api_key:
            authenticator = _IBMSecretsAuth(apikey=self._api_key)
            self._client = SecretsManagerV2(authenticator=authenticator)
            self._client.set_service_url(
                f"https://{self._instance_id}.{self._region}.secrets-manager.appdomain.cloud"
            )
        return self._client

    def get_secret(self, secret_name: str) -> Optional[str]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.get_secret(id=secret_name).get_result()
            return resp.get("payload", {}).get("data", "")
        except Exception:
            return None

    def put_secret(self, secret_name: str, secret_value: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.create_secret(secret_prototype={
                "secret_type": "arbitrary",
                "name": secret_name,
                "payload": {"data": secret_value},
            })
            return True
        except Exception:
            return False

    def list_secrets(self) -> List[str]:
        client = self._get_client()
        if not client:
            return []
        try:
            resp = client.list_secrets().get_result()
            return [s.get("name", "") for s in resp.get("secrets", [])]
        except Exception:
            return []

    def delete_secret(self, secret_name: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.delete_secret(id=secret_name)
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        return _HAS_IBM_SECRETS and bool(self._api_key) and bool(self._instance_id)


# ============================================================
# Local (.env file) — stdlib only, air-gap safe (D224)
# ============================================================
class LocalSecretsProvider(SecretsProvider):
    """Local .env file secrets provider (stdlib only, air-gap safe)."""

    def __init__(self, env_file: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self._env_path = Path(env_file) if env_file else base_dir / ".env"

    @property
    def provider_name(self) -> str:
        return "local"

    def _read_env(self) -> Dict[str, str]:
        """Read .env file into dict."""
        secrets = {}
        if self._env_path.exists():
            for line in self._env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    # Strip quotes
                    value = value.strip().strip("'\"")
                    secrets[key.strip()] = value
        return secrets

    def _write_env(self, secrets: Dict[str, str]):
        """Write dict back to .env file."""
        lines = []
        for k, v in sorted(secrets.items()):
            lines.append(f"{k}={v}")
        self._env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def get_secret(self, secret_name: str) -> Optional[str]:
        # Check env vars first, then .env file
        val = os.environ.get(secret_name)
        if val:
            return val
        return self._read_env().get(secret_name)

    def put_secret(self, secret_name: str, secret_value: str) -> bool:
        try:
            secrets = self._read_env()
            secrets[secret_name] = secret_value
            self._write_env(secrets)
            return True
        except Exception:
            return False

    def list_secrets(self) -> List[str]:
        return list(self._read_env().keys())

    def delete_secret(self, secret_name: str) -> bool:
        try:
            secrets = self._read_env()
            if secret_name in secrets:
                del secrets[secret_name]
                self._write_env(secrets)
                return True
            return False
        except Exception:
            return False

    def check_availability(self) -> bool:
        return True  # Local always available
