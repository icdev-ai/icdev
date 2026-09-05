# CUI // SP-CTI
"""Twin over the LOCAL floci-gcp GCP emulator (flx-gcp-01).

The GCP sibling of :mod:`tools.twin_core.adapters.floci` and
:mod:`tools.twin_core.adapters.floci_az`. Like both, every read goes through the
governed DataBridge door as ``twin_observatory_analyst``, and every snapshot is
marked ``emulated``. What differs is MEASURED rather than assumed -- see
``docs/spikes/flx-gcp-parity.md``.

``target_csp`` IS ``gcp``, WHICH IS THE POINT OF THE CARD
----------------------------------------------------------
``gcp`` is already a recognised value in ``schema.normalize_csp`` and
``target_presets._csp_to_target`` returns it for any scope, so no vocabulary
needed widening. The region is ``us-central1`` -- a third spelling, matching
neither ``us-gov-west-1`` (AWS) nor ``usgovvirginia`` (Azure).

THE PRESET IS ``gcp_assured_workloads`` AND ITS SCOPE IS NOT COSMETIC
---------------------------------------------------------------------
GCP has no GovCloud-style partition. Every GCP entry in the service catalog
carries ``govcloud_available: false`` and ``assured_workloads: true``, so a
preset scoped ``government`` marks ALL EIGHT GCP services unavailable -- eight
fabricated findings on the first delta anyone simulates -- while a ``commercial``
scope silently drops the government question. ``region_scope:
assured_workloads`` is read by ``target_presets._available_in_target``, which
this card extended additively (no shipped preset used that scope, so no existing
verdict moved).

THE READ LADDER DIFFERS FROM BOTH SIBLINGS', MEASURABLY
--------------------------------------------------------
* **No ``sdk_unavailable`` rung.** The AWS connector needs ``boto3`` for five of
  its seven tables and ``boto3`` is not a declared dependency, so "the SDK is
  missing" is a real outcome there. ``floci_gcp_connector`` uses ``urllib``
  only, so that rung cannot fire and is absent rather than dead.
* **No ``scope_unmeasured`` rung, and its absence is a measurement.** The Azure
  twin needs one because a subscription-scoped ARM list returns an empty body
  for a populated estate, forcing a per-resource-group fan-out that can be
  incomplete. Measured here, project-scoped lists reflect writes -- so each
  table is ONE request, an empty result is a real answer, and there is no
  partial state to represent. Carrying the rung anyway would imply a hazard this
  emulator does not have.
* **No ``unsupported_without_docker`` rung.** Four services ARE container-backed
  (Cloud SQL, Managed Kafka, GKE, Cloud Run), but LISTING them spawns nothing.
  Refusing an inventory read for a missing socket would be a fabricated refusal.
  The socket still matters for DATA planes and :meth:`simulate_delta` reports it
  there.

WHAT THE ESTATE COUNT DELIBERATELY EXCLUDES
--------------------------------------------
``health`` and ``enabled_services`` describe the EMULATOR, not the estate it
holds. Counting either would make an empty project report 24 resources -- one
per declared service plus the health row -- which is the fabricated-population
mirror of the fabricated empty this file is otherwise shaped around.
``enabled_services`` is the sharper of the two: it returns 23 rows on an
emulator with nothing in it at all.

READ ONLY. ICDEV ships no GCP IaC executor, so nothing here applies anything.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.cloud import emulator_gcp
from tools.databridge import broker
from tools.databridge.connectors.floci_gcp_connector import (
    PROBE_TABLES,
    TABLES,
    table_scope,
)
from tools.logging.icdev_logger import get_logger
from tools.twin_core.registry import TwinAdapter, register_twin
from tools.twin_core.schema import PROVENANCE_EMULATED, canonical_violation

logger = get_logger(__name__)

#: The agent id every brokered read is made as. NOT a borrowed one: the twin
#: holds this grant, and the audit row must name the twin rather than a lender.
BROKER_AGENT_ID = "twin_observatory_analyst"

#: The connector name in the access manifest.
CONNECTOR = "floci_gcp"

#: Provenance every snapshot written here carries. NOT a parameter.
PROVENANCE = PROVENANCE_EMULATED

#: ``gcp`` is already canonical in ``schema.normalize_csp`` -- unlike ``azure``,
#: which normalizes to ``azure_gov``. No alias needed.
TARGET_CSP = "gcp"
#: Kept in step with ``emulator_gcp.DEFAULT_REGION``.
TARGET_REGION = "us-central1"
#: Preset used when a caller names none. A DEFAULT, never a fixed target.
DEFAULT_TARGET_PRESET = "gcp_assured_workloads"

# ── read outcomes ────────────────────────────────────────────────────────────
READ_ANSWERED = "answered"
READ_DISABLED = "disabled"
READ_DENIED = "denied"
READ_ERROR = "error"

#: Rows counted toward ``resource_count``. Both probe tables are excluded --
#: they describe the emulator, not the estate. ``project`` is excluded for the
#: same reason a subscription is excluded on the Azure twin: it is the
#: CONTAINER, not a resource in it, and counting it would make an empty project
#: report one resource.
_ESTATE_TABLES: tuple[str, ...] = tuple(
    t for t in TABLES if t not in PROBE_TABLES and t != "project"
)

_MAX_ROWS_PER_TABLE = 200


# ── read classification ──────────────────────────────────────────────────────


def classify_read(outcome: Any) -> str:
    """Categorize one brokered read into a ``READ_*`` outcome.

    Pure over a :class:`~tools.databridge.broker.FetchOutcome`, so the ladder is
    testable without a broker, a connector or an emulator.

    Order matters. A DENIAL is tested first: a refused call never reached the
    connector, so its (empty) ``connector_status`` says nothing at all, and
    reading it as "answered with no rows" is exactly the conflation this adapter
    exists to refuse.

    ``partial`` is deliberately NOT a rung here, unlike the Azure twin. Each
    table is one request against a lane measured to reflect writes, so a read
    either answered or did not -- and a status this ladder does not recognise
    falls to ``error`` below rather than being invented into an answer.
    """
    if not getattr(outcome, "ok", False):
        # A denial, an air-gap refusal, an unresolvable connection, or a fetch
        # whose audit row could not be written. None of them measured anything.
        return READ_DENIED
    status = str(getattr(outcome, "connector_status", "") or "")
    if status == READ_DISABLED:
        return READ_DISABLED
    if status == "ok":
        return READ_ANSWERED
    # An unrecognised status is NOT an answer -- including `partial`, which this
    # connector never emits. Defaulting to `answered` is how a new connector
    # state would silently become a `pass`.
    return READ_ERROR


def denial_basis(granted: bool | None, emulator_enabled: bool | None) -> str:
    """Why did every brokered read come back refused?

    THE GOVERNED DOOR LOSES A DISTINCTION AND THIS RESTORES IT, structurally.
    ``saas_base.connect`` returns False whenever ``health_check`` is not
    ``healthy`` -- which covers BOTH "the emulator is switched off" and "the
    emulator is on and nothing answered" -- and the broker turns that into one
    refusal. So both causes arrive here looking identical.

    The verdict is ``unknown`` either way and is NOT at stake. The BASIS is,
    because the repairs differ: set ``FLOCI_GCP_ENABLED=true``, versus
    ``docker compose --profile floci-gcp up -d``.

    Resolved from STRUCTURED facts, never from the refusal's prose -- a basis
    keyed on an error string goes silently wrong the day that string changes.
    """
    if granted is False:
        return "broker_denied"
    if emulator_enabled is False:
        return "disabled"
    if granted and emulator_enabled:
        return "unreachable"
    return "broker_denied"


def classify_verdict(
    reads: dict[str, str],
    *,
    granted: bool | None = None,
    emulator_enabled: bool | None = None,
) -> tuple[str, str]:
    """Return ``(verdict, basis)`` for a whole snapshot's per-table outcomes.

    Pure and total over its inputs. ``granted`` / ``emulator_enabled`` refine
    only the BASIS of a refusal and can never move the verdict.
    """
    if not reads:
        return "unknown", "unmeasured"
    values = set(reads.values())
    if READ_DISABLED in values:
        return "unknown", "disabled"
    if READ_DENIED in values:
        return "unknown", denial_basis(granted, emulator_enabled)
    # The emulator's OWN health path failing is unreachability, not a failure of
    # an emulated service -- and an unreachable emulator was never measured.
    if any(reads.get(t) == READ_ERROR for t in PROBE_TABLES):
        return "unknown", "unreachable"
    if READ_ERROR in values:
        return "fail", "emulator_errors"
    return "pass", "all_tables_answered"


# ── adapter ──────────────────────────────────────────────────────────────────


@register_twin
class FlociGcpTwinAdapter(TwinAdapter):
    """Twin over the local floci-gcp GCP emulator. Reads only through the broker."""

    canvas_key = "floci_gcp"
    display_name = "Floci GCP Emulator"
    #: Provenance carried onto every canonical violation. The word ``emulator``
    #: is load-bearing on the observatory: a reader seeing this method beside
    #: ``heuristic`` or ``iqe-gate`` is told, without opening the row, that the
    #: finding came from an emulated estate.
    method = "emulator-probe"
    snapshot_table = "floci_gcp_twin_snapshots"
    snapshot_time_col = "created_at"

    def _fleet_conn(self):
        """Connection for the snapshot table.

        ``get_canvas_connection`` rather than ``get_connection``:
        ``floci_gcp_twin_snapshots`` carries no ``classification`` /
        ``tenant_id`` column, so the global RLS predicate would raise
        ``UndefinedColumn`` on every query.
        """
        from tools.db.storage import get_canvas_connection

        return get_canvas_connection()

    # -- brokered reads -------------------------------------------------------

    def _read_table(self, table: str, limit: int = _MAX_ROWS_PER_TABLE):
        """One governed read. Never raises -- a refusal is a result."""
        return broker.fetch(
            BROKER_AGENT_ID,
            CONNECTOR,
            table,
            limit=limit,
            classification="UNCLASSIFIED",
        )

    @staticmethod
    def _is_granted() -> bool | None:
        """Is ``floci_gcp`` reachable by this twin's agent id? ``None`` if unknown.

        ``broker.list_available`` is the broker's OWN answer to "what may this
        agent read", so the adapter never re-derives the manifest rule.
        """
        try:
            return any(
                entry.get("connector") == CONNECTOR
                for entry in broker.list_available(BROKER_AGENT_ID)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("floci-gcp twin: grant lookup unavailable: %s", exc)
            return None

    @staticmethod
    def _emulator_enabled() -> bool | None:
        """The ONE GCP switch. Configuration only -- no socket opens."""
        try:
            return bool(emulator_gcp.enabled())
        except Exception as exc:  # noqa: BLE001
            logger.debug("floci-gcp twin: emulator seam unavailable: %s", exc)
            return None

    def _read_all(self, tables: tuple[str, ...] = TABLES) -> dict[str, dict]:
        """Read every declared table through the broker; return per-table detail."""
        detail: dict[str, dict] = {}
        for table in tables:
            outcome = self._read_table(table)
            kind = classify_read(outcome)
            detail[table] = {
                "outcome": kind,
                "connector_status": getattr(outcome, "connector_status", ""),
                # NONE, never 0, unless the table actually answered.
                "row_count": (
                    getattr(outcome, "row_count", 0) if kind == READ_ANSWERED else None
                ),
                "scope": table_scope(table),
                # Both error channels, kept apart: the BROKER's refusal reason
                # and the CONNECTOR's own errors are different findings.
                "broker_error": getattr(outcome, "error", "") or "",
                "connector_errors": list(getattr(outcome, "connector_errors", None) or []),
                "audited": bool(getattr(outcome, "audited", True)),
            }
        return detail

    # -- snapshot -------------------------------------------------------------

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        """Freeze the emulated GCP estate as read through the broker.

        ``target_id`` names the emulator instance being frozen (a deployment may
        run more than one); it is a label, not a lookup key -- the endpoint comes
        from the ``tools/cloud/emulator_gcp.py`` seam, which is the one switch.
        """
        target_id = str(target_id or "local")
        detail = self._read_all()
        reads = {t: d["outcome"] for t, d in detail.items()}
        verdict, basis = classify_verdict(
            reads,
            granted=self._is_granted(),
            emulator_enabled=self._emulator_enabled(),
        )

        # NONE, never 0, when nothing was measured. An unreachable emulator holds
        # an unknown number of resources, and 0 asserts it holds none.
        answered = [t for t in _ESTATE_TABLES if reads.get(t) == READ_ANSWERED]
        resource_count = (
            sum(int(detail[t]["row_count"] or 0) for t in answered) if answered else None
        )
        tables_ok = sum(1 for v in reads.values() if v == READ_ANSWERED)

        snap = {
            "id": f"floci-gcp-snap-{uuid.uuid4().hex[:12]}",
            "target_id": target_id,
            "label": label or "",
            "provenance": PROVENANCE,
            "target_csp": TARGET_CSP,
            "region": TARGET_REGION,
            "project": emulator_gcp.project_id(),
            "verdict": verdict,
            "verdict_basis": basis,
            "resource_count": resource_count,
            # False unless EVERY estate table answered. A consumer that quotes
            # `resource_count` without reading this is quoting a subtotal.
            "resource_count_is_complete": bool(answered)
            and all(reads.get(t) == READ_ANSWERED for t in _ESTATE_TABLES),
            # Named on every snapshot, not only when something is wrong: these
            # hold data this twin structurally cannot read, so an estate summary
            # that omitted them would be silently scoped.
            "unread_services": {
                "grpc_only": sorted(emulator_gcp.GRPC_ONLY_SERVICES),
                "no_route_found": sorted(emulator_gcp.DECLARED_UNREACHABLE_SERVICES),
            },
            "tables_ok": tables_ok,
            "tables_declared": len(TABLES),
            "tables": detail,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        snap["persisted"] = self._persist_snapshot(snap)
        snap["snapshot_id"] = snap["id"]
        return snap

    def _persist_snapshot(self, snap: dict) -> bool:
        """INSERT one snapshot row. Best-effort; returns whether it landed.

        THIS FUNCTION TAKES NO ``provenance`` ARGUMENT AND NEVER WILL. The column
        is bound to the module constant :data:`PROVENANCE`, which is
        ``schema.PROVENANCE_EMULATED`` -- a caller cannot pass a provenance in,
        and ``snap`` is not consulted for one. ``tests/cloud/test_floci_gcp_twin``
        reads this function's AST to prove it, because a behavioural test over
        today's callers -- which pass none -- would still pass the day somebody
        threads a kwarg through.
        """
        try:
            conn = self._fleet_conn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("floci-gcp twin: snapshot connection failed: %s", exc)
            return False
        try:
            conn.execute(
                "INSERT INTO floci_gcp_twin_snapshots "
                "(id, target_id, label, provenance, target_csp, region, project_id, "
                "verdict, verdict_basis, resource_count, tables_ok, tables_declared, "
                "payload_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    snap["id"],
                    snap["target_id"],
                    snap["label"],
                    PROVENANCE,
                    snap["target_csp"],
                    snap["region"],
                    snap["project"],
                    snap["verdict"],
                    snap["verdict_basis"],
                    snap["resource_count"],
                    snap["tables_ok"],
                    snap["tables_declared"],
                    json.dumps(snap["tables"], default=str),
                ),
            )
            conn.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("floci-gcp twin: snapshot INSERT failed: %s", exc)
            return False
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def list_snapshots(self, target_id: str, limit: int = 20, **kwargs) -> list[dict]:
        """Persisted snapshots for ``target_id``, newest first (empty on error)."""
        try:
            conn = self._fleet_conn()
        except Exception:  # noqa: BLE001
            return []
        try:
            rows = conn.execute(
                "SELECT id, target_id, label, provenance, target_csp, region, "
                "project_id, verdict, verdict_basis, resource_count, tables_ok, "
                "tables_declared, created_at "
                "FROM floci_gcp_twin_snapshots WHERE target_id=%s "
                "ORDER BY created_at DESC LIMIT %s",
                (str(target_id or "local"), int(limit)),
            ).fetchall()
        except Exception:  # noqa: BLE001 -- table may not exist yet
            return []
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        keys = ("id", "target_id", "label", "provenance", "target_csp", "region",
                "project_id", "verdict", "verdict_basis", "resource_count",
                "tables_ok", "tables_declared", "created_at")
        out: list[dict] = []
        for row in rows or []:
            if isinstance(row, (list, tuple)):
                out.append({k: row[i] if i < len(row) else None for i, k in enumerate(keys)})
            else:
                try:
                    out.append({k: row[k] for k in keys})
                except (KeyError, TypeError, IndexError):
                    out.append(dict(row) if hasattr(row, "keys") else {})
        return out

    def latest_status(self, target_id: str, **kwargs) -> dict:
        """The newest PERSISTED verdict for ``target_id`` -- no emulator probe.

        Deliberately does not read the estate: this is the cheap status the
        observatory renders, and a fan-out across eleven brokered tables on every
        page render would put many emulator round trips on the dashboard.

        With nothing persisted the verdict is ``unknown`` with basis
        ``no_snapshot`` -- the twin has never looked, which is not a clean bill
        of health and must not read as one.
        """
        kwargs.pop("limit", None)
        snaps = self.list_snapshots(target_id, limit=1, **kwargs)
        latest = snaps[0] if snaps else None
        return {
            "canvas": self.canvas_key,
            "target_id": str(target_id or "local"),
            "verdict": (latest or {}).get("verdict") or "unknown",
            "verdict_basis": (latest or {}).get("verdict_basis") or "no_snapshot",
            "provenance": (latest or {}).get("provenance") or PROVENANCE,
            "target_csp": (latest or {}).get("target_csp") or TARGET_CSP,
            "snapshot_count": len(snaps),
            "latest_snapshot": latest,
            "method": self.method,
        }

    # -- simulation -----------------------------------------------------------

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """Score a proposed set of GCP services against emulator + target reality.

        ``delta`` is anything naming GCP services -- ``{"services": [...]}``, a
        Terraform plan, a list of resource types. FOUR independent questions are
        asked and none substitutes for another:

        * **IS IT REACHABLE ON THIS EMULATOR AT ALL?** ``cloudtasks`` is listed
          as enabled and no route reached it (measured 2026-09-05). A
          ``service_parity`` finding at ``medium``, reported SEPARATELY from the
          docker one because the repair differs: there is nothing to mount.
        * **CAN A REST CLIENT READ IT?** ``firestore`` and ``datastore`` work --
          over gRPC only. That is a finding at ``low``: the service is fine and
          ICDEV's own connector stack cannot see it, which is a statement about
          our reach rather than about the emulator or the target.
        * **CAN THIS HOST EXERCISE ITS DATA PLANE?** A container-backed service
          with no docker socket, at ``medium``. For ``cloudrun`` the severity is
          raised to ``high``: measured, a socket-less deploy returns **200** with
          a service body indistinguishable from a real one, so the failure is
          silent and a rehearsal there produces a fabricated success.
        * **IS IT AVAILABLE IN THE TARGET?** Delegated to the Assured Workloads
          preset through :meth:`TwinAdapter._target_augment`. That is a fact
          about GCP and is NOT weakened by running against an emulator.

        The verdict starts at ``unknown``: this simulation is static (it asks the
        seam's measured constants and the docker seam, not the emulator), so a
        delta naming no service is honestly unscored rather than a free ``pass``.
        """
        delta = delta if delta is not None else {}
        preset = kwargs.get("target_preset", DEFAULT_TARGET_PRESET)

        violations: list[dict] = []
        for service in sorted(self._services_in(delta)):
            if service in emulator_gcp.DECLARED_UNREACHABLE_SERVICES:
                violations.append(canonical_violation(
                    "medium",
                    "service_parity",
                    f"floci-gcp lists '{service}' among its enabled services but no "
                    f"route reached it (measured 2026-09-05, floci/floci-gcp:0.8.0, "
                    f"REST and gRPC, several prefixes and verbs). This service cannot "
                    f"be rehearsed on this emulator at all -- there is nothing to mount "
                    f"or configure. Rehearse it elsewhere, and do not design a "
                    f"capability against it. This says nothing about the target "
                    f"environment.",
                    title=f"Emulator lists '{service}' but does not serve it",
                    rule_id="floci-gcp-service-declared-unreachable",
                    target_csp=TARGET_CSP,
                    source_canvas=self.canvas_key,
                    method=self.method,
                    detail=service,
                ))
            elif service in emulator_gcp.GRPC_ONLY_SERVICES:
                violations.append(canonical_violation(
                    "low",
                    "service_parity",
                    f"'{service}' works on this emulator over gRPC ONLY -- every REST "
                    f"path tried returned 404 or 405 (measured 2026-09-05). The service "
                    f"is fine; ICDEV's connector stack reads over HTTP and so cannot "
                    f"inventory it, which means its resources are absent from this "
                    f"twin's snapshots. A gRPC client rehearses against it normally. "
                    f"This says nothing about the target environment.",
                    title=f"'{service}' is gRPC-only and invisible to this twin",
                    rule_id="floci-gcp-service-grpc-only",
                    target_csp=TARGET_CSP,
                    source_canvas=self.canvas_key,
                    method=self.method,
                    detail=service,
                ))
            elif not emulator_gcp.data_plane_supported(service):
                fabricates = service in emulator_gcp.FABRICATED_SUCCESS_WITHOUT_DOCKER
                violations.append(canonical_violation(
                    # HIGH for the one service that lies. The others fail loudly
                    # with a 500; a rehearsal against this one SUCCEEDS and
                    # produces nothing, which no amount of reading the response
                    # will reveal.
                    "high" if fabricates else "medium",
                    "service_parity",
                    f"The local emulator cannot exercise '{service}'s data plane on this "
                    f"host: it is container-backed and no docker socket was found. "
                    + (
                        "MEASURED: without a socket a Cloud Run deploy still returns "
                        "HTTP 200 and a service body carrying uid, createTime, traffic "
                        "and a urls entry -- structurally indistinguishable from a real "
                        "deployment. A rehearsal here reports success and starts "
                        "nothing. "
                        if fabricates
                        else "Without a socket this service returns HTTP 500. "
                    )
                    + "Mount one (DOCKER_HOST / FLOCI_GCP_DOCKER_SOCKET) or rehearse "
                    "elsewhere. Its inventory still lists normally -- measured, "
                    "listing spawns no container. This says nothing about the target "
                    "environment.",
                    title=f"Emulator cannot serve '{service}' data plane on this host",
                    rule_id="floci-gcp-service-unsupported-locally",
                    target_csp=TARGET_CSP,
                    source_canvas=self.canvas_key,
                    method=self.method,
                    detail=service,
                ))

        verdict = "warn" if violations else "unknown"
        violations, verdict = self._target_augment(
            delta, violations, verdict, {**kwargs, "target_preset": preset}
        )
        return self._wrap(
            str(target_id or "local"),
            verdict,
            violations,
            extra={
                # Carried on EVERY envelope, including a clean one: a consumer
                # that only learns the estate was emulated when something is
                # wrong will read a clean simulation as evidence about a real
                # deployment.
                "provenance": PROVENANCE,
                "target_csp": TARGET_CSP,
                "region": TARGET_REGION,
                "target_preset": preset,
                "docker_backed": emulator_gcp.docker_backed(),
                "iac_execution_supported": emulator_gcp.IAC_EXECUTION_SUPPORTED,
                "basis": (
                    "static: the seam's measured service constants + docker seam; "
                    "the emulator was not probed"
                ),
            },
        )

    @staticmethod
    def _services_in(delta: Any) -> set[str]:
        """GCP service names named anywhere in ``delta`` (best-effort)."""
        from tools.twin_core.target_presets import _iter_strings

        known = (
            set(emulator_gcp.CONTAINER_BACKED_SERVICES)
            | set(emulator_gcp.DECLARED_UNREACHABLE_SERVICES)
            | set(emulator_gcp.GRPC_ONLY_SERVICES)
        )
        found: set[str] = set()
        for text in _iter_strings(delta):
            lowered = str(text).lower()
            for service in known:
                # Token match, so `google_cloud_run_service` matches `cloudrun`
                # only if the token is present -- `cloudrunx` does not.
                if service in lowered.replace("-", "_").replace(".", "_").split("_") \
                        or lowered == service:
                    found.add(service)
        return found
