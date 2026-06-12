# CUI // SP-CTI
"""Partner Registry — CRUD and unified view for nc_partners."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def _safe_fetch(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> list[dict]:
    def _rows(cur) -> list[dict]:
        rows = cur.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]
    try:
        cur = conn.cursor()
        cur.execute(sql_pg, params)
        return _rows(cur)
    except Exception:
        try:
            cur = conn.cursor()
            cur.execute(sql_sq, params)
            return _rows(cur)
        except Exception:
            return []


def _try_exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> None:
    try:
        conn.execute(sql_pg, params)
    except Exception:
        conn.execute(sql_sq, params)


def create_partner(conn, data: dict) -> dict:
    """Insert a new nc_partners row. Returns {partner_id, name}."""
    partner_id = data.get("id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    _try_exec(
        conn,
        "INSERT INTO nc_partners (id, name, partner_type, asn, noc_email, noc_phone, legal_entity, contract_manager, status, notes, classification, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CUI',%s,%s)",
        "INSERT INTO nc_partners (id, name, partner_type, asn, noc_email, noc_phone, legal_entity, contract_manager, status, notes, classification, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'CUI',?,?)",
        (
            partner_id,
            data.get("name", ""),
            data.get("partner_type", "isp"),
            data.get("asn"),
            data.get("noc_email", ""),
            data.get("noc_phone", ""),
            data.get("legal_entity", ""),
            data.get("contract_manager", ""),
            data.get("status", "active"),
            data.get("notes", ""),
            now,
            now,
        ),
    )
    conn.commit()
    return {"partner_id": partner_id, "name": data.get("name", "")}


def get_partner(conn, partner_id: str) -> dict | None:
    """Fetch a single partner by ID. Returns None if not found."""
    rows = _safe_fetch(
        conn,
        "SELECT * FROM nc_partners WHERE id = %s",
        "SELECT * FROM nc_partners WHERE id = ?",
        (partner_id,),
    )
    return rows[0] if rows else None


def list_partners(conn, status: str = "") -> list[dict]:
    """List all partners, optionally filtered by status."""
    if status:
        return _safe_fetch(
            conn,
            "SELECT * FROM nc_partners WHERE status = %s ORDER BY name",
            "SELECT * FROM nc_partners WHERE status = ? ORDER BY name",
            (status,),
        )
    return _safe_fetch(
        conn,
        "SELECT * FROM nc_partners ORDER BY name",
        "SELECT * FROM nc_partners ORDER BY name",
    )


def update_partner(conn, partner_id: str, data: dict) -> dict:
    """Update partner fields (name, status, noc_email, etc.)."""
    now = datetime.now(timezone.utc).isoformat()
    allowed = ["name", "partner_type", "asn", "noc_email", "noc_phone",
               "legal_entity", "contract_manager", "status", "notes"]
    updates = {k: v for k, v in data.items() if k in allowed}
    updates["updated_at"] = now
    sets_pg = ", ".join(f"{k} = %s" for k in updates)
    sets_sq = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [partner_id]
    _try_exec(
        conn,
        f"UPDATE nc_partners SET {sets_pg} WHERE id = %s",  # nosec B608
        f"UPDATE nc_partners SET {sets_sq} WHERE id = ?",   # nosec B608
        tuple(vals),
    )
    conn.commit()
    return {"partner_id": partner_id, "updated": list(updates.keys())}


def get_unified_view(conn_nc, partner_id: str) -> dict:
    """Return partner record + all agreements + BGP sessions + cross-connects.

    Opens separate CCC connection for cross-connect data.
    partner_id → ccc_cross_connects.partner_id is a logical FK only (cross-DB).
    """
    partner = get_partner(conn_nc, partner_id)
    if not partner:
        return {}

    agreements = _safe_fetch(
        conn_nc,
        "SELECT id, peer_name, peer_asn, peering_type, status, contract_start, contract_end, monthly_cost, noc_email, sla_uptime_pct FROM nc_peering_agreements WHERE partner_id = %s ORDER BY created_at DESC",
        "SELECT id, peer_name, peer_asn, peering_type, status, contract_start, contract_end, monthly_cost, noc_email, sla_uptime_pct FROM nc_peering_agreements WHERE partner_id = ? ORDER BY created_at DESC",
        (partner_id,),
    )

    agreement_ids = [a["id"] for a in agreements]
    bgp_sessions: list[dict] = []
    if agreement_ids:
        placeholders_pg = ", ".join(["%s"] * len(agreement_ids))
        placeholders_sq = ", ".join(["?"] * len(agreement_ids))
        bgp_sessions = _safe_fetch(
            conn_nc,
            f"SELECT * FROM nc_peering_sessions WHERE agreement_id IN ({placeholders_pg})",  # nosec B608
            f"SELECT * FROM nc_peering_sessions WHERE agreement_id IN ({placeholders_sq})",  # nosec B608
            tuple(agreement_ids),
        )

    cross_connects: list[dict] = []
    try:
        from tools.ccc_canvas.db.init_db import get_connection as ccc_conn_factory
        ccc = ccc_conn_factory()
        try:
            cross_connects = _safe_fetch(
                ccc,
                "SELECT id, xc_number, facility, port_a, port_z, speed_gbps, status, order_status, monthly_cost_usd FROM ccc_cross_connects WHERE partner_id = %s ORDER BY created_at DESC",
                "SELECT id, xc_number, facility, port_a, port_z, speed_gbps, status, order_status, monthly_cost_usd FROM ccc_cross_connects WHERE partner_id = ? ORDER BY created_at DESC",
                (partner_id,),
            )
        finally:
            try:
                ccc.close()
            except Exception:
                pass
    except Exception:
        pass

    # Compute next renewal date
    next_renewal = None
    for a in agreements:
        end = a.get("contract_end", "")
        if end and a.get("status") == "operational":
            if next_renewal is None or end < next_renewal:
                next_renewal = end

    return {
        "partner": partner,
        "agreements": agreements,
        "bgp_sessions": bgp_sessions,
        "cross_connects": cross_connects,
        "next_renewal_date": next_renewal,
        "active_agreement_count": sum(1 for a in agreements if a.get("status") == "operational"),
        "bgp_session_count": len(bgp_sessions),
        "cross_connect_count": len(cross_connects),
    }
