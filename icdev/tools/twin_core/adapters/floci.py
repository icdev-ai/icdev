# CUI // SP-CTI — floci AWS-emulator twin adapter (flx-twin-01)
"""Thin twin adapter over the LOCAL floci AWS emulator.

The twelfth adapter in ``tools/twin_core/adapters/``, carrying the same uniform
surface as the other eleven — ``take_snapshot`` / ``simulate_delta`` /
``list_snapshots`` / ``latest_status`` — over an estate that is *emulated*.

THREE RULES, AND EACH ONE IS THE POINT OF THE CARD
==================================================

1. IT READS THROUGH THE BROKER, NEVER THE CONNECTOR
---------------------------------------------------
Every read of the emulated estate goes through
``tools.databridge.broker.fetch``, under the ``twin_observatory_analyst``
identity the flx-bridge-02 grant scopes the ``floci`` connector to. The
authorization decision is therefore made against the manifest, and one
``databridge_agent_access_log`` row lands per call, allowed or denied.

``from tools.databridge.connectors.floci_connector import FlociConnector`` and
reading it directly would produce the SAME rows with NO authorization check and
NO audit row — the ungoverned side channel the whole cef-fnd-03 design exists to
close. ``tests/test_floci_twin_adapter.py`` reads this module's AST and refuses
any call that reaches a connector's ``read``/``write``.

Two module-level imports of ``floci_connector`` remain and neither is a read:
``TABLES`` (the declared surface, so the adapter cannot drift from the grant)
and ``boto3_available`` / ``table_is_docker_backed`` (capability predicates that
open no socket). Both are pinned by that same test.

2. FOUR VERDICTS, AND UNKNOWN IS NEVER PASS
--------------------------------------------
``pass``    every declared table ANSWERED.
``warn``    the emulator answered, and at least one declared table could not be
            asked — a container-backed table on a socket-less host
            (``unsupported_without_docker``), or a boto3-backed table on a host
            with no AWS SDK. The estate was read in part.
``fail``    a REACHABLE emulator returned an error.
``unknown`` the emulator is disabled, unreachable, or the broker refused. The
            snapshot is UNMEASURED and is not a clean bill of health.

The last one is the whole reason this ladder is written out rather than derived
from a row count. An emulator nobody has started holds no S3 buckets and an
emulator with an empty bucket list holds no S3 buckets, and only one of those is
a measurement. ``resource_count`` is ``None`` — never 0 — on an unmeasured
snapshot, and the persisted row carries ``verdict_basis`` naming which rung of
the ladder produced the verdict.

WHY A MISSING SDK IS ``warn`` AND NOT ``fail``. ``boto3`` is not a declared
dependency of this repo, so five of the seven logical tables answer
``status="error"`` on a host without it. That error did not come from the
emulator — no socket opened — so scoring it ``fail`` would attribute a local
tooling gap to the emulated estate. It is an UNANSWERED table, which is exactly
what ``warn`` means here, and ``verdict_basis`` says ``sdk_unavailable`` rather
than lumping it in with the docker case, because the two have different repairs.

3. THE ESTATE IS ``emulated``, AND THE WRITER CANNOT BE TALKED OUT OF IT
------------------------------------------------------------------------
``target_csp`` is ``aws`` in region ``us-gov-west-1``, so the existing GovCloud
presets in ``args/twin_target_presets.yaml`` and their ``service_parity`` flags
apply to a simulation — that is the point of naming a real CSP. But every
snapshot this adapter writes carries provenance ``emulated``
(``twin_core.schema.PROVENANCE_EMULATED``), following the ``ni_devices.source``
vocabulary in which ``synthetic`` is spelled out as "NOT evidence of anything".
An emulated estate must never be readable as an observed one: a floci S3 bucket
is a container's in-process state, and ranking it beside a real inventory is the
rmf-disc-02 defect one layer up.

``_persist_snapshot`` takes NO provenance parameter, ``PROVENANCE`` is a module
constant, and a test reads this module's AST to prove no caller-supplied value
reaches the INSERT — asserted STRUCTURALLY, as rmf-disc-02 did, because the
failure mode is a future edit threading a ``provenance=`` kwarg through, which a
behavioural test over today's callers would still pass. The database enforces it
too: migration ``20260905070028`` derives a ``CHECK`` from
``schema.SNAPSHOT_PROVENANCES``.

NO NEW PAGE. This adapter renders inside the existing Twin Observatory, which
composes ``observer.observe()`` and the recent twin events — so the 8-point page
completeness gate does not apply. The snapshot path emits the existing
``twin_snapshot_taken`` event through ``event_bridge.snapshot``.

NEVER source a performance, cost or capacity claim from this twin. An emulator
reproduces the AWS API contract, not its performance characteristics — the
standing guard from ``docs/spikes/twx-spk-01-localstack-go-no-go.md``, which
this project supersedes on the air-gap question only.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.databridge import broker
from tools.databridge.connectors.floci_connector import (
    TABLES,
    boto3_available,
    table_is_docker_backed,
    table_needs_boto3,
    table_service,
)
from tools.logging.icdev_logger import get_logger
from tools.twin_core.registry import TwinAdapter, register_twin
from tools.twin_core.schema import PROVENANCE_EMULATED, canonical_violation

logger = get_logger("icdev.twin_core.adapters.floci")

#: The identity the flx-bridge-02 grant scopes the ``floci`` connector to.
#: Spelled here as a constant rather than taken from the caller: an adapter that
#: fetched under whatever agent id it was handed would let any caller borrow
#: this grant, and the audit row would name the borrower rather than the twin.
BROKER_AGENT_ID = "twin_observatory_analyst"

#: The connector name in the access manifest.
CONNECTOR = "floci"

#: Provenance every snapshot written here carries. NOT a parameter.
PROVENANCE = PROVENANCE_EMULATED

#: The deployment target a floci estate stands in for. ``aws`` normalizes to
#: ``aws_govcloud`` through ``schema.normalize_csp``, which is what makes the
#: GovCloud presets applicable to :meth:`FlociTwinAdapter.simulate_delta`.
TARGET_CSP = "aws"
TARGET_REGION = "us-gov-west-1"
#: Preset used when a caller names none. A DEFAULT, never a fixed target — a
#: caller may pass any ``target_preset`` in ``args/twin_target_presets.yaml``.
DEFAULT_TARGET_PRESET = "aws_govcloud_west"

#: Tables that are the EMULATOR'S OWN health API rather than an emulated AWS
#: service. Derived from ``table_service`` returning ``None``, never respelled:
#: an emulator that cannot answer its health path is UNREACHABLE, which is a
#: different verdict with a different repair from an AWS service erroring.
PROBE_TABLES: tuple[str, ...] = tuple(t for t in TABLES if table_service(t) is None)

#: Per-table read outcomes, in the categories the verdict ladder rungs on.
READ_ANSWERED = "answered"
READ_UNSUPPORTED_DOCKER = "unsupported_without_docker"
READ_SDK_UNAVAILABLE = "sdk_unavailable"
READ_DISABLED = "disabled"
READ_DENIED = "denied"
READ_ERROR = "error"

#: Rows counted toward ``resource_count``. ``health``/``services`` describe the
#: emulator, not the estate it holds, so counting them would inflate an empty
#: estate to a non-zero resource count.
_ESTATE_TABLES: tuple[str, ...] = tuple(t for t in TABLES if t not in PROBE_TABLES)

_MAX_ROWS_PER_TABLE = 200


# ── read classification ───────────────────────────────────────────────────────

def classify_read(outcome: Any, table: str) -> str:
    """Categorize one brokered read into a :data:`READ_*` outcome.

    Pure over a :class:`~tools.databridge.broker.FetchOutcome`, so the ladder is
    testable without a broker, a connector or an emulator.

    Order matters. A DENIAL is tested first: a refused call never reached the
    connector, so its (empty) ``connector_status`` says nothing at all and
    reading it as "answered with no rows" is exactly the conflation this adapter
    exists to refuse.
    """
    if not getattr(outcome, "ok", False):
        # A denial, an air-gap refusal, an unresolvable connection, or a fetch
        # whose audit row could not be written (rows withheld). None of them
        # measured anything.
        return READ_DENIED
    status = str(getattr(outcome, "connector_status", "") or "")
    if status == READ_DISABLED:
        return READ_DISABLED
    if status == READ_UNSUPPORTED_DOCKER:
        return READ_UNSUPPORTED_DOCKER
    if status == "ok":
        return READ_ANSWERED
    if status == "error":
        # The SDK gate is asked rather than the error prose parsed: `boto3 is
        # required for table 'x'` is the connector's wording, and a reader that
        # matched on it would go silently wrong the day that string changes.
        if table_needs_boto3(table) and not boto3_available():
            return READ_SDK_UNAVAILABLE
        return READ_ERROR
    # An unrecognised status is NOT an answer. Defaulting to `answered` here is
    # how a new connector state would silently become a `pass`.
    return READ_ERROR


def denial_basis(granted: bool | None, emulator_enabled: bool | None) -> str:
    """Why did every brokered read come back refused?

    THE GOVERNED DOOR LOSES A DISTINCTION AND THIS RESTORES IT, structurally.
    ``saas_base.connect`` returns False whenever ``health_check`` is not
    ``healthy`` — which covers BOTH "the emulator is switched off" and "the
    emulator is on and nothing answered" — and the broker turns that into one
    refusal, ``connector 'floci' refused to connect``. So the connector's own
    ``disabled`` status never reaches a brokered read, and both causes arrive
    here looking identical.

    The verdict is ``unknown`` either way and is NOT at stake. The BASIS is,
    because the two have different repairs: set ``FLOCI_ENABLED=true``, versus
    ``docker compose --profile floci up -d``. Collapsing them would leave an
    operator with "unknown" and nowhere to go.

    Resolved from STRUCTURED facts, never from the refusal's prose — a basis
    keyed on an error string goes silently wrong the day that string changes:

    * ``granted`` — is this connector in ``broker.list_available`` for the twin's
      agent id? A ``False`` here is a manifest/air-gap refusal and no statement
      about the emulator at all.
    * ``emulator_enabled`` — ``tools/cloud/emulator.py::enabled()``, the ONE
      switch (flx-seam-01). Reading it is a CONFIGURATION read that opens no
      socket and returns no estate data, so it is not the ungoverned side
      channel this adapter refuses.

    ``None`` on either input means it could not be determined, and the answer
    falls back to the least specific claim rather than guessing.
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

    ``reads`` maps table name -> :data:`READ_*` outcome. Pure and total; the
    four verdicts are exhaustive over its inputs. ``granted`` /
    ``emulator_enabled`` refine only the BASIS of a refusal (see
    :func:`denial_basis`) and can never move the verdict.
    """
    if not reads:
        return "unknown", "unmeasured"
    values = set(reads.values())
    if READ_DISABLED in values:
        return "unknown", "disabled"
    if READ_DENIED in values:
        return "unknown", denial_basis(granted, emulator_enabled)
    # The emulator's OWN health path failing is unreachability, not a failure of
    # an emulated service — and an unreachable emulator was never measured.
    if any(reads.get(t) == READ_ERROR for t in PROBE_TABLES):
        return "unknown", "unreachable"
    if READ_ERROR in values:
        return "fail", "emulator_errors"
    if READ_UNSUPPORTED_DOCKER in values:
        return "warn", READ_UNSUPPORTED_DOCKER
    if READ_SDK_UNAVAILABLE in values:
        return "warn", READ_SDK_UNAVAILABLE
    return "pass", "all_tables_answered"


# ── adapter ───────────────────────────────────────────────────────────────────

@register_twin
class FlociTwinAdapter(TwinAdapter):
    """Twin over the local floci AWS emulator. Reads only through the broker."""

    canvas_key = "floci"
    display_name = "Floci AWS Emulator"
    #: Provenance carried onto every canonical violation. `emulator-probe`, and
    #: the word `emulator` is load-bearing on the observatory: a reader seeing
    #: this method beside `heuristic` or `iqe-gate` is told, without opening the
    #: row, that the finding came from an emulated estate.
    method = "emulator-probe"
    snapshot_table = "floci_twin_snapshots"
    snapshot_time_col = "created_at"

    def _fleet_conn(self):
        """Connection for the snapshot table.

        ``get_canvas_connection`` rather than ``get_connection``:
        ``floci_twin_snapshots`` carries no ``classification`` / ``tenant_id``
        column, so the global RLS predicate would raise ``UndefinedColumn`` on
        every query — the canvas-connection rule in CLAUDE.md, applied to a
        table that follows the canvas twin-snapshot convention.
        """
        from tools.db.storage import get_canvas_connection

        return get_canvas_connection()

    # -- brokered reads -------------------------------------------------------

    def _read_table(self, table: str, limit: int = _MAX_ROWS_PER_TABLE):
        """One governed read. Never raises — a refusal is a result."""
        return broker.fetch(
            BROKER_AGENT_ID,
            CONNECTOR,
            table,
            limit=limit,
            classification="UNCLASSIFIED",
        )

    @staticmethod
    def _is_granted() -> bool | None:
        """Is ``floci`` reachable by this twin's agent id? ``None`` if unknown.

        ``broker.list_available`` is the broker's OWN answer to "what may this
        agent read", so the adapter never re-derives the manifest rule.
        """
        try:
            return any(
                entry.get("connector") == CONNECTOR
                for entry in broker.list_available(BROKER_AGENT_ID)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("floci twin: grant lookup unavailable: %s", exc)
            return None

    @staticmethod
    def _emulator_enabled() -> bool | None:
        """The ONE switch (flx-seam-01). Configuration only — no socket opens."""
        try:
            from tools.cloud import emulator

            return bool(emulator.enabled())
        except Exception as exc:  # noqa: BLE001
            logger.debug("floci twin: emulator seam unavailable: %s", exc)
            return None

    def _read_all(self, tables: tuple[str, ...] = TABLES) -> dict[str, dict]:
        """Read every declared table through the broker; return per-table detail."""
        detail: dict[str, dict] = {}
        for table in tables:
            outcome = self._read_table(table)
            outcome_kind = classify_read(outcome, table)
            detail[table] = {
                "outcome": outcome_kind,
                "connector_status": getattr(outcome, "connector_status", ""),
                "row_count": getattr(outcome, "row_count", 0) if outcome_kind == READ_ANSWERED else None,
                "docker_backed": table_is_docker_backed(table),
                "service": table_service(table),
                # Both error channels, kept apart: the BROKER's refusal reason
                # and the CONNECTOR's own errors are different findings.
                "broker_error": getattr(outcome, "error", "") or "",
                "connector_errors": list(getattr(outcome, "connector_errors", None) or []),
                "audited": bool(getattr(outcome, "audited", True)),
            }
        return detail

    # -- snapshot -------------------------------------------------------------

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        """Freeze the emulated estate as read through the broker.

        ``target_id`` names the emulator instance being frozen (a deployment may
        run more than one); it is a label, not a lookup key — the endpoint comes
        from the ``tools/cloud/emulator.py`` seam, which is the one switch.
        """
        target_id = str(target_id or "local")
        detail = self._read_all()
        reads = {t: d["outcome"] for t, d in detail.items()}
        verdict, basis = classify_verdict(
            reads,
            granted=self._is_granted(),
            emulator_enabled=self._emulator_enabled(),
        )

        # NONE, never 0, when nothing was measured. An unreachable emulator
        # holds an unknown number of buckets, and 0 asserts it holds none.
        answered_estate = [t for t in _ESTATE_TABLES if reads.get(t) == READ_ANSWERED]
        resource_count = (
            sum(int(detail[t]["row_count"] or 0) for t in answered_estate)
            if answered_estate
            else None
        )
        tables_ok = sum(1 for v in reads.values() if v == READ_ANSWERED)

        snap = {
            "id": f"floci-snap-{uuid.uuid4().hex[:12]}",
            "target_id": target_id,
            "label": label or "",
            "provenance": PROVENANCE,
            "target_csp": TARGET_CSP,
            "region": TARGET_REGION,
            "verdict": verdict,
            "verdict_basis": basis,
            "resource_count": resource_count,
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

        THIS FUNCTION TAKES NO ``provenance`` ARGUMENT AND NEVER WILL. The
        column is bound to the module constant :data:`PROVENANCE`, which is
        ``schema.PROVENANCE_EMULATED`` — a caller cannot pass a provenance in,
        and ``snap`` is not consulted for one. ``tests/test_floci_twin_adapter``
        asserts that structurally over this function's AST, because a
        behavioural test over today's callers would still pass the day somebody
        threads a kwarg through.

        A failed INSERT is reported (``persisted: False``), never swallowed into
        an apparently successful snapshot: a snapshot that was not recorded and
        one that was must not read alike.
        """
        try:
            conn = self._fleet_conn()
        except Exception as exc:  # noqa: BLE001 — an unreachable DB is not a twin failure
            logger.warning("floci twin: no connection for snapshot persist: %s", exc)
            return False
        try:
            conn.execute(
                "INSERT INTO floci_twin_snapshots "
                "(id, target_id, label, provenance, target_csp, region, verdict, "
                " verdict_basis, resource_count, tables_ok, tables_declared, "
                " payload_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                    json.dumps(snap["tables"]),
                    snap["created_at"],
                ),
            )
            conn.commit()
            return True
        except Exception as exc:  # noqa: BLE001 — table may not exist yet
            logger.warning("floci twin: snapshot INSERT failed: %s", exc)
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
                "FROM floci_twin_snapshots WHERE target_id=%s "
                "ORDER BY created_at DESC LIMIT %s",
                (str(target_id or "local"), int(limit)),
            ).fetchall()
        except Exception:  # noqa: BLE001 — table may not exist yet
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
        """The newest PERSISTED verdict for ``target_id`` — no emulator probe.

        Deliberately does not read the estate: this is the cheap status the
        observatory renders, and a fan-out across seven brokered tables on every
        page render would put an emulator round trip on the dashboard.

        With nothing persisted the verdict is ``unknown`` with basis
        ``no_snapshot`` — the twin has never looked, which is not a clean bill
        of health and must not read as one.
        """
        # `limit` is fixed at 1 and dropped from kwargs rather than forwarded:
        # a caller passing its own would raise TypeError on a duplicate keyword.
        kwargs.pop("limit", None)
        snaps = self.list_snapshots(target_id, limit=1, **kwargs)
        latest = snaps[0] if snaps else None
        return {
            "canvas": self.canvas_key,
            "target_id": str(target_id or "local"),
            "verdict": (latest or {}).get("verdict") or "unknown",
            "verdict_basis": (latest or {}).get("verdict_basis") or "no_snapshot",
            "provenance": (latest or {}).get("provenance") or PROVENANCE,
            "snapshot_count": len(snaps),
            "latest_snapshot": latest,
            "method": self.method,
        }

    # -- simulation -----------------------------------------------------------

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """Score a proposed set of AWS services against emulator + target reality.

        ``delta`` is anything naming AWS services — ``{"services": [...]}``, a
        Terraform plan, a list of resource types. Two independent questions are
        asked and neither substitutes for the other:

        * CAN THIS EMULATOR EXERCISE IT? A container-backed service on a host
          with no docker socket is a ``service_parity`` finding about the LOCAL
          rehearsal, and it is reported at ``medium`` — the deployment is fine,
          the rehearsal is not.
        * IS IT AVAILABLE IN THE TARGET? Delegated to the existing GovCloud
          presets through :meth:`TwinAdapter._target_augment`, which raises a
          ``deployment_blocker`` for a service absent from the target region.
          That is a fact about AWS GovCloud and is NOT weakened by running
          against an emulator.

        The verdict starts at ``unknown``: this simulation is static (it asks
        the catalogue and the docker seam, not the emulator), so a delta naming
        no service is honestly unscored rather than a free ``pass``.
        """
        delta = delta if delta is not None else {}
        preset = kwargs.get("target_preset", DEFAULT_TARGET_PRESET)

        violations: list[dict] = []
        for service in sorted(self._services_in(delta)):
            if not self._emulator_can_serve(service):
                violations.append(canonical_violation(
                    "medium",
                    "service_parity",
                    f"The local emulator cannot exercise '{service}' on this host: it is "
                    f"container-backed and no docker socket was found. Mount one "
                    f"(DOCKER_HOST / FLOCI_DOCKER_SOCKET) or rehearse this service "
                    f"elsewhere. This says nothing about the target environment.",
                    title=f"Emulator cannot serve '{service}' on this host",
                    rule_id="floci-service-unsupported-locally",
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
                "docker_backed": self._docker_backed(),
                "basis": "static: service catalogue + docker seam; the emulator was not probed",
            },
        )

    @staticmethod
    def _services_in(delta: Any) -> set[str]:
        """AWS service names named anywhere in ``delta`` (best-effort)."""
        from tools.cloud import emulator

        from tools.twin_core.target_presets import _iter_strings

        known = set(emulator.CONTAINER_BACKED_SERVICES) | {
            s for s in (table_service(t) for t in TABLES) if s
        }
        found: set[str] = set()
        for text in _iter_strings(delta):
            lowered = str(text).lower()
            for service in known:
                # Token match, so `aws_lambda_function` matches `lambda` while
                # `lambdas3` does not silently match either.
                if service in lowered.replace("-", "_").split("_") or lowered == service:
                    found.add(service)
        return found

    @staticmethod
    def _emulator_can_serve(service: str) -> bool:
        from tools.cloud import emulator

        return bool(emulator.service_supported(service))

    @staticmethod
    def _docker_backed():
        """Tri-state: True / False / None (cannot tell). Never coerced to a bool."""
        from tools.cloud import emulator

        return emulator.docker_backed()
