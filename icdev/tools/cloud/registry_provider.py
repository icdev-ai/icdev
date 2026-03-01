#!/usr/bin/env python3
# CUI // SP-CTI
"""Registry Provider — cloud-agnostic container image registry.

ABC + 6 implementations: ECR, ACR, Artifact Registry, OCIR, IBM Container Registry, Local Docker.
Pattern: tools/llm/provider.py (D66 provider ABC).
Each implementation ~40-60 lines with try/except ImportError.
"""

import json
import logging
import os
import sqlite3
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("icdev.cloud.registry")


class RegistryProvider(ABC):
    """Abstract base class for container image registry."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier."""

    @abstractmethod
    def create_repository(self, name: str, **kwargs) -> Optional[Dict]:
        """Create a container image repository."""

    @abstractmethod
    def list_repositories(self) -> List[Dict]:
        """List all repositories."""

    @abstractmethod
    def list_images(self, repository: str) -> List[Dict]:
        """List images/tags in a repository."""

    @abstractmethod
    def delete_image(self, repository: str, tag: str) -> bool:
        """Delete an image by repository and tag."""

    @abstractmethod
    def get_login_command(self) -> Optional[str]:
        """Get docker login command for the registry."""

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if registry provider is available."""


# ============================================================
# AWS ECR
# ============================================================
try:
    import boto3 as _boto3_ecr
    _HAS_BOTO3_ECR = True
except ImportError:
    _HAS_BOTO3_ECR = False


class AWSECRProvider(RegistryProvider):
    """AWS Elastic Container Registry implementation."""

    def __init__(self, region: str = "us-gov-west-1"):
        self._region = region
        self._client = None

    @property
    def provider_name(self) -> str:
        return "aws_ecr"

    def _get_client(self):
        if self._client is None and _HAS_BOTO3_ECR:
            self._client = _boto3_ecr.client("ecr", region_name=self._region)
        return self._client

    def create_repository(self, name: str, **kwargs) -> Optional[Dict]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.create_repository(
                repositoryName=name,
                imageScanningConfiguration={"scanOnPush": True},
                imageTagMutability="IMMUTABLE",
                encryptionConfiguration={"encryptionType": "AES256"},
            )
            repo = resp["repository"]
            return {"name": repo["repositoryName"], "uri": repo["repositoryUri"],
                    "arn": repo["repositoryArn"]}
        except Exception:
            return None

    def list_repositories(self) -> List[Dict]:
        client = self._get_client()
        if not client:
            return []
        try:
            resp = client.describe_repositories(maxResults=100)
            return [{"name": r["repositoryName"], "uri": r["repositoryUri"],
                      "arn": r["repositoryArn"]}
                     for r in resp.get("repositories", [])]
        except Exception:
            return []

    def list_images(self, repository: str) -> List[Dict]:
        client = self._get_client()
        if not client:
            return []
        try:
            resp = client.list_images(repositoryName=repository, maxResults=100)
            return [{"tag": img.get("imageTag", "untagged"),
                      "digest": img.get("imageDigest", "")}
                     for img in resp.get("imageIds", [])]
        except Exception:
            return []

    def delete_image(self, repository: str, tag: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.batch_delete_image(
                repositoryName=repository,
                imageIds=[{"imageTag": tag}],
            )
            return True
        except Exception:
            return False

    def get_login_command(self) -> Optional[str]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.get_authorization_token()
            auth_data = resp["authorizationData"][0]
            endpoint = auth_data["proxyEndpoint"]
            return f"aws ecr get-login-password --region {self._region} | docker login --username AWS --password-stdin {endpoint}"
        except Exception:
            return None

    def check_availability(self) -> bool:
        if not _HAS_BOTO3_ECR:
            return False
        try:
            client = self._get_client()
            client.describe_repositories(maxResults=1)
            return True
        except Exception:
            return False


# ============================================================
# Azure Container Registry (ACR)
# ============================================================
try:
    from azure.containerregistry import ContainerRegistryClient
    from azure.identity import DefaultAzureCredential as _AzureCredACR
    _HAS_AZURE_ACR = True
except ImportError:
    _HAS_AZURE_ACR = False


class AzureACRProvider(RegistryProvider):
    """Azure Container Registry implementation."""

    def __init__(self, registry_url: str = ""):
        self._registry_url = registry_url or os.environ.get("AZURE_ACR_URL", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "azure_acr"

    def _get_client(self):
        if self._client is None and _HAS_AZURE_ACR and self._registry_url:
            credential = _AzureCredACR()
            self._client = ContainerRegistryClient(
                endpoint=self._registry_url, credential=credential,
            )
        return self._client

    def create_repository(self, name: str, **kwargs) -> Optional[Dict]:
        # ACR repositories are created implicitly on first push
        return {"name": name, "uri": f"{self._registry_url}/{name}",
                "note": "ACR repositories are created on first push"}

    def list_repositories(self) -> List[Dict]:
        client = self._get_client()
        if not client:
            return []
        try:
            return [{"name": name, "uri": f"{self._registry_url}/{name}"}
                     for name in client.list_repository_names()]
        except Exception:
            return []

    def list_images(self, repository: str) -> List[Dict]:
        client = self._get_client()
        if not client:
            return []
        try:
            results = []
            for manifest in client.list_manifest_properties(repository):
                for tag in (manifest.tags or []):
                    results.append({"tag": tag, "digest": manifest.digest})
            return results
        except Exception:
            return []

    def delete_image(self, repository: str, tag: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.delete_tag(repository, tag)
            return True
        except Exception:
            return False

    def get_login_command(self) -> Optional[str]:
        if not self._registry_url:
            return None
        registry_name = self._registry_url.replace("https://", "").split(".")[0]
        return f"az acr login --name {registry_name}"

    def check_availability(self) -> bool:
        return _HAS_AZURE_ACR and bool(self._registry_url)


# ============================================================
# GCP Artifact Registry
# ============================================================
try:
    from google.cloud import artifactregistry_v1 as _gcp_ar
    _HAS_GCP_AR = True
except ImportError:
    _HAS_GCP_AR = False


class GCPArtifactRegistryProvider(RegistryProvider):
    """Google Cloud Artifact Registry implementation."""

    def __init__(self, project_id: str = "", location: str = "us-east4"):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._location = location
        self._client = None

    @property
    def provider_name(self) -> str:
        return "gcp_artifact_registry"

    def _get_client(self):
        if self._client is None and _HAS_GCP_AR:
            self._client = _gcp_ar.ArtifactRegistryClient()
        return self._client

    def create_repository(self, name: str, **kwargs) -> Optional[Dict]:
        client = self._get_client()
        if not client or not self._project_id:
            return None
        try:
            parent = f"projects/{self._project_id}/locations/{self._location}"
            repo = _gcp_ar.Repository(
                format_=_gcp_ar.Repository.Format.DOCKER,
                description=kwargs.get("description", "ICDEV container repository"),
            )
            op = client.create_repository(
                request={"parent": parent, "repository_id": name, "repository": repo}
            )
            result = op.result()
            return {"name": result.name, "uri": f"{self._location}-docker.pkg.dev/{self._project_id}/{name}"}
        except Exception:
            return None

    def list_repositories(self) -> List[Dict]:
        client = self._get_client()
        if not client or not self._project_id:
            return []
        try:
            parent = f"projects/{self._project_id}/locations/{self._location}"
            repos = client.list_repositories(request={"parent": parent})
            return [{"name": r.name.split("/")[-1],
                      "format": str(r.format_)}
                     for r in repos]
        except Exception:
            return []

    def list_images(self, repository: str) -> List[Dict]:
        client = self._get_client()
        if not client or not self._project_id:
            return []
        try:
            parent = f"projects/{self._project_id}/locations/{self._location}/repositories/{repository}"
            images = client.list_docker_images(request={"parent": parent})
            return [{"uri": img.uri, "tags": list(img.tags),
                      "upload_time": str(img.upload_time)}
                     for img in images]
        except Exception:
            return []

    def delete_image(self, repository: str, tag: str) -> bool:
        # Artifact Registry deletion requires version-specific API — simplified stub
        return False

    def get_login_command(self) -> Optional[str]:
        if not self._location:
            return None
        return f"gcloud auth configure-docker {self._location}-docker.pkg.dev"

    def check_availability(self) -> bool:
        return _HAS_GCP_AR and bool(self._project_id)


# ============================================================
# OCI Container Image Registry (OCIR)
# ============================================================
try:
    import oci as _oci_reg
    _HAS_OCI_REG = True
except ImportError:
    _HAS_OCI_REG = False


class OCIOCIRProvider(RegistryProvider):
    """Oracle Cloud Infrastructure Container Image Registry implementation."""

    def __init__(self, compartment_id: str = "", region: str = ""):
        self._compartment_id = compartment_id or os.environ.get("OCI_COMPARTMENT_OCID", "")
        self._region = region or os.environ.get("OCI_REGION", "us-langley-1")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "oci_ocir"

    def _get_client(self):
        if self._client is None and _HAS_OCI_REG:
            try:
                config = _oci_reg.config.from_file()
                self._client = _oci_reg.artifacts.ContainerImageClient(config)
            except Exception:
                pass
        return self._client

    def create_repository(self, name: str, **kwargs) -> Optional[Dict]:
        client = self._get_client()
        if not client or not self._compartment_id:
            return None
        try:
            details = _oci_reg.artifacts.models.CreateContainerRepositoryDetails(
                compartment_id=self._compartment_id,
                display_name=name,
                is_immutable=True,
                is_public=False,
            )
            resp = client.create_container_repository(details)
            repo = resp.data
            return {"name": repo.display_name, "id": repo.id}
        except Exception:
            return None

    def list_repositories(self) -> List[Dict]:
        # OCI list requires ContainerRepositoryCollection — simplified stub
        return []

    def list_images(self, repository: str) -> List[Dict]:
        return []

    def delete_image(self, repository: str, tag: str) -> bool:
        return False

    def get_login_command(self) -> Optional[str]:
        return f"docker login {self._region}.ocir.io"

    def check_availability(self) -> bool:
        return _HAS_OCI_REG and bool(self._compartment_id)


# ============================================================
# IBM Container Registry (D237)
# ============================================================
class IBMRegistryProvider(RegistryProvider):
    """IBM Container Registry implementation (D237).

    Uses urllib.request (stdlib) for REST API — no additional SDK required.
    """

    def __init__(self, api_key: str = "", region: str = "us-south"):
        self._api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY", "")
        self._region = region
        self._registry_url = f"https://{self._region}.icr.io"

    @property
    def provider_name(self) -> str:
        return "ibm_container_registry"

    def create_repository(self, name: str, **kwargs) -> Optional[Dict]:
        # IBM CR repositories are created implicitly on first push
        return {"name": name, "uri": f"{self._registry_url}/{name}",
                "note": "IBM CR repositories are created on first push"}

    def list_repositories(self) -> List[Dict]:
        if not self._api_key:
            return []
        try:
            import json as _json
            import urllib.request
            url = f"{self._registry_url}/api/v1/images"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            })
            resp = urllib.request.urlopen(req, timeout=10)
            data = _json.loads(resp.read().decode())
            repos = list({img.get("RepoTags", [""])[0].rsplit(":", 1)[0]
                         for img in data if img.get("RepoTags")})
            return [{"name": r, "uri": f"{self._registry_url}/{r}"} for r in repos]
        except Exception:
            return []

    def list_images(self, repository: str) -> List[Dict]:
        if not self._api_key:
            return []
        try:
            import json as _json
            import urllib.request
            url = f"{self._registry_url}/v2/{repository}/tags/list"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            })
            resp = urllib.request.urlopen(req, timeout=10)
            data = _json.loads(resp.read().decode())
            return [{"tag": t} for t in data.get("tags", [])]
        except Exception:
            return []

    def delete_image(self, repository: str, tag: str) -> bool:
        return False  # Simplified — requires digest-based deletion

    def get_login_command(self) -> Optional[str]:
        return "ibmcloud cr login"

    def check_availability(self) -> bool:
        return bool(self._api_key)


# ============================================================
# Local Docker Registry — SQLite tracking (stdlib only, air-gap safe, D224)
# ============================================================
class LocalDockerProvider(RegistryProvider):
    """Local Docker registry tracking via SQLite (stdlib only, air-gap safe)."""

    def __init__(self, data_dir: Optional[str] = None):
        root = Path(__file__).resolve().parent.parent.parent
        self._data_dir = Path(data_dir) if data_dir else root / "data"
        self._db_path = self._data_dir / "local_registry.db"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create registry tracking tables if not exists."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_repositories (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_images (
                    id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    digest TEXT DEFAULT '',
                    size_bytes INTEGER DEFAULT 0,
                    pushed_at TEXT NOT NULL,
                    FOREIGN KEY (repository) REFERENCES local_repositories(name),
                    UNIQUE(repository, tag)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_local_images_repo
                ON local_images(repository)
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Failed to init local registry DB: %s", e)

    @property
    def provider_name(self) -> str:
        return "local"

    def create_repository(self, name: str, **kwargs) -> Optional[Dict]:
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT OR IGNORE INTO local_repositories (name, created_at) VALUES (?, ?)",
                (name, now),
            )
            conn.commit()
            conn.close()
            return {"name": name, "uri": f"localhost:5000/{name}", "created_at": now}
        except Exception:
            return None

    def list_repositories(self) -> List[Dict]:
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM local_repositories ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return [{"name": r["name"], "uri": f"localhost:5000/{r['name']}",
                      "created_at": r["created_at"]} for r in rows]
        except Exception:
            return []

    def list_images(self, repository: str) -> List[Dict]:
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM local_images WHERE repository = ? ORDER BY pushed_at DESC",
                (repository,),
            ).fetchall()
            conn.close()
            return [{"tag": r["tag"], "digest": r["digest"],
                      "size_bytes": r["size_bytes"], "pushed_at": r["pushed_at"]}
                     for r in rows]
        except Exception:
            return []

    def delete_image(self, repository: str, tag: str) -> bool:
        try:
            conn = sqlite3.connect(str(self._db_path))
            cursor = conn.execute(
                "DELETE FROM local_images WHERE repository = ? AND tag = ?",
                (repository, tag),
            )
            conn.commit()
            deleted = cursor.rowcount > 0
            conn.close()
            return deleted
        except Exception:
            return False

    def get_login_command(self) -> Optional[str]:
        return "docker login localhost:5000"

    def check_availability(self) -> bool:
        return True  # Local tracking always available
