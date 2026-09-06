# CUI // SP-CTI
"""Twin over the LOCAL floci-az Azure emulator (flx-az-01).

The Azure sibling of :mod:`tools.twin_core.adapters.floci`. Like it, every read
goes through the governed DataBridge door as ``twin_observatory_analyst``, and
every snapshot is marked ``emulated``. Unlike it, three things differ, and each
difference is MEASURED rather than assumed -- see ``docs/spikes/flx-az-parity.md``.

``target_csp`` IS ``azure``, WHICH IS THE POINT OF THE CARD
-----------------------------------------------------------
``azure`` normalizes to ``azure_gov`` through ``schema.normalize_csp``, which is
what makes the ``azure_gov`` / ``azure_gov_il5`` presets in
``args/twin_target_presets.yaml`` applicable to :meth:`simulate_delta`. The
region is ``usgovvirginia`` -- NOT ``us-gov-west-1``, which is AWS's spelling
and matches no Azure region.

A SEPARATE SNAPSHOT TABLE, ON PURPOSE
--------------------------------------
``floci_az_twin_snapshots`` (migration 20260905093010), not a ``target_csp``
column on the AWS twin's table. The two emulators are different estates; merging
them would make a query for "the AWS estate" silently include Azure rows, and a
``target_csp`` filter that somebody forgets is a silent wrong answer rather than
an error. Per-twin snapshot tables are already this tree's convention
(``odc_``, ``idc_``, ``qdc_``, ``floci_``).

THE READ LADDER DIFFERS FROM THE AWS TWIN'S, MEASURABLY
--------------------------------------------------------
* **No ``sdk_unavailable`` rung.** The AWS connector needs ``boto3`` for five of
  its seven tables and ``boto3`` is not a declared dependency, so "the SDK is
  missing" is a real and common outcome there. ``floci_az_connector`` uses
  ``urllib`` only, so that rung cannot fire and is absent rather than dead.
* **No ``unsupported_without_docker`` rung.** Measured: the ARM MANAGEMENT lane
  answered IDENTICALLY on a socket-mounted emulator and on one whose
  ``FLOCI_AZ_DOCKER_DOCKER_HOST`` pointed at a provably absent path -- listing
  metadata spawns no container. Refusing an inventory read for a missing socket
  would be a FABRICATED refusal, the mirror image of the fabricated empty this
  file is otherwise shaped around. The socket still matters for DATA planes and
  :meth:`simulate_delta` reports it there.
* **A ``partial`` rung the AWS twin has no need for.** Every resource-type table
  fans out per resource group, so a read can succeed for some groups and fail
  for others. That is neither an answer nor a failure, and folding it into
  either loses the one fact that matters -- the inventory is INCOMPLETE and by
  how much.

WHY THE FAN-OUT EXISTS AT ALL
------------------------------
Measured 2026-09-05: a subscription-scoped ARM list returns ``200 {"value":[]}``
for an estate that demonstrably holds resources, while the resource-group-scoped
list returns them. A twin reading at subscription scope would report
``resource_count: 0`` -- a confident, well-formed, entirely fabricated clean
bill of health. ``floci_az_connector`` does the fan-out; this adapter reads its
``scope`` and ``resource_groups_enumerated`` metadata rather than re-deriving
the rule.

READ ONLY. ICDEV ships no Azure IaC executor, so nothing here applies anything.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.databridge import broker
from tools.cloud import emulator_az
from tools.databridge.connectors.floci_az_connector import (
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
CONNECTOR = "floci_az"

#: Provenance every snapshot written here carries. NOT a parameter.
PROVENANCE = PROVENANCE_EMULATED

#: ``azure`` normalizes to ``azure_gov`` through ``schema.normalize_csp``.
TARGET_CSP = "azure"
#: Azure Government's region name. Kept in step with ``emulator_az.DEFAULT_REGION``.
TARGET_REGION = "usgovvirginia"
#: Preset used when a caller names none. A DEFAULT, never a fixed target.
DEFAULT_TARGET_PRESET = "azure_gov"

# ── read outcomes ────────────────────────────────────────────────────────────
READ_ANSWERED = "answered"
READ_PARTIAL = "partial"
READ_SCOPE_UNMEASURED = "scope_unmeasured"
READ_DISABLED = "disabled"
READ_DENIED = "denied"
READ_ERROR = "error"

#: Rows counted toward ``resource_count``. ``health`` describes the EMULATOR,
#: not the estate it holds, so counting it would inflate an empty estate to a
#: non-zero resource count. ``subscriptions`` is excluded for the same reason --
#: a subscription is the container, not a resource in it.
_ESTATE_TABLES: tuple[str, ...] = tuple(
    t for t in TABLES if t not in PROBE_TABLES and t != "subscriptions"
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

    ``scope_unmeasured`` is the rung this ladder has and the AWS one does not.
    The connector returns ``error`` when the resource-group enumeration failed,
    because with no group list there is nothing to ask -- and zero rows there is
    a statement about our reach, not about the estate.
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
    if status == "partial":
        return READ_PARTIAL
    if status == "error":
        errors = " ".join(str(e) for e in (getattr(outcome, "connector_errors", None) or []))
        # Matched on the STRUCTURAL phrase the connector emits for exactly this
        # case. It is not ideal to read prose, and the alternative was worse:
        # the broker's FetchOutcome carries no field for "which precondition
        # failed", so the only other option is to fold an unmeasured scope into
        # a generic error -- which loses the one distinction the fan-out exists
        # to preserve. `tests/cloud/test_floci_az_twin.py` pins the phrase in
        # BOTH modules so a reworded connector fails loudly rather than
        # silently reclassifying.
        if "enumerating resource groups failed" in errors:
            return READ_SCOPE_UNMEASURED
        return READ_ERROR
    # An unrecognised status is NOT an answer. Defaulting to `answered` here is
    # how a new connector state would silently become a `pass`.
    return READ_ERROR


def denial_basis(granted: bool | None, emulator_enabled: bool | None) -> str:
    """Why did every brokered read come back refused?

    THE GOVERNED DOOR LOSES A DISTINCTION AND THIS RESTORES IT, structurally.
    ``saas_base.connect`` returns False whenever ``health_check`` is not
    ``healthy`` -- which covers BOTH "the emulator is switched off" and "the
    emulator is on and nothing answered" -- and the broker turns that into one
    refusal. So both causes arrive here looking identical.

    The verdict is ``unknown`` either way and is NOT at stake. The BASIS is,
    because the repairs differ: set ``FLOCI_AZ_ENABLED=true``, versus
    ``docker compose --profile floci-az up -d``.

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

    ``scope_unmeasured`` scores ``warn``, NOT ``fail`` and NOT ``pass``. Nothing
    is broken -- the emulator answered -- but the inventory is not a measurement
    of the estate, and a ``pass`` beside ``resource_count: 0`` is precisely the
    fabricated clean bill this adapter exists to refuse.
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
    if READ_SCOPE_UNMEASURED in values:
        return "warn", READ_SCOPE_UNMEASURED
    if READ_PARTIAL in values:
        return "warn", "partial_inventory"
    return "pass", "all_tables_answered"


# ── adapter ──────────────────────────────────────────────────────────────────


@register_twin
class FlociAzTwinAdapter(TwinAdapter):
    """Twin over the local floci-az Azure emulator. Reads only through the broker."""

    canvas_key = "floci_az"
    display_name = "Floci Azure Emulator"
    #: Provenance carried onto every canonical violation. The word ``emulator``
    #: is load-bearing on the observatory: a reader seeing this method beside
    #: ``heuristic`` or ``iqe-gate`` is told, without opening the row, that the
    #: finding came from an emulated estate.
    method = "emulator-probe"
    snapshot_table = "floci_az_twin_snapshots"
    snapshot_time_col = "created_at"

    def _fleet_conn(self):
        """Connection for the snapshot table.

        ``get_canvas_connection`` rather than ``get_connection``:
        ``floci_az_twin_snapshots`` carries no ``classification`` /
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
        """Is ``floci_az`` reachable by this twin's agent id? ``None`` if unknown.

        ``broker.list_available`` is the broker's OWN answer to "what may this
        agent read", so the adapter never re-derives the manifest rule.
        """
        try:
            return any(
                entry.get("connector") == CONNECTOR
                for entry in broker.list_available(BROKER_AGENT_ID)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("floci-az twin: grant lookup unavailable: %s", exc)
            return None

    @staticmethod
    def _emulator_enabled() -> bool | None:
        """The ONE Azure switch. Configuration only -- no socket opens."""
        try:
            return bool(emulator_az.enabled())
        except Exception as exc:  # noqa: BLE001
            logger.debug("floci-az twin: emulator seam unavailable: %s", exc)
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
                # NONE, never 0, unless the table actually answered. A partial
                # read HAS a row count and it is a floor, not a total -- carried
                # separately so a consumer cannot sum it as if it were complete.
                "row_count": getattr(outcome, "row_count", 0) if kind == READ_ANSWERED else None,
                "partial_row_count": (
                    getattr(outcome, "row_count", 0) if kind == READ_PARTIAL else None
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
        """Freeze the emulated Azure estate as read through the broker.

        ``target_id`` names the emulator instance being frozen (a deployment may
        run more than one); it is a label, not a lookup key -- the endpoint comes
        from the ``tools/cloud/emulator_az.py`` seam, which is the one switch.
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
        #
        # A PARTIAL read is deliberately EXCLUDED from the total rather than
        # added to it: summing a floor with complete counts produces a number
        # that is neither, under a name (`resource_count`) that claims to be a
        # total. `resource_count_is_complete` says which.
        answered = [t for t in _ESTATE_TABLES if reads.get(t) == READ_ANSWERED]
        partials = [t for t in _ESTATE_TABLES if reads.get(t) == READ_PARTIAL]
        resource_count = (
            sum(int(detail[t]["row_count"] or 0) for t in answered) if answered else None
        )
        tables_ok = sum(1 for v in reads.values() if v == READ_ANSWERED)

        snap = {
            "id": f"floci-az-snap-{uuid.uuid4().hex[:12]}",
            "target_id": target_id,
            "label": label or "",
            "provenance": PROVENANCE,
            "target_csp": TARGET_CSP,
            "region": TARGET_REGION,
            "verdict": verdict,
            "verdict_basis": basis,
            "resource_count": resource_count,
            # False whenever ANY estate table was partial or unmeasured. A
            # consumer that quotes `resource_count` without reading this is
            # quoting a floor as a total.
            "resource_count_is_complete": bool(answered) and not partials
            and all(reads.get(t) == READ_ANSWERED for t in _ESTATE_TABLES),
            "partial_tables": partials,
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
        and ``snap`` is not consulted for one. ``tests/cloud/test_floci_az_twin``
        reads this function's AST to prove it, because a behavioural test over
        today's callers -- which pass none -- would still pass the day somebody
        threads a kwarg through.
        """
        try:
            conn = self._fleet_conn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("floci-az twin: snapshot connection failed: %s", exc)
            return False
        try:
            conn.execute(
                "INSERT INTO floci_az_twin_snapshots "
                "(id, target_id, label, provenance, target_csp, region, verdict, "
                "verdict_basis, resource_count, tables_ok, tables_declared, payload_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    snap["id"],
                    snap["target_id"],
                    snap["label"],
                    PROVENANCE,
                    snap["target_csp"],
                    snap["region"],
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
            logger.warning("floci-az twin: snapshot INSERT failed: %s", exc)
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
                "SELECT id, target_id, label, provenance, target_csp, region, verdict, "
                "verdict_basis, resource_count, tables_ok, tables_declared, created_at "
                "FROM floci_az_twin_snapshots WHERE target_id=%s "
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
                "verdict", "verdict_basis", "resource_count", "tables_ok",
                "tables_declared", "created_at")
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
        observatory renders, and a fan-out across nine brokered tables (each of
        which is itself a fan-out per resource group) on every page render would
        put many emulator round trips on the dashboard.

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
        """Score a proposed set of Azure services against emulator + target reality.

        ``delta`` is anything naming Azure services -- ``{"services": [...]}``, a
        Bicep or Terraform plan, a list of resource types. THREE independent
        questions are asked and none substitutes for another:

        * **IS IT REACHABLE ON THIS EMULATOR AT ALL?** ``appconfig``,
          ``eventgrid``, ``functions`` and ``monitor`` are marked ``[enabled ]``
          in floci-az's own startup banner and NO route reached any of them
          (measured 2026-09-05). That is a ``service_parity`` finding at
          ``medium``, and it is reported SEPARATELY from the docker one because
          the repair is different: there is nothing to mount.
        * **CAN THIS HOST EXERCISE ITS DATA PLANE?** A container-backed service
          with no docker socket, reported at ``medium`` -- the deployment is
          fine, the local rehearsal is not.
        * **IS IT AVAILABLE IN THE TARGET?** Delegated to the Azure Government
          presets through :meth:`TwinAdapter._target_augment`. That is a fact
          about Azure Government and is NOT weakened by running against an
          emulator.

        The verdict starts at ``unknown``: this simulation is static (it asks the
        seam's measured constants and the docker seam, not the emulator), so a
        delta naming no service is honestly unscored rather than a free ``pass``.
        """
        delta = delta if delta is not None else {}
        preset = kwargs.get("target_preset", DEFAULT_TARGET_PRESET)

        violations: list[dict] = []
        for service in sorted(self._services_in(delta)):
            if service in emulator_az.DECLARED_UNREACHABLE_SERVICES:
                violations.append(canonical_violation(
                    "medium",
                    "service_parity",
                    f"floci-az declares '{service}' enabled in its startup banner but no "
                    f"route reached it (measured 2026-09-05, floci/floci-az:0.12.0). "
                    f"This service cannot be rehearsed on this emulator at all -- "
                    f"there is nothing to mount or configure. Rehearse it elsewhere, "
                    f"and do not design a capability against it. This says nothing "
                    f"about the target environment.",
                    title=f"Emulator declares '{service}' but does not serve it",
                    rule_id="floci-az-service-declared-unreachable",
                    target_csp=TARGET_CSP,
                    source_canvas=self.canvas_key,
                    method=self.method,
                    detail=service,
                ))
            elif not emulator_az.data_plane_supported(service):
                violations.append(canonical_violation(
                    "medium",
                    "service_parity",
                    f"The local emulator cannot exercise '{service}'s data plane on this "
                    f"host: it is container-backed and no docker socket was found. Mount "
                    f"one (DOCKER_HOST / FLOCI_AZ_DOCKER_SOCKET) or rehearse this service "
                    f"elsewhere. Its ARM inventory still lists normally -- measured, the "
                    f"management lane does not need a socket. This says nothing about the "
                    f"target environment.",
                    title=f"Emulator cannot serve '{service}' data plane on this host",
                    rule_id="floci-az-service-unsupported-locally",
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
                "docker_backed": emulator_az.docker_backed(),
                "iac_execution_supported": emulator_az.IAC_EXECUTION_SUPPORTED,
                "basis": (
                    "static: the seam's measured service constants + docker seam; "
                    "the emulator was not probed"
                ),
            },
        )

    @staticmethod
    def _services_in(delta: Any) -> set[str]:
        """Azure service names named anywhere in ``delta`` (best-effort)."""
        from tools.twin_core.target_presets import _iter_strings

        known = set(emulator_az.CONTAINER_BACKED_SERVICES) | set(
            emulator_az.DECLARED_UNREACHABLE_SERVICES
        )
        found: set[str] = set()
        for text in _iter_strings(delta):
            lowered = str(text).lower()
            for service in known:
                # Token match, so `azurerm_function_app` matches `functions`
                # only if the token is present -- `functionsx` does not.
                if service in lowered.replace("-", "_").replace(".", "_").split("_") \
                        or lowered == service:
                    found.add(service)
        return found
