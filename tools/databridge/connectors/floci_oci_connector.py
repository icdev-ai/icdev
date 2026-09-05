# CUI // SP-CTI
"""DataBridge connector for the floci-oci OCI emulator (flx-oci-01). READ ONLY.

THE GOVERNED DOOR, AND WHY IT IS THE ONLY ONE
---------------------------------------------
Every read of the OCI emulator that ICDEV performs goes through
``tools/databridge/broker.py::fetch``, which authorizes the (agent, connector,
table) triple against ``args/databridge_agent_access.yaml`` and writes one
``databridge_agent_access_log`` row per call, allowed or denied. Importing this
class and calling :meth:`read` directly returns the same rows with NO
authorization check and NO audit row -- the ungoverned side channel ``cef-fnd-03``
exists to close. A structural test refuses a direct connector read from the twin
adapter for exactly that reason.

WHAT THIS CONNECTOR IS *NOT* EVIDENCE OF
-----------------------------------------
It reads the emulator over plain ``urllib``, so it works regardless of the
``oci`` SDK being absent. That makes it a NEW consumer, and it must not be read
as ICDEV's OCI support coming to life: ``tools/cloud/*_provider.py``'s OCI
classes are stubs that return constants and cannot reach any endpoint --
``emulator_oci.PROVIDER_LAYER_IS_STUBBED``, ``docs/spikes/flx-oci-parity.md`` §1.
A row here says the EMULATOR answered, never that ICDEV can talk to Oracle Cloud.

MEASURED SHAPES THIS CONNECTOR HANDLES AND ITS SIBLINGS DO NOT
--------------------------------------------------------------
* **Two row envelopes on one emulator.** ``queues`` wraps rows in
  ``{"items": [...]}``; the other ten lanes return a bare list. Handled in the
  seam's ``rows_from`` so the rule lives in one place.
* **An OKE cluster reporting ``ACTIVE`` is not a working cluster.** floci-oci
  0.4.0 spawns ``rancher/k3s`` without a ``--token``, k3s dies immediately, and
  the API never re-checks. The ``clusters`` table therefore carries
  ``lifecycle_is_unverified`` and this connector never promotes
  ``lifecycleState`` to a health verdict.
* **An empty list IS a real answer here.** ``compartmentId`` is honoured
  (measured: a bogus compartment returns 0 rows for four services), so unlike
  the Azure sibling there is no scope trap to fan out around.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from tools.cloud import emulator_oci
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
RESOURCE_TABLES: tuple[str, ...] = tuple(emulator_oci.REST_RESOURCE_PATHS)

#: Every logical table this connector serves, in declaration order.
TABLES: tuple[str, ...] = PROBE_TABLES + RESOURCE_TABLES

#: Scope reported on every schema, so a reader can tell what a row is ABOUT.
SCOPE_EMULATOR = "emulator"
SCOPE_COMPARTMENT = "compartment"


def table_scope(table: str) -> str:
    """Is *table* about the EMULATOR or about the COMPARTMENT's estate?

    Load-bearing for the twin adapter: ``health`` and ``enabled_services``
    describe the emulator, so counting their rows toward a resource count would
    make an empty estate look populated.
    """
    if table in PROBE_TABLES:
        return SCOPE_EMULATOR
    if table in RESOURCE_TABLES:
        return SCOPE_COMPARTMENT
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
        errors=["OCI emulator (floci-oci) integration is disabled"],
        metadata={
            "connector": "floci_oci",
            "hint": (
                "Set FLOCI_OCI_ENABLED=true and run: "
                f"docker compose --profile {emulator_oci.MODE} up -d"
            ),
        },
    )


@register_connector
class FlociOciConnector(SaaSBaseConnector):
    """floci-oci OCI emulation -- DataBridge connector. READ ONLY.

    Single egress point for all OCI-emulator HTTP traffic. When the seam
    reports the emulator off (the default), every method returns a safe
    disabled response -- no network calls are made and no exceptions raised.
    """

    _connector_name = "floci_oci"
    _default_base_url = emulator_oci.DEFAULT_ENDPOINT
    _endpoints = {"health": emulator_oci.HEALTH_PATH}

    def __init__(self) -> None:
        super().__init__()
        self._endpoint: str = ""

    # -- Auth ----------------------------------------------------------------

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """No auth headers.

        MEASURED: every REST lane this connector reads answered unauthenticated
        (HTTP 200) on ``floci/floci-oci:0.4.0``. Real OCI requires request
        signing with a tenancy/user/fingerprint/private-key quadruple; this
        emulator implements none of it, and minting a signature it would ignore
        would mean carrying an OCI private key for no gain.
        """
        return {}

    def _assert_endpoint_allowed(self, endpoint: str) -> None:
        """Refuse an endpoint whose HOST the connection does not allow.

        THE ENDPOINT IS THE ONLY DESTINATION THIS CONNECTOR HAS and it comes
        from the seam (``FLOCI_OCI_ENDPOINT``), so the connection row does not
        pin it -- a second copy would be a second switch. What the row DOES
        carry is the ceiling: which host the seam may point at. A seam mis-set
        to ``http://169.254.169.254`` is then refused rather than dialled.

        WHY NOT ``_guard_egress``, WHICH THIS CLASS INHERITS. ``egress_guard`` is
        an internet SSRF gate and refuses this connector's own default endpoint
        twice over -- ``http://localhost:4599`` is ``scheme_not_https``, and the
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
                f"floci-oci endpoint {endpoint!r} refused ({reason}): host "
                f"{host or '<none>'!r} is not in this connection's egress_allowlist. "
                f"This is a HOST allowlist, not an SSRF gate -- it performs no "
                f"DNS resolution."
            )

    def _ensure_configured(self) -> None:
        if self._endpoint:
            return
        endpoint = self._config.get("endpoint", emulator_oci.endpoint())
        # Before anything is cached: a refused endpoint must not become
        # reachable by calling twice, which the early return above would allow.
        self._assert_endpoint_allowed(endpoint)
        self._endpoint = endpoint
        self._base_url = self._endpoint

    # -- Health --------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        if not emulator_oci.enabled():
            return {
                "status": "disabled",
                "connector": self._connector_name,
                "reason": "FLOCI_OCI_ENABLED is not set",
            }
        self._ensure_configured()
        # Reported on BOTH legs: `docker_backed: null` says we cannot tell
        # rather than asserting either way.
        docker = {
            "docker_backed": emulator_oci.docker_backed(),
            "docker_basis": emulator_oci.docker_basis(),
        }
        url = f"{self._endpoint}{emulator_oci.HEALTH_PATH}"
        try:
            data = self._http_get_noauth(url)
            services = data.get("services")
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "endpoint": self._endpoint,
                # MEASURED: this body carries the real release (0.4.0).
                # Reported under a name that says where it came from.
                "health_reported_version": data.get("version", "unknown"),
                "version_is_real": emulator_oci.HEALTH_REPORTS_REAL_VERSION,
                # THE COUNT IS NOT A HEALTH SIGNAL, and the flag beside it is
                # what stops it being read as one. Measured: this map is
                # byte-identical on a deployment that provably cannot start a
                # container, and "running" is its only observed value.
                "declared_service_count": len(services) if isinstance(services, dict) else None,
                "service_map_is_enablement_only": (
                    emulator_oci.HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY
                ),
                # The emulator's two self-reports disagree; say so rather than
                # letting a reader assume the map is authoritative.
                "service_list_self_reports_disagree": (
                    emulator_oci.SERVICE_LIST_SELF_REPORTS_DISAGREE
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
                    f"docker compose --profile {emulator_oci.MODE} up -d"
                ),
                **docker,
            }

    # -- Read ----------------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        if not emulator_oci.enabled():
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
            errors=[f"Unknown floci-oci table: {table!r}. Valid: {sorted(TABLES)}"],
        )

    def _read_health(self, start: float) -> ConnectorResponse:
        url = f"{self._endpoint}{emulator_oci.HEALTH_PATH}"
        try:
            raw = self._http_get_noauth(url)
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(status="error", errors=[str(exc)], duration_ms=_ms(start))
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

        This is the one table whose NAME is doing safety work. floci-oci
        publishes eight services, every one reading ``"running"``, and the map
        is byte-identical on an emulator that provably cannot start a container.
        It reports what is compiled in, never what works.

        So: the rows carry the NAME only, never the ``"running"`` string -- a
        status field that always says the same thing is a constant wearing a
        measurement's name, and someone would render it as a health badge. The
        metadata says so in words as well, because a caller reading rows out of
        a DataBridge response does not see this docstring.
        """
        try:
            raw = self._http_get_noauth(f"{self._endpoint}{emulator_oci.HEALTH_PATH}")
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(status="error", errors=[str(exc)], duration_ms=_ms(start))
        services = raw.get("services") if isinstance(raw, dict) else None
        if not isinstance(services, dict):
            # NOT an empty list: the body was read and did not carry the map, so
            # "which services are declared" is unanswerable rather than answered
            # with none.
            return ConnectorResponse(
                status="error",
                # Explicitly EMPTY rather than left to default to None: the
                # caller must be able to tell "read, carried no map" from a
                # crash, and both an absent list and a null read as neither.
                data=[],
                row_count=0,
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
                # The emulator's startup log names SEVEN services and this map
                # names EIGHT. Carried so a reader comparing the two is not
                # left to guess which is wrong; measured, the log line is.
                "self_reports_disagree": emulator_oci.SERVICE_LIST_SELF_REPORTS_DISAGREE,
                "omitted_from_startup_log": sorted(emulator_oci.SERVICE_LIST_LOG_LINE_OMITS),
                "container_backed_services": sorted(emulator_oci.CONTAINER_BACKED_SERVICES),
                "known_broken_services": sorted(emulator_oci.FABRICATED_ACTIVE_WITH_DOCKER),
            },
        )

    def _read_resource(
        self, table: str, request: ConnectorRequest, start: float
    ) -> ConnectorResponse:
        """ONE request. No fan-out -- ``compartmentId`` is honoured here.

        An empty result IS a real answer, and the metadata says so, because the
        Azure sibling's identical-looking empty is NOT.
        """
        try:
            url = emulator_oci.resource_url(table)
        except KeyError:  # pragma: no cover -- RESOURCE_TABLES comes from the seam
            return ConnectorResponse(
                status="error",
                errors=[f"no measured REST lane for table {table!r}"],
                duration_ms=_ms(start),
            )
        try:
            body = self._http_get_noauth(url)
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(status="error", errors=[str(exc)], duration_ms=_ms(start))
        # Through the seam: `queues` wraps its rows and the other ten do not.
        rows = emulator_oci.rows_from(table, body)
        if request.limit:
            rows = rows[: request.limit]

        service = emulator_oci.TABLE_SERVICE.get(table, "unknown")
        metadata: Dict[str, Any] = {
            "scope": SCOPE_COMPARTMENT,
            "compartment_id": emulator_oci.compartment_id(),
            "empty_is_a_real_answer": True,
            "service": service,
        }
        if service in emulator_oci.FABRICATED_ACTIVE_WITH_DOCKER:
            # An OKE row says a record exists, never that a cluster runs.
            # Measured: floci-oci 0.4.0 spawns k3s with no --token, k3s exits
            # immediately, and lifecycleState stays ACTIVE with a dead endpoint.
            metadata["lifecycle_is_unverified"] = emulator_oci.OKE_LIFECYCLE_IS_UNVERIFIED
            metadata["note"] = (
                "lifecycleState on these rows is NOT verified. floci-oci 0.4.0 "
                "starts k3s without a --token; the container exits immediately "
                "and the API keeps reporting ACTIVE with an endpoint that has no "
                "listener. Treat a row as 'a record exists', never as 'a cluster runs'."
            )
        return ConnectorResponse(
            status="ok",
            data=rows,
            row_count=len(rows),
            duration_ms=_ms(start),
            metadata=metadata,
        )

    # -- Write ---------------------------------------------------------------

    def write(self, request: ConnectorRequest, data: Any = None) -> ConnectorResponse:
        """Always refused, for TWO independent reasons, and both are named.

        A caller must be able to tell "this platform cannot do that" from "that
        call failed" -- only the first is true, and a generic error would send
        someone debugging the emulator.
        """
        return ConnectorResponse(
            status="unsupported",
            data=[],
            row_count=0,
            errors=[
                "floci_oci is READ-ONLY, for two independent reasons. (1) ICDEV "
                "ships no OCI IaC executor -- tools/cloud/aws_config_executor.py "
                "has no OCI analogue. (2) ICDEV's OCI provider layer is stubbed: "
                "OCIObjectStorageProvider.list_objects is `return []` and its "
                "siblings are the same shape, so no endpoint can reach them "
                "(docs/spikes/flx-oci-parity.md §1). This is a platform "
                "capability gap, not a failed call."
            ],
            metadata={
                "connector": self._connector_name,
                "iac_execution_supported": emulator_oci.IAC_EXECUTION_SUPPORTED,
                "provider_layer_is_stubbed": emulator_oci.PROVIDER_LAYER_IS_STUBBED,
            },
        )

    # -- Schema inference ----------------------------------------------------

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Declared schema for *table_name*, carrying its read SCOPE.

        ``SchemaDefinition`` has no ``table_name`` field; the name rides in
        ``metadata``, the convention ``saas_base.infer_schema`` already uses.
        """
        ocid_named = [SchemaField("id"), SchemaField("displayName"), SchemaField("compartmentId")]
        schemas: Dict[str, List[SchemaField]] = {
            "health": [SchemaField("services"), SchemaField("version"), SchemaField("edition")],
            "enabled_services": [SchemaField("service")],
            "buckets": [
                SchemaField("namespace"),
                SchemaField("name"),
                SchemaField("compartmentId"),
                SchemaField("timeCreated"),
            ],
            "compartments": [
                SchemaField("id"),
                SchemaField("name"),
                SchemaField("compartmentId"),
                SchemaField("lifecycleState"),
            ],
            "users": ocid_named,
            "groups": ocid_named,
            "policies": ocid_named,
            "vaults": [
                SchemaField("id"),
                SchemaField("displayName"),
                SchemaField("compartmentId"),
                SchemaField("vaultType"),
                SchemaField("lifecycleState"),
            ],
            "keys": ocid_named,
            "queues": [
                SchemaField("id"),
                SchemaField("displayName"),
                SchemaField("compartmentId"),
                SchemaField("lifecycleState"),
            ],
            "streams": [
                SchemaField("id"),
                SchemaField("name"),
                SchemaField("compartmentId"),
                SchemaField("partitions"),
                SchemaField("streamPoolId"),
            ],
            "applications": [
                SchemaField("id"),
                SchemaField("displayName"),
                SchemaField("compartmentId"),
                SchemaField("subnetIds"),
                SchemaField("shape"),
            ],
            "clusters": [
                SchemaField("id"),
                SchemaField("name"),
                SchemaField("compartmentId"),
                SchemaField("kubernetesVersion"),
                # Present because the emulator returns it. NOT a health field --
                # see _read_resource's `lifecycle_is_unverified`.
                SchemaField("lifecycleState"),
            ],
        }
        return SchemaDefinition(
            fields=schemas.get(table_name, ocid_named),
            metadata={
                "source": self._connector_name,
                "table": table_name,
                # Declared for EVERY table: a flag present only on some would
                # make its absence ambiguous.
                "scope": table_scope(table_name),
                "service": emulator_oci.TABLE_SERVICE.get(table_name, "emulator"),
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
            # READ ONLY -- see write(). No OCI IaC executor, stubbed providers.
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

        A non-2xx raises rather than returning a body. On this emulator an
        unrouted path 404s cleanly, and a lane missing its required
        ``compartmentId`` returns 400 -- letting either through as data would
        put an error document into an inventory.
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
            raise RuntimeError(f"floci-oci emulator unreachable at {url}: {exc}") from exc


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
