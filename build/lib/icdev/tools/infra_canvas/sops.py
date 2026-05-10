# CUI // SP-CTI
"""Infrastructure Design Canvas — Standard Operating Procedures (SOPs).

CRUD + approval workflow for IDC SOPs. Status lifecycle:
  draft → pending_review → approved
  pending_review → rejected → draft
  approved → archived

Examples: Infrastructure Change Management, Capacity Planning Review,
Cloud Account Onboarding.
"""

import json
import uuid
from datetime import datetime, timezone


def _get_conn():
    from tools.infra_canvas.db.init_db import get_connection
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
    d["tags"] = _parse_json_field(d.get("tags"), [])
    return d


# ── Read ──────────────────────────────────────────────────────────────────────

def get_all_sops(category=None, status=None):
    """Return all SOPs, optionally filtered by category and/or status."""
    conditions = []
    params = []
    if category:
        conditions.append("category=?")
        params.append(category)
    if status:
        conditions.append("status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM idc_sops {where} ORDER BY status, title"
    conn = _get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_sop_to_dict(r) for r in rows]


def get_sop_by_id(sop_id):
    """Return a single SOP dict or None."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM idc_sops WHERE id=?", (sop_id,)).fetchone()
    finally:
        conn.close()
    return _sop_to_dict(row)


# ── Write ─────────────────────────────────────────────────────────────────────

def create_sop(data):
    """Create a new SOP in draft status. Returns the new SOP dict."""
    sop_id = str(uuid.uuid4())
    now = _now()
    steps = json.dumps(data.get("steps", []))
    nist_controls = json.dumps(data.get("nist_controls", []))
    tags = json.dumps(data.get("tags", []))
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO idc_sops
               (id, title, category, description, purpose, scope,
                steps, status, version, owner, reviewer, approver,
                effective_date, review_interval_days,
                nist_controls, tags, classification, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sop_id,
                data.get("title", "Untitled SOP"),
                data.get("category", "general"),
                data.get("description", ""),
                data.get("purpose", ""),
                data.get("scope", ""),
                steps,
                "draft",
                data.get("version", "1.0"),
                data.get("owner", ""),
                data.get("reviewer", ""),
                data.get("approver", ""),
                data.get("effective_date", ""),
                int(data.get("review_interval_days", 365)),
                nist_controls,
                tags,
                data.get("classification", "CUI"),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_sop_by_id(sop_id)


def update_sop(sop_id, data):
    """Update an existing SOP's content fields (not status). Returns updated dict or None."""
    existing = get_sop_by_id(sop_id)
    if not existing:
        return None
    now = _now()
    steps = json.dumps(data.get("steps", existing["steps"]))
    nist_controls = json.dumps(data.get("nist_controls", existing["nist_controls"]))
    tags = json.dumps(data.get("tags", existing["tags"]))
    conn = _get_conn()
    try:
        conn.execute(
            """UPDATE idc_sops SET
               title=?, category=?, description=?, purpose=?, scope=?,
               steps=?, version=?, owner=?, reviewer=?, approver=?,
               effective_date=?, review_interval_days=?,
               nist_controls=?, tags=?, classification=?, updated_at=?
               WHERE id=?""",
            (
                data.get("title", existing["title"]),
                data.get("category", existing["category"]),
                data.get("description", existing["description"]),
                data.get("purpose", existing["purpose"]),
                data.get("scope", existing["scope"]),
                steps,
                data.get("version", existing["version"]),
                data.get("owner", existing["owner"]),
                data.get("reviewer", existing["reviewer"]),
                data.get("approver", existing["approver"]),
                data.get("effective_date", existing["effective_date"]),
                int(data.get("review_interval_days", existing["review_interval_days"])),
                nist_controls,
                tags,
                data.get("classification", existing["classification"]),
                now,
                sop_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_sop_by_id(sop_id)


# ── Approval workflow ─────────────────────────────────────────────────────────

_VALID_TRANSITIONS = {
    "draft": {"pending_review"},
    "pending_review": {"approved", "rejected"},
    "rejected": {"draft"},
    "approved": {"archived"},
    "archived": set(),
}


def transition_status(sop_id, new_status, actor=""):
    """Advance or revert SOP status. Returns updated dict or error dict."""
    existing = get_sop_by_id(sop_id)
    if not existing:
        return {"error": "not found"}
    current = existing["status"]
    if new_status not in _VALID_TRANSITIONS.get(current, set()):
        allowed = sorted(_VALID_TRANSITIONS.get(current, set()))
        return {"error": f"Cannot transition from '{current}' to '{new_status}'. Allowed: {allowed}"}
    now = _now()
    extra_fields = ""
    extra_params = []
    if new_status == "approved":
        extra_fields = ", approved_at=?"
        extra_params = [now]
    elif new_status == "pending_review":
        extra_fields = ", reviewed_at=?"
        extra_params = [now]
    conn = _get_conn()
    try:
        conn.execute(
            f"UPDATE idc_sops SET status=?{extra_fields}, updated_at=? WHERE id=?",
            [new_status] + extra_params + [now, sop_id],
        )
        conn.commit()
    finally:
        conn.close()
    return get_sop_by_id(sop_id)


def delete_sop(sop_id):
    """Delete a SOP. Returns True if deleted, False if not found."""
    existing = get_sop_by_id(sop_id)
    if not existing:
        return False
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM idc_sops WHERE id=?", (sop_id,))
        conn.commit()
    finally:
        conn.close()
    return True


# ── Seeds ─────────────────────────────────────────────────────────────────────

_SEED_SOPS = [
    {
        "id": "sop-idc-infra-change-mgmt",
        "title": "Infrastructure Change Management",
        "category": "infra_change_management",
        "description": (
            "Defines the standard process for planning, reviewing, approving, and "
            "implementing infrastructure changes to minimize risk and ensure audit "
            "continuity. Aligns with NIST CM-3 and ITIL Change Management."
        ),
        "purpose": (
            "Ensure all infrastructure changes are assessed for risk, approved by "
            "appropriate stakeholders, and traceable in the audit trail before implementation."
        ),
        "scope": (
            "Applies to all changes to production infrastructure components including "
            "compute, storage, networking, security groups, IAM policies, and IaC modules."
        ),
        "steps": [
            {
                "action": "Submit a Change Request (CR) in the change management system (ServiceNow/Jira) with: description, risk level (low/medium/high/critical), affected systems, rollback plan, and test results from non-prod.",
                "responsible_party": "Change Initiator",
                "expected_outcome": "CR created with all required fields populated and status set to 'Submitted'.",
                "verification": "CR ticket number assigned; auto-notification sent to change advisory board (CAB).",
            },
            {
                "action": "Change Advisory Board (CAB) reviews the CR. For low/medium risk: async approval within 24 hours. For high/critical: synchronous CAB meeting required within 4 hours of submission.",
                "responsible_party": "Change Advisory Board",
                "expected_outcome": "CR approved or rejected with documented rationale.",
                "verification": "CR status updated to 'Approved' or 'Rejected'; approver name and timestamp recorded.",
            },
            {
                "action": "Schedule the change within the approved maintenance window. Notify all affected teams at least 2 hours before implementation. Confirm rollback resources are staged (snapshots, previous IaC version, rollback scripts).",
                "responsible_party": "Change Implementer",
                "expected_outcome": "Maintenance window confirmed; affected teams notified; rollback resources verified.",
                "verification": "Notification log captured; rollback resources tagged with change ticket ID.",
            },
            {
                "action": "Implement the change using IaC (Terraform/Ansible). Do NOT make manual console changes. Capture the full apply log and artifact versions deployed.",
                "responsible_party": "Change Implementer",
                "expected_outcome": "Change applied successfully; apply log archived in change ticket.",
                "verification": "terraform plan shows zero diff post-apply; service health check returns HTTP 200.",
            },
            {
                "action": "Run post-implementation health checks: smoke test critical endpoints, verify monitoring alerts are not firing, confirm deployment in all required regions/AZs.",
                "responsible_party": "Change Implementer",
                "expected_outcome": "All health checks pass; no new alerts triggered within 15 minutes of change.",
                "verification": "Health check report attached to CR; monitoring dashboard screenshot captured.",
            },
            {
                "action": "Close the change ticket with: actual implementation time, issues encountered, post-change status, and lessons learned. Update CMDB with new configuration state.",
                "responsible_party": "Change Implementer",
                "expected_outcome": "CR closed; CMDB updated; lessons learned documented.",
                "verification": "CR status set to 'Closed'; CMDB record shows updated configuration.",
            },
        ],
        "status": "approved",
        "version": "1.0",
        "owner": "Platform Engineering",
        "reviewer": "Security Engineering",
        "approver": "CTO / ISSO",
        "review_interval_days": 180,
        "nist_controls": ["CM-3", "CM-4", "CM-5", "AU-2", "AU-9"],
        "tags": ["change-management", "iac", "terraform", "cab", "cmdb"],
        "classification": "CUI",
    },
    {
        "id": "sop-idc-capacity-planning",
        "title": "Capacity Planning Review",
        "category": "capacity_planning",
        "description": (
            "Defines the quarterly capacity planning process to ensure infrastructure "
            "resources are provisioned ahead of demand. Prevents capacity-driven outages "
            "and aligns spend with workload growth projections. Aligns with NIST CP-2 and SC-5."
        ),
        "purpose": (
            "Proactively identify resource shortfalls, right-size underutilized resources, "
            "and submit capacity increase requests before thresholds are breached."
        ),
        "scope": (
            "Applies to all production and staging compute (EC2, AKS, GKE), storage (EBS, S3, "
            "Azure Blob), databases (RDS, Cosmos, Cloud SQL), and network bandwidth across "
            "all managed cloud environments."
        ),
        "steps": [
            {
                "action": "Pull 90-day utilization metrics for all monitored resources: CPU, memory, disk, IOPS, and network throughput. Use the ICDEV monitoring dashboard (/monitor) or cloud-native tooling (CloudWatch, Azure Monitor, Stackdriver).",
                "responsible_party": "Infrastructure Engineer",
                "expected_outcome": "Utilization report exported covering P50/P90/P99 metrics for the trailing 90 days.",
                "verification": "Report file saved to capacity-planning/<quarter>/ in shared storage.",
            },
            {
                "action": "Identify resources breaching or approaching thresholds: CPU >70% sustained, memory >80%, disk >75%, or auto-scaler triggered >10 times in 30 days. Flag these as 'at-risk' for immediate action.",
                "responsible_party": "Infrastructure Engineer",
                "expected_outcome": "At-risk resource list with current utilization, threshold, and projected breach date.",
                "verification": "At-risk list reviewed by team lead; priority tier (P1/P2/P3) assigned to each.",
            },
            {
                "action": "Identify under-utilized resources: CPU <10% sustained for 30 days, memory <20%. Flag for right-sizing or decommissioning to reduce cost.",
                "responsible_party": "Infrastructure Engineer",
                "expected_outcome": "Right-sizing candidates list with estimated monthly savings.",
                "verification": "Right-sizing list shared with application owners for validation before any changes.",
            },
            {
                "action": "Forecast 6-month capacity demand using historical growth rate + planned project pipeline. Collaborate with application teams to confirm upcoming capacity drivers (new features, migrations, user growth).",
                "responsible_party": "Infrastructure Lead",
                "expected_outcome": "6-month capacity forecast documented with growth assumptions and confidence interval.",
                "verification": "Forecast reviewed and signed off by Finance and Engineering leadership.",
            },
            {
                "action": "Submit capacity increase requests for at-risk resources. For cloud quota increases: file via cloud vendor portal (AWS Service Quotas, Azure support, GCP quota page). For hardware: submit procurement request with lead-time buffer.",
                "responsible_party": "Infrastructure Lead",
                "expected_outcome": "Quota increase requests submitted with ticket IDs; expected approval dates tracked.",
                "verification": "Request tracker updated; follow-up reminders set for open requests older than 5 business days.",
            },
            {
                "action": "Document capacity planning review findings in the quarterly report. Include: utilization summary, at-risk resources, right-sizing actions taken, quota requests submitted, and next review date.",
                "responsible_party": "Infrastructure Lead",
                "expected_outcome": "Quarterly capacity report published to shared documentation; stakeholders notified.",
                "verification": "Report version-controlled in docs repo; next review date calendar invite sent.",
            },
        ],
        "status": "approved",
        "version": "1.0",
        "owner": "Infrastructure Engineering",
        "reviewer": "Finance Operations",
        "approver": "VP Engineering",
        "review_interval_days": 90,
        "nist_controls": ["CP-2", "SC-5", "SI-4", "AU-6"],
        "tags": ["capacity", "utilization", "rightsizing", "forecasting", "cloudwatch"],
        "classification": "CUI",
    },
    {
        "id": "sop-idc-cloud-account-onboarding",
        "title": "Cloud Account Onboarding",
        "category": "cloud_account_onboarding",
        "description": (
            "Standard procedure for provisioning and hardening a new cloud account (AWS, "
            "Azure, GCP) before workloads are deployed. Ensures security baseline, IAM "
            "structure, logging, and compliance guardrails are in place from day one. "
            "Aligns with NIST AC-2, AU-2, CM-6, and FedRAMP boundary requirements."
        ),
        "purpose": (
            "Prevent misconfigured cloud accounts from becoming security or compliance "
            "liabilities. Establish a consistent security posture baseline for every "
            "new account before first workload deployment."
        ),
        "scope": (
            "Applies to all new cloud accounts provisioned under the organization's landing "
            "zone / management organization. Covers AWS Organizations, Azure Management Groups, "
            "and GCP Folders."
        ),
        "steps": [
            {
                "action": "Create the new account via the organization's account vending machine (Control Tower / Landing Zone / Terraform module). Assign the account to the correct OU/Management Group based on workload classification (dev, staging, prod, sandbox).",
                "responsible_party": "Cloud Platform Engineer",
                "expected_outcome": "Account created; OU assignment confirmed; account ID recorded in CMDB.",
                "verification": "Account visible in AWS Organizations / Azure Tenant / GCP Resource Hierarchy; tagged with environment and owner.",
            },
            {
                "action": "Apply the organization Security Control Policy (SCP / Azure Policy / GCP Org Policy): (a) Block root/global admin usage. (b) Enforce MFA for all console access. (c) Restrict allowed regions to approved list. (d) Deny public S3 bucket creation / public blob access.",
                "responsible_party": "Cloud Security Engineer",
                "expected_outcome": "All SCPs/policies applied and verified active; no policy exceptions outstanding.",
                "verification": "Run policy compliance check: AWS Config, Azure Policy Compliance, GCP Asset Inventory — 0 non-compliant resources.",
            },
            {
                "action": "Enable mandatory logging and monitoring: (a) CloudTrail (all regions, S3 + CloudWatch). (b) Config recorder + delivery channel. (c) GuardDuty / Defender for Cloud / Security Command Center. (d) VPC Flow Logs for all VPCs. Centralize logs to the security account/SIEM.",
                "responsible_party": "Cloud Security Engineer",
                "expected_outcome": "All logging services enabled; log delivery to central SIEM confirmed within 15 minutes.",
                "verification": "Generate a test API call and confirm it appears in CloudTrail logs within 5 minutes.",
            },
            {
                "action": "Provision IAM baseline: (a) Create the standard role set (Admin, Developer, ReadOnly, SecurityAudit) from the approved IAM template. (b) Enable IAM Access Analyzer. (c) Set password policy: 14+ chars, MFA required, 90-day rotation. (d) Remove or disable unused default service accounts.",
                "responsible_party": "Cloud Platform Engineer",
                "expected_outcome": "Standard roles created; no overly-permissive wildcards; Access Analyzer enabled.",
                "verification": "IAM policy simulation confirms least-privilege for Developer role; no Admin wildcards present.",
            },
            {
                "action": "Deploy the network baseline via IaC: (a) Create VPC/VNet with public, private, and isolated subnets per the approved network template. (b) Configure NAT gateway (egress only from private subnets). (c) Enable VPC Flow Logs. (d) Configure default security groups to deny all inbound.",
                "responsible_party": "Network Engineer",
                "expected_outcome": "Network baseline deployed; no resources in default VPC; all SGs deny-by-default.",
                "verification": "terraform plan on the network module shows 'No changes'; network topology validated in IDC canvas.",
            },
            {
                "action": "Register the account in the ATO boundary: update the System Security Plan (SSP) to include the new account, its workload classification, data types, and boundary diagram in the Infrastructure Design Canvas. Add account ID to the authorization boundary.",
                "responsible_party": "ISSO / Compliance Engineer",
                "expected_outcome": "SSP updated; IDC canvas boundary diagram reflects new account; ATO boundary review scheduled.",
                "verification": "SSP version incremented; new account visible in boundary canvas with correct classification label.",
            },
            {
                "action": "Run the ICDEV security scan on the new account: python tools/security/scanner.py --scope cloud-account --account-id <id> --json. Resolve all CAT1/critical findings before first workload deployment. Document all CAT2/high findings in POAM.",
                "responsible_party": "Security Engineer",
                "expected_outcome": "Security scan passes with 0 CAT1 findings; all CAT2 findings logged in POAM.",
                "verification": "Scan report archived in security/scans/<account-id>/; POAM entries created for CAT2 items.",
            },
        ],
        "status": "draft",
        "version": "1.0",
        "owner": "Cloud Platform Engineering",
        "reviewer": "Security Engineering",
        "approver": "CISO / ISSO",
        "review_interval_days": 365,
        "nist_controls": ["AC-2", "AC-17", "AU-2", "CM-6", "CM-7", "IA-2", "SC-7"],
        "tags": ["cloud", "account", "onboarding", "iam", "landing-zone", "security-baseline"],
        "classification": "CUI",
    },
]


def seed_sops():
    """Insert seed SOPs if they don't already exist. Returns count added."""
    added = 0
    conn = _get_conn()
    try:
        for sop in _SEED_SOPS:
            existing = conn.execute(
                "SELECT id FROM idc_sops WHERE id=?", (sop["id"],)
            ).fetchone()
            if existing:
                continue
            now = _now()
            approved_at = now if sop.get("status") == "approved" else ""
            conn.execute(
                """INSERT INTO idc_sops
                   (id, title, category, description, purpose, scope,
                    steps, status, version, owner, reviewer, approver,
                    approved_at, effective_date, review_interval_days,
                    nist_controls, tags, classification, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sop["id"],
                    sop["title"],
                    sop["category"],
                    sop["description"],
                    sop["purpose"],
                    sop["scope"],
                    json.dumps(sop["steps"]),
                    sop["status"],
                    sop["version"],
                    sop["owner"],
                    sop["reviewer"],
                    sop["approver"],
                    approved_at,
                    sop.get("effective_date", ""),
                    sop.get("review_interval_days", 365),
                    json.dumps(sop["nist_controls"]),
                    json.dumps(sop["tags"]),
                    sop["classification"],
                    now,
                    now,
                ),
            )
            added += 1
        if added:
            conn.commit()
    finally:
        conn.close()
    return added
