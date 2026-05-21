# CUI // SP-CTI
"""Peering Agreement Lifecycle — workflow, document generation, and PMC sync."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone


_LIFECYCLE_STATES = [
    "evaluation",
    "negotiation",
    "agreement_signed",
    "technical_design",
    "implemented",
    "operational",
    "decommissioned",
]


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


def _get_agreement(conn, agreement_id: str) -> dict | None:
    rows = _safe_fetch(
        conn,
        "SELECT * FROM nc_peering_agreements WHERE id = %s",
        "SELECT * FROM nc_peering_agreements WHERE id = ?",
        (agreement_id,),
    )
    return rows[0] if rows else None


def generate_agreement_document(conn, agreement_id: str) -> str:
    """Return a structured text peering agreement document.

    Queries nc_peering_agreements (LEFT JOIN nc_partners if partner_id set).
    Pure f-string template — no LLM calls. Classification: CUI.
    """
    a = _get_agreement(conn, agreement_id)
    if not a:
        raise ValueError(f"Agreement {agreement_id} not found")

    partner_name = a.get("peer_name", "")
    partner_asn = a.get("peer_asn", "N/A")
    our_asn = a.get("our_asn", "N/A")
    peering_type = a.get("peering_type", "settlement_free")
    contract_start = a.get("contract_start", "")
    contract_end = a.get("contract_end", "")
    port_speed = a.get("port_speed", "")
    traffic_commit = a.get("traffic_commit", "N/A")
    ratio_limit = a.get("ratio_limit", "N/A")
    sla_latency = a.get("sla_latency_ms", "N/A")
    sla_loss = a.get("sla_packet_loss", "N/A")
    sla_uptime = a.get("sla_uptime_pct", 99.9)
    noc_email = a.get("noc_email", "")
    noc_phone = a.get("noc_phone", "")
    legal_entity = a.get("legal_entity", partner_name)
    notes = a.get("notes", "")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    doc = f"""// CUI // SP-CTI //
================================================================================
INTERNET PEERING AGREEMENT
================================================================================
Generated: {generated_at}
Agreement ID: {agreement_id}
Peering Type: {peering_type.replace("_", " ").title()}

PARTIES
-------
Party A (Our Organization):
  ASN: AS{our_asn}

Party B (Peer):
  Legal Entity: {legal_entity}
  Organization: {partner_name}
  ASN: AS{partner_asn}
  NOC Email: {noc_email}
  NOC Phone: {noc_phone}

TERM
----
Effective Date: {contract_start or "TBD"}
Expiration Date: {contract_end or "Evergreen — terminable with 30-day written notice"}

1. DEFINITIONS
--------------
"Peering" means the direct exchange of Internet traffic between Party A and
Party B at mutually agreed interconnection points.

"Traffic" means all IP packets originated by or destined to the autonomous
systems of each party as identified by their respective ASNs above.

2. TRAFFIC EXCHANGE
-------------------
Exchange Type: {peering_type.replace("_", " ").title()}
Committed Rate: {traffic_commit}
Traffic Ratio Limit: {ratio_limit}
Port Speed: {port_speed or "As negotiated per location"}

3. ROUTING POLICY
-----------------
a) Each party shall announce only routes originated by or legitimately held
   within its own autonomous system or customer cone.
b) Neither party shall announce third-party routes (transit routes) to the
   other without prior written consent.
c) Route objects must be registered in an appropriate Internet Routing Registry
   (IRR) and RPKI ROAs must be valid for all announced prefixes.
d) Maximum prefix limits shall be mutually agreed per session.

4. SERVICE LEVEL AGREEMENT
--------------------------
Availability Target: {sla_uptime}%
Maximum Latency: {sla_latency} ms (round-trip, within interconnection points)
Maximum Packet Loss: {sla_loss}%

5. NOC & NOTIFICATION REQUIREMENTS
-----------------------------------
a) Each party shall maintain a 24×7 Network Operations Center reachable at the
   contact information provided above.
b) Planned maintenance affecting the peering session shall be communicated at
   least 72 hours in advance.
c) Unplanned outages shall be reported within 30 minutes of detection.
d) Escalation response time for P1 incidents: 1 hour.

6. SECURITY
-----------
a) Both parties agree to implement Resource Public Key Infrastructure (RPKI)
   route origin validation and to filter RPKI-invalid routes.
b) BGP session authentication (MD5 or TCP-AO) shall be implemented where
   technically feasible.
c) Both parties shall promptly notify the other of any suspected BGP hijack,
   route leak, or security incident affecting the interconnection.

7. COSTS
--------
This agreement is {("settlement-free (no monetary exchange)" if peering_type == "settlement_free" else f"a paid arrangement. Monthly charges as separately negotiated.")}.

8. TERM & TERMINATION
---------------------
Either party may terminate this agreement with 30 days written notice to the
other party's NOC and legal contact. Immediate termination is permissible in
the event of a material breach, including but not limited to: unauthorized
transit, persistent route leaks, or security incidents not remediated within
48 hours of notification.

9. LIMITATION OF LIABILITY
---------------------------
Neither party shall be liable to the other for indirect, incidental, or
consequential damages arising from this agreement.

10. GOVERNING LAW
-----------------
This agreement shall be governed by applicable law as agreed by the parties
in a separate Master Services Agreement, or in the absence thereof, by the
laws of the jurisdiction of Party A.

{("NOTES\n------\n" + notes + chr(10)) if notes else ""}
SIGNATURES
----------
Party A Authorized Signature:
  Name: ________________________________
  Title: _______________________________
  Date: ________________________________

Party B Authorized Signature:
  Name: ________________________________
  Title: _______________________________
  Date: ________________________________

================================================================================
// CUI // SP-CTI // FOR AUTHORIZED USE ONLY //
================================================================================
"""
    return doc


def approve_agreement(conn, agreement_id: str, approver: str, role: str) -> dict:
    """Advance agreement status one step in the lifecycle and record the approver.

    Raises ValueError if agreement not found or already at a terminal state.
    """
    a = _get_agreement(conn, agreement_id)
    if not a:
        raise ValueError(f"Agreement {agreement_id} not found")

    current = a.get("status", "evaluation")
    if current not in _LIFECYCLE_STATES:
        raise ValueError(f"Unknown status: {current}")

    current_idx = _LIFECYCLE_STATES.index(current)
    if current_idx >= len(_LIFECYCLE_STATES) - 1:
        raise ValueError(f"Agreement is already in terminal state: {current}")

    new_status = _LIFECYCLE_STATES[current_idx + 1]
    now = datetime.now(timezone.utc).isoformat()

    _try_exec(
        conn,
        "UPDATE nc_peering_agreements SET status=%s, approver_name=%s, approver_role=%s, approved_at=%s, updated_at=%s WHERE id=%s",
        "UPDATE nc_peering_agreements SET status=?, approver_name=?, approver_role=?, approved_at=?, updated_at=? WHERE id=?",
        (new_status, approver, role, now, now, agreement_id),
    )
    conn.commit()
    return {
        "agreement_id": agreement_id,
        "old_status": current,
        "new_status": new_status,
        "approver": approver,
        "role": role,
        "approved_at": now,
    }


def create_amendment(conn, agreement_id: str, changes: dict, reason: str,
                     amended_by: str, effective_date: str = "") -> dict:
    """Insert a new nc_agreement_amendments record.

    Amendment number is auto-incremented per agreement.
    """
    rows = _safe_fetch(
        conn,
        "SELECT COALESCE(MAX(amendment_number), 0) AS max_num FROM nc_agreement_amendments WHERE agreement_id = %s",
        "SELECT COALESCE(MAX(amendment_number), 0) AS max_num FROM nc_agreement_amendments WHERE agreement_id = ?",
        (agreement_id,),
    )
    next_num = (rows[0].get("max_num", 0) if rows else 0) + 1
    amendment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    _try_exec(
        conn,
        "INSERT INTO nc_agreement_amendments (id, agreement_id, amendment_number, changes_json, amended_by, reason, effective_date, classification, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'CUI',%s)",
        "INSERT INTO nc_agreement_amendments (id, agreement_id, amendment_number, changes_json, amended_by, reason, effective_date, classification, created_at) VALUES (?,?,?,?,?,?,?,'CUI',?)",
        (amendment_id, agreement_id, next_num, json.dumps(changes), amended_by, reason, effective_date, now),
    )
    conn.commit()
    return {
        "amendment_id": amendment_id,
        "agreement_id": agreement_id,
        "amendment_number": next_num,
    }


def get_renewal_risk(conn, days_ahead: int = 90) -> list[dict]:
    """Return operational peering agreements with contract_end within days_ahead days."""
    rows = _safe_fetch(
        conn,
        f"SELECT id, peer_name, peer_asn, partner_id, contract_end, monthly_cost, noc_email FROM nc_peering_agreements WHERE status='operational' AND contract_end != '' AND contract_end <= date('now', '+{days_ahead} days') ORDER BY contract_end",
        f"SELECT id, peer_name, peer_asn, partner_id, contract_end, monthly_cost, noc_email FROM nc_peering_agreements WHERE status='operational' AND contract_end != '' AND contract_end <= date('now', '+{days_ahead} days') ORDER BY contract_end",
    )
    today = datetime.now(timezone.utc).date()
    result = []
    for r in rows:
        try:
            end = datetime.strptime(r["contract_end"][:10], "%Y-%m-%d").date()
            days_remaining = (end - today).days
        except Exception:
            days_remaining = None
        result.append({**r, "days_remaining": days_remaining})
    return result


def sync_to_pmc(conn_nc, agreement_id: str) -> dict:
    """When agreement reaches 'operational', upsert partner into PMC peering_peers.

    Opens a separate PMC connection. Returns gracefully if PMC DB unreachable.
    """
    a = _get_agreement(conn_nc, agreement_id)
    if not a:
        return {"synced": False, "reason": "agreement not found"}

    asn_str = a.get("peer_asn", "")
    if not asn_str:
        return {"synced": False, "reason": "no peer_asn on agreement"}

    try:
        asn = int(asn_str)
    except (ValueError, TypeError):
        return {"synced": False, "reason": f"invalid peer_asn: {asn_str}"}

    try:
        from tools.pmc_canvas.db.init_db import get_connection as pmc_conn
        pmc = pmc_conn()
    except Exception as e:
        return {"synced": False, "reason": f"PMC DB unreachable: {e}"}

    try:
        now = datetime.now(timezone.utc).isoformat()
        peer_id = str(uuid.uuid4())
        try:
            pmc.execute(
                "INSERT INTO peering_peers (id, asn, org_name, peer_type, policy, status, noc_email, classification, created_at, updated_at) VALUES (%s,%s,%s,'public','selective','active',%s,'CUI',%s,%s) ON CONFLICT(asn) DO UPDATE SET status='active', org_name=%s, noc_email=%s, updated_at=%s",
                (peer_id, asn, a.get("peer_name", ""), a.get("noc_email", ""), now, now,
                 a.get("peer_name", ""), a.get("noc_email", ""), now),
            )
        except Exception:
            pmc.execute(
                "INSERT OR IGNORE INTO peering_peers (id, asn, org_name, peer_type, policy, status, noc_email, classification, created_at, updated_at) VALUES (?,?,?,'public','selective','active',?,'CUI',?,?)",
                (peer_id, asn, a.get("peer_name", ""), a.get("noc_email", ""), now, now),
            )
        pmc.commit()
        return {"synced": True, "asn": asn, "peer_id": peer_id}
    except Exception as e:
        return {"synced": False, "reason": str(e)}
    finally:
        try:
            pmc.close()
        except Exception:
            pass
