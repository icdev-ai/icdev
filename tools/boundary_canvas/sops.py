# CUI // SP-CTI
"""Boundary Design Canvas — Standard Operating Procedures (SOPs).

Provides CRUD operations and approval workflow for SOPs linked to the
Boundary Design Canvas. Examples: ISA renewal procedure, boundary change
approval, cross-domain transfer, interconnection decommission.
"""

import json
import uuid
from datetime import datetime, timezone


def _get_conn():
    from tools.boundary_canvas.db.init_db import get_connection
    return get_connection()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_json_field(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    if value is None:
        return default
    return value


def _sop_to_dict(row):
    if not row:
        return None
    d = dict(row)
    d["steps"] = _parse_json_field(d.get("steps"), [])
    d["nist_controls"] = _parse_json_field(d.get("nist_controls"), [])
    return d


# ── Read ───────────────────────────────────────────────────────────────────────

def get_all_sops(sop_type=None, approval_status=None):
    """Return all SOPs, optionally filtered by type and/or approval_status."""
    if sop_type and approval_status:
        sql = "SELECT * FROM bdc_sops WHERE sop_type = %s AND approval_status = %s ORDER BY updated_at DESC"
        params = [sop_type, approval_status]
    elif sop_type:
        sql = "SELECT * FROM bdc_sops WHERE sop_type = %s ORDER BY updated_at DESC"
        params = [sop_type]
    elif approval_status:
        sql = "SELECT * FROM bdc_sops WHERE approval_status = %s ORDER BY updated_at DESC"
        params = [approval_status]
    else:
        sql = "SELECT * FROM bdc_sops ORDER BY updated_at DESC"
        params = []
    with _get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_sop_to_dict(r) for r in rows]


def get_sop_by_id(sop_id):
    """Return a single SOP dict or None."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM bdc_sops WHERE id=%s", (sop_id,)).fetchone()
    return _sop_to_dict(row)


# ── Write ──────────────────────────────────────────────────────────────────────

def create_sop(data):
    """Create a new SOP. Returns the new SOP dict."""
    sop_id = str(uuid.uuid4())
    now = _now()
    steps = json.dumps(data.get("steps", []))
    nist_controls = json.dumps(data.get("nist_controls", []))
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO bdc_sops
               (id, title, sop_type, description, purpose, scope,
                steps, nist_controls, owner, reviewer,
                approval_status, version, next_review_date,
                classification, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                sop_id,
                data.get("title", "Untitled SOP"),
                data.get("sop_type", "custom"),
                data.get("description", ""),
                data.get("purpose", ""),
                data.get("scope", ""),
                steps,
                nist_controls,
                data.get("owner", ""),
                data.get("reviewer", ""),
                "draft",
                data.get("version", "1.0"),
                data.get("next_review_date", ""),
                data.get("classification", "CUI // SP-CTI"),
                now,
                now,
            ),
        )
        conn.commit()
    return get_sop_by_id(sop_id)


def update_sop(sop_id, data):
    """Update an existing SOP. Returns updated dict or None."""
    existing = get_sop_by_id(sop_id)
    if not existing:
        return None
    now = _now()
    steps = json.dumps(data.get("steps", existing["steps"]))
    nist_controls = json.dumps(data.get("nist_controls", existing["nist_controls"]))
    with _get_conn() as conn:
        conn.execute(
            """UPDATE bdc_sops SET
               title=%s, sop_type=%s, description=%s, purpose=%s, scope=%s,
               steps=%s, nist_controls=%s, owner=%s, reviewer=%s,
               version=%s, next_review_date=%s, classification=%s, updated_at=%s
               WHERE id=%s""",
            (
                data.get("title", existing["title"]),
                data.get("sop_type", existing["sop_type"]),
                data.get("description", existing["description"]),
                data.get("purpose", existing["purpose"]),
                data.get("scope", existing["scope"]),
                steps,
                nist_controls,
                data.get("owner", existing["owner"]),
                data.get("reviewer", existing["reviewer"]),
                data.get("version", existing["version"]),
                data.get("next_review_date", existing["next_review_date"]),
                data.get("classification", existing["classification"]),
                now,
                sop_id,
            ),
        )
        conn.commit()
    return get_sop_by_id(sop_id)


def delete_sop(sop_id):
    """Delete a SOP. Returns True if deleted."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM bdc_sops WHERE id=%s", (sop_id,))
        conn.commit()
    return cur.rowcount > 0


# ── Approval Workflow ──────────────────────────────────────────────────────────

def submit_for_review(sop_id):
    """Move SOP from draft → pending_review. Returns updated dict or None."""
    existing = get_sop_by_id(sop_id)
    if not existing:
        return None, "SOP not found"
    if existing["approval_status"] not in ("draft", "rejected"):
        return None, f"Cannot submit from status '{existing['approval_status']}'"
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE bdc_sops SET approval_status='pending_review', updated_at=%s WHERE id=%s",
            (now, sop_id),
        )
        conn.commit()
    return get_sop_by_id(sop_id), None


def approve_sop(sop_id, approved_by=""):
    """Approve a pending SOP. Returns updated dict or None."""
    existing = get_sop_by_id(sop_id)
    if not existing:
        return None, "SOP not found"
    if existing["approval_status"] != "pending_review":
        return None, f"Cannot approve from status '{existing['approval_status']}'"
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            """UPDATE bdc_sops SET
               approval_status='approved', approved_by=%s, approved_at=%s,
               rejected_reason=NULL, updated_at=%s
               WHERE id=%s""",
            (approved_by, now, now, sop_id),
        )
        conn.commit()
    return get_sop_by_id(sop_id), None


def reject_sop(sop_id, reason="", rejected_by=""):
    """Reject a pending SOP. Returns updated dict or None."""
    existing = get_sop_by_id(sop_id)
    if not existing:
        return None, "SOP not found"
    if existing["approval_status"] != "pending_review":
        return None, f"Cannot reject from status '{existing['approval_status']}'"
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            """UPDATE bdc_sops SET
               approval_status='rejected', rejected_reason=%s,
               approved_by=%s, updated_at=%s
               WHERE id=%s""",
            (reason, rejected_by, now, sop_id),
        )
        conn.commit()
    return get_sop_by_id(sop_id), None


# ── Seed data ──────────────────────────────────────────────────────────────────

SEED_SOPS = [
    {
        "title": "ISA Renewal Procedure",
        "sop_type": "isa_renewal",
        "description": "Step-by-step procedure for renewing an Interconnection Security Agreement (ISA) before expiry, ensuring continuous authorized data flow across boundary connections.",
        "purpose": "Prevent unauthorized interconnection due to expired ISA documents and maintain ATO boundary integrity.",
        "scope": "All boundary interconnections with active ISA agreements at IL4 and above.",
        "steps": [
            {"order": 1, "description": "Identify ISAs expiring within 90 days via the ISA Tracker dashboard.", "responsible_party": "ISSO"},
            {"order": 2, "description": "Notify the remote system owner and security POC of pending expiry.", "responsible_party": "ISSO"},
            {"order": 3, "description": "Review ISA content for accuracy — verify data flows, ports, protocols, and classification levels.", "responsible_party": "Security Engineer"},
            {"order": 4, "description": "Update ISA document with current system details, FIPS-validated encryption, and revised MOU/MOA if applicable.", "responsible_party": "System Owner"},
            {"order": 5, "description": "Submit updated ISA for co-signature by both system owners and ISSOs.", "responsible_party": "System Owner"},
            {"order": 6, "description": "Obtain AO approval and store signed ISA in the designated secure repository.", "responsible_party": "AO"},
            {"order": 7, "description": "Update ISA Tracker with new expiry date and set 90/60/30-day reminder alerts.", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["CA-3", "SA-9", "SC-7", "PL-2"],
        "owner": "ISSO",
        "reviewer": "AO",
        "version": "1.0",
    },
    {
        "title": "Boundary Change Approval",
        "sop_type": "boundary_change_approval",
        "description": "Formal approval process for adding, modifying, or removing components within the ATO authorization boundary to maintain compliance with NIST 800-53 CM controls.",
        "purpose": "Ensure all boundary changes are reviewed, risk-assessed, and approved before implementation to protect the ATO.",
        "scope": "Any change to the authorization boundary including new systems, data flows, interconnections, or boundary component removal.",
        "steps": [
            {"order": 1, "description": "Submit a boundary change request (BCR) with full description, rationale, and impacted components.", "responsible_party": "System Owner"},
            {"order": 2, "description": "ISSO performs initial impact analysis — assess effect on existing controls, data flows, and PPS.", "responsible_party": "ISSO"},
            {"order": 3, "description": "Update Boundary Design Canvas to reflect proposed change and generate delta assessment.", "responsible_party": "Security Engineer"},
            {"order": 4, "description": "Conduct risk analysis — identify new attack surfaces, required mitigations, and control gaps.", "responsible_party": "Security Assessor"},
            {"order": 5, "description": "Present change package (updated SSP section, boundary diagram, risk memo) to the Change Control Board.", "responsible_party": "ISSO"},
            {"order": 6, "description": "Obtain AO approval via signed authorization memo before implementing any change.", "responsible_party": "AO"},
            {"order": 7, "description": "Implement change, update POA&M if residual risk accepted, and notify all stakeholders.", "responsible_party": "DevSecOps"},
            {"order": 8, "description": "Document final boundary state in SSP and update all boundary artifacts within 5 business days.", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["CM-3", "CM-4", "CA-6", "RA-3", "PL-2"],
        "owner": "System Owner",
        "reviewer": "AO",
        "version": "1.0",
    },
    {
        "title": "Cross-Domain Transfer Procedure",
        "sop_type": "cross_domain_transfer",
        "description": "Controlled procedure for transferring data across classification boundaries using approved Cross-Domain Solutions (CDS) in compliance with DISA CDS policy and NIST 800-53 SC controls.",
        "purpose": "Ensure data moving between classification domains (e.g., CUI to SECRET) is filtered, validated, and logged per cross-domain policy.",
        "scope": "All data transfers crossing authorization boundary classification levels (IL4/IL5/IL6).",
        "steps": [
            {"order": 1, "description": "Identify the source and destination classification levels and verify CDS is approved for that domain pair.", "responsible_party": "Security Engineer"},
            {"order": 2, "description": "Confirm data content type is authorized for cross-domain transfer per approved transfer list.", "responsible_party": "Data Owner"},
            {"order": 3, "description": "Pre-screen data using content inspection rules — strip disallowed content types (executables, embedded scripts).", "responsible_party": "Security Engineer"},
            {"order": 4, "description": "Submit transfer request through the CDS management interface with justification and data manifest.", "responsible_party": "Data Owner"},
            {"order": 5, "description": "CDS performs automated content filtering and generates transfer audit log with hash verification.", "responsible_party": "CDS System"},
            {"order": 6, "description": "Security officer reviews transfer audit log and confirms no policy violations.", "responsible_party": "ISSO"},
            {"order": 7, "description": "Notify destination system owner and verify data integrity at receiving end.", "responsible_party": "System Administrator"},
            {"order": 8, "description": "Retain transfer audit log per NIST AU-11 retention requirements (minimum 3 years for IL5).", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["SC-7", "SC-16", "AU-10", "AU-12", "AC-4"],
        "owner": "ISSO",
        "reviewer": "Security Assessor",
        "version": "1.0",
    },
    {
        "title": "Interconnection Decommission",
        "sop_type": "interconnection_decommission",
        "description": "Safe, auditable process for decommissioning a boundary interconnection, including ISA termination, firewall rule removal, and SSP update per NIST 800-53 SA-9 and CM-3.",
        "purpose": "Ensure interconnections are cleanly terminated without leaving residual access paths or orphaned firewall rules that increase attack surface.",
        "scope": "Any authorized interconnection being retired, including ISA-governed links, VPN tunnels, and API integrations at the ATO boundary.",
        "steps": [
            {"order": 1, "description": "Initiate decommission request with business justification and target decommission date.", "responsible_party": "System Owner"},
            {"order": 2, "description": "Identify all dependent data flows, applications, and users relying on the interconnection.", "responsible_party": "Security Engineer"},
            {"order": 3, "description": "Coordinate with remote system owner to agree on decommission timeline and data migration plan.", "responsible_party": "System Owner"},
            {"order": 4, "description": "Disable interconnection (block firewall rules, revoke certificates) in a maintenance window.", "responsible_party": "Network Engineer"},
            {"order": 5, "description": "Monitor for 5 business days to confirm no legitimate traffic is disrupted.", "responsible_party": "Security Operations"},
            {"order": 6, "description": "Permanently remove firewall rules, VPN configurations, and API credentials. Revoke ISA.", "responsible_party": "Network Engineer"},
            {"order": 7, "description": "Update SSP, boundary diagram, and PPS matrix to remove decommissioned interconnection.", "responsible_party": "ISSO"},
            {"order": 8, "description": "Notify AO and document completion in the audit trail.", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["SA-9", "CM-3", "SC-7", "CA-3", "AU-12"],
        "owner": "System Owner",
        "reviewer": "AO",
        "version": "1.0",
    },
]


def seed_sops():
    """Seed example SOPs if the table is empty."""
    with _get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM bdc_sops").fetchone()[0]
    if count > 0:
        return
    for sop_data in SEED_SOPS:
        create_sop(sop_data)
