# CUI // SP-CTI
"""IQE adapter for Circuit & Capacity Canvas (CCC).

Registers 5 collections: ccc.circuits, ccc.cross_connects, ccc.loa,
ccc.capacity_plans, ccc.dwdm_spans.
"""
from __future__ import annotations

import os


def _ccc_conn(conn):
    if conn is not None:
        return conn, False
    from tools.ccc_canvas.db.init_db import get_connection
    return get_connection(), True


def _safe_fetch(conn, sql: str, params=()) -> list[dict]:
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]
    except Exception:
        return []


def circuits_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _ccc_conn(conn)
    try:
        q = query.lower()
        if "active" in q:
            sql = "SELECT circuit_id,carrier,circuit_type,bandwidth_gbps,status,utilization_pct,mrr_usd FROM ccc_circuits WHERE status='active' ORDER BY carrier"
        elif "high utilization" in q or "near capacity" in q or "overloaded" in q:
            sql = "SELECT circuit_id,carrier,bandwidth_gbps,utilization_pct,status FROM ccc_circuits WHERE utilization_pct >= 70 ORDER BY utilization_pct DESC"
        elif "maintenance" in q:
            sql = "SELECT circuit_id,carrier,circuit_type,bandwidth_gbps,status FROM ccc_circuits WHERE status='maintenance'"
        elif "expir" in q:
            sql = "SELECT circuit_id,carrier,bandwidth_gbps,contract_end,mrr_usd FROM ccc_circuits WHERE contract_end != '' ORDER BY contract_end ASC LIMIT 20"
        elif "decommission" in q:
            sql = "SELECT circuit_id,carrier,circuit_type,bandwidth_gbps FROM ccc_circuits WHERE status='decommissioned'"
        elif "dark fiber" in q:
            sql = "SELECT circuit_id,carrier,bandwidth_gbps,endpoint_a,endpoint_z,status FROM ccc_circuits WHERE circuit_type='dark_fiber'"
        elif "wavelength" in q or "wave" in q:
            sql = "SELECT circuit_id,carrier,bandwidth_gbps,endpoint_a,endpoint_z,status FROM ccc_circuits WHERE circuit_type='wavelength'"
        elif "100g" in q or "400g" in q:
            t = "400g" if "400g" in q else "100g"
            sql = f"SELECT circuit_id,carrier,bandwidth_gbps,endpoint_a,endpoint_z,status FROM ccc_circuits WHERE circuit_type='{t}'"
        else:
            sql = "SELECT circuit_id,carrier,circuit_type,bandwidth_gbps,status,utilization_pct FROM ccc_circuits ORDER BY carrier LIMIT 50"
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try: _conn.close()
            except Exception: pass


def cross_connects_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _ccc_conn(conn)
    try:
        q = query.lower()
        if "pending" in q or "installing" in q:
            sql = "SELECT xc_number,facility,port_a,port_z,speed_gbps,status FROM ccc_cross_connects WHERE status IN ('pending_loa','installing') ORDER BY created_at DESC"
        elif "equinix" in q:
            sql = "SELECT xc_number,facility_id,port_a,port_z,speed_gbps,status,monthly_cost_usd FROM ccc_cross_connects WHERE facility='equinix' ORDER BY xc_number"
        elif "megaport" in q:
            sql = "SELECT xc_number,facility_id,port_a,port_z,speed_gbps,status,monthly_cost_usd FROM ccc_cross_connects WHERE facility='megaport' ORDER BY xc_number"
        elif "active" in q:
            sql = "SELECT xc_number,facility,port_a,port_z,speed_gbps,monthly_cost_usd FROM ccc_cross_connects WHERE status='active' ORDER BY facility, xc_number"
        else:
            sql = "SELECT xc_number,facility,port_a,port_z,speed_gbps,status,monthly_cost_usd FROM ccc_cross_connects ORDER BY facility, xc_number LIMIT 50"
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try: _conn.close()
            except Exception: pass


def loa_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _ccc_conn(conn)
    try:
        q = query.lower()
        if "open" in q or "pending" in q:
            sql = "SELECT loa_number,facility,requester_company,status,valid_days,created_at FROM ccc_loa_requests WHERE status IN ('draft','submitted','approved') ORDER BY created_at DESC"
        elif "expir" in q:
            sql = "SELECT loa_number,facility,requester_company,status,valid_days,submitted_at FROM ccc_loa_requests WHERE status='approved' ORDER BY submitted_at ASC"
        elif "complet" in q:
            sql = "SELECT loa_number,facility,requester_company,approved_at FROM ccc_loa_requests WHERE status='completed' ORDER BY approved_at DESC LIMIT 20"
        else:
            sql = "SELECT loa_number,facility,requester_company,status,created_at FROM ccc_loa_requests ORDER BY created_at DESC LIMIT 50"
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try: _conn.close()
            except Exception: pass


def capacity_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _ccc_conn(conn)
    try:
        q = query.lower()
        if "urgent" in q or "critical" in q or "saturat" in q:
            sql = "SELECT cp.*, c.circuit_id, c.carrier FROM ccc_capacity_plans cp JOIN ccc_circuits c ON c.id=cp.circuit_id WHERE cp.months_to_saturation <= 6 ORDER BY cp.months_to_saturation ASC"
        elif "warn" in q or "high" in q:
            sql = "SELECT cp.*, c.circuit_id, c.carrier FROM ccc_capacity_plans cp JOIN ccc_circuits c ON c.id=cp.circuit_id WHERE cp.current_utilization_pct >= 70 ORDER BY cp.current_utilization_pct DESC"
        else:
            sql = "SELECT cp.circuit_id AS plan_circuit_id, cp.plan_date, cp.current_utilization_pct, cp.months_to_saturation, cp.recommended_action, c.circuit_id, c.carrier FROM ccc_capacity_plans cp JOIN ccc_circuits c ON c.id=cp.circuit_id ORDER BY cp.plan_date DESC LIMIT 50"
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try: _conn.close()
            except Exception: pass


def dwdm_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _ccc_conn(conn)
    try:
        q = query.lower()
        if "degraded" in q or "alarm" in q:
            sql = "SELECT span_id,fiber_route,total_capacity_tbps,used_capacity_tbps,osnr_db,status FROM ccc_dwdm_spans WHERE status='degraded'"
        elif "dark" in q:
            sql = "SELECT span_id,fiber_route,total_capacity_tbps,available_wavelengths FROM ccc_dwdm_spans WHERE status='dark'"
        elif "available" in q or "free" in q:
            sql = "SELECT span_id,fiber_route,available_wavelengths,total_capacity_tbps,used_capacity_tbps FROM ccc_dwdm_spans WHERE available_wavelengths > 0 ORDER BY available_wavelengths DESC"
        else:
            sql = "SELECT span_id,fiber_route,total_capacity_tbps,used_capacity_tbps,available_wavelengths,osnr_db,status FROM ccc_dwdm_spans ORDER BY span_id"
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try: _conn.close()
            except Exception: pass


# ── Dispatch ──────────────────────────────────────────────────────────────────

_COLLECTION_MAP = {
    "ccc.circuits":       circuits_adapter,
    "ccc.cross_connects": cross_connects_adapter,
    "ccc.loa":            loa_adapter,
    "ccc.capacity_plans": capacity_adapter,
    "ccc.dwdm_spans":     dwdm_adapter,
}


def handle_query(conn, query: str, collection: str = "") -> dict:
    """Route a natural-language query to the right collection adapter."""
    q = query.lower()
    if not collection:
        if any(k in q for k in ("cross-connect", "cross connect", "xc ", "patch")):
            collection = "ccc.cross_connects"
        elif any(k in q for k in ("loa", "letter of auth", "authorization")):
            collection = "ccc.loa"
        elif any(k in q for k in ("capacity", "saturat", "utiliz", "growth")):
            collection = "ccc.capacity_plans"
        elif any(k in q for k in ("dwdm", "wavelength", "fiber span", "osnr")):
            collection = "ccc.dwdm_spans"
        else:
            collection = "ccc.circuits"
    fn = _COLLECTION_MAP.get(collection, circuits_adapter)
    rows = fn(query, conn=conn)
    return {"collection": collection, "query": query, "rows": rows, "count": len(rows)}


def register(registry: dict) -> None:
    """Register CCC collections into the global IQE registry."""
    if os.environ.get("ICDEV_CCC_ENABLED", "").lower() not in ("true", "1", "yes"):
        return
    for name, fn in _COLLECTION_MAP.items():
        registry[name] = fn
