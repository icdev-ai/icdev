# CUI // SP-CTI
"""DataBridge connector for the floci-gcp GCP service emulator (flx-gcp-01).

Single egress point for all GCP-emulator HTTP traffic. Follows the same
DataBridge connector pattern as :mod:`~tools.databridge.connectors.floci_connector`
and :mod:`~tools.databridge.connectors.floci_az_connector`, and differs from
both in ways that were MEASURED against ``floci/floci-gcp:0.8.0`` on 2026-09-05
-- see ``docs/spikes/flx-gcp-parity.md``.

THE SWITCH IS ``tools/cloud/emulator_gcp.py`` and this module owns no second
copy of it. ``FLOCI_GCP_ENABLED``, default false, air-gap safe. When disabled,
``health_check()`` reports ``disabled`` and every read returns a disabled
``ConnectorResponse`` -- no network calls, no exceptions raised.

READ ONLY. THERE IS NO GCP IaC EXECUTOR, AND THIS SAYS SO
---------------------------------------------------------
ICDEV ships ``tools/cloud/aws_config_executor.py`` and no GCP analogue, so
``capabilities.supports_write`` is ``False`` and :meth:`FlociGcpConnector.write`
returns a refusal that NAMES the missing executor rather than a generic error.
A caller must be able to tell "this platform cannot do that" from "that call
failed", and only the first is true here.

NO FAN-OUT, AND THAT IS A MEASUREMENT RATHER THAN AN OMISSION
-------------------------------------------------------------
The Azure connector enumerates resource groups and issues one list per group,
because a subscription-scoped ARM list returns an empty body for a populated
estate. **The GCP analogue does not have that defect**: project-scoped lists
were measured reflecting writes made moments earlier, for buckets, topics,
secrets and key rings alike. So each table here is ONE request, and an empty
result IS a real answer.

Inheriting the Azure fan-out "to be safe" would add a loop nothing here needs
and, worse, a comment citing a trap this emulator does not have.

WHAT IS ABSENT, BY NAME AND FOR STATED REASONS
-----------------------------------------------
* **firestore / datastore.** They answer NO REST path -- every one tried
  returned 404 or 405 -- while the same operations over gRPC answered
  immediately. This connector reads over HTTP, so it cannot read them, and a
  REST 404 is indistinguishable from "no such resource". Shipping a table that
  reliably returned ``[]`` would be a fabricated empty for two services that
  hold data. See ``emulator_gcp.GRPC_ONLY_SERVICES``.
* **cloudtasks.** No route found at any prefix or verb tried
  (``emulator_gcp.DECLARED_UNREACHABLE_SERVICES``).
* **a table at the documented GKE path.** ``/v1/projects/{p}/locations/{l}/
  clusters`` is served here by the Managed Kafka handler -- proven by the
  Redpanda container a create against it spawned. ``gke_clusters`` composes the
  ``/container/v1`` prefix instead, through the seam, so this connector never
  hand-builds the colliding form.

``unsupported_without_docker`` -- AND WHY NO INVENTORY TABLE USES IT
---------------------------------------------------------------------
Four services here are container-backed (Cloud SQL, Managed Kafka, GKE, Cloud
Run). LISTING them spawns nothing, so refusing ``sql_instances`` or
``gke_clusters`` on a socket-less host would be a fabricated refusal for a lane
that answers. ``emulator_gcp.data_plane_supported()`` exists for callers that
reach a data plane; no table in this READ-ONLY inventory connector does.

Logical tables:
  "health"            -> GET /health                (emulator's own, no auth)
  "enabled_services"  -> the health body's service map -- ENABLEMENT, NOT HEALTH
  "project"           -> GET /v1/projects/{p}
  "buckets"           -> GCS
  "topics"            -> Pub/Sub
  "secrets"           -> Secret Manager
  "key_rings"         -> Cloud KMS
  "service_accounts"  -> IAM
  "sql_instances"     -> Cloud SQL      (container-backed; listing is not)
  "datasets"          -> BigQuery
  "gke_clusters"      -> GKE, at the /container/v1 prefix

Config keys:
  endpoint - emulator endpoint (default: from the seam)
  timeout  - HTTP timeout in seconds (default: 15)
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from tools.cloud import emulator_gcp
from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    SchemaDefinition,
    SchemaField,
)
from tools.databridge.connectors.saas_base import SaaSBaseConnector
from tools.databridge.registry import register_connector

#: Tables served straight off the emulator's own health body, no REST lane.
PROBE_TABLES: tuple[str, ...] = ("health", "enabled_services")

#: Tables backed by a measured-answering REST lane. Taken FROM THE SEAM rather
#: than written out a second time -- a lane added there appears here for free,
#: and one removed cannot linger as a table that 404s.
RESOURCE_TABLES: tuple[str, ...] = tuple(emulator_gcp.REST_RESOURCE_PATHS)

#: Every logical table this connector serves, in declaration order.
TABLES: tuple[str, ...] = PROBE_TABLES + RESOURCE_TABLES

#: Scope reported on every schema, so a reader can tell what a row is ABOUT.
SCOPE_EMULATOR = "emulator"
SCOPE_PROJECT = "project"


def table_scope(table: str) -> str:
    """Is *table* about the EMULATOR or about the PROJECT's estate?

    The distinction is load-bearing for the twin adapter: ``health`` and
    ``enabled_services`` describe the emulator, so counting their rows toward a
    resource count would make an empty estate look populated.
    """
    if table in PROBE_TABLES:
        return SCOPE_EMULATOR
    if table in RESOURCE_TABLES:
        return SCOPE_PROJECT
    return "unknown"


def _disabled_response() -> ConnectorResponse:
    """A fresh disabled response.

    Built per call rather than shared as a module constant: ``ConnectorResponse``
    is a mutable dataclass and one caller appending to a shared instance would
    poison every later refusal.
    """
    return ConnectorResponse(
        status="disabled",
        data=[],
        row_count=0,
        errors=["GCP emulator (floci-gcp) integration is disabled"],
        metadata={
            "connector": "floci_gcp",
            "hint": (
                "Set FLOCI_GCP_ENABLED=true and run: "
                f"docker compose --profile {emulator_gcp.MODE} up -d"
            ),
        },
    )


@register_connector
class FlociGcpConnector(SaaSBaseConnector):
    """floci-gcp GCP emulation -- DataBridge connector. READ ONLY.

    Single egress point for all GCP-emulator HTTP traffic. When the seam
    reports the emulator off (the default), every method returns a safe
    disabled response -- no network calls are made and no exceptions raised.
    """

    _connector_name = "floci_gcp"
    _default_base_url = emulator_gcp.DEFAULT_ENDPOINT
    _endpoints = {"health": emulator_gcp.HEALTH_PATH}

    def __init__(self) -> None:
        super().__init__()
        self._endpoint: str = ""

    # -- Auth ----------------------------------------------------------------

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """No auth headers.

        MEASURED: every REST lane this connector reads answered
        unauthenticated (HTTP 200). Two surfaces on this emulator DO demand a
        credential -- Firebase Auth's project-scoped account lane returns 401
        without one, and `iamcredentials` will mint an opaque impersonation
        token on request -- and this connector reads neither. Minting a token we
        do not need would hand the emulator a credential for no gain.
        """
        return {}

    def _assert_endpoint_allowed(self, endpoint: str) -> None:
        """Refuse an endpoint whose HOST the connection does not allow.

        THE ENDPOINT IS THE ONLY DESTINATION THIS CONNECTOR HAS and it comes
        from the seam (``FLOCI_GCP_ENDPOINT``), so the connection row does not
        pin it -- a second copy would be a second switch. What the row DOES
        carry is the ceiling: which host the seam may point at. A seam mis-set
        to ``http://169.254.169.254`` is then refused rather than dialled.

        The metadata-server hazard is SMALLER here than on the Azure sibling and
        the guard is kept anyway: floci-gcp serves no ``/computeMetadata``
        surface at all (measured 404), so there is no emulator endpoint to
        confuse with the real link-local one -- but the guard bounds where the
        seam may point, which is a question about ICDEV's configuration rather
        than about this emulator's surface area.

        WHY NOT ``_guard_egress``, WHICH THIS CLASS INHERITS. ``egress_guard`` is
        an internet SSRF gate and refuses this connector's own default endpoint
        twice over -- ``http://localhost:4588`` is ``scheme_not_https``, and the
        https spelling is ``denied_ip_range``. A loopback emulator over plain
        http is precisely what that guard exists to refuse. So the applicable
        half of its rule (which HOST) is applied from its own ``host_allowed``
        rather than a second copy, and the inapplicable half is named here
        rather than quietly dropped.

        Checked where the destination is DECIDED, not per URL, so it covers
        every table read rather than only the ones routed through one helper. An
        empty allowlist is no restriction, matching ``egress_guard``'s
        default-off semantics.
        """
        allowlist = list(self._config.get("egress_allowlist") or [])
        denylist = list(self._config.get("egress_denylist") or [])
        if not allowlist and not denylist:
            return

        from tools.http.egress_guard import host_allowed

        host = urllib.parse.urlsplit(endpoint).hostname or ""
        allowed, reason = host_allowed(host, {"allowlist": allowlist, "denylist": denylist})
        if not allowed:
            raise PermissionError(
                f"floci-gcp endpoint {endpoint!r} refused ({reason}): host "
                f"{host or '<none>'!r} is not in this connection's egress_allowlist. "
                f"This is a HOST allowlist, not an SSRF gate -- it performs no "
                f"DNS resolution."
            )

    def _ensure_configured(self) -> None:
        if self._endpoint:
            return
        endpoint = self._config.get("endpoint", emulator_gcp.endpoint())
        # Before anything is cached: a refused endpoint must not become
        # reachable by calling twice, which the early return above would allow.
        self._assert_endpoint_allowed(endpoint)
        self._endpoint = endpoint
        self._base_url = self._endpoint

    # -- Health --------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        if not emulator_gcp.enabled():
            return {
                "status": "disabled",
                "connector": self._connector_name,
                "reason": "FLOCI_GCP_ENABLED is not set",
            }
        self._ensure_configured()
        # Reported on BOTH legs: `docker_backed: null` says we cannot tell
        # rather than asserting either way.
        docker = {
            "docker_backed": emulator_gcp.docker_backed(),
            "docker_basis": emulator_gcp.docker_basis(),
        }
        url = f"{self._endpoint}{emulator_gcp.HEALTH_PATH}"
        try:
            data = self._http_get_noauth(url)
            services = data.get("services")
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "endpoint": self._endpoint,
                # MEASURED: unlike floci-az, this body DOES carry the real
                # release. Reported under a name that says where it came from.
                "health_reported_version": data.get("version", "unknown"),
                "version_is_real": emulator_gcp.HEALTH_REPORTS_REAL_VERSION,
                # THE COUNT IS NOT A HEALTH SIGNAL, and the flag beside it is
                # what stops it being read as one. Measured: this map is
                # byte-identical on a deployment that provably cannot start a
                # container, and "running" is its only observed value.
                "declared_service_count": len(services) if isinstance(services, dict) else None,
                "service_map_is_enablement_only": (
                    emulator_gcp.HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY
                ),
                **docker,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "unhealthy",
                "connector": self._connector_name,
                "endpoint": self._endpoint,
                "error": str(exc),
                "hint": (
                    "Is the emulator running? "
                    f"docker compose --profile {emulator_gcp.MODE} up -d"
                ),
                **docker,
            }

    # -- Read ----------------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        if not emulator_gcp.enabled():
            return _disabled_response()
        self._ensure_configured()

        start = time.monotonic()
        table = request.table_name

        if table == "health":
            return self._read_health(start)
        if table == "enabled_services":
            return self._read_enabled_services(start)
        if table in RESOURCE_TABLES:
            return self._read_resource(table, request, start)

        return ConnectorResponse(
            status="error",
            errors=[f"Unknown floci-gcp table: {table!r}. Valid: {sorted(TABLES)}"],
        )

    def _read_health(self, start: float) -> ConnectorResponse:
        url = f"{self._endpoint}{emulator_gcp.HEALTH_PATH}"
        try:
            raw = self._http_get_noauth(url)
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error", errors=[str(exc)], duration_ms=_ms(start)
            )
        rows = [raw] if isinstance(raw, dict) else (raw or [])
        return ConnectorResponse(
            status="ok",
            data=rows,
            row_count=len(rows),
            duration_ms=_ms(start),
            metadata={"scope": SCOPE_EMULATOR},
        )

    def _read_enabled_services(self, start: float) -> ConnectorResponse:
        """The service map, as ENABLEMENT -- deliberately not called ``services``.

        This is the one table whose NAME is doing safety work. floci-gcp
        publishes 23 services, every one reading ``"running"``, and the map is
        byte-identical on an emulator that provably cannot start a container. It
        reports what is compiled in, never what works.

        So: the rows carry the NAME only, never the ``"running"`` string -- a
        status field that always says the same thing is a constant wearing a
        measurement's name, and someone would render it as a health badge. The
        metadata says so in words as well, because a caller reading rows out of
        a DataBridge response does not see this docstring.
        """
        try:
            raw = self._http_get_noauth(f"{self._endpoint}{emulator_gcp.HEALTH_PATH}")
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error", errors=[str(exc)], duration_ms=_ms(start)
            )
        services = raw.get("services") if isinstance(raw, dict) else None
        if not isinstance(services, dict):
            # NOT an empty list: the body was read and did not carry the map, so
            # "which services are declared" is unanswerable rather than answered
            # with none.
            return ConnectorResponse(
                status="error",
                errors=["health body carried no `services` map; enablement is unknown"],
                duration_ms=_ms(start),
                metadata={"scope": SCOPE_EMULATOR},
            )
        rows = [{"service": name} for name in sorted(services)]
        return ConnectorResponse(
            status="ok",
            data=rows,
            row_count=len(rows),
            duration_ms=_ms(start),
            metadata={
                "scope": SCOPE_EMULATOR,
                "is_enablement_not_health": True,
                "note": (
                    "Declared/compiled-in services. MEASURED byte-identical on an "
                    "emulator with no docker socket, which cannot start a "
                    "container -- this is not evidence that anything works."
                ),
                "grpc_only_services": sorted(emulator_gcp.GRPC_ONLY_SERVICES),
                "unreachable_services": sorted(
                    emulator_gcp.DECLARED_UNREACHABLE_SERVICES
                ),
            },
        )

    def _read_resource(
        self, table: str, request: ConnectorRequest, start: float
    ) -> ConnectorResponse:
        """ONE request. No fan-out -- project-scoped lists reflect writes here.

        An empty result IS a real answer, and the metadata says so, because the
        Azure sibling's identical-looking empty is NOT.
        """
        try:
            url = emulator_gcp.resource_url(table)
        except KeyError:  # pragma: no cover -- RESOURCE_TABLES comes from the seam
            return ConnectorResponse(
                status="error",
                errors=[f"no measured REST lane for table {table!r}"],
                duration_ms=_ms(start),
            )
        try:
            body = self._http_get_noauth(url)
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error", errors=[str(exc)], duration_ms=_ms(start)
            )
        # Through the seam: the row key is NOT uniform across lanes, and several
        # answer a bare `{}` with no key at all.
        rows = emulator_gcp.rows_from(table, body)
        if request.limit:
            rows = rows[: request.limit]
        return ConnectorResponse(
            status="ok",
            data=rows,
            row_count=len(rows),
            duration_ms=_ms(start),
            metadata={
                "scope": SCOPE_PROJECT,
                "project": emulator_gcp.project_id(),
                "empty_is_a_real_answer": True,
                "service": emulator_gcp.TABLE_SERVICE.get(table, "unknown"),
            },
        )

    # -- Write ---------------------------------------------------------------

    def write(self, request: ConnectorRequest, data: Any = None) -> ConnectorResponse:
        """Always refused. ICDEV has no GCP IaC executor.

        NAMED rather than generic. ``tools/cloud/aws_config_executor.py`` exists
        and has no GCP analogue, so a caller must be able to tell "this platform
        cannot do that" from "that call failed" -- only the first is true, and a
        generic error would send someone debugging the emulator.
        """
        return ConnectorResponse(
            status="unsupported",
            data=[],
            row_count=0,
            errors=[
                "floci_gcp is READ-ONLY. ICDEV ships no GCP IaC executor "
                "(tools/cloud/aws_config_executor.py has no GCP analogue), so "
                "this connector deliberately declares no write capability rather "
                "than declaring execution support nothing backs. This is a "
                "platform capability gap, not a failed call."
            ],
            metadata={
                "connector": self._connector_name,
                "iac_execution_supported": emulator_gcp.IAC_EXECUTION_SUPPORTED,
            },
        )

    # -- Schema inference ----------------------------------------------------

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Declared schema for *table_name*, carrying its read SCOPE.

        ``SchemaDefinition`` has no ``table_name`` field; the name rides in
        ``metadata``, the convention ``saas_base.infer_schema`` already uses.
        """
        named = [SchemaField("name")]
        schemas: Dict[str, List[SchemaField]] = {
            "health": [SchemaField("services"), SchemaField("version")],
            "enabled_services": [SchemaField("service")],
            "project": [
                SchemaField("projectId"),
                SchemaField("projectNumber"),
                SchemaField("lifecycleState"),
            ],
            "buckets": [SchemaField("id"), SchemaField("name"), SchemaField("location")],
            "topics": named,
            "secrets": [SchemaField("name"), SchemaField("createTime")],
            "key_rings": [SchemaField("name"), SchemaField("createTime")],
            "service_accounts": [SchemaField("name"), SchemaField("email")],
            "sql_instances": [
                SchemaField("name"),
                SchemaField("databaseVersion"),
                SchemaField("connectionName"),
            ],
            "datasets": [SchemaField("id"), SchemaField("datasetReference")],
            "gke_clusters": [SchemaField("name"), SchemaField("status")],
        }
        return SchemaDefinition(
            fields=schemas.get(table_name, named),
            metadata={
                "source": self._connector_name,
                "table": table_name,
                # Declared for EVERY table: a flag present only on some would
                # make its absence ambiguous.
                "scope": table_scope(table_name),
                "service": emulator_gcp.TABLE_SERVICE.get(table_name, "emulator"),
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
            # READ ONLY -- see write(). No GCP IaC executor exists.
            supports_read=True,
            supports_write=False,
            supports_schema_inference=True,
            supports_incremental=False,
            max_batch_size=200,
            supported_formats=["json"],
        )

    # -- Internal helpers ----------------------------------------------------

    def _http_get_noauth(self, url: str) -> Any:
        """GET a URL without auth headers.

        A non-2xx raises rather than returning a body. That matters more than
        it looks on this emulator: an unrouted path 404s in ONE OF TWO SHAPES --
        a GCS-style error JSON when the first path segment parses as a bucket
        name, and an HTML ``Resource not found`` page otherwise. Letting either
        through as data would put an error document into an inventory, and the
        JSON one would parse cleanly while doing it.
        """
        timeout = self._config.get("timeout", 15)
        req = urllib.request.Request(  # noqa: S310 -- operator-configured endpoint
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
            raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {body[:300]}") from exc
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise RuntimeError(f"floci-gcp emulator unreachable at {url}: {exc}") from exc


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
