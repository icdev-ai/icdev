# CUI // SP-CTI
"""Cross-Connect Order Manager — order lifecycle and carrier API integration."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.ccc_canvas.constants import XC_IN_FLIGHT_STATUSES

_EQUINIX_STATE_MAP: dict[str, str] = {
    "PROVISIONED": "active",
    "PROVISIONING": "installing",
    "PENDING_APPROVAL": "loa_approved",
    "REQUESTED": "loa_submitted",
    "CANCELLED": "cancelled",
    "DEPROVISIONED": "cancelled",
}

_MEGAPORT_STATE_MAP: dict[str, str] = {
    "LIVE": "active",
    "CONFIGURED": "installing",
    "DESIGN": "loa_submitted",
    "CANCELLED": "cancelled",
    "DECOMMISSIONED": "cancelled",
    "FAILED": "cancelled",
}


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


def _get_xc(conn, xc_id: int) -> dict | None:
    rows = _safe_fetch(
        conn,
        "SELECT * FROM ccc_cross_connects WHERE id = %s",
        "SELECT * FROM ccc_cross_connects WHERE id = ?",
        (xc_id,),
    )
    return rows[0] if rows else None


def _record_event(conn, xc_id: int, event_type: str, old_status: str,
                  new_status: str, note: str = "", actor: str = "system",
                  carrier_ref: str = "") -> None:
    """Append-only insert to ccc_xc_order_events."""
    now = datetime.now(timezone.utc).isoformat()
    _try_exec(
        conn,
        "INSERT INTO ccc_xc_order_events (xc_id, event_type, old_status, new_status, note, actor, carrier_ref, classification, ts) VALUES (%s,%s,%s,%s,%s,%s,%s,'CUI',%s)",
        "INSERT INTO ccc_xc_order_events (xc_id, event_type, old_status, new_status, note, actor, carrier_ref, classification, ts) VALUES (?,?,?,?,?,?,?,'CUI',?)",
        (xc_id, event_type, old_status, new_status, note, actor, carrier_ref, now),
    )


def _set_order_status(conn, xc_id: int, new_status: str, order_id: str = "",
                      carrier_ticket_id: str = "", ordered_by: str = "",
                      estimated_delivery: str = "") -> None:
    sets_pg = "order_status = %s"
    sets_sq = "order_status = ?"
    params: list = [new_status]

    if order_id:
        sets_pg += ", order_id = %s"
        sets_sq += ", order_id = ?"
        params.append(order_id)
    if carrier_ticket_id is not None:
        sets_pg += ", carrier_ticket_id = %s"
        sets_sq += ", carrier_ticket_id = ?"
        params.append(carrier_ticket_id)
    if ordered_by:
        sets_pg += ", ordered_by = %s"
        sets_sq += ", ordered_by = ?"
        params.append(ordered_by)
    if estimated_delivery:
        sets_pg += ", estimated_delivery = %s"
        sets_sq += ", estimated_delivery = ?"
        params.append(estimated_delivery)

    params.append(xc_id)
    _try_exec(
        conn,
        f"UPDATE ccc_cross_connects SET {sets_pg} WHERE id = %s",  # nosec B608
        f"UPDATE ccc_cross_connects SET {sets_sq} WHERE id = ?",   # nosec B608
        tuple(params),
    )


def create_cross_connect_order(conn, xc_id: int, ordered_by: str) -> dict:
    """Transition order_status → 'ordered'.

    Attempts Equinix/Megaport write API if credentials configured.
    Falls back gracefully if no credentials (fallback=True in return).
    """
    xc = _get_xc(conn, xc_id)
    if not xc:
        raise ValueError(f"Cross-connect {xc_id} not found")

    old_status = xc.get("order_status", "not_ordered")
    order_id = str(uuid.uuid4())
    carrier_ticket_id = ""
    fallback = True
    note = ""
    facility = (xc.get("facility") or "").lower()

    if facility == "equinix":
        try:
            import os
            if os.environ.get("EQUINIX_ECX_CLIENT_ID"):
                from tools.databridge.connectors.equinix_ecx_connector import EquinixECXConnector
                client = EquinixECXConnector()
                # Use facility_id as port UUID if it looks like a UUID, else skip API
                fid = xc.get("facility_id", "")
                if fid and len(fid) == 36 and fid.count("-") == 4:
                    payload = {
                        "name": xc.get("xc_number", f"XC-{xc_id}"),
                        "type": "EVPL_VC",
                        "bandwidth": int(xc.get("speed_gbps", 1) * 1000),
                        "aSide": {"accessPoint": {"type": "COLO", "port": {"uuid": fid}}},
                        "zSide": {"accessPoint": {"type": "COLO", "port": {"uuid": xc.get("port_z", "")}}},
                    }
                    resp = client.create_connection(payload)
                    carrier_ticket_id = resp.get("uuid", "")
                    fallback = False
                    note = f"Equinix connection submitted: {carrier_ticket_id}"
                else:
                    note = "Equinix: facility_id is not a port UUID; skipping API call"
        except Exception as e:
            note = f"Equinix API error: {e}"

    elif facility == "megaport":
        try:
            import os
            if os.environ.get("MEGAPORT_USER"):
                from tools.databridge.connectors.megaport_connector import MegaportConnector
                client = MegaportConnector()
                # Only proceed if we have a valid productUid (stored in facility_id)
                fid = xc.get("facility_id", "")
                if fid:
                    payload = {
                        "productName": xc.get("xc_number", f"XC-{xc_id}"),
                        "productType": "VXC",
                        "rateLimit": int(xc.get("speed_gbps", 1) * 1000),
                        "aEnd": {"productUid": fid},
                        "bEnd": {"productUid": xc.get("port_z", "")},
                    }
                    from tools.databridge.connector import ConnectorRequest
                    req = ConnectorRequest(table_name="vxcs", filters=payload)
                    resp = client.write(req)
                    result = resp.rows[0] if resp.rows else {}
                    carrier_ticket_id = result.get("productUid", "")
                    fallback = False
                    note = f"Megaport VXC submitted: {carrier_ticket_id}"
                else:
                    note = "Megaport: no facility_id (productUid); skipping API call"
        except Exception as e:
            note = f"Megaport API error: {e}"
    else:
        note = f"No API integration for facility '{facility}'; order recorded locally"

    _set_order_status(conn, xc_id, "ordered", order_id=order_id,
                      carrier_ticket_id=carrier_ticket_id, ordered_by=ordered_by)
    _record_event(conn, xc_id, "order_placed", old_status, "ordered",
                  note=note, actor=ordered_by, carrier_ref=carrier_ticket_id)
    conn.commit()

    return {
        "xc_id": xc_id,
        "order_id": order_id,
        "order_status": "ordered",
        "carrier_ticket_id": carrier_ticket_id,
        "fallback": fallback,
        "note": note,
    }


def submit_loa(conn, xc_id: int, loa_id: int | None = None) -> dict:
    """Auto-create LOA if loa_id is None, then transition → 'loa_submitted'."""
    xc = _get_xc(conn, xc_id)
    if not xc:
        raise ValueError(f"Cross-connect {xc_id} not found")

    old_status = xc.get("order_status", "ordered")
    facility = (xc.get("facility") or "generic").lower()

    if loa_id is None:
        from tools.ccc_canvas.loa_workflow import create_loa_request
        loa_data = {
            "facility": xc.get("facility", "other"),
            "xc_id": xc_id,
            "requester_name": "NOC",
            "requester_email": "",
            "requester_company": "",
        }
        loa_result = create_loa_request(conn, loa_data)
        loa_id = loa_result.get("loa_id")

    from tools.ccc_canvas.loa_workflow import generate_loa_for_facility
    loa_text = generate_loa_for_facility(conn, loa_id, facility)

    _set_order_status(conn, xc_id, "loa_submitted", carrier_ticket_id=xc.get("carrier_ticket_id", ""))
    _record_event(conn, xc_id, "loa_submitted", old_status, "loa_submitted",
                  note=f"LOA {loa_id} submitted", actor="system")
    conn.commit()

    return {"xc_id": xc_id, "loa_id": loa_id, "order_status": "loa_submitted",
            "document": loa_text}


def poll_order_status(conn, xc_id: int) -> dict:
    """Query carrier API for latest status; update if changed."""
    xc = _get_xc(conn, xc_id)
    if not xc:
        raise ValueError(f"Cross-connect {xc_id} not found")

    old_status = xc.get("order_status", "not_ordered")
    carrier_ticket_id = xc.get("carrier_ticket_id", "")
    facility = (xc.get("facility") or "").lower()

    if not carrier_ticket_id:
        return {"xc_id": xc_id, "changed": False, "order_status": old_status,
                "note": "no carrier_ticket_id — cannot poll"}

    carrier_status = ""
    new_status = old_status

    if facility == "equinix":
        try:
            from tools.databridge.connectors.equinix_ecx_connector import EquinixECXConnector
            client = EquinixECXConnector()
            resp = client.get_connection_status(carrier_ticket_id)
            carrier_status = resp.get("state", "")
            mapped = _EQUINIX_STATE_MAP.get(carrier_status.upper(), "")
            if mapped and mapped != old_status:
                new_status = mapped
        except Exception as e:
            return {"xc_id": xc_id, "changed": False, "order_status": old_status,
                    "note": f"Equinix poll error: {e}"}

    elif facility == "megaport":
        try:
            from tools.databridge.connectors.megaport_connector import MegaportConnector
            client = MegaportConnector()
            from tools.databridge.connector import ConnectorRequest
            req = ConnectorRequest(table_name="services", filters={})
            resp = client.read(req)
            for row in resp.rows:
                if row.get("productUid") == carrier_ticket_id:
                    carrier_status = row.get("provisioningStatus", "")
                    mapped = _MEGAPORT_STATE_MAP.get(carrier_status.upper(), "")
                    if mapped and mapped != old_status:
                        new_status = mapped
                    break
        except Exception as e:
            return {"xc_id": xc_id, "changed": False, "order_status": old_status,
                    "note": f"Megaport poll error: {e}"}

    changed = new_status != old_status
    if changed:
        _set_order_status(conn, xc_id, new_status, carrier_ticket_id=carrier_ticket_id)
        _record_event(conn, xc_id, "status_polled", old_status, new_status,
                      note=f"carrier_status={carrier_status}", actor="xc_order_poller",
                      carrier_ref=carrier_ticket_id)
        conn.commit()

    return {
        "xc_id": xc_id,
        "changed": changed,
        "old_status": old_status,
        "order_status": new_status,
        "carrier_status": carrier_status,
    }


def cancel_order(conn, xc_id: int, reason: str, actor: str = "system") -> dict:
    """Transition order_status → 'cancelled'. Calls carrier cancel API if possible."""
    xc = _get_xc(conn, xc_id)
    if not xc:
        raise ValueError(f"Cross-connect {xc_id} not found")

    old_status = xc.get("order_status", "not_ordered")
    carrier_ticket_id = xc.get("carrier_ticket_id", "")
    facility = (xc.get("facility") or "").lower()
    note = reason

    if carrier_ticket_id and facility == "equinix":
        try:
            from tools.databridge.connectors.equinix_ecx_connector import EquinixECXConnector
            client = EquinixECXConnector()
            client.cancel_connection(carrier_ticket_id)
            note += " | Equinix connection cancelled via API"
        except Exception as e:
            note += f" | Equinix cancel API error: {e}"
    elif carrier_ticket_id and facility == "megaport":
        note += " | Megaport: no cancel API — order recorded locally only"

    _set_order_status(conn, xc_id, "cancelled", carrier_ticket_id=carrier_ticket_id)
    _record_event(conn, xc_id, "order_cancelled", old_status, "cancelled",
                  note=note, actor=actor, carrier_ref=carrier_ticket_id)
    conn.commit()

    return {"xc_id": xc_id, "order_status": "cancelled", "note": note}


def list_in_flight_orders(conn) -> list[dict]:
    """Return all cross-connects with an in-flight order_status."""
    placeholders_pg = ", ".join(["%s"] * len(XC_IN_FLIGHT_STATUSES))
    placeholders_sq = ", ".join(["?"] * len(XC_IN_FLIGHT_STATUSES))
    return _safe_fetch(
        conn,
        f"SELECT id, xc_number, facility, port_a, port_z, speed_gbps, order_status, order_id, carrier_ticket_id, estimated_delivery, ordered_by, partner_id, monthly_cost_usd FROM ccc_cross_connects WHERE order_status IN ({placeholders_pg}) ORDER BY order_status, facility",  # nosec B608
        f"SELECT id, xc_number, facility, port_a, port_z, speed_gbps, order_status, order_id, carrier_ticket_id, estimated_delivery, ordered_by, partner_id, monthly_cost_usd FROM ccc_cross_connects WHERE order_status IN ({placeholders_sq}) ORDER BY order_status, facility",  # nosec B608
        tuple(XC_IN_FLIGHT_STATUSES),
    )
