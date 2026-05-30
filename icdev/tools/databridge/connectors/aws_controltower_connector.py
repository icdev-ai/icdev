# CUI // SP-CTI
"""DataBridge connector for AWS Control Tower account vending machine integration.

Provides secure API interaction with AWS Control Tower for:
  - Listing managed accounts (``list_managed_accounts``)
  - Listing organizational units (``list_organizational_units``)
  - Describing account creation status (``describe_account``)
  - Vending new accounts (``create_managed_account``)

All AWS credentials are pulled from config or standard AWS environment variables
(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_REGION).
No credentials are ever hard-coded.

Feature flag: AWS_CONTROL_TOWER_ENABLED in .env (default: false, air-gap safe).
When disabled, all methods return a typed disabled response — no exceptions.

Config keys:
  region              - AWS region (default: from AWS_REGION env, "us-east-1")
  access_key_id       - AWS access key ID (default: from AWS_ACCESS_KEY_ID env)
  secret_access_key   - AWS secret access key (default: from AWS_SECRET_ACCESS_KEY env)
  session_token       - AWS session token (default: from AWS_SESSION_TOKEN env)
  timeout             - boto3 API call timeout in seconds (default: 30)
  role_arn            - Optional cross-account role ARN to assume
  external_id         - Optional external ID for role assumption
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
import time
from typing import Any, Dict, List

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
    SchemaDefinition,
    SchemaField,
)
from tools.databridge.registry import register_connector

try:
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError, NoCredentialsError

    _BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None  # type: ignore[assignment]
    BotoConfig = None  # type: ignore[assignment,misc]
    ClientError = Exception  # type: ignore[assignment,misc]
    NoCredentialsError = Exception  # type: ignore[assignment,misc]
    _BOTO3_AVAILABLE = False

logger = get_logger("databridge.connectors.aws_control_tower")

# Table names -> (boto3 service, method name, result key)
_READ_MAP: Dict[str, tuple[str, str, str]] = {
    "managed_accounts": ("controltower", "list_managed_accounts", "managedAccounts"),
    "organizational_units": ("organizations", "list_organizational_units_for_parent", "OrganizationalUnits"),
    "accounts": ("organizations", "list_accounts", "Accounts"),
}

# Write table -> (boto3 service, method name)
_WRITE_MAP: Dict[str, tuple[str, str]] = {
    "create_managed_account": ("controltower", "create_managed_account"),
    "provision_product": ("servicecatalog", "provision_product"),
}

_DEFAULT_REGION = "us-east-1"
_DEFAULT_TIMEOUT = 30


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def _feature_enabled() -> bool:
    return _truthy(os.getenv("AWS_CONTROL_TOWER_ENABLED", "false"))


def _disabled_response() -> ConnectorResponse:
    return ConnectorResponse(
        status="disabled",
        data=[],
        row_count=0,
        errors=[
            "AWS Control Tower integration is disabled. "
            "Set AWS_CONTROL_TOWER_ENABLED=true in .env to enable."
        ],
        metadata={"hint": "Requires AWS IAM credentials with Control Tower / Organizations permissions."},
    )


def _disabled_health() -> Dict[str, Any]:
    return {
        "status": "disabled",
        "connector": "aws_control_tower",
        "enabled": False,
        "reason": "AWS_CONTROL_TOWER_ENABLED is not set to true in environment.",
    }


@register_connector
class AWSControlTowerConnector(DataConnector):
    """AWS Control Tower account vending machine connector.

    Securely interacts with AWS Control Tower and AWS Organizations APIs
    for account lifecycle operations. All credentials are resolved from
    config or standard AWS environment variables.
    """

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._region: str = _DEFAULT_REGION
        self._timeout: int = _DEFAULT_TIMEOUT
        self._session: Any = None
        self._connected: bool = False

    # -- ABC properties --------------------------------------------------------

    @property
    def connector_name(self) -> str:
        return "aws_control_tower"

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.SAAS_API

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,
            supports_schema_inference=True,
            supports_incremental=False,
            max_batch_size=500,
            supported_formats=["json"],
        )

    # -- Connection lifecycle --------------------------------------------------

    def connect(self, config: Dict[str, Any]) -> bool:
        """Establish AWS session with credentials from config or env."""
        if not _BOTO3_AVAILABLE:
            logger.error("boto3 is required for AWS Control Tower connector")
            return False

        self._config = config
        self._region = config.get("region", os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", _DEFAULT_REGION)))
        self._timeout = int(config.get("timeout", _DEFAULT_TIMEOUT))

        try:
            session = self._create_session()
            # Validate connectivity with a lightweight STS call
            sts = session.client("sts", config=self._boto_config())
            identity = sts.get_caller_identity()
            self._session = session
            self._connected = True
            logger.info(
                "AWS Control Tower connector connected: account=%s user=%s",
                identity.get("Account"),
                identity.get("Arn"),
            )
            return True
        except NoCredentialsError as exc:
            logger.error("AWS credentials not found: %s", exc)
            return False
        except ClientError as exc:
            logger.error("AWS connection test failed: %s", exc)
            return False
        except Exception as exc:
            logger.error("Unexpected error during AWS connect: %s", exc)
            return False

    def disconnect(self) -> None:
        """Clear session and connection state."""
        self._session = None
        self._connected = False
        self._config = {}

    def health_check(self) -> Dict[str, Any]:
        """Check AWS Control Tower health by calling ListLandingZones."""
        if not _feature_enabled():
            return _disabled_health()
        if not _BOTO3_AVAILABLE:
            return {"status": "unhealthy", "error": "boto3 not installed"}
        if not self._session:
            return {"status": "unhealthy", "error": "Not connected"}

        start = time.time()
        try:
            client = self._session.client("controltower", config=self._boto_config())
            resp = client.list_landing_zones()
            landing_zones = resp.get("landingZones", [])
            return {
                "status": "healthy",
                "connector": self.connector_name,
                "region": self._region,
                "landing_zones": len(landing_zones),
                "latency_ms": int((time.time() - start) * 1000),
            }
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            error_msg = exc.response.get("Error", {}).get("Message", str(exc))
            return {
                "status": "unhealthy",
                "connector": self.connector_name,
                "region": self._region,
                "error": f"{error_code}: {error_msg}",
                "latency_ms": int((time.time() - start) * 1000),
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "connector": self.connector_name,
                "error": str(exc),
                "latency_ms": int((time.time() - start) * 1000),
            }

    # -- Read operations -------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read from AWS Control Tower or Organizations API.

        Supported table_names:
          - managed_accounts      : Control Tower ListManagedAccounts
          - organizational_units  : Organizations ListOrganizationalUnitsForParent
          - accounts              : Organizations ListAccounts
        """
        if not _feature_enabled():
            return _disabled_response()
        if not _BOTO3_AVAILABLE:
            return ConnectorResponse(status="error", errors=["boto3 is required. Install: pip install boto3"])
        if not self._session:
            return ConnectorResponse(status="error", errors=["Not connected. Call connect() first."])

        start = time.time()
        table = request.table_name

        if table not in _READ_MAP:
            return ConnectorResponse(
                status="error",
                errors=[
                    f"Unknown table: {table!r}. "
                    f"Available: {list(_READ_MAP.keys())}"
                ],
            )

        svc, method, result_key = _READ_MAP[table]
        params: Dict[str, Any] = {}

        # Apply filters as API params
        if request.filters:
            if table == "organizational_units" and "parent_id" in request.filters:
                params["ParentId"] = request.filters["parent_id"]
            if table == "accounts" and "parent_id" in request.filters:
                params["ParentId"] = request.filters["parent_id"]

        try:
            client = self._session.client(svc, config=self._boto_config())
            api_method = getattr(client, method)

            # Handle pagination for Organizations APIs
            if svc == "organizations":
                rows = self._paginate_organizations(api_method, result_key, params, request.limit)
            else:
                resp = api_method(**params)
                rows = resp.get(result_key, [])
                if not isinstance(rows, list):
                    rows = [rows] if rows else []
                if request.limit:
                    rows = rows[: request.limit]

            duration_ms = int((time.time() - start) * 1000)
            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows),
                duration_ms=duration_ms,
                metadata={"service": svc, "method": method, "table": table},
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            error_msg = exc.response.get("Error", {}).get("Message", str(exc))
            return ConnectorResponse(
                status="error",
                errors=[f"AWS {error_code}: {error_msg}"],
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - start) * 1000),
            )

    # -- Write operations ------------------------------------------------------

    def write(self, request: ConnectorRequest, data: Any = None) -> ConnectorResponse:
        """Write (vend) accounts via AWS Control Tower or Service Catalog.

        Supported table_names:
          - create_managed_account  : Control Tower CreateManagedAccount
          - provision_product         : Service Catalog ProvisionProduct (Account Factory)

        ``data`` must be a dict containing the API parameters.
        """
        if not _feature_enabled():
            return _disabled_response()
        if not _BOTO3_AVAILABLE:
            return ConnectorResponse(status="error", errors=["boto3 is required. Install: pip install boto3"])
        if not self._session:
            return ConnectorResponse(status="error", errors=["Not connected. Call connect() first."])

        start = time.time()
        table = request.table_name

        if table not in _WRITE_MAP:
            return ConnectorResponse(
                status="error",
                errors=[
                    f"Unknown write table: {table!r}. "
                    f"Available: {list(_WRITE_MAP.keys())}"
                ],
            )

        svc, method = _WRITE_MAP[table]
        payload = data if isinstance(data, dict) else {}

        try:
            client = self._session.client(svc, config=self._boto_config())
            api_method = getattr(client, method)
            resp = api_method(**payload)
            # Strip boto3 response metadata
            resp.pop("ResponseMetadata", None)
            duration_ms = int((time.time() - start) * 1000)
            return ConnectorResponse(
                status="ok",
                data=[resp],
                row_count=1,
                duration_ms=duration_ms,
                metadata={"service": svc, "method": method, "table": table},
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            error_msg = exc.response.get("Error", {}).get("Message", str(exc))
            return ConnectorResponse(
                status="error",
                errors=[f"AWS {error_code}: {error_msg}"],
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - start) * 1000),
            )

    # -- Schema inference ------------------------------------------------------

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Return inferred schema for known tables."""
        schemas: Dict[str, List[SchemaField]] = {
            "managed_accounts": [
                SchemaField("accountIdentifier", "utf8"),
                SchemaField("accountName", "utf8"),
                SchemaField("organizationalUnit", "utf8"),
                SchemaField("organizationalUnitName", "utf8"),
                SchemaField("state", "utf8"),
            ],
            "organizational_units": [
                SchemaField("Id", "utf8"),
                SchemaField("Name", "utf8"),
                SchemaField("Arn", "utf8"),
            ],
            "accounts": [
                SchemaField("Id", "utf8"),
                SchemaField("Arn", "utf8"),
                SchemaField("Email", "utf8"),
                SchemaField("Name", "utf8"),
                SchemaField("Status", "utf8"),
                SchemaField("JoinedMethod", "utf8"),
                SchemaField("JoinedTimestamp", "utf8"),
            ],
            "create_managed_account": [
                SchemaField("operationIdentifier", "utf8"),
            ],
            "provision_product": [
                SchemaField("RecordDetail", "json"),
            ],
        }
        return SchemaDefinition(
            fields=schemas.get(table_name, [SchemaField("data", "json")]),
            metadata={"source": self.connector_name, "table": table_name},
        )

    def list_tables(self) -> List[str]:
        """Return available table/resource names."""
        return list(_READ_MAP.keys()) + list(_WRITE_MAP.keys())

    # -- Internal helpers ------------------------------------------------------

    def _create_session(self) -> Any:
        """Create a boto3 session with optional role assumption."""
        region = self._region
        access_key = self._config.get("access_key_id", os.getenv("AWS_ACCESS_KEY_ID"))
        secret_key = self._config.get("secret_access_key", os.getenv("AWS_SECRET_ACCESS_KEY"))
        session_token = self._config.get("session_token", os.getenv("AWS_SESSION_TOKEN"))
        role_arn = self._config.get("role_arn", os.getenv("AWS_CONTROL_TOWER_ROLE_ARN", ""))
        external_id = self._config.get("external_id", os.getenv("AWS_CONTROL_TOWER_EXTERNAL_ID", ""))

        session_kwargs: Dict[str, Any] = {"region_name": region}
        if access_key and secret_key:
            session_kwargs["aws_access_key_id"] = access_key
            session_kwargs["aws_secret_access_key"] = secret_key
        if session_token:
            session_kwargs["aws_session_token"] = session_token

        session = boto3.Session(**session_kwargs)

        if role_arn:
            sts = session.client("sts")
            assume_kwargs: Dict[str, Any] = {"RoleArn": role_arn, "RoleSessionName": "icdev-controltower-connector"}
            if external_id:
                assume_kwargs["ExternalId"] = external_id
            assumed = sts.assume_role(**assume_kwargs)
            creds = assumed["Credentials"]
            session = boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
                region_name=region,
            )
            logger.info("Assumed role %s for Control Tower connector", role_arn)

        return session

    def _boto_config(self) -> Any:
        """Return botocore Config with timeout and retries."""
        if BotoConfig is None:
            return None
        return BotoConfig(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=self._timeout,
            read_timeout=self._timeout,
        )

    def _paginate_organizations(
        self,
        api_method: Any,
        result_key: str,
        params: Dict[str, Any],
        limit: int | None,
    ) -> List[Dict[str, Any]]:
        """Paginate an AWS Organizations API call."""
        rows: List[Dict[str, Any]] = []
        next_token: str | None = None

        while True:
            if next_token:
                params["NextToken"] = next_token
            elif "NextToken" in params:
                del params["NextToken"]

            resp = api_method(**params)
            page = resp.get(result_key, [])
            if isinstance(page, list):
                rows.extend(page)
            elif page:
                rows.append(page)

            if limit and len(rows) >= limit:
                rows = rows[:limit]
                break

            next_token = resp.get("NextToken")
            if not next_token:
                break

        return rows
