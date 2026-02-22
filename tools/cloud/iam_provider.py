#!/usr/bin/env python3
# CUI // SP-CTI
"""IAM Provider — cloud-agnostic identity and access management.

ABC + 6 implementations: AWS IAM, Entra ID, Cloud IAM, OCI IAM, IBM Cloud IAM, Local (SQLite).
Pattern: tools/llm/provider.py (D66 provider ABC).
Each implementation ~40-60 lines with try/except ImportError.
"""

import json
import os
import sqlite3
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import logging

logger = logging.getLogger("icdev.cloud.iam")


class IAMProvider(ABC):
    """Abstract base class for identity and access management."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier."""

    @abstractmethod
    def create_service_account(self, name: str, description: str = "") -> Optional[Dict]:
        """Create a service account / service principal. Returns account details."""

    @abstractmethod
    def get_service_account(self, account_id: str) -> Optional[Dict]:
        """Get service account details by ID."""

    @abstractmethod
    def list_service_accounts(self) -> List[Dict]:
        """List all service accounts."""

    @abstractmethod
    def assign_role(self, account_id: str, role: str, scope: str = "") -> bool:
        """Assign a role to a service account."""

    @abstractmethod
    def check_permission(self, account_id: str, action: str,
                         resource: str = "") -> bool:
        """Check if a service account has permission for an action on a resource."""

    @abstractmethod
    def delete_service_account(self, account_id: str) -> bool:
        """Delete a service account."""

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if IAM provider is available."""


# ============================================================
# AWS IAM
# ============================================================
try:
    import boto3 as _boto3_iam
    _HAS_BOTO3_IAM = True
except ImportError:
    _HAS_BOTO3_IAM = False


class AWSIAMProvider(IAMProvider):
    """AWS IAM implementation."""

    def __init__(self, region: str = "us-gov-west-1"):
        self._region = region
        self._client = None

    @property
    def provider_name(self) -> str:
        return "aws_iam"

    def _get_client(self):
        if self._client is None and _HAS_BOTO3_IAM:
            self._client = _boto3_iam.client("iam", region_name=self._region)
        return self._client

    def create_service_account(self, name: str, description: str = "") -> Optional[Dict]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.create_user(UserName=name, Tags=[
                {"Key": "Description", "Value": description or "ICDEV service account"},
                {"Key": "ManagedBy", "Value": "icdev"},
            ])
            user = resp["User"]
            return {"id": user["UserName"], "arn": user["Arn"],
                    "created_at": str(user["CreateDate"])}
        except Exception:
            return None

    def get_service_account(self, account_id: str) -> Optional[Dict]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.get_user(UserName=account_id)
            user = resp["User"]
            return {"id": user["UserName"], "arn": user["Arn"],
                    "created_at": str(user["CreateDate"])}
        except Exception:
            return None

    def list_service_accounts(self) -> List[Dict]:
        client = self._get_client()
        if not client:
            return []
        try:
            resp = client.list_users(MaxItems=100)
            return [{"id": u["UserName"], "arn": u["Arn"],
                      "created_at": str(u["CreateDate"])}
                     for u in resp.get("Users", [])]
        except Exception:
            return []

    def assign_role(self, account_id: str, role: str, scope: str = "") -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.attach_user_policy(UserName=account_id, PolicyArn=role)
            return True
        except Exception:
            return False

    def check_permission(self, account_id: str, action: str,
                         resource: str = "") -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            resp = client.simulate_principal_policy(
                PolicySourceArn=account_id,
                ActionNames=[action],
                ResourceArns=[resource] if resource else [],
            )
            results = resp.get("EvaluationResults", [])
            return all(r.get("EvalDecision") == "allowed" for r in results)
        except Exception:
            return False

    def delete_service_account(self, account_id: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.delete_user(UserName=account_id)
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        if not _HAS_BOTO3_IAM:
            return False
        try:
            client = self._get_client()
            client.list_users(MaxItems=1)
            return True
        except Exception:
            return False


# ============================================================
# Azure Entra ID (formerly Azure AD)
# ============================================================
try:
    from azure.identity import DefaultAzureCredential as _AzureCredIAM
    from azure.mgmt.authorization import AuthorizationManagementClient
    _HAS_AZURE_IAM = True
except ImportError:
    _HAS_AZURE_IAM = False


class AzureEntraIDProvider(IAMProvider):
    """Azure Entra ID (formerly Azure AD) implementation."""

    def __init__(self, tenant_id: str = "", subscription_id: str = ""):
        self._tenant_id = tenant_id or os.environ.get("AZURE_TENANT_ID", "")
        self._subscription_id = subscription_id or os.environ.get("AZURE_SUBSCRIPTION_ID", "")

    @property
    def provider_name(self) -> str:
        return "azure_entra_id"

    def create_service_account(self, name: str, description: str = "") -> Optional[Dict]:
        # Entra ID app registration requires MS Graph API — simplified stub
        return None

    def get_service_account(self, account_id: str) -> Optional[Dict]:
        return None

    def list_service_accounts(self) -> List[Dict]:
        return []

    def assign_role(self, account_id: str, role: str, scope: str = "") -> bool:
        return False

    def check_permission(self, account_id: str, action: str,
                         resource: str = "") -> bool:
        return False

    def delete_service_account(self, account_id: str) -> bool:
        return False

    def check_availability(self) -> bool:
        return _HAS_AZURE_IAM and bool(self._tenant_id) and bool(self._subscription_id)


# ============================================================
# GCP Cloud IAM
# ============================================================
try:
    from google.cloud import iam_admin_v1 as _gcp_iam
    _HAS_GCP_IAM = True
except ImportError:
    _HAS_GCP_IAM = False


class GCPCloudIAMProvider(IAMProvider):
    """Google Cloud IAM implementation."""

    def __init__(self, project_id: str = ""):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "gcp_cloud_iam"

    def _get_client(self):
        if self._client is None and _HAS_GCP_IAM:
            self._client = _gcp_iam.IAMClient()
        return self._client

    def create_service_account(self, name: str, description: str = "") -> Optional[Dict]:
        client = self._get_client()
        if not client or not self._project_id:
            return None
        try:
            sa = client.create_service_account(
                request={
                    "name": f"projects/{self._project_id}",
                    "account_id": name,
                    "service_account": {"display_name": description or name},
                }
            )
            return {"id": sa.unique_id, "email": sa.email, "name": sa.display_name}
        except Exception:
            return None

    def get_service_account(self, account_id: str) -> Optional[Dict]:
        client = self._get_client()
        if not client or not self._project_id:
            return None
        try:
            name = f"projects/{self._project_id}/serviceAccounts/{account_id}"
            sa = client.get_service_account(request={"name": name})
            return {"id": sa.unique_id, "email": sa.email, "name": sa.display_name}
        except Exception:
            return None

    def list_service_accounts(self) -> List[Dict]:
        client = self._get_client()
        if not client or not self._project_id:
            return []
        try:
            resp = client.list_service_accounts(
                request={"name": f"projects/{self._project_id}"}
            )
            return [{"id": sa.unique_id, "email": sa.email, "name": sa.display_name}
                     for sa in resp.accounts]
        except Exception:
            return []

    def assign_role(self, account_id: str, role: str, scope: str = "") -> bool:
        # GCP role assignment requires Resource Manager API — simplified stub
        return False

    def check_permission(self, account_id: str, action: str,
                         resource: str = "") -> bool:
        return False

    def delete_service_account(self, account_id: str) -> bool:
        client = self._get_client()
        if not client or not self._project_id:
            return False
        try:
            name = f"projects/{self._project_id}/serviceAccounts/{account_id}"
            client.delete_service_account(request={"name": name})
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        return _HAS_GCP_IAM and bool(self._project_id)


# ============================================================
# OCI IAM
# ============================================================
try:
    import oci as _oci_iam
    _HAS_OCI_IAM = True
except ImportError:
    _HAS_OCI_IAM = False


class OCIIAMProvider(IAMProvider):
    """Oracle Cloud Infrastructure IAM implementation."""

    def __init__(self, compartment_id: str = "", tenancy_id: str = ""):
        self._compartment_id = compartment_id or os.environ.get("OCI_COMPARTMENT_OCID", "")
        self._tenancy_id = tenancy_id or os.environ.get("OCI_TENANCY_OCID", "")

    @property
    def provider_name(self) -> str:
        return "oci_iam"

    def create_service_account(self, name: str, description: str = "") -> Optional[Dict]:
        # OCI uses dynamic groups + policies — simplified stub
        return None

    def get_service_account(self, account_id: str) -> Optional[Dict]:
        return None

    def list_service_accounts(self) -> List[Dict]:
        return []

    def assign_role(self, account_id: str, role: str, scope: str = "") -> bool:
        return False

    def check_permission(self, account_id: str, action: str,
                         resource: str = "") -> bool:
        return False

    def delete_service_account(self, account_id: str) -> bool:
        return False

    def check_availability(self) -> bool:
        return _HAS_OCI_IAM and bool(self._compartment_id)


# ============================================================
# IBM Cloud IAM (D237)
# ============================================================
try:
    from ibm_platform_services import IamIdentityV1
    from ibm_cloud_sdk_core.authenticators import IAMAuthenticator as _IBMIAMAuth
    _HAS_IBM_IAM = True
except ImportError:
    _HAS_IBM_IAM = False


class IBMIAMProvider(IAMProvider):
    """IBM Cloud IAM implementation (D237)."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or os.environ.get("IBM_CLOUD_API_KEY", "")
        self._client = None

    @property
    def provider_name(self) -> str:
        return "ibm_iam"

    def _get_client(self):
        if self._client is None and _HAS_IBM_IAM and self._api_key:
            authenticator = _IBMIAMAuth(apikey=self._api_key)
            self._client = IamIdentityV1(authenticator=authenticator)
        return self._client

    def create_service_account(self, name: str, description: str = "") -> Optional[Dict]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.create_service_id(
                account_id=os.environ.get("IBM_ACCOUNT_ID", ""),
                name=name,
                description=description,
            ).get_result()
            return {"id": resp.get("id", ""), "name": name}
        except Exception:
            return None

    def get_service_account(self, account_id: str) -> Optional[Dict]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.get_service_id(id=account_id).get_result()
            return {"id": resp.get("id", ""), "name": resp.get("name", "")}
        except Exception:
            return None

    def list_service_accounts(self) -> List[Dict]:
        client = self._get_client()
        if not client:
            return []
        try:
            resp = client.list_service_ids(
                account_id=os.environ.get("IBM_ACCOUNT_ID", ""),
            ).get_result()
            return [{"id": s.get("id", ""), "name": s.get("name", "")}
                    for s in resp.get("serviceids", [])]
        except Exception:
            return []

    def assign_role(self, account_id: str, role: str, scope: str = "") -> bool:
        # IBM IAM role assignment requires Policy Management API — simplified stub
        return False

    def check_permission(self, account_id: str, action: str,
                         resource: str = "") -> bool:
        # IBM IAM permission check requires Authorization API — simplified stub
        return False

    def delete_service_account(self, account_id: str) -> bool:
        client = self._get_client()
        if not client:
            return False
        try:
            client.delete_service_id(id=account_id)
            return True
        except Exception:
            return False

    def get_current_identity(self) -> Optional[Dict]:
        client = self._get_client()
        if not client:
            return None
        try:
            resp = client.get_api_keys_details(
                iam_api_key=self._api_key,
            ).get_result()
            return {"id": resp.get("id", ""), "name": resp.get("name", ""),
                    "account_id": resp.get("account_id", "")}
        except Exception:
            return None

    def check_availability(self) -> bool:
        return _HAS_IBM_IAM and bool(self._api_key)


# ============================================================
# Local IAM — SQLite (stdlib only, air-gap safe, D224)
# ============================================================
class LocalIAMProvider(IAMProvider):
    """Local SQLite-based IAM provider (stdlib only, air-gap safe)."""

    def __init__(self, data_dir: Optional[str] = None):
        root = Path(__file__).resolve().parent.parent.parent
        self._data_dir = Path(data_dir) if data_dir else root / "data"
        self._db_path = self._data_dir / "local_iam.db"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create IAM tables if not exists."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_service_accounts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_role_assignments (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    scope TEXT DEFAULT '',
                    assigned_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES local_service_accounts(id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_local_roles_account
                ON local_role_assignments(account_id)
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Failed to init local IAM DB: %s", e)

    @property
    def provider_name(self) -> str:
        return "local"

    def create_service_account(self, name: str, description: str = "") -> Optional[Dict]:
        try:
            account_id = f"local-sa-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT INTO local_service_accounts (id, name, description, created_at) "
                "VALUES (?, ?, ?, ?)",
                (account_id, name, description, now),
            )
            conn.commit()
            conn.close()
            return {"id": account_id, "name": name, "description": description,
                    "created_at": now}
        except Exception:
            return None

    def get_service_account(self, account_id: str) -> Optional[Dict]:
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM local_service_accounts WHERE id = ? AND status = 'active'",
                (account_id,),
            ).fetchone()
            conn.close()
            if row:
                return dict(row)
            return None
        except Exception:
            return None

    def list_service_accounts(self) -> List[Dict]:
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM local_service_accounts WHERE status = 'active' "
                "ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def assign_role(self, account_id: str, role: str, scope: str = "") -> bool:
        try:
            role_id = f"role-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT INTO local_role_assignments (id, account_id, role, scope, assigned_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (role_id, account_id, role, scope, now),
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def check_permission(self, account_id: str, action: str,
                         resource: str = "") -> bool:
        try:
            conn = sqlite3.connect(str(self._db_path))
            row = conn.execute(
                "SELECT COUNT(*) FROM local_role_assignments "
                "WHERE account_id = ? AND (role = ? OR role = 'admin')",
                (account_id, action),
            ).fetchone()
            conn.close()
            return row[0] > 0 if row else False
        except Exception:
            return False

    def delete_service_account(self, account_id: str) -> bool:
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "UPDATE local_service_accounts SET status = 'deleted' WHERE id = ?",
                (account_id,),
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def check_availability(self) -> bool:
        return True  # Local always available
