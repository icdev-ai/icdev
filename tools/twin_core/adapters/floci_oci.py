# CUI // SP-CTI
"""Twin Observatory adapter over the local floci-oci OCI emulator (flx-oci-01).

THE FOURTH EMULATOR TWIN, AND IT READS THROUGH THE GOVERNED DOOR
-----------------------------------------------------------------
Every read is ``tools/databridge/broker.py::fetch`` as
``twin_observatory_analyst``, so each of the thirteen logical tables is
authorized against ``args/databridge_agent_access.yaml`` and lands one
``databridge_agent_access_log`` row. Importing ``FlociOciConnector`` and calling
``read()`` would return the same rows with NO authorization check and NO audit
row -- the ungoverned side channel ``cef-fnd-03`` exists to close, and a
structural AST test refuses a direct connector read from this module.

PROVENANCE IS THE POINT, AND IT IS ASSERTED THREE WAYS
-------------------------------------------------------
``target_csp`` is ``oci`` so the government preset in
``args/twin_target_presets.yaml`` applies to :meth:`simulate_delta` -- but every
snapshot carries provenance ``emulated``. An OCI bucket in this emulator is a
container's in-process state; ranking it beside a real inventory is the
``rmf-disc-02`` defect one layer up. So :meth:`_persist_snapshot` takes NO
provenance parameter and binds the module constant, a test reads its AST to
prove it, and the migration derives a CHECK constraint from
``schema.SNAPSHOT_PROVENANCES``.

WHAT THIS TWIN CANNOT TELL YOU, STATED ON EVERY SNAPSHOT
---------------------------------------------------------
An OKE row is NOT evidence of a running cluster. Measured 2026-09-05 on
``floci/floci-oci:0.4.0``: the emulator spawns ``rancher/k3s:v1.30.1-k3s1``
without a ``--token``, k3s exits immediately, and the API keeps reporting
``lifecycleState: ACTIVE`` with a ``kubernetes`` endpoint that has no listener.
So ``clusters`` rows are counted as RECORDS and ``unverified_tables`` names them
on every snapshot -- including a clean one, because a reader who only learns of
the caveat when something looks wrong will read a clean snapshot as a working
cluster.

TWO REGIONS, NEVER MERGED -- and this twin differs from its GCP sibling here
----------------------------------------------------------------------------
The GCP twin could use one region constant because ``us-central1`` is both the
emulator's default and the Assured Workloads preset's region. OCI's government
story is a SEPARATE PARTITION, not an overlay, so the two genuinely differ: a
snapshot records the region it actually READ (``emulator_oci.region()``,
``us-ashburn-1`` by default -- commercial), while :meth:`simulate_delta` scores
against ``oci_gov_il5`` / ``us-langley-1``. Reporting one number for both would
claim a government read that never happened.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.cloud import emulator_oci
from tools.databridge import broker
from tools.databridge.connectors.floci_oci_connector import (
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
CONNECTOR = "floci_oci"

#: Provenance every snapshot written here carries. NOT a parameter.
PROVENANCE = PROVENANCE_EMULATED

#: ``oci`` is already canonical in ``schema.normalize_csp`` -- unlike ``azure``,
#: which normalizes to ``azure_gov``. No alias needed.
TARGET_CSP = "oci"

#: The preset :meth:`simulate_delta` scores against when a caller names none. A
#: DEFAULT, never a fixed target. Its region is ``us-langley-1``, deliberately
#: NOT the emulator's -- see the module docstring.
DEFAULT_TARGET_PRESET = "oci_gov_il5"

#: The region the PRESET targets. Kept apart from ``emulator_oci.region()``,
#: which is what a snapshot actually read.
TARGET_REGION = "us-langley-1"

# ── read outcomes ────────────────────────────────────────────────────────────
READ_ANSWERED = "answered"
READ_DISABLED = "disabled"
READ_DENIED = "denied"
READ_ERROR = "error"

#: Rows counted toward ``resource_count``. Both probe tables are excluded --
#: they describe the emulator, not the estate.
#:
#: ``compartments`` IS counted, unlike the GCP twin's ``project``. The reasoning
#: is the same and lands the other way: a project is the CONTAINER a GCP estate
#: lives in, so counting it would make an empty project report one resource. An
#: OCI compartment is a RESOURCE INSIDE the tenancy -- it is created, listed and
#: deleted like any other -- and the container here is the tenancy, which this
#: twin never counts. A fresh emulator lists zero compartments, measured.
_ESTATE_TABLES: tuple[str, ...] = tuple(t for t in TABLES if t not in PROBE_TABLES)

#: Estate tables whose rows are records the emulator cannot be trusted to have
#: actually realised. Derived from the seam so a future release that fixes OKE
#: empties this list by changing one constant.
_UNVERIFIED_TABLES: tuple[str, ...] = tuple(
    t
    for t in _ESTATE_TABLES
    if emulator_oci.TABLE_SERVICE.get(t) in emulator_oci.FABRICATED_ACTIVE_WITH_DOCKER
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

    ``partial`` is deliberately NOT a rung here, as on the GCP twin. Each table
    is one request against a lane measured to reflect writes under a
    compartment filter that is honoured, so a read either answered or did not --
    and a status this ladder does not recognise falls to ``error`` below rather
    than being invented into an answer.
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
    # An unrecognised status is NOT an answer. Defaulting to `answered` is how a
    # new connector state would silently become a `pass`.
    return READ_ERROR


def denial_basis(granted: bool | None, emulator_enabled: bool | None) -> str:
    """Why did every brokered read come back refused?

    THE GOVERNED DOOR LOSES A DISTINCTION AND THIS RESTORES IT, structurally.
    ``saas_base.connect`` returns False whenever ``health_check`` is not
    ``healthy`` -- which covers BOTH "the emulator is switched off" and "the
    emulator is on and nothing answered" -- and the broker turns that into one
    refusal. So both causes arrive here looking identical.

    The verdict is ``unknown`` either way and is NOT at stake. The BASIS is,
    because the repairs differ: set ``FLOCI_OCI_ENABLED=true``, versus
    ``docker compose --profile floci-oci up -d``.

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
class FlociOciTwinAdapter(TwinAdapter):
    """Twin over the local floci-oci OCI emulator. Reads only through the broker."""

    canvas_key = "floci_oci"
    display_name = "Floci OCI Emulator"
    #: Provenance carried onto every canonical violation. The word ``emulator``
    #: is load-bearing on the observatory: a reader seeing this method beside
    #: ``heuristic`` or ``iqe-gate`` is told, without opening the row, that the
    #: finding came from an emulated estate.
    method = "emulator-probe"
    snapshot_table = "floci_oci_twin_snapshots"
    snapshot_time_col = "created_at"

    def _fleet_conn(self):
        """Connection for the snapshot table.

        ``get_canvas_connection`` rather than ``get_connection``:
        ``floci_oci_twin_snapshots`` carries no ``classification`` /
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
        """Is ``floci_oci`` reachable by this twin's agent id? ``None`` if unknown.

        ``broker.list_available`` is the broker's OWN answer to "what may this
        agent read", so the adapter never re-derives the manifest rule.
        """
        try:
            return any(
                entry.get("connector") == CONNECTOR
                for entry in broker.list_available(BROKER_AGENT_ID)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("floci-oci twin: grant lookup unavailable: %s", exc)
            return None

    @staticmethod
    def _emulator_enabled() -> bool | None:
        """The ONE OCI switch. Configuration only -- no socket opens."""
        try:
            return bool(emulator_oci.enabled())
        except Exception as exc:  # noqa: BLE001
            logger.debug("floci-oci twin: emulator seam unavailable: %s", exc)
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
            if table in _UNVERIFIED_TABLES:
                # Per table as well as on the snapshot: a consumer reading one
                # table's detail must not have to know the snapshot-level caveat.
                detail[table]["rows_are_unverified_records"] = True
        return detail

    # -- snapshot -------------------------------------------------------------

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        """Freeze the emulated OCI estate as read through the broker.

        ``target_id`` names the emulator instance being frozen (a deployment may
        run more than one); it is a label, not a lookup key -- the endpoint comes
        from the ``tools/cloud/emulator_oci.py`` seam, which is the one switch.
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
            "id": f"floci-oci-snap-{uuid.uuid4().hex[:12]}",
            "target_id": target_id,
            "label": label or "",
            "provenance": PROVENANCE,
            "target_csp": TARGET_CSP,
            # The region actually READ, from the seam -- not TARGET_REGION,
            # which is where simulate_delta scores. They differ on OCI.
            "region": emulator_oci.region(),
            "namespace": emulator_oci.namespace(),
            "compartment_id": emulator_oci.compartment_id(),
            "verdict": verdict,
            "verdict_basis": basis,
            "resource_count": resource_count,
            # False unless EVERY estate table answered. A consumer that quotes
            # `resource_count` without reading this is quoting a subtotal.
            "resource_count_is_complete": bool(answered)
            and all(reads.get(t) == READ_ANSWERED for t in _ESTATE_TABLES),
            # Named on EVERY snapshot, not only when something is wrong. These
            # tables' rows are records whose backing the emulator does not
            # verify: floci-oci 0.4.0 starts k3s with no --token, the container
            # exits, and lifecycleState stays ACTIVE. A reader who learns that
            # only from a failing snapshot will read a clean one as a working
            # cluster.
            "unverified_tables": list(_UNVERIFIED_TABLES),
            "unverified_reason": (
                "OKE rows are records, not running clusters: floci-oci 0.4.0 "
                "starts rancher/k3s without a --token, the container exits "
                "immediately, and the API keeps reporting lifecycleState ACTIVE "
                "with an endpoint that has no listener (measured 2026-09-05)."
            )
            if _UNVERIFIED_TABLES
            else "",
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
        and ``snap`` is not consulted for one. ``tests/cloud/test_floci_oci_seam``
        reads this function's AST to prove it, because a behavioural test over
        today's callers -- which pass none -- would still pass the day somebody
        threads a kwarg through.
        """
        try:
            conn = self._fleet_conn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("floci-oci twin: snapshot connection failed: %s", exc)
            return False
        try:
            conn.execute(
                "INSERT INTO floci_oci_twin_snapshots "
                "(id, target_id, label, provenance, target_csp, region, namespace, "
                "compartment_id, verdict, verdict_basis, resource_count, tables_ok, "
                "tables_declared, payload_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    snap["id"],
                    snap["target_id"],
                    snap["label"],
                    PROVENANCE,
                    snap["target_csp"],
                    snap["region"],
                    snap["namespace"],
                    snap["compartment_id"],
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
            logger.warning("floci-oci twin: snapshot INSERT failed: %s", exc)
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
                "namespace, compartment_id, verdict, verdict_basis, resource_count, "
                "tables_ok, tables_declared, created_at "
                "FROM floci_oci_twin_snapshots WHERE target_id=%s "
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
                "namespace", "compartment_id", "verdict", "verdict_basis",
                "resource_count", "tables_ok", "tables_declared", "created_at")
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
        observatory renders, and a fan-out across thirteen brokered tables on
        every page render would put many emulator round trips on the dashboard.

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
        """Score a proposed set of OCI services against emulator + target reality.

        ``delta`` is anything naming OCI services -- ``{"services": [...]}``, a
        Terraform plan, a list of resource types. THREE independent questions are
        asked and none substitutes for another:

        * **IS THE SERVICE ITSELF BROKEN ON THIS EMULATOR?** ``oke`` is, and
          this rung has NO analogue on the three sibling twins. It is reported
          at ``high`` REGARDLESS of the docker socket, because the socket is not
          the problem: with one, floci-oci 0.4.0 starts k3s without a
          ``--token``, the container exits, and the API reports ACTIVE anyway.
          A rehearsal there produces a fabricated success no amount of reading
          the response will reveal.
        * **CAN THIS HOST EXERCISE ITS DATA PLANE?** A container-backed service
          with no docker socket, at ``medium``. Unlike the GCP sibling there is
          no severity escalation for a fabricating service here, because
          floci-oci's socket-absent path is the HONEST one -- OKE returns 500
          and records nothing. The fabrication is covered by the rung above.
        * **IS IT AVAILABLE IN THE TARGET?** Delegated to the ``oci_gov_il5``
          preset through :meth:`TwinAdapter._target_augment`. That is a fact
          about Oracle Cloud and is NOT weakened by running against an emulator.
          Note this is a REAL government partition (``us-langley-1``), not the
          Assured Workloads overlay the GCP preset needed.

        The verdict starts at ``unknown``: this simulation is static (it asks the
        seam's measured constants and the docker seam, not the emulator), so a
        delta naming no service is honestly unscored rather than a free ``pass``.
        """
        delta = delta if delta is not None else {}
        preset = kwargs.get("target_preset", DEFAULT_TARGET_PRESET)

        violations: list[dict] = []
        for service in sorted(self._services_in(delta)):
            if service in emulator_oci.FABRICATED_ACTIVE_WITH_DOCKER:
                violations.append(canonical_violation(
                    # HIGH, and NOT conditional on the socket. A socket makes
                    # this WORSE, not better: without one the call fails
                    # honestly with a 500, with one it succeeds and lies.
                    "high",
                    "service_parity",
                    f"'{service}' is BROKEN on this emulator and a rehearsal against it "
                    f"produces a fabricated success. MEASURED 2026-09-05 on "
                    f"floci/floci-oci:0.4.0: a create returns HTTP 202, the emulator "
                    f"spawns rancher/k3s:v1.30.1-k3s1 WITHOUT a --token, the container "
                    f"exits immediately ('--token is required'), and the API keeps "
                    f"reporting lifecycleState ACTIVE with a kubernetes endpoint that "
                    f"has no listener. Mounting a docker socket does not fix this -- "
                    f"without one the call at least fails loudly with a 500. Rehearse "
                    f"'{service}' elsewhere and treat any row this twin reports for it "
                    f"as a record, never as a running cluster. This says nothing about "
                    f"the target environment.",
                    title=f"'{service}' reports ACTIVE on this emulator but never runs",
                    rule_id="floci-oci-service-fabricates-active",
                    target_csp=TARGET_CSP,
                    source_canvas=self.canvas_key,
                    method=self.method,
                    detail=service,
                ))
            elif not emulator_oci.data_plane_supported(service):
                # UNREACHABLE ON 0.4.0, DELIBERATELY KEPT, AND TESTED AS SUCH.
                # This branch serves a container-backed service that is NOT
                # broken -- and on floci-oci 0.4.0 that set is EMPTY, because
                # CONTAINER_BACKED_SERVICES and FABRICATED_ACTIVE_WITH_DOCKER
                # are both exactly {"oke"}, so the rung above always wins.
                # Deleting it would mean a later release that adds a WORKING
                # container-backed service silently gets no docker-socket
                # finding; keeping it silently would be dead code wearing a
                # safety net's name. So the coincidence is asserted by
                # `test_the_docker_rung_is_currently_unreachable_and_that_is_measured`,
                # which fails the day the two sets diverge and this goes live.
                violations.append(canonical_violation(
                    "medium",
                    "service_parity",
                    f"The local emulator cannot exercise '{service}'s data plane on this "
                    f"host: it is container-backed and no docker socket was found. "
                    f"Without a socket this service returns HTTP 500 and records "
                    f"nothing -- an honest failure. Mount one (DOCKER_HOST / "
                    f"FLOCI_OCI_DOCKER_SOCKET) or rehearse elsewhere. Its inventory "
                    f"still lists normally -- measured, listing spawns no container. "
                    f"This says nothing about the target environment.",
                    title=f"Emulator cannot serve '{service}' data plane on this host",
                    rule_id="floci-oci-service-unsupported-locally",
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
                # BOTH regions, labelled. They differ on OCI and merging them
                # would claim a government read that never happened.
                "target_region": TARGET_REGION,
                "emulator_region": emulator_oci.region(),
                "target_preset": preset,
                "docker_backed": emulator_oci.docker_backed(),
                "iac_execution_supported": emulator_oci.IAC_EXECUTION_SUPPORTED,
                # The §1 finding, carried onto every simulation: enabling this
                # emulator does not make ICDEV's OCI provider layer work.
                "provider_layer_is_stubbed": emulator_oci.PROVIDER_LAYER_IS_STUBBED,
                "basis": (
                    "static: the seam's measured service constants + docker seam; "
                    "the emulator was not probed"
                ),
            },
        )

    @staticmethod
    def _services_in(delta: Any) -> set[str]:
        """OCI service names named anywhere in ``delta`` (best-effort)."""
        from tools.twin_core.target_presets import _iter_strings

        known = set(emulator_oci.CONTAINER_BACKED_SERVICES) | set(
            emulator_oci.FABRICATED_ACTIVE_WITH_DOCKER
        )
        found: set[str] = set()
        for text in _iter_strings(delta):
            lowered = str(text).lower()
            for service in known:
                # Token match, so `oci_containerengine_oke` matches `oke` only if
                # the token is present -- `okex` does not.
                if service in lowered.replace("-", "_").replace(".", "_").split("_") \
                        or lowered == service:
                    found.add(service)
        return found
