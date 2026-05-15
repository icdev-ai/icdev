# CUI // SP-CTI
"""DataBridge connector for LocalStack AWS service emulation.

Single egress point for all LocalStack HTTP traffic from IDC and other tools.
Follows the same DataBridge connector pattern as GNS3Connector.

LocalStack exposes a single HTTP endpoint (default: http://localhost:4566) that
emulates AWS service APIs. This connector wraps that endpoint so all traffic
benefits from DataBridge secret resolution, audit logging, and health probing.

Feature flag: LOCALSTACK_ENABLED in .env (default: false, air-gap safe).
When disabled, health_check() returns a "disabled" status and all read/write
calls return a disabled ConnectorResponse — no exceptions raised.

Logical table names:
  "health"              -> GET /_localstack/health           (no auth needed)
  "services"            -> parsed from /_localstack/health
  "s3_buckets"          -> AWS S3 ListBuckets    (requires boto3)
  "dynamodb_tables"     -> AWS DynamoDB ListTables (requires boto3)
  "lambda_functions"    -> AWS Lambda ListFunctions (requires boto3)
  "sqs_queues"          -> AWS SQS ListQueues    (requires boto3)
  "ecr_repositories"    -> AWS ECR DescribeRepositories (requires boto3)

Write operations use request.filters["service"] to select the AWS service and
request.query as the boto3 method name. Data is the method kwargs dict.

Config keys:
  endpoint   - LocalStack endpoint (default: from LOCALSTACK_ENDPOINT env)
  region     - AWS region (default: from LOCALSTACK_REGION env, "us-east-1")
  access_key - AWS access key ID (default: "test" — LocalStack dummy)
  secret_key - AWS secret access key (default: "test" — LocalStack dummy)
  timeout    - HTTP timeout in seconds (default: 15)
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    SchemaDefinition,
    SchemaField,
)
from tools.databridge.connectors.saas_base import SaaSBaseConnector
from tools.databridge.feature_flags import IntegrationFeatureFlags
from tools.databridge.registry import register_connector

try:
    import boto3  # type: ignore[import-not-found]

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False

# urllib-accessible read endpoints (no auth required on LocalStack)
_URLLIB_ENDPOINTS: Dict[str, str] = {
    "health": "/_localstack/health",
    "services": "/_localstack/health",
}

# boto3 table → (service_name, method_name, result_key)
_BOTO3_READ_MAP: Dict[str, tuple[str, str, str]] = {
    "s3_buckets": ("s3", "list_buckets", "Buckets"),
    "dynamodb_tables": ("dynamodb", "list_tables", "TableNames"),
    "lambda_functions": ("lambda", "list_functions", "Functions"),
    "sqs_queues": ("sqs", "list_queues", "QueueUrls"),
    "ecr_repositories": ("ecr", "describe_repositories", "repositories"),
}

_DISABLED_RESPONSE = ConnectorResponse(
    status="disabled",
    data=[],
    row_count=0,
    errors=["LocalStack integration is disabled (LOCALSTACK_ENABLED=false in .env)"],
    metadata={"hint": "Set LOCALSTACK_ENABLED=true and run: docker compose --profile localstack up -d"},
)


@register_connector
class LocalStackConnector(SaaSBaseConnector):
    """LocalStack AWS emulation — DataBridge connector.

    Single egress point for all LocalStack HTTP traffic.
    When LOCALSTACK_ENABLED=false (default), all methods return a safe
    disabled response — no network calls are made and no exceptions raised.
    """

    _connector_name = "localstack"
    _default_base_url = "http://localhost:4566"
    _endpoints = _URLLIB_ENDPOINTS

    def __init__(self) -> None:
        super().__init__()
        self._flag = IntegrationFeatureFlags.localstack()
        # Lazily configured by _ensure_configured()
        self._endpoint: str = ""
        self._region: str = ""
        self._access_key: str = ""
        self._secret_key: str = ""

    # ── Auth (LocalStack accepts any non-empty credentials) ───────────────────

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        # LocalStack health endpoints need no auth; boto3 handles AWS auth.
        return {}

    def _ensure_configured(self) -> None:
        if self._endpoint:
            return
        self._endpoint = self._config.get("endpoint", IntegrationFeatureFlags.localstack_endpoint())
        self._region = self._config.get("region", IntegrationFeatureFlags.localstack_region())
        ak, sk = IntegrationFeatureFlags.localstack_credentials()
        self._access_key = self._config.get("access_key", ak)
        self._secret_key = self._config.get("secret_key", sk)
        self._base_url = self._endpoint

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check(self) -> Dict[str, Any]:
        if not self._flag.enabled:
            return {
                "status": "disabled",
                "connector": self._connector_name,
                **self._flag.as_disabled_response(),
            }
        self._ensure_configured()
        url = f"{self._endpoint}/_localstack/health"
        try:
            data = self._http_get_noauth(url)
            services = data.get("services", {})
            running = [svc for svc, state in services.items() if state in ("running", "available")]
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "endpoint": self._endpoint,
                "localstack_version": data.get("version", "unknown"),
                "services_running": running,
                "services_total": len(services),
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "connector": self._connector_name,
                "endpoint": self._endpoint,
                "error": str(exc),
                "hint": "Is LocalStack running? docker compose --profile localstack up -d",
            }

    # ── Read ──────────────────────────────────────────────────────────────────

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        if not self._flag.enabled:
            return _DISABLED_RESPONSE
        self._ensure_configured()

        import time

        start = time.monotonic()
        table = request.table_name

        # urllib-accessible endpoints (no boto3 needed)
        if table in _URLLIB_ENDPOINTS:
            return self._read_urllib(table, start)

        # boto3-backed endpoints
        if table in _BOTO3_READ_MAP:
            return self._read_boto3(table, request, start)

        return ConnectorResponse(
            status="error",
            errors=[
                f"Unknown LocalStack table: {table!r}. "
                f"Valid: {sorted(list(_URLLIB_ENDPOINTS) + list(_BOTO3_READ_MAP))}"
            ],
        )

    def _read_urllib(self, table: str, start: float) -> ConnectorResponse:
        import time

        url = f"{self._endpoint}{_URLLIB_ENDPOINTS[table]}"
        try:
            raw = self._http_get_noauth(url)
        except Exception as exc:
            dur = int((time.monotonic() - start) * 1000)
            return ConnectorResponse(status="error", errors=[str(exc)], duration_ms=dur)

        if table == "services":
            services = raw.get("services", {})
            rows = [{"service": k, "status": v} for k, v in services.items()]
        else:
            rows = [raw] if isinstance(raw, dict) else (raw or [])

        dur = int((time.monotonic() - start) * 1000)
        return ConnectorResponse(status="ok", data=rows, row_count=len(rows), duration_ms=dur)

    def _read_boto3(self, table: str, request: ConnectorRequest, start: float) -> ConnectorResponse:
        import time

        if not _BOTO3_AVAILABLE:
            return ConnectorResponse(
                status="error",
                errors=[
                    f"boto3 is required for table '{table}'. "
                    "Install it: pip install boto3"
                ],
            )
        svc, method, result_key = _BOTO3_READ_MAP[table]
        try:
            client = self._get_boto3_client(svc)
            resp = getattr(client, method)()
            rows = resp.get(result_key, [])
            if not isinstance(rows, list):
                rows = [rows]
            if request.limit:
                rows = rows[: request.limit]
            dur = int((time.monotonic() - start) * 1000)
            return ConnectorResponse(status="ok", data=rows, row_count=len(rows), duration_ms=dur)
        except Exception as exc:
            dur = int((time.monotonic() - start) * 1000)
            return ConnectorResponse(status="error", errors=[str(exc)], duration_ms=dur)

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, request: ConnectorRequest, data: Any = None) -> ConnectorResponse:
        """Call a boto3 service method on LocalStack.

        request.filters["service"] : boto3 service name (e.g. "s3", "dynamodb")
        request.query              : boto3 method name  (e.g. "create_bucket")
        data                       : dict of kwargs to pass to the method
        """
        if not self._flag.enabled:
            return _DISABLED_RESPONSE
        self._ensure_configured()

        if not _BOTO3_AVAILABLE:
            return ConnectorResponse(
                status="error",
                errors=["boto3 is required for LocalStack write operations. Install: pip install boto3"],
            )

        import time

        start = time.monotonic()
        filters = request.filters or {}
        service = filters.get("service", "")
        method = (request.query or "").strip()

        if not service or not method:
            return ConnectorResponse(
                status="error",
                errors=["filters['service'] and query (method name) are required for LocalStack write()"],
            )

        try:
            client = self._get_boto3_client(service)
            kwargs = data if isinstance(data, dict) else {}
            result = getattr(client, method)(**kwargs)
            # Strip boto3 response metadata
            result.pop("ResponseMetadata", None)
            dur = int((time.monotonic() - start) * 1000)
            return ConnectorResponse(status="ok", data=[result], row_count=1, duration_ms=dur)
        except Exception as exc:
            dur = int((time.monotonic() - start) * 1000)
            return ConnectorResponse(status="error", errors=[str(exc)], duration_ms=dur)

    # ── Schema inference ──────────────────────────────────────────────────────

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        _schemas: Dict[str, List[SchemaField]] = {
            "health": [
                SchemaField("version"),
                SchemaField("services", "object"),
                SchemaField("edition"),
            ],
            "services": [SchemaField("service"), SchemaField("status")],
            "s3_buckets": [SchemaField("Name"), SchemaField("CreationDate")],
            "dynamodb_tables": [SchemaField("TableName")],
            "lambda_functions": [
                SchemaField("FunctionName"),
                SchemaField("Runtime"),
                SchemaField("Handler"),
                SchemaField("FunctionArn"),
            ],
            "sqs_queues": [SchemaField("QueueUrl")],
            "ecr_repositories": [
                SchemaField("repositoryName"),
                SchemaField("repositoryUri"),
                SchemaField("registryId"),
            ],
        }
        return SchemaDefinition(
            table_name=table_name,
            fields=_schemas.get(table_name, [SchemaField("data", "object")]),
        )

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
            max_batch_size=200,
            supported_formats=["json"],
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_boto3_client(self, service: str) -> Any:
        """Return a boto3 client pointed at LocalStack."""
        return boto3.client(  # type: ignore[name-defined]
            service,
            endpoint_url=self._endpoint,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )

    def _http_get_noauth(self, url: str) -> Any:
        """GET a URL without auth headers (LocalStack health endpoints)."""
        timeout = self._config.get("timeout", 15)
        req = urllib.request.Request(  # noqa: S310
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "ICDEV-DataBridge/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {body}") from exc
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise RuntimeError(f"LocalStack unreachable at {url}: {exc}") from exc
