# CUI // SP-CTI
"""ICDEV /cpmp DataBridge connector — the contract bridge (prem-cpmp-01).

Read-mostly local-DB connector giving external delivery tools (compass) a
scoped window into the contract portfolio, plus three narrow write paths that
close the delivery loop:

  READ  contracts            id/number/title/type, total/funded/ceiling value,
                             PoP, status, health, CPARS rating
  READ  clins                funding per CLIN
  READ  milestones           baseline/forecast/actual + status
  READ  deliverables         CDRLs — due/submitted/status
  READ  cpars_assessments    the customer's own report card, per rating area
  READ  negative_events      findings + corrective-action state (the actionable
                             half: an open CAP is a thing a recompete campaign
                             can actually close before the RFP drops)
  WRITE evm_periods          PV/EV/AC per period (CPI/SPI derived here)
  WRITE deliverable_status   submission status transition on a deliverable
  WRITE mod_recommendations  PMO-negotiated scope change → a 'requested'
                             contract mod for contracts staff to action

A mod recommendation lands as a normal ``cpmp_contract_mods`` row at status
'requested' — the existing lifecycle already models exactly this (requested →
in_review → approved/rejected → executed), so the bridge adds no new status and
takes no approval authority. Compass proposes; contracts staff dispose.

Access control: the feeds surface requires ``databridge:icdev_cpmp:read`` /
``:write`` scopes, and every READ row is classification-filtered against the
caller's ceiling (Bell-LaPadula read-down via ``classifications_dominated_by``
— the feeds blueprint injects ``_caller_classification`` from the service-key
binding). Rows carrying compartments are excluded for feed callers entirely:
service keys have no compartment grants.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
)
from tools.databridge.registry import register_connector
from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("databridge.icdev_cpmp")

_READ_TABLES = ("contracts", "clins", "milestones", "deliverables",
                "cpars_assessments", "negative_events")
_WRITE_TABLES = ("evm_periods", "deliverable_status", "mod_recommendations")

# cpmp_contract_mods.type CHECK — the bridge must never invent a value outside it.
_MOD_TYPES = ("admin", "funding", "scope", "pop")
_DEFAULT_LIMIT = 200

_CONTRACT_COLUMNS = (
    "id, contract_number, title, agency, contract_type, total_value, "
    "funded_value, ceiling_value, billed_value, pop_start, pop_end, "
    "pop_base_end, option_years, status, health, cpars_rating_current, "
    "classification, compartments"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dominated(caller_classification: str) -> set:
    try:
        from tools.security.security_context import classifications_dominated_by

        allowed = set(classifications_dominated_by(caller_classification) or [])
        allowed.add(caller_classification)
        return allowed
    except Exception:  # noqa: BLE001 — on any doubt, only the caller's own level
        return {caller_classification}


def _mac_visible(rows: List[dict], caller_classification: str) -> List[dict]:
    """Read-down filter + compartment exclusion for feed callers."""
    allowed = _dominated(caller_classification or "CUI")
    visible = []
    for row in rows:
        if (row.get("classification") or "CUI") not in allowed:
            continue
        compartments = row.get("compartments")
        if compartments:
            try:
                if json.loads(compartments):
                    continue  # compartmented rows never flow to feed callers
            except (ValueError, TypeError):
                continue
        row.pop("compartments", None)
        visible.append(row)
    return visible


@register_connector
class ICDEVCpmpConnector(DataConnector):
    """Contract portfolio bridge for external delivery tools."""

    _connector_name = "icdev_cpmp"

    def __init__(self) -> None:
        self._connected = False

    @property
    def connector_name(self) -> str:
        return self._connector_name

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.DATABASE

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,  # evm_periods + deliverable_status only
            supports_schema_inference=True,
            max_batch_size=_DEFAULT_LIMIT,
            supported_formats=["json"],
        )

    def connect(self, config: Dict[str, Any]) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def health_check(self) -> Dict[str, Any]:
        try:
            conn = get_connection()
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM cpmp_contracts").fetchone()["n"]
            conn.close()
            return {"status": "healthy", "connector": self._connector_name,
                    "contracts": count}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "connector": self._connector_name,
                    "error": str(exc)}

    # -- READ ------------------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name or request.query
        if table not in _READ_TABLES:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown table '{table}'. Readable: {list(_READ_TABLES)}"],
            )
        filters = dict(request.filters or {})
        caller_classification = str(filters.pop("_caller_classification", "") or "CUI")
        contract_id = filters.get("contract_id")
        limit = int(request.limit or _DEFAULT_LIMIT)
        try:
            conn = get_connection()
            try:
                rows = self._query(conn, table, contract_id, limit)
            finally:
                conn.close()
            visible = _mac_visible(rows, caller_classification)
            return ConnectorResponse(
                status="ok", data=visible, row_count=len(visible),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"table": table,
                          "mac_filtered": len(rows) - len(visible)},
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error", errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    def _query(self, conn, table: str, contract_id: Optional[str],
               limit: int) -> List[dict]:
        if table == "contracts":
            sql = (f"SELECT {_CONTRACT_COLUMNS} FROM cpmp_contracts "  # nosec B608 — fixed column list
                   f"ORDER BY updated_at DESC LIMIT %s")
            rows = conn.execute(sql, (limit,)).fetchall()
        elif table == "clins":
            rows = conn.execute(
                "SELECT id, contract_id, clin_number, description, clin_type, "
                " total_value, funded_value, billed_value, status, "
                " classification, '[]' AS compartments "
                "FROM cpmp_clins WHERE (%s IS NULL OR contract_id = %s) "
                "ORDER BY clin_number LIMIT %s",
                (contract_id, contract_id, limit)).fetchall()
        elif table == "milestones":
            rows = conn.execute(
                "SELECT id, contract_id, title, baseline_date, forecast_date, "
                " actual_date, status, classification, '[]' AS compartments "
                "FROM cpmp_milestones WHERE (%s IS NULL OR contract_id = %s) "
                "ORDER BY baseline_date LIMIT %s",
                (contract_id, contract_id, limit)).fetchall()
        elif table == "cpars_assessments":
            # The customer's own report card — what a recompete is graded on.
            rows = conn.execute(
                "SELECT id, contract_id, period_start, period_end, quality_rating, "
                " schedule_rating, cost_rating, management_rating, "
                " small_business_rating, overall_rating, overall_score, "
                " classification, '[]' AS compartments "
                "FROM cpmp_cpars_assessments WHERE (%s IS NULL OR contract_id = %s) "
                "ORDER BY period_end DESC LIMIT %s",
                (contract_id, contract_id, limit)).fetchall()
        elif table == "negative_events":
            # Corrective-action state is the actionable half: an open CAP is a
            # concrete thing a recompete campaign can close before the RFP.
            rows = conn.execute(
                "SELECT id, contract_id, event_type, severity, description, "
                " corrective_action, corrective_action_status, "
                " corrective_action_due, cpars_impact, "
                " classification, '[]' AS compartments "
                "FROM cpmp_negative_events WHERE (%s IS NULL OR contract_id = %s) "
                "ORDER BY severity DESC LIMIT %s",
                (contract_id, contract_id, limit)).fetchall()
        else:  # deliverables
            rows = conn.execute(
                "SELECT id, contract_id, cdrl_number, title, deliverable_type, "
                " frequency, due_date, submitted_date, status, days_overdue, "
                " classification, '[]' AS compartments "
                "FROM cpmp_deliverables WHERE (%s IS NULL OR contract_id = %s) "
                "ORDER BY due_date LIMIT %s",
                (contract_id, contract_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # -- WRITE (narrow, delivery-loop only) --------------------------------------

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name or request.query
        if table not in _WRITE_TABLES:
            return ConnectorResponse(
                status="error",
                errors=[f"Table '{table}' is not writable "
                        f"(writable: {list(_WRITE_TABLES)})"],
            )
        if not isinstance(data, dict):
            return ConnectorResponse(status="error", errors=["body must be an object"])
        try:
            if table == "evm_periods":
                result = self._write_evm_period(data)
            elif table == "mod_recommendations":
                result = self._write_mod_recommendation(data)
            else:
                result = self._write_deliverable_status(data)
            return ConnectorResponse(
                status="ok", data=result, row_count=1,
                duration_ms=int((time.time() - t0) * 1000),
            )
        except (KeyError, ValueError) as exc:
            return ConnectorResponse(status="error", errors=[f"invalid payload: {exc}"])
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(status="error", errors=[str(exc)])

    def _write_evm_period(self, data: dict) -> dict:
        contract_id = str(data["contract_id"])
        period_date = str(data["period_date"])
        pv = float(data["pv"])
        ev = float(data["ev"])
        ac = float(data["ac"])
        bac = float(data.get("bac") or 0.0)
        cpi = round(ev / ac, 4) if ac else None
        spi = round(ev / pv, 4) if pv else None
        row_id = str(uuid.uuid4())
        conn = get_connection()
        try:
            exists = conn.execute(
                "SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"contract {contract_id} not found")
            conn.execute(
                "INSERT INTO cpmp_evm_periods (id, contract_id, period_date, "
                " budget_at_completion, bac, planned_value, pv, earned_value, ev, "
                " actual_cost, ac, bcws, bcwp, acwp, cpi, spi, cost_variance, cv) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                " %s, %s, %s, %s)",
                (row_id, contract_id, period_date, bac, bac, pv, pv, ev, ev,
                 ac, ac, pv, ev, ac, cpi, spi, ev - ac, ev - ac),
            )
            conn.commit()
        finally:
            conn.close()
        return {"id": row_id, "contract_id": contract_id, "period_date": period_date,
                "cpi": cpi, "spi": spi}

    def _write_deliverable_status(self, data: dict) -> dict:
        deliverable_id = str(data["deliverable_id"])
        status = str(data["status"])
        allowed = ("in_progress", "draft_complete", "internal_review", "submitted",
                   "government_review", "resubmitted")
        if status not in allowed:
            raise ValueError(f"status must be one of {allowed} (acceptance/rejection "
                             f"stays a government-side action inside /cpmp)")
        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE cpmp_deliverables SET status = %s, "
                " submitted_date = CASE WHEN %s = 'submitted' THEN %s "
                "                       ELSE submitted_date END, "
                " notes = COALESCE(%s, notes), updated_at = %s WHERE id = %s",
                (status, status, data.get("submitted_date") or _now()[:10],
                 data.get("notes"), _now(), deliverable_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise ValueError(f"deliverable {deliverable_id} not found")
        finally:
            conn.close()
        return {"id": deliverable_id, "status": status}

    def _write_mod_recommendation(self, data: dict) -> dict:
        """PMO-negotiated scope change → a 'requested' contract mod.

        Lands at status 'requested' (never 'approved'): the bridge proposes,
        contracts staff dispose. ``provenance`` — the decision id, requirement,
        ROM and schedule impact behind the ask — is preserved in ``metadata``
        so a reviewer can trace the number back to the customer's signature.
        """
        contract_id = str(data["contract_id"])
        description = str(data["description"]).strip()
        if not description:
            raise ValueError("description is required — a mod with no ask is noise")

        mod_type = str(data.get("type") or "scope")
        if mod_type not in _MOD_TYPES:
            raise ValueError(f"type must be one of {_MOD_TYPES}")

        value_delta = float(data.get("value_delta") or 0.0)
        provenance = data.get("provenance") or {}
        if not isinstance(provenance, dict):
            raise ValueError("provenance must be an object")

        row_id = str(uuid.uuid4())
        conn = get_connection()
        try:
            exists = conn.execute(
                "SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"contract {contract_id} not found")

            # mod_number is NOT NULL and per-contract sequential.
            row = conn.execute(
                "SELECT COALESCE(MAX(mod_number), 0) AS n FROM cpmp_contract_mods "
                "WHERE contract_id = %s", (contract_id,)
            ).fetchone()
            mod_number = int(dict(row)["n"]) + 1

            conn.execute(
                "INSERT INTO cpmp_contract_mods (id, contract_id, mod_number, type, "
                " description, value_delta, status, requested_by, requested_at, "
                " effective_date, metadata, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row_id, contract_id, mod_number, mod_type, description, value_delta,
                 "requested", str(data.get("requested_by") or "compass"), _now(),
                 data.get("effective_date"), json.dumps(provenance), _now(), _now()),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("cpmp mod recommendation %s (contract %s, mod %s) from %s",
                    row_id, contract_id, mod_number, data.get("requested_by"))
        return {"id": row_id, "contract_id": contract_id, "mod_number": mod_number,
                "type": mod_type, "status": "requested", "value_delta": value_delta}

    def list_tables(self) -> List[str]:
        return list(_READ_TABLES) + list(_WRITE_TABLES)
