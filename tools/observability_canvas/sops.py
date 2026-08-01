# CUI // SP-CTI
"""Observability Design Canvas — Standard Operating Procedures (SOPs).

Provides CRUD operations and approval workflow for SOPs linked to the
Observability Design Canvas. Examples: observability onboarding for new
services, alert tuning, dashboard review cadence, log retention policy.
"""

import json
import uuid
from datetime import datetime, timezone


def _get_conn():
    from tools.observability_canvas.db.init_db import get_connection
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
        sql = "SELECT * FROM odc_sops WHERE sop_type = %s AND approval_status = %s ORDER BY updated_at DESC"
        params = [sop_type, approval_status]
    elif sop_type:
        sql = "SELECT * FROM odc_sops WHERE sop_type = %s ORDER BY updated_at DESC"
        params = [sop_type]
    elif approval_status:
        sql = "SELECT * FROM odc_sops WHERE approval_status = %s ORDER BY updated_at DESC"
        params = [approval_status]
    else:
        sql = "SELECT * FROM odc_sops ORDER BY updated_at DESC"
        params = []
    with _get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_sop_to_dict(r) for r in rows]


def get_sop_by_id(sop_id):
    """Return a single SOP dict or None."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM odc_sops WHERE id=%s", (sop_id,)).fetchone()
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
            """INSERT INTO odc_sops
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
            """UPDATE odc_sops SET
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
        cur = conn.execute("DELETE FROM odc_sops WHERE id=%s", (sop_id,))
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
            "UPDATE odc_sops SET approval_status='pending_review', updated_at=%s WHERE id=%s",
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
            """UPDATE odc_sops SET
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
            """UPDATE odc_sops SET
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
        "title": "Observability Onboarding for New Services",
        "sop_type": "service_onboarding",
        "description": "Step-by-step procedure for integrating a new service into the ICDEV observability platform, ensuring logs, metrics, traces, and alerts are configured before deployment to production.",
        "purpose": "Guarantee every production service has baseline observability coverage (AU-2, SI-4) before going live, preventing blind spots in monitoring and incident response.",
        "scope": "All new microservices, APIs, and background workers deployed to IL4 and above environments.",
        "steps": [
            {"order": 1, "description": "Register the service in the Observability Design Canvas — assign service tier (critical/standard/background) and link to parent system boundary.", "responsible_party": "DevSecOps Engineer"},
            {"order": 2, "description": "Instrument the service with the approved OTEL SDK (OpenTelemetry). Verify traces export to the central OTEL Collector at the designated endpoint.", "responsible_party": "Developer"},
            {"order": 3, "description": "Configure structured logging (JSON format) with required fields: timestamp (UTC ISO-8601), severity, service_name, trace_id, span_id, user_id (masked).", "responsible_party": "Developer"},
            {"order": 4, "description": "Define and deploy service-level metrics: request_rate, error_rate, p50/p95/p99 latency, saturation. Register metric names in the central metric registry.", "responsible_party": "Developer"},
            {"order": 5, "description": "Create baseline alert rules in the alerting platform: error rate > 5%, p99 latency > SLO threshold, pod restarts > 3 in 5 min. Assign PagerDuty escalation policy.", "responsible_party": "SRE"},
            {"order": 6, "description": "Build and publish the service dashboard from the approved template in Grafana/observability platform. Include RED metrics, saturation, and dependency health panels.", "responsible_party": "SRE"},
            {"order": 7, "description": "Run observability smoke test: trigger test errors, verify they appear in logs and traces within 60 seconds, confirm alerts fire and notifications reach on-call.", "responsible_party": "QA / SRE"},
            {"order": 8, "description": "Perform coverage assessment in the ODC Canvas — confirm score >= 70% before sign-off. Document any gaps in POA&M.", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["AU-2", "AU-3", "AU-12", "SI-4", "IR-4"],
        "owner": "DevSecOps",
        "reviewer": "ISSO",
        "version": "1.0",
    },
    {
        "title": "Alert Tuning Procedure",
        "sop_type": "alert_tuning",
        "description": "Formal process for reviewing, adjusting, and validating alert thresholds to reduce noise and improve signal quality across the observability platform.",
        "purpose": "Maintain high signal-to-noise ratio in alerting, preventing alert fatigue while ensuring all actionable conditions reliably page on-call responders (NIST SI-4, IR-5).",
        "scope": "All production alert rules across infrastructure, application, and security monitoring tiers.",
        "steps": [
            {"order": 1, "description": "Pull last 30-day alert history: count total fires per rule, MTTA, and false-positive rate. Flag any rule firing > 20 times/week with < 30% actionability rate.", "responsible_party": "SRE"},
            {"order": 2, "description": "Classify each flagged alert as: (a) noisy — threshold too low, (b) stale — condition no longer relevant, (c) misconfigured — wrong severity or routing.", "responsible_party": "SRE"},
            {"order": 3, "description": "Propose threshold adjustments using the 95th-percentile baseline from the past 14 days of metric data. Document proposed changes with before/after values.", "responsible_party": "SRE"},
            {"order": 4, "description": "Review proposed changes with the service team owner — confirm no actionable scenarios are silenced by the new thresholds.", "responsible_party": "Service Owner"},
            {"order": 5, "description": "Deploy threshold changes to staging environment. Monitor for 48 hours to verify alert behavior matches expectations.", "responsible_party": "SRE"},
            {"order": 6, "description": "Promote changes to production. Notify on-call team of updated thresholds and expected alert behavior.", "responsible_party": "SRE"},
            {"order": 7, "description": "Document all changes in the alert registry and update the ODC Canvas runbook links. Log tuning event in the audit trail.", "responsible_party": "SRE"},
        ],
        "nist_controls": ["SI-4", "IR-5", "AU-6", "CA-7"],
        "owner": "SRE Lead",
        "reviewer": "ISSO",
        "version": "1.0",
    },
    {
        "title": "Dashboard Review Cadence",
        "sop_type": "dashboard_review",
        "description": "Scheduled review process for validating observability dashboards remain accurate, actionable, and aligned with current system architecture and SLOs.",
        "purpose": "Prevent dashboard decay — stale panels, broken queries, and missing coverage — so on-call responders always have reliable situational awareness (NIST CA-7, AU-6).",
        "scope": "All production-tier observability dashboards across infrastructure, application, and security domains.",
        "steps": [
            {"order": 1, "description": "Run automated dashboard health check: verify all panel data sources are reachable, no 'No Data' panels, all variable dropdowns populate. Flag broken dashboards.", "responsible_party": "SRE (automated)"},
            {"order": 2, "description": "Review each dashboard against the current system architecture — remove panels for decommissioned services, add panels for new dependencies added in the last quarter.", "responsible_party": "SRE"},
            {"order": 3, "description": "Validate SLO panels: confirm thresholds match current SLO agreements. Update error budget burn rate calculations if SLOs were renegotiated.", "responsible_party": "Service Owner"},
            {"order": 4, "description": "Review security monitoring panels: verify SIEM query coverage maps to current threat model. Confirm MITRE ATT&CK detection coverage hasn't regressed.", "responsible_party": "Security Engineer"},
            {"order": 5, "description": "Gather on-call feedback: review post-incident reports for dashboard gaps. Add panels that would have accelerated last quarter's incidents.", "responsible_party": "SRE Lead"},
            {"order": 6, "description": "Publish review summary with pass/fail status per dashboard, list of changes made, and any deferred items tracked in POA&M.", "responsible_party": "SRE Lead"},
        ],
        "nist_controls": ["CA-7", "AU-6", "SI-4", "IR-8"],
        "owner": "SRE Lead",
        "reviewer": "ISSO",
        "version": "1.0",
    },
    {
        "title": "Log Retention Policy Enforcement",
        "sop_type": "log_retention",
        "description": "Procedure for reviewing, enforcing, and auditing log retention settings across all logging tiers to comply with NIST AU-11, FedRAMP, and organizational retention requirements.",
        "purpose": "Ensure audit logs are retained for the required period (minimum 90 days hot, 1 year cold for IL4; 3 years for IL5) and purged per policy to control storage cost and PII exposure.",
        "scope": "All log streams ingested by the observability platform across IL4 and IL5 environments.",
        "steps": [
            {"order": 1, "description": "Inventory all active log streams: name, source system, classification level, current retention setting, and estimated daily volume (GB/day).", "responsible_party": "SRE"},
            {"order": 2, "description": "Compare current retention settings against policy table: IL4 = 90 days hot / 1 year cold; IL5 = 90 days hot / 3 years cold; CUI = minimum 1 year total.", "responsible_party": "ISSO"},
            {"order": 3, "description": "Identify non-compliant streams: too short (retention risk) or excessively long (PII/cost risk). Generate findings report.", "responsible_party": "ISSO"},
            {"order": 4, "description": "Submit retention change request for each non-compliant stream. For CUI streams, obtain ISSO sign-off before reducing retention.", "responsible_party": "SRE"},
            {"order": 5, "description": "Apply approved retention changes in the logging platform. Verify changes took effect by querying index lifecycle policies.", "responsible_party": "SRE"},
            {"order": 6, "description": "Run purge validation: confirm logs older than retention limit are no longer queryable. Verify cold-tier archives are accessible for compliance retrieval.", "responsible_party": "SRE"},
            {"order": 7, "description": "Document final retention state for all streams in the SSP AU-11 control narrative. Store signed review record in compliance artifact store.", "responsible_party": "ISSO"},
        ],
        "nist_controls": ["AU-11", "AU-4", "AU-9", "SI-12"],
        "owner": "ISSO",
        "reviewer": "AO",
        "version": "1.0",
    },
]


def seed_sops():
    """Seed example SOPs if the table is empty."""
    with _get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM odc_sops").fetchone()[0]
    if count > 0:
        return
    for sop_data in SEED_SOPS:
        create_sop(sop_data)
