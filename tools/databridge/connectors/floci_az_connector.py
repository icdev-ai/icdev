# CUI // SP-CTI
"""DataBridge connector for the floci-az Azure service emulator (flx-az-01).

Single egress point for all Azure-emulator HTTP traffic. Follows the same
DataBridge connector pattern as :mod:`~tools.databridge.connectors.floci_connector`,
and deliberately differs from it in three ways that were MEASURED against
``floci/floci-az:0.12.0`` on 2026-09-05 -- see ``docs/spikes/flx-az-parity.md``.

THE SWITCH IS ``tools/cloud/emulator_az.py`` and this module owns no second copy
of it. ``FLOCI_AZ_ENABLED``, default false, air-gap safe. When disabled,
``health_check()`` reports ``disabled`` and every read returns a disabled
``ConnectorResponse`` -- no network calls, no exceptions raised.

READ ONLY. THERE IS NO AZURE IaC EXECUTOR, AND THIS SAYS SO
------------------------------------------------------------
ICDEV ships ``tools/cloud/aws_config_executor.py`` and no Azure analogue, so
``capabilities.supports_write`` is ``False`` and :meth:`FlociAzConnector.write`
returns a refusal that NAMES the missing executor rather than a generic error.
A caller must be able to tell "this platform cannot do that" from "that call
failed", and only the first is true here. Declaring execution support nothing
backs is the declared-but-unconsumed defect this platform ships most.

THE SUBSCRIPTION-SCOPED LIST IS EMPTY, AND THAT IS WHY THIS FANS OUT
--------------------------------------------------------------------
Measured: a subscription-scoped ARM list returns ``200 {"value":[]}`` for an
estate that demonstrably holds resources, while the resource-group-scoped list
returns them. Nothing in the status code or body distinguishes that from a
genuinely empty subscription.

So every inventory table here enumerates RESOURCE GROUPS first and issues one
list per group, through ``emulator_az.resource_list_paths`` -- the one place
that scope rule is spelled. Two consequences that the code makes explicit:

* **If the resource-group enumeration FAILS, the table reports ``error``,
  never ``ok`` with no rows.** A fan-out over zero groups produces zero rows,
  and returning that as a successful empty result would reproduce the exact
  fabricated-empty this connector was written to avoid.
* **A per-group list that fails is reported too** -- ``partial`` status with the
  failing groups named, never a silently short list. A partial inventory
  presented as a whole one is the same defect wearing a smaller number.

``unsupported_without_docker`` -- AND WHY NO INVENTORY TABLE USES IT
---------------------------------------------------------------------
The AWS connector refuses container-backed tables without a docker socket. The
measurement here says that would be WRONG for inventory: the ARM management
lane for every container-backed Azure service answered IDENTICALLY on a
socket-mounted and a provably socket-less emulator, because listing metadata
spawns no container. A socket is needed to START one.

So ``emulator_az.data_plane_supported()`` exists and is consulted by callers
that reach a data plane; no table in this READ-ONLY inventory connector does,
and refusing one here would be a fabricated refusal. The seam's constant is
re-exported for the twin adapter rather than re-derived.

Logical tables:
  "health"                  -> GET /_floci/health   (emulator's own, no auth)
  "subscriptions"           -> ARM, subscription lane   (measured: answers)
  "resource_groups"         -> ARM, subscription lane   (measured: answers)
  "resources"               -> ARM, PER RESOURCE GROUP
  "virtual_networks"        -> ARM, PER RESOURCE GROUP
  "network_security_groups" -> ARM, PER RESOURCE GROUP
  "storage_accounts"        -> ARM, PER RESOURCE GROUP
  "key_vaults"              -> ARM, PER RESOURCE GROUP
  "managed_identities"      -> ARM, PER RESOURCE GROUP

There is deliberately NO ``services`` table. The AWS emulator's health body
carries a service map; floci-az's does not
(``emulator_az.HEALTH_HAS_SERVICE_MAP`` is False), so the question is
unanswerable here and a table returning ``[]`` would be a fabrication.

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
from typing import Any, Dict, List, Optional

from tools.cloud import emulator_az
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

#: Tables served straight off the emulator's own API, no ARM involved.
PROBE_TABLES: tuple[str, ...] = ("health",)

#: Tables the SUBSCRIPTION lane answers. Both MEASURED to reflect writes --
#: ``resource_groups`` returned a group created moments earlier. This is the
#: narrow exception to the fan-out rule and it is enumerated, never inferred.
SUBSCRIPTION_TABLES: tuple[str, ...] = ("subscriptions", "resource_groups")

#: Tables that MUST fan out per resource group. ``resources`` is the generic
#: lane; the rest come from the seam's measured-answering ARM types, never a
#: second hand-written list -- a type added to the seam appears here for free,
#: and one removed cannot linger.
PER_RG_TABLES: tuple[str, ...] = ("resources",) + tuple(emulator_az.ARM_RESOURCE_TYPES)

#: Every logical table this connector serves, in declaration order.
TABLES: tuple[str, ...] = PROBE_TABLES + SUBSCRIPTION_TABLES + PER_RG_TABLES

#: Cap on resource groups fanned out over in one read. A bound that is REPORTED
#: rather than silent: exceeding it yields ``partial`` with the skipped groups
#: named, because a truncated sweep reporting only its successes reads as full
#: coverage.
MAX_RESOURCE_GROUPS = 200


def table_is_per_rg(table: str) -> bool:
    """Does *table* require the per-resource-group fan-out?"""
    return table in PER_RG_TABLES


def table_scope(table: str) -> str:
    """``probe`` | ``subscription`` | ``resource_group`` -- how *table* is read.

    Exposed so the twin adapter can report a read's SCOPE without pattern
    matching on the table name, and so a reader can tell a subscription-lane
    empty (a real answer) from a fan-out empty (which depends on whether the
    group enumeration succeeded).
    """
    if table in PROBE_TABLES:
        return "probe"
    if table in SUBSCRIPTION_TABLES:
        return "subscription"
    if table in PER_RG_TABLES:
        return "resource_group"
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
        errors=["Azure emulator (floci-az) integration is disabled"],
        metadata={
            "connector": "floci_az",
            "hint": (
                "Set FLOCI_AZ_ENABLED=true and run: "
                f"docker compose --profile {emulator_az.MODE} up -d"
            ),
        },
    )


@register_connector
class FlociAzConnector(SaaSBaseConnector):
    """floci-az Azure emulation -- DataBridge connector. READ ONLY.

    Single egress point for all Azure-emulator HTTP traffic. When the seam
    reports the emulator off (the default), every method returns a safe
    disabled response -- no network calls are made and no exceptions raised.
    """

    _connector_name = "floci_az"
    _default_base_url = emulator_az.DEFAULT_ENDPOINT
    _endpoints = {"health": emulator_az.HEALTH_PATH}

    def __init__(self) -> None:
        super().__init__()
        self._endpoint: str = ""

    # -- Auth ----------------------------------------------------------------

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        """No auth headers.

        MEASURED: the emulator's health path and every ARM lane this connector
        reads answered unauthenticated (HTTP 200). Only floci-az's Key Vault
        DATA plane demanded a bearer token (401 without one), and this connector
        does not read it -- ``key_vaults`` is the ARM MANAGEMENT list, which is
        a different surface. Minting a token we do not need would hand the
        emulator a credential for no gain.
        """
        return {}

    def _assert_endpoint_allowed(self, endpoint: str) -> None:
        """Refuse an endpoint whose HOST the connection does not allow.

        THE ENDPOINT IS THE ONLY DESTINATION THIS CONNECTOR HAS and it comes
        from the seam (``FLOCI_AZ_ENDPOINT``), so the connection row does not
        pin it -- a second copy would be a second switch. What the row DOES
        carry is the ceiling: which host the seam may point at. A seam mis-set
        to ``http://169.254.169.254`` is then refused rather than dialled, which
        matters more here than for the AWS emulator: floci-az serves an IMDS
        token endpoint of its own, so a confusion between the emulator's
        ``/metadata/identity`` and the real link-local one is a live hazard.

        WHY NOT ``_guard_egress``, WHICH THIS CLASS INHERITS. ``egress_guard`` is
        an internet SSRF gate and refuses this connector's own default endpoint
        twice over -- ``http://localhost:4577`` is ``scheme_not_https``, and the
        https spelling is ``denied_ip_range``. A loopback emulator over plain
        http is precisely what that guard exists to refuse. So the applicable
        half of its rule (which HOST) is applied from its own ``host_allowed``
        rather than a second copy, and the inapplicable half is named here
        rather than quietly dropped.

        Checked where the destination is DECIDED, not per URL, so it covers
        every ARM fan-out call rather than only the ones routed through one
        helper. An empty allowlist is no restriction, matching
        ``egress_guard``'s default-off semantics.
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
                f"floci-az endpoint {endpoint!r} refused ({reason}): host "
                f"{host or '<none>'!r} is not in this connection's egress_allowlist. "
                f"This is a HOST allowlist, not an SSRF gate -- it performs no "
                f"DNS resolution."
            )

    def _ensure_configured(self) -> None:
        if self._endpoint:
            return
        endpoint = self._config.get("endpoint", emulator_az.endpoint())
        # Before anything is cached: a refused endpoint must not become
        # reachable by calling twice, which the early return above would allow.
        self._assert_endpoint_allowed(endpoint)
        self._endpoint = endpoint
        self._base_url = self._endpoint

    # -- Health --------------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        if not emulator_az.enabled():
            return {
                "status": "disabled",
                "connector": self._connector_name,
                "reason": "FLOCI_AZ_ENABLED is not set",
            }
        self._ensure_configured()
        # Reported on BOTH legs: `docker_backed: null` says we cannot tell
        # rather than asserting either way.
        docker = {
            "docker_backed": emulator_az.docker_backed(),
            "docker_basis": emulator_az.docker_basis(),
        }
        url = f"{self._endpoint}{emulator_az.HEALTH_PATH}"
        try:
            data = self._http_get_noauth(url)
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "endpoint": self._endpoint,
                # MEASURED: the health body reports "dev", not the release. The
                # real version is in the image's FLOCI_AZ_VERSION env var, which
                # is not readable over HTTP -- so this is reported under a name
                # that says what it is rather than as `emulator_version`.
                "health_reported_version": data.get("version", "unknown"),
                "version_is_real": emulator_az.HEALTH_REPORTS_REAL_VERSION,
                "edition": data.get("edition", "unknown"),
                # There is NO services map on this emulator. Stated positively
                # so a reader cannot take its absence for "nothing is running".
                "services_enumerable": emulator_az.HEALTH_HAS_SERVICE_MAP,
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
                    f"docker compose --profile {emulator_az.MODE} up -d"
                ),
                **docker,
            }

    # -- Read ----------------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        if not emulator_az.enabled():
            return _disabled_response()
        self._ensure_configured()

        start = time.monotonic()
        table = request.table_name

        if table in PROBE_TABLES:
            return self._read_health(start)
        if table in SUBSCRIPTION_TABLES:
            return self._read_subscription_lane(table, request, start)
        if table in PER_RG_TABLES:
            return self._read_per_rg(table, request, start)

        return ConnectorResponse(
            status="error",
            errors=[f"Unknown floci-az table: {table!r}. Valid: {sorted(TABLES)}"],
        )

    def _read_health(self, start: float) -> ConnectorResponse:
        url = f"{self._endpoint}{emulator_az.HEALTH_PATH}"
        try:
            raw = self._http_get_noauth(url)
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error", errors=[str(exc)], duration_ms=_ms(start)
            )
        rows = [raw] if isinstance(raw, dict) else (raw or [])
        return ConnectorResponse(
            status="ok", data=rows, row_count=len(rows), duration_ms=_ms(start),
            metadata={"scope": "probe", "services_enumerable": False},
        )

    def _read_subscription_lane(
        self, table: str, request: ConnectorRequest, start: float
    ) -> ConnectorResponse:
        """The two tables the SUBSCRIPTION lane genuinely answers.

        Enumerated in :data:`SUBSCRIPTION_TABLES`, never inferred. An empty
        result here IS a real answer -- both were measured reflecting a write
        made moments earlier -- which is exactly what is NOT true of the
        resource-type lanes below.
        """
        if table == "subscriptions":
            path = f"/subscriptions?api-version={emulator_az.RESOURCES_API_VERSION}"
        else:
            path = emulator_az.resource_groups_path()
        try:
            body = self._http_get_noauth(f"{self._endpoint}{path}")
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error", errors=[str(exc)], duration_ms=_ms(start)
            )
        rows = _arm_value(body)
        if request.limit:
            rows = rows[: request.limit]
        return ConnectorResponse(
            status="ok", data=rows, row_count=len(rows), duration_ms=_ms(start),
            metadata={"scope": "subscription", "empty_is_a_real_answer": True},
        )

    def _read_per_rg(
        self, table: str, request: ConnectorRequest, start: float
    ) -> ConnectorResponse:
        """Fan out one ARM list per resource group and merge.

        THE ORDER OF THE TWO FAILURE CHECKS IS THE DESIGN. The group
        enumeration is asked FIRST and its failure is terminal, because every
        later step is conditioned on it: a fan-out over an unknown set of
        groups produces zero rows for a reason that has nothing to do with the
        estate, and reporting that as ``ok`` with ``row_count: 0`` is the
        fabricated empty this whole connector is shaped around avoiding.
        """
        groups, err = self._resource_groups()
        if err is not None:
            return ConnectorResponse(
                status="error",
                data=[],
                row_count=0,
                errors=[
                    f"Cannot list {table!r}: enumerating resource groups failed "
                    f"({err}). floci-az returns an EMPTY subscription-scoped "
                    f"list for a populated estate, so this connector fans out "
                    f"per resource group -- with no group list there is nothing "
                    f"to ask, and reporting zero rows here would assert an "
                    f"empty estate that was never measured."
                ],
                duration_ms=_ms(start),
                metadata={"scope": "resource_group", "resource_groups_enumerated": False},
            )

        skipped = groups[MAX_RESOURCE_GROUPS:]
        groups = groups[:MAX_RESOURCE_GROUPS]

        if table == "resources":
            paths = [emulator_az.generic_resources_path(rg) for rg in groups]
        else:
            paths = emulator_az.resource_list_paths(table, groups)

        rows: List[Dict[str, Any]] = []
        failed: List[str] = []
        for rg, path in zip(groups, paths):
            try:
                rows.extend(_arm_value(self._http_get_noauth(f"{self._endpoint}{path}")))
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{rg}: {exc}")

        if request.limit:
            rows = rows[: request.limit]

        # A partial sweep is never `ok`. Naming the groups that failed or were
        # skipped is what stops a short list reading as a complete one.
        partial = bool(failed or skipped)
        errors = []
        if failed:
            errors.append(f"{len(failed)} resource group(s) failed: {'; '.join(failed[:5])}")
        if skipped:
            errors.append(
                f"{len(skipped)} resource group(s) beyond MAX_RESOURCE_GROUPS="
                f"{MAX_RESOURCE_GROUPS} were not read: {', '.join(skipped[:5])}"
            )
        return ConnectorResponse(
            status="partial" if partial else "ok",
            data=rows,
            row_count=len(rows),
            errors=errors,
            duration_ms=_ms(start),
            metadata={
                "scope": "resource_group",
                "resource_groups_enumerated": True,
                "resource_groups_read": len(groups) - len(failed),
                "resource_groups_failed": failed,
                "resource_groups_skipped": skipped,
            },
        )

    def _resource_groups(self) -> tuple[List[str], Optional[str]]:
        """``(names, None)`` or ``([], reason)``. Never ``([], None)`` on failure.

        The two-value return is what keeps "no groups exist" (a real, measured
        answer) apart from "we could not find out" -- collapsing them into an
        empty list is precisely how a fan-out fabricates an empty estate.
        """
        try:
            body = self._http_get_noauth(
                f"{self._endpoint}{emulator_az.resource_groups_path()}"
            )
        except Exception as exc:  # noqa: BLE001
            return [], str(exc)
        names = [
            str(item["name"])
            for item in _arm_value(body)
            if isinstance(item, dict) and item.get("name")
        ]
        return names, None

    # -- Write ---------------------------------------------------------------

    def write(self, request: ConnectorRequest, data: Any = None) -> ConnectorResponse:
        """Always refused. ICDEV has no Azure IaC executor.

        NAMED rather than generic. ``tools/cloud/aws_config_executor.py`` exists
        and has no Azure analogue, so a caller must be able to tell "this
        platform cannot do that" from "that call failed" -- only the first is
        true, and a generic error would send someone debugging the emulator.
        """
        return ConnectorResponse(
            status="unsupported",
            data=[],
            row_count=0,
            errors=[
                "floci_az is READ-ONLY. ICDEV ships no Azure IaC executor "
                "(tools/cloud/aws_config_executor.py has no Azure analogue), so "
                "this connector deliberately declares no write capability rather "
                "than declaring execution support nothing backs. This is a "
                "platform capability gap, not a failed call."
            ],
            metadata={
                "connector": self._connector_name,
                "iac_execution_supported": emulator_az.IAC_EXECUTION_SUPPORTED,
            },
        )

    # -- Schema inference ----------------------------------------------------

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        """Declared schema for *table_name*, carrying its read SCOPE.

        ``SchemaDefinition`` has no ``table_name`` field; the name rides in
        ``metadata``, the convention ``saas_base.infer_schema`` already uses.
        """
        arm_fields = [
            SchemaField("id"),
            SchemaField("name"),
            SchemaField("type"),
            SchemaField("location"),
        ]
        schemas: Dict[str, List[SchemaField]] = {
            "health": [
                SchemaField("status"),
                SchemaField("edition"),
                SchemaField("version"),
            ],
            "subscriptions": [
                SchemaField("id"),
                SchemaField("subscriptionId"),
                SchemaField("displayName"),
            ],
            "resource_groups": arm_fields,
        }
        return SchemaDefinition(
            fields=schemas.get(table_name, arm_fields),
            metadata={
                "source": self._connector_name,
                "table": table_name,
                # Declared for EVERY table: a flag present only on the fanned-out
                # tables would make its absence ambiguous.
                "scope": table_scope(table_name),
                "per_resource_group": table_is_per_rg(table_name),
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
            # READ ONLY -- see write(). No Azure IaC executor exists.
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

        A 501 raises, and that is deliberate. floci-az routes an unknown path
        into its blob handler, which answers **501 NotImplemented** rather than
        404 -- so "did not 404" proves nothing on this emulator, and letting an
        error status through as data would turn an unrouted surface into a row.
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
            raise RuntimeError(f"floci-az emulator unreachable at {url}: {exc}") from exc


def _arm_value(body: Any) -> List[Dict[str, Any]]:
    """Rows out of an ARM ``{"value": [...]}`` envelope.

    A body that is not that shape yields NO rows rather than being wrapped as
    one: ARM's list contract is the envelope, and anything else is a response we
    did not understand -- manufacturing a row out of it would put an error
    document into an inventory.
    """
    if isinstance(body, dict):
        value = body.get("value")
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
