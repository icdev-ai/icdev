# CUI // SP-CTI
"""Pipeline Design Canvas — Standard Operating Procedures (SOPs).

Provides CRUD operations and approval workflow for SOPs linked to the
Pipeline Design Canvas. Examples: release approval process, hotfix
deployment, pipeline credential rotation, artifact promotion.
"""

import json
import uuid
from contextlib import closing
from datetime import datetime, timezone


def _get_conn():
    from tools.pipeline.db.init_db import get_connection
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
        sql = "SELECT * FROM pdc_sops WHERE sop_type = %s AND approval_status = %s ORDER BY updated_at DESC"
        params = [sop_type, approval_status]
    elif sop_type:
        sql = "SELECT * FROM pdc_sops WHERE sop_type = %s ORDER BY updated_at DESC"
        params = [sop_type]
    elif approval_status:
        sql = "SELECT * FROM pdc_sops WHERE approval_status = %s ORDER BY updated_at DESC"
        params = [approval_status]
    else:
        sql = "SELECT * FROM pdc_sops ORDER BY updated_at DESC"
        params = []
    with closing(_get_conn()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_sop_to_dict(r) for r in rows]


def get_sop_by_id(sop_id):
    """Return a single SOP dict or None."""
    with closing(_get_conn()) as conn:
        row = conn.execute("SELECT * FROM pdc_sops WHERE id=%s", (sop_id,)).fetchone()
    return _sop_to_dict(row)


# ── Write ──────────────────────────────────────────────────────────────────────

def create_sop(data):
    """Create a new SOP. Returns the new SOP dict."""
    sop_id = str(uuid.uuid4())
    now = _now()
    steps = json.dumps(data.get("steps", []))
    nist_controls = json.dumps(data.get("nist_controls", []))
    with closing(_get_conn()) as conn:
        conn.execute(
            """INSERT INTO pdc_sops
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
    with closing(_get_conn()) as conn:
        conn.execute(
            """UPDATE pdc_sops SET
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
    with closing(_get_conn()) as conn:
        cur = conn.execute("DELETE FROM pdc_sops WHERE id=%s", (sop_id,))
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
    with closing(_get_conn()) as conn:
        conn.execute(
            "UPDATE pdc_sops SET approval_status='pending_review', updated_at=%s WHERE id=%s",
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
    with closing(_get_conn()) as conn:
        conn.execute(
            """UPDATE pdc_sops SET
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
    with closing(_get_conn()) as conn:
        conn.execute(
            """UPDATE pdc_sops SET
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
        "title": "Release Approval Process",
        "sop_type": "release_approval",
        "description": "Formal gate process for approving software releases through the pipeline, ensuring all quality, security, and compliance checks pass before production deployment.",
        "purpose": "Prevent unauthorized or non-compliant code from reaching production and ensure all release artifacts are signed and traceable.",
        "scope": "All production deployments and release candidates across IL4 and above environments.",
        "steps": [
            {"order": 1, "description": "Verify all pipeline stages (build, unit test, SAST, SCA, container scan) show green status.", "responsible_party": "DevSecOps Engineer"},
            {"order": 2, "description": "Confirm SBOM is generated and no critical/high CVEs are present in the dependency manifest.", "responsible_party": "Security Engineer"},
            {"order": 3, "description": "Review SLSA provenance attestation and verify artifact hashes match the build output.", "responsible_party": "Release Manager"},
            {"order": 4, "description": "Run smoke tests against staging environment and confirm zero critical failures.", "responsible_party": "QA Engineer"},
            {"order": 5, "description": "Obtain sign-off from Product Owner and ISSO via the release approval ticket.", "responsible_party": "Product Owner / ISSO"},
            {"order": 6, "description": "Tag the release commit with the version number and sign with GPG key.", "responsible_party": "Release Manager"},
            {"order": 7, "description": "Trigger production deployment pipeline and monitor health dashboards for 15 minutes post-deploy.", "responsible_party": "DevSecOps Engineer"},
            {"order": 8, "description": "Document release completion in the audit log with artifact hashes and approver identities.", "responsible_party": "Release Manager"},
        ],
        "nist_controls": ["CM-3", "CM-4", "SA-10", "SI-2", "AU-12"],
        "owner": "Release Manager",
        "reviewer": "ISSO",
        "version": "1.0",
    },
    {
        "title": "Hotfix Deployment Procedure",
        "sop_type": "hotfix_deployment",
        "description": "Expedited deployment process for critical hotfixes that bypass standard sprint cycles, with compensating security controls and mandatory post-deploy review.",
        "purpose": "Enable rapid response to critical production defects or security vulnerabilities while maintaining auditability and preventing unauthorized changes.",
        "scope": "Production hotfixes rated P0/P1 severity affecting system availability, data integrity, or active exploitation.",
        "steps": [
            {"order": 1, "description": "Create a hotfix branch from the production tag and document the defect/CVE being addressed.", "responsible_party": "Lead Developer"},
            {"order": 2, "description": "Obtain emergency change approval from the Change Control Board (CCB) via out-of-band channel.", "responsible_party": "Engineering Manager"},
            {"order": 3, "description": "Implement the minimal fix — no scope creep. Peer review required from a second engineer.", "responsible_party": "Lead Developer"},
            {"order": 4, "description": "Run abbreviated test suite (unit tests + targeted integration tests) — minimum 80% coverage of changed code.", "responsible_party": "QA Engineer"},
            {"order": 5, "description": "Perform SAST scan on the diff only. Block on any new HIGH or CRITICAL findings.", "responsible_party": "Security Engineer"},
            {"order": 6, "description": "Deploy to production with rollback plan ready and on-call engineer monitoring.", "responsible_party": "DevSecOps Engineer"},
            {"order": 7, "description": "Verify fix effectiveness in production via monitoring dashboards and health checks.", "responsible_party": "SRE"},
            {"order": 8, "description": "Within 48 hours: complete full test suite, update SBOM, and file post-incident review.", "responsible_party": "Engineering Manager"},
        ],
        "nist_controls": ["CM-3", "SI-2", "IR-4", "AU-12", "SA-10"],
        "owner": "Engineering Manager",
        "reviewer": "ISSO",
        "version": "1.0",
    },
    {
        "title": "Pipeline Credential Rotation",
        "sop_type": "credential_rotation",
        "description": "Scheduled and emergency rotation of all secrets used within the CI/CD pipeline including API keys, signing certificates, container registry tokens, and deployment credentials.",
        "purpose": "Limit the blast radius of credential compromise and comply with NIST SP 800-53 IA-5 authenticator management requirements.",
        "scope": "All pipeline credentials, service account tokens, signing keys, and secrets stored in the secrets manager.",
        "steps": [
            {"order": 1, "description": "Inventory all active pipeline credentials from the secrets manager and document current expiry dates.", "responsible_party": "Security Engineer"},
            {"order": 2, "description": "Generate new credentials in the secrets manager without deactivating existing ones.", "responsible_party": "Security Engineer"},
            {"order": 3, "description": "Update pipeline variables and CI/CD configuration to reference new credential identifiers.", "responsible_party": "DevSecOps Engineer"},
            {"order": 4, "description": "Run a test pipeline execution to confirm all stages authenticate successfully with new credentials.", "responsible_party": "DevSecOps Engineer"},
            {"order": 5, "description": "Revoke old credentials only after confirming at least one successful pipeline run with new credentials.", "responsible_party": "Security Engineer"},
            {"order": 6, "description": "Update the credential inventory with new expiry dates and rotation completion timestamp.", "responsible_party": "Security Engineer"},
            {"order": 7, "description": "Notify all dependent teams of the rotation completion and provide updated integration guidance.", "responsible_party": "Engineering Manager"},
        ],
        "nist_controls": ["IA-5", "SC-12", "CM-3", "AU-12", "AC-2"],
        "owner": "Security Engineer",
        "reviewer": "ISSO",
        "version": "1.0",
    },
    {
        "title": "Artifact Promotion Procedure",
        "sop_type": "artifact_promotion",
        "description": "Controlled process for promoting build artifacts (container images, binaries, packages) from lower environments (dev/test) to higher environments (staging/production) with integrity verification.",
        "purpose": "Ensure only verified, tested, and signed artifacts are promoted across environments, preventing artifact tampering or unauthorized substitution.",
        "scope": "All build artifacts subject to multi-environment promotion: container images, signed binaries, Helm charts, and IaC packages.",
        "steps": [
            {"order": 1, "description": "Identify the artifact version to be promoted and verify its SHA-256 digest against the build record.", "responsible_party": "Release Manager"},
            {"order": 2, "description": "Confirm the artifact passed all required gate checks in the source environment (SAST, DAST, compliance scan).", "responsible_party": "QA Engineer"},
            {"order": 3, "description": "Verify the SLSA provenance attestation is valid and the signing certificate has not expired.", "responsible_party": "Security Engineer"},
            {"order": 4, "description": "Copy artifact to the target environment registry using the promotion pipeline (no rebuild).", "responsible_party": "DevSecOps Engineer"},
            {"order": 5, "description": "Re-verify artifact digest in the target registry to confirm no tampering occurred during transfer.", "responsible_party": "Security Engineer"},
            {"order": 6, "description": "Update the artifact promotion log with source digest, target registry path, and approver identity.", "responsible_party": "Release Manager"},
            {"order": 7, "description": "Notify target environment operators that the artifact is ready for deployment.", "responsible_party": "Release Manager"},
        ],
        "nist_controls": ["SA-10", "CM-3", "SI-7", "AU-10", "SC-28"],
        "owner": "Release Manager",
        "reviewer": "Security Engineer",
        "version": "1.0",
    },
]


def seed_sops():
    """Seed example SOPs if the table is empty."""
    with closing(_get_conn()) as conn:
        count = conn.execute("SELECT COUNT(*) FROM pdc_sops").fetchone()[0]
    if count > 0:
        return
    for sop_data in SEED_SOPS:
        create_sop(sop_data)
