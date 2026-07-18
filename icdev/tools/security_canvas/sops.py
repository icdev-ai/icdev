# [CUI // SP-CTI]
"""Security Design Canvas — Standard Operating Procedures (SOPs).

Provides CRUD operations and approval workflow for SOPs linked to the
Security Design Canvas. Examples: vulnerability disclosure, access review
cadence, incident classification, evidence collection for ATO.
"""

import json
import uuid
from datetime import datetime, timezone


def _get_conn():
    from tools.security_canvas.db.init_db import get_connection
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
    # Build query from fixed allowed clauses — no user string interpolated into SQL
    if sop_type and approval_status:
        sql = "SELECT * FROM sdc_sops WHERE sop_type = %s AND approval_status = %s ORDER BY updated_at DESC"
        params = [sop_type, approval_status]
    elif sop_type:
        sql = "SELECT * FROM sdc_sops WHERE sop_type = %s ORDER BY updated_at DESC"
        params = [sop_type]
    elif approval_status:
        sql = "SELECT * FROM sdc_sops WHERE approval_status = %s ORDER BY updated_at DESC"
        params = [approval_status]
    else:
        sql = "SELECT * FROM sdc_sops ORDER BY updated_at DESC"
        params = []
    with _get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_sop_to_dict(r) for r in rows]


def get_sop_by_id(sop_id):
    """Return a single SOP dict or None."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM sdc_sops WHERE id=%s", (sop_id,)).fetchone()
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
            """INSERT INTO sdc_sops
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
                data.get("classification", "CUI"),
                now,
                now,
            ),
        )
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
            """UPDATE sdc_sops SET
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
    return get_sop_by_id(sop_id)


def delete_sop(sop_id):
    """Delete a SOP. Returns True if deleted."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM sdc_sops WHERE id=%s", (sop_id,))
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
            "UPDATE sdc_sops SET approval_status='pending_review', updated_at=%s WHERE id=%s",
            (now, sop_id),
        )
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
            """UPDATE sdc_sops SET
               approval_status='approved', approved_by=%s, approved_at=%s,
               rejected_reason=NULL, updated_at=%s
               WHERE id=%s""",
            (approved_by, now, now, sop_id),
        )
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
            """UPDATE sdc_sops SET
               approval_status='rejected', rejected_reason=%s,
               approved_by=%s, updated_at=%s
               WHERE id=%s""",
            (reason, rejected_by, now, sop_id),
        )
    return get_sop_by_id(sop_id), None


# ── Seed data ──────────────────────────────────────────────────────────────────

SEED_SOPS = [
    {
        "title": "Vulnerability Disclosure SOP",
        "sop_type": "vulnerability_disclosure",
        "description": "Defines how discovered vulnerabilities are reported, triaged, and remediated in compliance with NIST 800-53 RA-5 and SI-2.",
        "purpose": "Ensure timely, consistent handling of vulnerability disclosures to minimize risk exposure.",
        "scope": "All systems within the ATO boundary at IL4 and above.",
        "steps": [
            {"order": 1, "description": "Receive vulnerability report via official channel (email, ticketing system).", "responsible_party": "Security Operations"},
            {"order": 2, "description": "Validate and reproduce the vulnerability within 24 hours.", "responsible_party": "Security Engineer"},
            {"order": 3, "description": "Assign CVSS score and severity classification (CAT I/II/III).", "responsible_party": "Security Engineer"},
            {"order": 4, "description": "Notify system owner and ISSO within 4 hours for CAT I, 24 hours for CAT II.", "responsible_party": "ISSO"},
            {"order": 5, "description": "Develop and test remediation patch or workaround.", "responsible_party": "Development Team"},
            {"order": 6, "description": "Deploy remediation to production after change approval.", "responsible_party": "DevSecOps"},
            {"order": 7, "description": "Update POA&M and close vulnerability ticket with evidence.", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["RA-5", "SI-2", "SI-5", "CA-7"],
        "owner": "ISSO",
        "reviewer": "AO",
        "version": "1.0",
    },
    {
        "title": "Access Review Cadence SOP",
        "sop_type": "access_review",
        "description": "Periodic review of user accounts and privileges to ensure least privilege and detect orphaned accounts per AC-2 and AC-6.",
        "purpose": "Enforce least-privilege principle and remove unauthorized or stale access rights.",
        "scope": "All privileged and non-privileged accounts on systems within the ATO boundary.",
        "steps": [
            {"order": 1, "description": "Generate full account listing from IdP/directory (monthly).", "responsible_party": "System Administrator"},
            {"order": 2, "description": "Cross-reference against HR active roster to identify terminated users.", "responsible_party": "ISSO"},
            {"order": 3, "description": "Disable or remove accounts inactive > 90 days without exception.", "responsible_party": "System Administrator"},
            {"order": 4, "description": "Review privileged account justifications with role owners.", "responsible_party": "System Owner"},
            {"order": 5, "description": "Document review results and retention evidence in POA&M.", "responsible_party": "ISSO"},
            {"order": 6, "description": "Report metrics to AO quarterly.", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["AC-2", "AC-6", "IA-4", "AU-9"],
        "owner": "ISSO",
        "reviewer": "System Owner",
        "version": "1.0",
    },
    {
        "title": "Incident Classification SOP",
        "sop_type": "incident_classification",
        "description": "Framework for categorizing security incidents by severity, type, and impact to drive appropriate response actions per IR-4 and IR-5.",
        "purpose": "Enable rapid, consistent classification of security incidents to trigger correct response procedures.",
        "scope": "All security events detected on IL4/IL5 systems.",
        "steps": [
            {"order": 1, "description": "Receive alert or report from SIEM/SOC.", "responsible_party": "Security Operations"},
            {"order": 2, "description": "Determine if event is a confirmed incident or false positive within 1 hour.", "responsible_party": "Incident Responder"},
            {"order": 3, "description": "Classify incident category: CAT 1 (Unauthorized Access), CAT 2 (DoS), CAT 3 (Malicious Code), CAT 4 (Improper Usage), CAT 5 (Scans/Probes), CAT 6 (Investigation).", "responsible_party": "Incident Responder"},
            {"order": 4, "description": "Assign severity: Critical/High/Medium/Low based on impact scope.", "responsible_party": "Incident Responder"},
            {"order": 5, "description": "Notify ISSO and system owner per notification matrix.", "responsible_party": "Security Operations"},
            {"order": 6, "description": "Open incident ticket and begin response runbook.", "responsible_party": "Incident Responder"},
            {"order": 7, "description": "Escalate Critical incidents to CISO and AO within 1 hour.", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["IR-4", "IR-5", "IR-6", "AU-6"],
        "owner": "CISO",
        "reviewer": "AO",
        "version": "1.0",
    },
    {
        "title": "Evidence Collection for ATO SOP",
        "sop_type": "evidence_collection",
        "description": "Defines the process for gathering, packaging, and maintaining compliance evidence for Authorization to Operate (ATO) packages per CA-2 and CA-7.",
        "purpose": "Ensure continuous, audit-ready evidence is available to support ATO assessment and cATO maintenance.",
        "scope": "All NIST 800-53 controls within the system security plan (SSP).",
        "steps": [
            {"order": 1, "description": "Identify all controls requiring evidence in the current SSP control baseline.", "responsible_party": "ISSO"},
            {"order": 2, "description": "Run automated evidence collection via ICDEV compliance engine monthly.", "responsible_party": "DevSecOps"},
            {"order": 3, "description": "Manually collect evidence for controls not covered by automation (interviews, artifacts).", "responsible_party": "ISSO"},
            {"order": 4, "description": "Tag evidence with control ID, collection date, collector, and expiration.", "responsible_party": "ISSO"},
            {"order": 5, "description": "Store evidence in designated secure repository with access controls.", "responsible_party": "System Administrator"},
            {"order": 6, "description": "Review evidence freshness — flag items > 365 days old for renewal.", "responsible_party": "ISSO"},
            {"order": 7, "description": "Package evidence bundle for SAR and submit to assessor.", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["CA-2", "CA-7", "CA-8", "AU-12"],
        "owner": "ISSO",
        "reviewer": "Third-Party Assessor",
        "version": "1.0",
    },
]


def seed_sops():
    """Seed example SOPs if the table is empty."""
    with _get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sdc_sops").fetchone()[0]
    if count > 0:
        return
    for sop_data in SEED_SOPS:
        create_sop(sop_data)
