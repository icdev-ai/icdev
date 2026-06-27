# CUI // SP-CTI
"""Federal Network Peering Agreement — Peering Request Initiation (Step 1).

One network operator (the initiating party) submits a formal peering request to
another operator (the responding party) and documents the proposed interconnection.

Workflow: digitize-wfl-7f07
Step 1 of N: Initiation → [Negotiation] → [Agreement Signed] → [Technical Design]
             → [Implemented] → [Operational]

DB: stored in federal_peering_requests table (PMC canvas DB).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


# ── Constants ──────────────────────────────────────────────────────────────────

WORKFLOW_ID = "digitize-wfl-7f07"
STEP_NUMBER = 1
STEP_NAME = "peering_request_initiation"

PEERING_TYPES = (
    "public_bilateral",   # Settlement-free public peering (IX)
    "private_bilateral",  # Private Network Interconnect (PNI)
    "transit",            # Transit purchase from responding party
    "multilateral",       # Route-server-based IX peering
)

REQUEST_STATUSES = (
    "initiated",       # Request created; not yet sent
    "sent",            # Formally transmitted to responding party
    "acknowledged",    # Responding party has acknowledged receipt
    "negotiating",     # Active negotiation underway
    "accepted",        # Responding party accepted the request
    "rejected",        # Responding party rejected the request
    "withdrawn",       # Initiating party withdrew the request
)

CLASSIFICATION = "CUI // SP-CTI"

# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS federal_peering_requests (
    id                              TEXT PRIMARY KEY,
    workflow_id                     TEXT NOT NULL DEFAULT 'digitize-wfl-7f07',
    step_number                     INTEGER NOT NULL DEFAULT 1,

    -- Initiating operator
    initiating_org                  TEXT NOT NULL,
    initiating_asn                  INTEGER NOT NULL,
    initiating_cage_code            TEXT DEFAULT '',
    initiating_agency               TEXT DEFAULT '',
    initiating_poc_name             TEXT DEFAULT '',
    initiating_poc_email            TEXT DEFAULT '',
    initiating_poc_phone            TEXT DEFAULT '',

    -- Responding operator
    responding_org                  TEXT NOT NULL,
    responding_asn                  INTEGER,
    responding_poc_name             TEXT DEFAULT '',
    responding_poc_email            TEXT DEFAULT '',

    -- Interconnection proposal
    peering_type                    TEXT NOT NULL DEFAULT 'public_bilateral'
                                        CHECK(peering_type IN (
                                            'public_bilateral','private_bilateral',
                                            'transit','multilateral')),
    proposed_interconnection_points JSONB DEFAULT '[]',
    proposed_speed                  TEXT DEFAULT '',
    proposed_routing_protocol       TEXT DEFAULT 'BGP',
    mission_justification           TEXT DEFAULT '',

    -- Workflow state
    status                          TEXT NOT NULL DEFAULT 'initiated'
                                        CHECK(status IN (
                                            'initiated','sent','acknowledged',
                                            'negotiating','accepted','rejected','withdrawn')),
    pmc_request_id                  TEXT DEFAULT '',
    interconnection_doc             TEXT DEFAULT '',

    classification                  TEXT DEFAULT 'CUI // SP-CTI',
    created_at                      TIMESTAMPTZ DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fed_pr_workflow ON federal_peering_requests(workflow_id);
CREATE INDEX IF NOT EXISTS idx_fed_pr_status   ON federal_peering_requests(status);
CREATE INDEX IF NOT EXISTS idx_fed_pr_init_asn ON federal_peering_requests(initiating_asn);
CREATE INDEX IF NOT EXISTS idx_fed_pr_resp_asn ON federal_peering_requests(responding_asn);
"""

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS federal_peering_requests (
    id                              TEXT PRIMARY KEY,
    workflow_id                     TEXT NOT NULL DEFAULT 'digitize-wfl-7f07',
    step_number                     INTEGER NOT NULL DEFAULT 1,

    initiating_org                  TEXT NOT NULL,
    initiating_asn                  INTEGER NOT NULL,
    initiating_cage_code            TEXT DEFAULT '',
    initiating_agency               TEXT DEFAULT '',
    initiating_poc_name             TEXT DEFAULT '',
    initiating_poc_email            TEXT DEFAULT '',
    initiating_poc_phone            TEXT DEFAULT '',

    responding_org                  TEXT NOT NULL,
    responding_asn                  INTEGER,
    responding_poc_name             TEXT DEFAULT '',
    responding_poc_email            TEXT DEFAULT '',

    peering_type                    TEXT NOT NULL DEFAULT 'public_bilateral',
    proposed_interconnection_points TEXT DEFAULT '[]',
    proposed_speed                  TEXT DEFAULT '',
    proposed_routing_protocol       TEXT DEFAULT 'BGP',
    mission_justification           TEXT DEFAULT '',

    status                          TEXT NOT NULL DEFAULT 'initiated',
    pmc_request_id                  TEXT DEFAULT '',
    interconnection_doc             TEXT DEFAULT '',

    classification                  TEXT DEFAULT 'CUI // SP-CTI',
    created_at                      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at                      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fed_pr_workflow ON federal_peering_requests(workflow_id);
CREATE INDEX IF NOT EXISTS idx_fed_pr_status   ON federal_peering_requests(status);
"""


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_schema(conn) -> None:
    """Create federal_peering_requests table if it doesn't exist."""
    try:
        for stmt in (s.strip() for s in SCHEMA_PG.split(";") if s.strip()):
            conn.execute(stmt)
        conn.commit()
    except Exception:
        try:
            conn.executescript(SCHEMA_SQLITE)
        except Exception:
            pass


def _exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> None:
    try:
        conn.execute(sql_pg, params)
        conn.commit()
    except Exception:
        conn.execute(sql_sq, params)
        conn.commit()


def _fetchall(conn, sql: str, params: tuple = ()) -> list[dict]:
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql.replace("?", "%s"), params)
        except Exception:
            cur.execute(sql.replace("%s", "?"), params)
        rows = cur.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


# ── Core workflow function ─────────────────────────────────────────────────────

def initiate_peering_request(
    conn,
    initiating_org: str,
    initiating_asn: int,
    responding_org: str,
    peering_type: str = "public_bilateral",
    *,
    initiating_cage_code: str = "",
    initiating_agency: str = "",
    initiating_poc_name: str = "",
    initiating_poc_email: str = "",
    initiating_poc_phone: str = "",
    responding_asn: int | None = None,
    responding_poc_name: str = "",
    responding_poc_email: str = "",
    proposed_interconnection_points: list[dict] | None = None,
    proposed_speed: str = "",
    proposed_routing_protocol: str = "BGP",
    mission_justification: str = "",
    workflow_id: str = WORKFLOW_ID,
) -> dict[str, Any]:
    """Initiate a Federal Network Peering Agreement request (Step 1).

    Creates a federal_peering_requests record and generates the formal
    interconnection proposal document.

    Returns the new request dict including the generated document.

    Raises ValueError for invalid peering_type or missing required fields.
    """
    if not initiating_org.strip():
        raise ValueError("initiating_org is required")
    if not responding_org.strip():
        raise ValueError("responding_org is required")
    if peering_type not in PEERING_TYPES:
        raise ValueError(
            f"peering_type must be one of {PEERING_TYPES}, got {peering_type!r}"
        )

    _init_schema(conn)

    request_id = str(uuid.uuid4())
    now = _now()
    points_json = json.dumps(proposed_interconnection_points or [])

    # Generate interconnection proposal document before inserting
    proposal = _generate_proposal(
        request_id=request_id,
        initiating_org=initiating_org,
        initiating_asn=initiating_asn,
        initiating_cage_code=initiating_cage_code,
        initiating_agency=initiating_agency,
        initiating_poc_name=initiating_poc_name,
        initiating_poc_email=initiating_poc_email,
        initiating_poc_phone=initiating_poc_phone,
        responding_org=responding_org,
        responding_asn=responding_asn,
        responding_poc_name=responding_poc_name,
        responding_poc_email=responding_poc_email,
        peering_type=peering_type,
        proposed_interconnection_points=proposed_interconnection_points or [],
        proposed_speed=proposed_speed,
        proposed_routing_protocol=proposed_routing_protocol,
        mission_justification=mission_justification,
        created_at=now,
    )

    _exec(
        conn,
        """INSERT INTO federal_peering_requests (
               id, workflow_id, step_number,
               initiating_org, initiating_asn, initiating_cage_code,
               initiating_agency, initiating_poc_name, initiating_poc_email,
               initiating_poc_phone,
               responding_org, responding_asn, responding_poc_name,
               responding_poc_email,
               peering_type, proposed_interconnection_points,
               proposed_speed, proposed_routing_protocol,
               mission_justification, status, interconnection_doc,
               classification, created_at, updated_at
           ) VALUES (
               %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
               %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
           )""",
        """INSERT INTO federal_peering_requests (
               id, workflow_id, step_number,
               initiating_org, initiating_asn, initiating_cage_code,
               initiating_agency, initiating_poc_name, initiating_poc_email,
               initiating_poc_phone,
               responding_org, responding_asn, responding_poc_name,
               responding_poc_email,
               peering_type, proposed_interconnection_points,
               proposed_speed, proposed_routing_protocol,
               mission_justification, status, interconnection_doc,
               classification, created_at, updated_at
           ) VALUES (
               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
           )""",
        (
            request_id, workflow_id, STEP_NUMBER,
            initiating_org, initiating_asn, initiating_cage_code,
            initiating_agency, initiating_poc_name, initiating_poc_email,
            initiating_poc_phone,
            responding_org, responding_asn, responding_poc_name,
            responding_poc_email,
            peering_type, points_json,
            proposed_speed, proposed_routing_protocol,
            mission_justification, "initiated", proposal,
            CLASSIFICATION, now, now,
        ),
    )

    # Optionally sync a corresponding PMC peering_request record
    pmc_request_id = _sync_to_pmc(
        conn,
        request_id=request_id,
        initiating_asn=initiating_asn,
        responding_org=responding_org,
        responding_asn=responding_asn,
        peering_type=peering_type,
        proposed_speed=proposed_speed,
        now=now,
    )

    if pmc_request_id:
        _exec(
            conn,
            "UPDATE federal_peering_requests SET pmc_request_id=%s, updated_at=%s WHERE id=%s",
            "UPDATE federal_peering_requests SET pmc_request_id=?, updated_at=? WHERE id=?",
            (pmc_request_id, now, request_id),
        )

    return {
        "id": request_id,
        "workflow_id": workflow_id,
        "step_number": STEP_NUMBER,
        "step_name": STEP_NAME,
        "status": "initiated",
        "initiating_org": initiating_org,
        "initiating_asn": initiating_asn,
        "responding_org": responding_org,
        "responding_asn": responding_asn,
        "peering_type": peering_type,
        "pmc_request_id": pmc_request_id or "",
        "interconnection_doc": proposal,
        "created_at": now,
    }


# ── Read helpers ───────────────────────────────────────────────────────────────

def get_federal_peering_request(conn, request_id: str) -> dict | None:
    """Return a single federal peering request by ID, or None."""
    _init_schema(conn)
    rows = _fetchall(
        conn,
        "SELECT * FROM federal_peering_requests WHERE id = ?",
        (request_id,),
    )
    if not rows:
        return None
    row = rows[0]
    if isinstance(row.get("proposed_interconnection_points"), str):
        try:
            row["proposed_interconnection_points"] = json.loads(
                row["proposed_interconnection_points"]
            )
        except Exception:
            row["proposed_interconnection_points"] = []
    return row


def list_federal_peering_requests(
    conn,
    workflow_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Return federal peering requests, optionally filtered by workflow_id or status."""
    _init_schema(conn)
    conditions = []
    params: list[Any] = []
    if workflow_id:
        conditions.append("workflow_id = ?")
        params.append(workflow_id)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = _fetchall(
        conn,
        f"SELECT * FROM federal_peering_requests {where} ORDER BY created_at DESC",
        tuple(params),
    )
    for row in rows:
        if isinstance(row.get("proposed_interconnection_points"), str):
            try:
                row["proposed_interconnection_points"] = json.loads(
                    row["proposed_interconnection_points"]
                )
            except Exception:
                row["proposed_interconnection_points"] = []
    return rows


def update_request_status(conn, request_id: str, new_status: str) -> dict:
    """Advance the federal peering request status."""
    if new_status not in REQUEST_STATUSES:
        raise ValueError(f"Invalid status: {new_status!r}")
    _init_schema(conn)
    now = _now()
    _exec(
        conn,
        "UPDATE federal_peering_requests SET status=%s, updated_at=%s WHERE id=%s",
        "UPDATE federal_peering_requests SET status=?, updated_at=? WHERE id=?",
        (new_status, now, request_id),
    )
    return {"request_id": request_id, "status": new_status, "updated_at": now}


# ── Document generation ────────────────────────────────────────────────────────

def _generate_proposal(
    *,
    request_id: str,
    initiating_org: str,
    initiating_asn: int,
    initiating_cage_code: str,
    initiating_agency: str,
    initiating_poc_name: str,
    initiating_poc_email: str,
    initiating_poc_phone: str,
    responding_org: str,
    responding_asn: int | None,
    responding_poc_name: str,
    responding_poc_email: str,
    peering_type: str,
    proposed_interconnection_points: list[dict],
    proposed_speed: str,
    proposed_routing_protocol: str,
    mission_justification: str,
    created_at: str,
) -> str:
    """Generate a formal Federal Network Interconnection Proposal document (CUI)."""
    peering_label = {
        "public_bilateral": "Public Bilateral (Internet Exchange)",
        "private_bilateral": "Private Bilateral (Private Network Interconnect / PNI)",
        "transit": "Transit (IP Transit Purchase)",
        "multilateral": "Multilateral (Route-Server-based IX)",
    }.get(peering_type, peering_type.replace("_", " ").title())

    asn_b = f"AS{responding_asn}" if responding_asn else "TBD"
    cage = initiating_cage_code or "N/A"
    agency = initiating_agency or "N/A"
    speed = proposed_speed or "To Be Negotiated"
    protocol = proposed_routing_protocol or "BGP"

    if proposed_interconnection_points:
        points_text = "\n".join(
            f"    {i+1}. {p.get('name', 'Unnamed')} — {p.get('location', '')} "
            f"({'IX' if p.get('type') == 'ix' else 'PNI' if p.get('type') == 'pni' else p.get('type', '')})"
            for i, p in enumerate(proposed_interconnection_points)
        )
    else:
        points_text = "    (Specific interconnection points to be negotiated)"

    poc_b = responding_poc_name or "TBD"
    email_b = responding_poc_email or "TBD"

    justification = mission_justification.strip() or "(No justification provided)"

    doc = f"""// CUI // SP-CTI //
================================================================================
FEDERAL NETWORK PEERING AGREEMENT
INTERCONNECTION PROPOSAL — STEP 1: PEERING REQUEST INITIATION
================================================================================
Document ID:    {request_id}
Workflow:       {WORKFLOW_ID}
Created:        {created_at}
Classification: CUI // SP-CTI

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 1 — INITIATING PARTY (PARTY A)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Organization:          {initiating_org}
  Autonomous System:     AS{initiating_asn}
  CAGE Code:             {cage}
  Federal Agency:        {agency}

  Technical Point of Contact:
    Name:                {initiating_poc_name or "TBD"}
    Email:               {initiating_poc_email or "TBD"}
    Phone:               {initiating_poc_phone or "TBD"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 2 — RESPONDING PARTY (PARTY B)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Organization:          {responding_org}
  Autonomous System:     {asn_b}

  Technical Point of Contact:
    Name:                {poc_b}
    Email:               {email_b}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 3 — PROPOSED INTERCONNECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Peering Type:          {peering_label}
  Routing Protocol:      {protocol}
  Proposed Port Speed:   {speed}

  Proposed Interconnection Points:
{points_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 4 — MISSION JUSTIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{justification}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTION 5 — STANDARD REQUIREMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Both parties agree to adhere to the following requirements upon acceptance:

  a) RPKI Route Origin Validation — All announced prefixes must have valid ROAs.
     RPKI-invalid routes SHALL be filtered at the interconnection boundary.

  b) IRR Registration — Route objects for all announced prefixes must be
     registered in an authoritative IRR (ARIN, RIPE, APNIC, LACNIC, AFRINIC).

  c) BGP Security — MD5 or TCP-AO session authentication shall be configured
     where technically feasible per NIST SP 800-189.

  d) Maximum Prefix Limits — Mutually agreed per address family; sessions are
     automatically shut down upon violation per BCP 194 (RFC 7454).

  e) NOC Availability — Both parties maintain a 24×7 Network Operations Center.
     Planned maintenance notification: 72 hours minimum advance notice.
     Unplanned outage escalation: within 30 minutes of detection.

  f) Compliance — This interconnection is subject to FISMA, NIST SP 800-53
     (AC-4 Information Flow Enforcement, CA-3 System Interconnection Agreements),
     and any applicable Interconnection Security Agreement (ISA) requirements.

  g) Termination — Either party may terminate with 30 days written notice.
     Immediate termination applies for material breach or unresolved security
     incidents within 48 hours of notification.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NEXT STEP (STEP 2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Upon receipt and acceptance of this proposal by Party B, the workflow advances
to Step 2: Negotiation — where both parties agree on specific technical
parameters, SLAs, and legal terms before signing the Federal Network Peering
Agreement.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SIGNATURES — INITIATING PARTY (PARTY A)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Authorized Representative:
    Name:  ________________________________
    Title: ________________________________
    Date:  ________________________________

================================================================================
// CUI // SP-CTI // FOR AUTHORIZED USE ONLY //
================================================================================
"""
    return doc


# ── PMC sync ───────────────────────────────────────────────────────────────────

def _sync_to_pmc(
    conn,
    *,
    request_id: str,
    initiating_asn: int,
    responding_org: str,
    responding_asn: int | None,
    peering_type: str,
    proposed_speed: str,
    now: str,
) -> str | None:
    """Create a corresponding peering_requests record in the PMC canvas.

    Returns the PMC request ID, or None if PMC tables are not available.
    """
    # Map federal peering type → PMC contact_method (best approximation)
    pmc_type_map = {
        "public_bilateral": "peeringdb",
        "private_bilateral": "noc",
        "transit": "noc",
        "multilateral": "peeringdb",
    }
    contact_method = pmc_type_map.get(peering_type, "manual")

    pmc_req_id = str(uuid.uuid4())
    notes = json.dumps({
        "source": "federal_peering_workflow",
        "workflow_id": WORKFLOW_ID,
        "federal_request_id": request_id,
        "responding_org": responding_org,
        "peering_type": peering_type,
    })

    try:
        # Ensure peer record exists (upsert by ASN if responding_asn known)
        peer_id: str | None = None
        if responding_asn:
            rows = _fetchall(conn, "SELECT id FROM peering_peers WHERE asn = ?", (responding_asn,))
            if rows:
                peer_id = rows[0]["id"]
            else:
                peer_id = str(uuid.uuid4())
                try:
                    conn.execute(
                        """INSERT INTO peering_peers
                               (id, asn, org_name, peer_type, policy, status,
                                classification, created_at, updated_at)
                           VALUES (%s, %s, %s, 'public', 'selective', 'evaluation',
                                   'CUI', %s, %s)""",
                        (peer_id, responding_asn, responding_org, now, now),
                    )
                    conn.commit()
                except Exception:
                    try:
                        conn.execute(
                            """INSERT OR IGNORE INTO peering_peers
                                   (id, asn, org_name, peer_type, policy, status,
                                    classification, created_at, updated_at)
                               VALUES (%s, %s, %s, 'public', 'selective', 'evaluation',
                                       'CUI', %s, %s)""",
                            (peer_id, responding_asn, responding_org, now, now),
                        )
                        conn.commit()
                    except Exception:
                        peer_id = None

        try:
            conn.execute(
                """INSERT INTO peering_requests
                       (id, peer_id, request_type, status, contact_method,
                        proposed_speed, notes, classification, created_at, updated_at)
                   VALUES (%s, %s, 'new', 'pending', %s, %s, %s, 'CUI', %s, %s)""",
                (pmc_req_id, peer_id, contact_method,
                 proposed_speed, notes, now, now),
            )
            conn.commit()
        except Exception:
            conn.execute(
                """INSERT INTO peering_requests
                       (id, peer_id, request_type, status, contact_method,
                        proposed_speed, notes, classification, created_at, updated_at)
                   VALUES (%s, %s, 'new', 'pending', %s, %s, %s, 'CUI', %s, %s)""",
                (pmc_req_id, peer_id, contact_method,
                 proposed_speed, notes, now, now),
            )
            conn.commit()

        return pmc_req_id
    except Exception:
        return None
