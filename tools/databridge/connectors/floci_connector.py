# CUI // SP-CTI
"""DataBridge connector for the floci AWS service emulator (flx-bridge-01).

Single egress point for all emulator HTTP traffic from IDC and other tools.
Follows the same DataBridge connector pattern as GNS3Connector.

floci is MIT, Java/Quarkus, and serves the AWS API edge on port 4566. It is a
documented LocalStack drop-in: it keeps ``/_localstack/health`` and translates
``LOCALSTACK_*`` env vars by default, which is why that path is still spelled
here. The name it is REACHED BY, however, is ``floci`` -- a caller left asking
the registry for ``localstack`` now gets ``None``, which is a loud failure
rather than a stale one.

THE SWITCH IS ``tools/cloud/emulator.py`` (flx-seam-01) and this module does not
own a second copy of it. ``FLOCI_ENABLED``, default false, air-gap safe, with
``LOCALSTACK_ENABLED`` honoured as a deprecated alias. When disabled,
``health_check()`` reports ``disabled`` and every read/write returns a disabled
``ConnectorResponse`` -- no network calls, no exceptions raised.

``unsupported_without_docker`` IS THE POINT OF THIS CARD
--------------------------------------------------------
A docker socket is needed ONLY for CONTAINER-BACKED services (Lambda, RDS,
ElastiCache, OpenSearch, MSK, ECS/EC2/EKS). Everything else -- s3, dynamodb,
sqs, ecr -- is served in-process by the emulator and needs no socket.

``lambda_functions`` on a socket-less host used to reach boto3, raise, and come
back as an ``error`` carrying whatever the AWS SDK said; a caller that flattened
that to a list saw ``[]``. Empty means "this account has no functions".
Unsupported means "this deployment cannot answer that question". They are
different findings with different repairs, and conflating them is the
``rmf-disc-02`` ``nqe_client`` defect exactly: every local NQE query raised on a
table with no DDL anywhere in the repo, was swallowed by a broad ``except``,
returned ``[]``, and the attack-surface map correlated every advisory against
ZERO devices while reporting success.

So each logical table declares ``docker_backed`` -- DERIVED from the seam's
``CONTAINER_BACKED_SERVICES``, never a second hand-written list -- and a
container-backed table on a host whose socket is PROVEN absent returns
``status="unsupported_without_docker"``, which no reader can mistake for a row
count of zero.

REFUSE ONLY WHAT IS PROVEN UNAVAILABLE. ``emulator.docker_backed()`` is
tri-state and ``None`` (cannot tell -- a Windows named pipe is not reliably
stat-able) PERMITS the call: the emulator's own error is better evidence than
our guess. This module asks ``emulator.service_supported()``, which is that rule
stated once, rather than re-deriving it from ``docker_backed()``.

Logical table names:
  "health"              -> GET /_localstack/health           (no auth needed)
  "services"            -> parsed from /_localstack/health
  "s3_buckets"          -> AWS S3 ListBuckets               (requires boto3)
  "dynamodb_tables"     -> AWS DynamoDB ListTables          (requires boto3)
  "lambda_functions"    -> AWS Lambda ListFunctions         (requires boto3,
                                                             CONTAINER-BACKED)
  "sqs_queues"          -> AWS SQS ListQueues               (requires boto3)
  "ecr_repositories"    -> AWS ECR DescribeRepositories     (requires boto3)

Write operations use request.filters["service"] to select the AWS service and
request.query as the boto3 method name. Data is the method kwargs dict. A write
against a container-backed service is refused on the same terms as a read.

Config keys:
  endpoint   - emulator endpoint (default: from the seam)
  region     - AWS region         (default: from the seam, us-gov-west-1)
  access_key - AWS access key ID  (default: "test" -- the emulator dummy)
  secret_key - AWS secret key     (default: "test" -- the emulator dummy)
  timeout    - HTTP timeout in seconds (default: 15)
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from tools.cloud import emulator
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

# urllib-accessible read endpoints (no auth required on the emulator)
_URLLIB_ENDPOINTS: Dict[str, str] = {
    "health": emulator.HEALTH_PATH,
    "services": emulator.HEALTH_PATH,
}

# boto3 table -> (service_name, method_name, result_key)
_BOTO3_READ_MAP: Dict[str, tuple[str, str, str]] = {
    "s3_buckets": ("s3", "list_buckets", "Buckets"),
    "dynamodb_tables": ("dynamodb", "list_tables", "TableNames"),
    "lambda_functions": ("lambda", "list_functions", "Functions"),
    "sqs_queues": ("sqs", "list_queues", "QueueUrls"),
    "ecr_repositories": ("ecr", "describe_repositories", "repositories"),
}

#: Every logical table this connector serves, in declaration order.
TABLES: tuple[str, ...] = tuple(_URLLIB_ENDPOINTS) + tuple(_BOTO3_READ_MAP)


def table_service(table: str) -> Optional[str]:
    """AWS service backing *table*, or None for the emulator's own health API.

    ``health`` and ``services`` are the emulator's OWN endpoints rather than an
    emulated AWS service, so they have no service name and can never be
    container-backed -- an emulator that cannot answer its health path is
    ``unreachable``, which is a different verdict with a different repair.
    """
    mapped = _BOTO3_READ_MAP.get(table)
    return mapped[0] if mapped else None


def table_is_docker_backed(table: str) -> bool:
    """Does *table* need a docker socket to answer?

    DERIVED from ``emulator.CONTAINER_BACKED_SERVICES``. A second hand-written
    list here would be a second fact to keep in step, and its failure mode is
    silent: the seam gains a service, and this connector goes on returning an
    empty list for it.
    """
    service = table_service(table)
    return bool(service) and service in emulator.CONTAINER_BACKED_SERVICES


def _disabled_response() -> ConnectorResponse:
    """A fresh disabled response.

    Built per call rather than shared as a module constant: ``ConnectorResponse``
    is a mutable dataclass with mutable ``data`` / ``errors`` / ``metadata``, and
    one caller appending to a shared instance would poison every later refusal.
    """
    flag = IntegrationFeatureFlags.localstack()
    return ConnectorResponse(
        status="disabled",
        data=[],
        row_count=0,
        errors=[flag.reason or "AWS emulator (floci) integration is disabled"],
        metadata={
            "connector": "floci",
            "hint": (
                "Set FLOCI_ENABLED=true and run: "
                f"docker compose --profile {emulator.MODE} up -d"
            ),
        },
    )


def _unsupported_response(table: str, service: str) -> ConnectorResponse:
    """The refusal that must never read as an empty result.

    ``status`` is ``unsupported_without_docker``, NOT ``ok``, so a reader that
    checks status before data cannot take this for "no rows". ``data`` is empty
    because there is genuinely nothing to hand back -- the distinction lives in
    the status and the errors, which is the only place it can live without
    inventing rows that were never observed.
    """
    return ConnectorResponse(
        status=emulator.UNSUPPORTED_WITHOUT_DOCKER,
        data=[],
        row_count=0,
        errors=[
            f"Table {table!r} is backed by the container-backed AWS service "
            f"{service!r}; this deployment has no docker socket, so the emulator "
            f"cannot serve it. This is NOT an empty result -- the question was "
            f"not answered."
        ],
        metadata={
            "connector": "floci",
            "table": table,
            "service": service,
            "docker_backed": False,
            "docker_basis": emulator.docker_basis(),
            # probe=False on purpose: this refusal is a CONFIGURATION verdict
            # and must not cost an HTTP round trip. It therefore cannot say
            # `unreachable`; reachability is reported by whichever call actually
            # opened a socket.
            "emulator_status": emulator.status(probe=False),
            "hint": (
                "Mount a docker socket for the emulator (DOCKER_HOST or "
                "FLOCI_DOCKER_SOCKET), or ask a table the emulator serves "
                "in-process (s3_buckets, dynamodb_tables, sqs_queues, "
                "ecr_repositories)."
            ),
        },
    )


@register_connector
class FlociConnector(SaaSBaseConnector):
    """floci AWS emulation -- DataBridge connector.

    Single egress point for all emulator HTTP traffic. When the seam reports the
    emulator off (the default), every method returns a safe disabled response --
    no network calls are made and no exceptions raised.
    """

    _connector_name = "floci"
    _default_base_url = emulator.DEFAULT_ENDPOINT
    _endpoints = _URLLIB_ENDPOINTS

    def __init__(self) -> None:
        super().__init__()
        # The FeatureStatus SHAPE is DataBridge's (it carries the disabled
        # response every method returns); the ANSWER inside it is the seam's.
        self._flag = IntegrationFeatureFlags.localstack()
        # Lazily configured by _ensure_configured()
        self._endpoint: str = ""
        self._region: str = ""
        self._access_key: str = ""
        self._secret_key: str = ""

    # -- Auth (the emulator accepts any non-empty credentials) ----------------

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        # The health endpoint needs no auth; boto3 handles AWS SigV4.
        return {}

    def _ensure_configured(self) -> None:
        if self._endpoint:
            return
        self._endpoint = self._config.get("endpoint", emulator.endpoint())
        self._region = self._config.get("region", emulator.region())
        ak, sk = emulator.credentials()
        self._access_key = self._config.get("access_key", ak)
        self._secret_key = self._config.get("secret_key", sk)
        self._base_url = self._endpoint

    # -- Health --------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        if not self._flag.enabled:
            return {
                "status": "disabled",
                "connector": self._connector_name,
                **self._flag.as_disabled_response(),
            }
        self._ensure_configured()
        # Reported on BOTH legs: an operator looking at an unhealthy emulator
        # and one looking at a healthy emulator both need to know whether Lambda
        # can be served, and `docker_backed: null` says we cannot tell rather
        # than asserting either way.
        docker = {
            "docker_backed": emulator.docker_backed(),
            "docker_basis": emulator.docker_basis(),
            "unsupported_tables": self.unsupported_tables(),
        }
        url = f"{self._endpoint}{emulator.HEALTH_PATH}"
        try:
            data = self._http_get_noauth(url)
            services = data.get("services", {})
            running = [svc for svc, state in services.items() if state in ("running", "available")]
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "endpoint": self._endpoint,
                "emulator_version": data.get("version", "unknown"),
                "services_running": running,
                "services_total": len(services),
                **docker,
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "connector": self._connector_name,
                "endpoint": self._endpoint,
                "error": str(exc),
                "hint": (
                    "Is the emulator running? "
                    f"docker compose --profile {emulator.MODE} up -d"
                ),
                **docker,
            }

    def unsupported_tables(self) -> List[str]:
        """Tables this deployment PROVABLY cannot answer, named.

        Empty on a host whose socket is present OR unproven -- so an empty list
        here means "nothing is known to be unavailable", never "everything
        works". ``health_check()`` carries ``docker_basis`` beside it, which is
        what says which.
        """
        return sorted(
            t
            for t in TABLES
            if table_is_docker_backed(t)
            and not emulator.service_supported(str(table_service(t)))
        )

    # -- Read ----------------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        if not self._flag.enabled:
            return _disabled_response()
        self._ensure_configured()

        start = time.monotonic()
        table = request.table_name

        # The docker refusal comes BEFORE the boto3 availability check and
        # before any socket opens: "this deployment cannot answer" does not
        # depend on whether an optional SDK happens to be installed.
        service = table_service(table)
        if service and not emulator.service_supported(service):
            return _unsupported_response(table, service)

        # urllib-accessible endpoints (no boto3 needed)
        if table in _URLLIB_ENDPOINTS:
            return self._read_urllib(table, start)

        # boto3-backed endpoints
        if table in _BOTO3_READ_MAP:
            return self._read_boto3(table, request, start)

        return ConnectorResponse(
            status="error",
            errors=[f"Unknown floci table: {table!r}. Valid: {sorted(TABLES)}"],
        )

    def _read_urllib(self, table: str, start: float) -> ConnectorResponse:
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
        if not _BOTO3_AVAILABLE:
            return ConnectorResponse(
                status="error",
                errors=[f"boto3 is required for table '{table}'. Install it: pip install boto3"],
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

    # -- Write ---------------------------------------------------------------

    def write(self, request: ConnectorRequest, data: Any = None) -> ConnectorResponse:
        """Call a boto3 service method on the emulator.

        request.filters["service"] : boto3 service name (e.g. "s3", "dynamodb")
        request.query              : boto3 method name  (e.g. "create_bucket")
        data                       : dict of kwargs to pass to the method
        """
        if not self._flag.enabled:
            return _disabled_response()
        self._ensure_configured()

        start = time.monotonic()
        filters = request.filters or {}
        service = filters.get("service", "")
        method = (request.query or "").strip()

        if not service or not method:
            return ConnectorResponse(
                status="error",
                errors=[
                    "filters['service'] and query (method name) are required for floci write()"
                ],
            )

        # Same rule as read(): a create_function against a socket-less host is a
        # question this deployment cannot answer, not a call that failed.
        if not emulator.service_supported(str(service)):
            return _unsupported_response(f"write:{service}.{method}", str(service))

        if not _BOTO3_AVAILABLE:
            return ConnectorResponse(
                status="error",
                errors=["boto3 is required for floci write operations. Install: pip install boto3"],
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

    # -- Schema inference ----------------------------------------------------

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Declared schema for *table_name*, carrying its ``docker_backed`` flag.

        ``SchemaDefinition`` has no ``table_name`` field -- it carries ``fields``
        and ``metadata``. The previous spelling passed ``table_name=`` as a
        keyword and so raised ``TypeError`` on EVERY call, for every table, for
        the life of the connector; nothing caught it because nothing called it.
        The table name now rides in ``metadata``, which is the convention
        ``saas_base.infer_schema`` already uses.
        """
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
            fields=_schemas.get(table_name, [SchemaField("data", "object")]),
            metadata={
                "source": self._connector_name,
                "table": table_name,
                "service": table_service(table_name),
                # Declared for EVERY table, including the ones that never need a
                # socket: `false` is an answer, and a flag present only on the
                # container-backed tables would make its absence ambiguous.
                "docker_backed": table_is_docker_backed(table_name),
            },
        )

    def list_tables(self) -> List[str]:
        return list(TABLES)

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

    # -- Internal helpers ----------------------------------------------------

    def _get_boto3_client(self, service: str) -> Any:
        """Return a boto3 client pointed at the emulator."""
        return boto3.client(  # type: ignore[name-defined]
            service,
            endpoint_url=self._endpoint,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )

    def _http_get_noauth(self, url: str) -> Any:
        """GET a URL without auth headers (the emulator's health endpoint)."""
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
            raise RuntimeError(f"floci emulator unreachable at {url}: {exc}") from exc
