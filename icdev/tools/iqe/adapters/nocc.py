# CUI // SP-CTI
"""IQE NOC Operations Canvas collection adapters.

Importing this module registers six collections:
  noc.alarms              — active/cleared alarms; filter by severity, alarm_type, cleared.
  noc.incidents           — incident records; filter by severity, status.
  noc.rfcs                — RFC change requests; filter by status, change_type.
  noc.mops                — Methods of Procedure; filter by rfc_id.
  noc.maintenance_windows — scheduled/in-progress windows; filter by status, impact_scope.
  noc.sla_records         — SLA tracking; filter by circuit_id, breach.

Tables are created by tools/noc_canvas/db/init_db.py.
Adapters return [] gracefully if tables are absent.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _nocc_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.noc_canvas.db.init_db import get_connection
    return get_connection(), True


def _safe_fetch(conn: Any, sql: str) -> list[dict]:
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def alarms_adapter(conn: Any) -> list[dict]:
    c, owned = _nocc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, alarm_source, severity, alarm_type, device_name, device_ip, "
            "circuit_id, carrier, description, correlated_incident_id, "
            "suppressed, acknowledged, cleared, classification, first_seen, last_seen "
            "FROM noc_alarms ORDER BY "
            "CASE severity WHEN 'critical' THEN 0 WHEN 'major' THEN 1 "
            "WHEN 'minor' THEN 2 WHEN 'warning' THEN 3 ELSE 4 END, "
            "last_seen DESC LIMIT 500",
        )
    finally:
        if owned:
            c.close()


def incidents_adapter(conn: Any) -> list[dict]:
    c, owned = _nocc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, incident_number, title, severity, status, "
            "affected_circuit, affected_carrier, sla_breach, mttr_minutes, "
            "opened_by, assigned_to, classification, created_at, resolved_at "
            "FROM noc_incidents ORDER BY "
            "CASE severity WHEN 'p1' THEN 0 WHEN 'p2' THEN 1 "
            "WHEN 'p3' THEN 2 ELSE 3 END, created_at DESC LIMIT 200",
        )
    finally:
        if owned:
            c.close()


def rfcs_adapter(conn: Any) -> list[dict]:
    c, owned = _nocc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, rfc_number, title, change_type, status, risk_level, "
            "scheduled_start, scheduled_end, change_owner, approver, "
            "classification, created_at "
            "FROM noc_rfcs ORDER BY created_at DESC LIMIT 200",
        )
    finally:
        if owned:
            c.close()


def mops_adapter(conn: Any) -> list[dict]:
    c, owned = _nocc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, mop_number, title, rfc_id, generated_by, "
            "classification, created_at "
            "FROM noc_mops ORDER BY created_at DESC LIMIT 200",
        )
    finally:
        if owned:
            c.close()


def maintenance_adapter(conn: Any) -> list[dict]:
    c, owned = _nocc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, window_number, title, rfc_id, scheduled_start, scheduled_end, "
            "status, impact_scope, notification_sent, classification, created_at "
            "FROM noc_maintenance_windows ORDER BY scheduled_start ASC LIMIT 100",
        )
    finally:
        if owned:
            c.close()


def sla_adapter(conn: Any) -> list[dict]:
    c, owned = _nocc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, circuit_id, carrier, customer, sla_type, target_value, "
            "measured_value, measurement_period, breach, breach_minutes, "
            "credit_eligible, period_start, period_end, classification "
            "FROM noc_sla_records ORDER BY breach DESC, breach_minutes DESC LIMIT 200",
        )
    finally:
        if owned:
            c.close()


register_collection("noc.alarms", alarms_adapter)
register_collection("noc.incidents", incidents_adapter)
register_collection("noc.rfcs", rfcs_adapter)
register_collection("noc.mops", mops_adapter)
register_collection("noc.maintenance_windows", maintenance_adapter)
register_collection("noc.sla_records", sla_adapter)
