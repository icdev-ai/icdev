# CUI // SP-CTI
"""Security alert and escalation service for ICDEV™ compliance events.

Handles CAT I finding escalation, STIG alert dispatch, and POA&M
deadline reminders via db → render → notify chains.
"""

from __future__ import annotations

import json
from string import Template
from typing import Iterable

from tools.db.storage import get_connection
from .event_service import (
    render_template, render_to_string, render_string,
    send, sendmail, notify, emit, publish, dispatch, _now_iso,
)

# Narrative / digest limits and thresholds
_NARRATIVE_MAX_TOKENS  = 512
_NARRATIVE_TEMPERATURE = 0.3
_CAT1_DIGEST_LIMIT     = 5
_STIG_DIGEST_LIMIT     = 10
_POAM_DAYS_THRESHOLD   = 14
_DIGEST_DAYS_WINDOW    = 7
_POAM_AUTO_DUE_DAYS    = 90

ESCALATION_LEVELS = {
    "cat1": {"priority": "CRITICAL", "sla_hours": 24,  "auto_escalate": True,  "page": True},
    "cat2": {"priority": "HIGH",     "sla_hours": 72,  "auto_escalate": True,  "page": False},
    "cat3": {"priority": "MEDIUM",   "sla_hours": 168, "auto_escalate": False, "page": False},
    "info": {"priority": "LOW",      "sla_hours": 720, "auto_escalate": False, "page": False},
}

STIG_SEVERITY_MAP = {
    "I":   "CAT I — Critical",
    "II":  "CAT II — High",
    "III": "CAT III — Medium",
}

CAT1_ALERT_TEMPLATE = (
    "[CAT I SECURITY FINDING]\n"
    "Finding: $finding_title\n"
    "VID: $vid | Severity: $severity\n"
    "System: $system_name | Component: $component\n"
    "SLA: Remediate within $sla_hours hours\n"
    "POA&M ID: $poam_id\n"
    "Detected: $detected_at"
)

STIG_ALERT_TEMPLATE = (
    "[STIG ALERT — $stig_severity]\n"
    "Check: $check_id — $check_name\n"
    "System: $system_name | Workload: $workload_id\n"
    "Status: $status → Action Required: $action\n"
    "Remediation: $remediation\n"
    "Reference: $reference_url"
)

POAM_REMINDER_TEMPLATE = (
    "[POA&M DEADLINE REMINDER]\n"
    "Item: $poam_id — $title\n"
    "Project: $project_id | Framework: $framework\n"
    "Due: $due_date ($days_remaining days remaining)\n"
    "Severity: $severity | Owner: $owner\n"
    "Current Status: $status\n"
    "Milestone: $milestone"
)

# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5525, aiify-opp-5544, aiify-opp-5599, aiify-opp-5601, aiify-opp-5812): optional LLM-synthesized triage narrative.
#
# The db → render → notify chains above produce deterministic, template-based
# alert text that remains the AUTHORITATIVE payload — security operations must
# never depend on LLM availability for a CAT-I escalation. When a caller opts
# in via ``ai_narrative=True`` we ADDITIONALLY synthesize a short, grounded
# triage narrative (what happened, why it matters, recommended next action)
# and attach it under the ``narrative`` return key. Any failure — no-LLM mode,
# air-gap, network, missing credentials — degrades silently to ``None`` so the
# deterministic alert always ships.
# ---------------------------------------------------------------------------

_NARRATIVE_SYSTEM_PROMPT = (
    "You are a DoD/IC cybersecurity analyst triaging a compliance alert for "
    "security operations. Write a concise narrative (2-4 sentences) that: "
    "(1) states what the alert means in plain language, (2) explains why it "
    "matters given the severity and SLA, and (3) recommends the single most "
    "important next action. Use only the facts provided — never invent VIDs, "
    "dates, counts, or system names. Output only the narrative prose; no "
    "headers, no markdown, no preamble."
)


def _ai_alert_narrative(alert_kind: str, facts: dict) -> str | None:
    """Synthesize an optional LLM triage narrative for a rendered alert.

    Args:
        alert_kind: Human label for the alert family (e.g. "CAT-I security
            finding escalation"). Steers the model's framing.
        facts: Grounding facts already assembled for template rendering
            (the ``vars_`` dict). Passed verbatim so the narrative cannot
            drift from the deterministic payload.

    Returns:
        A short narrative string, or ``None`` if generation is unavailable
        or fails for any reason. Callers MUST treat ``None`` as "no
        narrative" and ship the deterministic alert unchanged.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        # Stable, ordered fact list keeps the prompt cache-friendly and the
        # output reproducible across calls with identical inputs.
        fact_lines = "\n".join(f"- {k}: {v}" for k, v in sorted(facts.items()))
        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Alert type: {alert_kind}\n"
                        f"Facts:\n{fact_lines}\n\n"
                        "Write the triage narrative."
                    ),
                }
            ],
            system_prompt=_NARRATIVE_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.3,
            skip_injection_scan=True,  # trusted first-party fact dict, not user input
            classification="CUI",
        )
        resp = LLMRouter().invoke("narrative_generation", req)
        if resp and resp.content:
            return resp.content.strip()
    except Exception:
        pass  # Graceful degradation — deterministic alert is authoritative.
    return None


def escalate_cat1_finding(
    finding_id: str,
    project_id: str,
    escalation_channels: Iterable[str],
    override_recipients: list[str] | None = None,
    ai_narrative: bool = False,
) -> dict:
    """Query CAT I finding, render escalation notice, and page security ops.

    Fetches finding details and associated POA&M from the DB, renders a
    severity-appropriate alert using the CAT-I template, and delivers to
    all security escalation channels (pager, email, Slack). Writes an
    immutable audit record on delivery.

    When ``ai_narrative`` is True, additionally synthesizes a short LLM
    triage narrative (returned under ``narrative`` and attached to Slack/
    Teams payloads). The deterministic template output remains the
    authoritative alert; the narrative is best-effort and is ``None`` when
    the LLM is unavailable.
    """
    conn = get_connection()
    try:
        # --- DB: fetch finding, POAM, and system metadata ---
        finding_row = conn.execute(
            "SELECT id, title, severity, vid, component, check_id, detected_at, remediation "
            "FROM stig_findings WHERE id = %s AND project_id = %s",
            (finding_id, project_id),
        ).fetchone()
        poam_row = conn.execute(
            "SELECT id, status, due_date, milestone, owner FROM poam_items "
            "WHERE finding_ref = %s AND project_id = %s LIMIT 1",
            (finding_id, project_id),
        ).fetchone()
        project_row = conn.execute(
            "SELECT name, classification, system_owner FROM projects WHERE id = %s",
            (project_id,),
        ).fetchone()
        open_cat1_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM stig_findings "
            "WHERE project_id = %s AND severity = 'I' AND status = 'open'",
            (project_id,),
        ).fetchone()

        if not finding_row:
            return {"status": "error", "reason": f"finding {finding_id!r} not found"}

        sla = ESCALATION_LEVELS["cat1"]["sla_hours"]
        vars_ = {
            "finding_title": finding_row["title"] or "(untitled)",
            "vid": finding_row["vid"] or "",
            "severity": STIG_SEVERITY_MAP.get(finding_row["severity"] or "I", "CAT I"),
            "component": finding_row["component"] or "unspecified",
            "system_name": (project_row or {}).get("name", project_id),
            "sla_hours": sla,
            "poam_id": (poam_row or {}).get("id", "TBD"),
            "detected_at": finding_row["detected_at"] or _now_iso(),
            "open_cat1_count": int((open_cat1_count or {}).get("cnt", 1)),
        }

        # --- Render: plain text + HTML ---
        rendered = Template(CAT1_ALERT_TEMPLATE).safe_substitute(vars_)
        rendered_html = render_template(
            "alerts/cat1_escalation.html",
            finding=finding_row,
            poam=poam_row,
            project=project_row,
            sla_hours=sla,
            open_count=vars_["open_cat1_count"],
        )
        rendered_sms = render_string(
            "[ICDEV CAT-I] $finding_title in $system_name. POA&M: $poam_id. SLA: $sla_hours h.",
            **vars_,
        )

        # --- AI (optional): synthesize triage narrative; None if unavailable ---
        narrative = _ai_alert_narrative(
            "CAT-I security finding escalation", vars_
        ) if ai_narrative else None

        # --- Notify: page + email + audit ---
        recipients = override_recipients or ["security-ops@icdev.local", "isso@icdev.local"]
        receipts = {}
        for ch in escalation_channels:
            if ch in ("email", "smtp"):
                for rcpt in recipients:
                    sendmail(to=rcpt, subject=f"[CAT-I ESCALATION] {vars_['finding_title']}", html=rendered_html)
                    receipts[f"email:{rcpt}"] = "sent"
            elif ch == "pager":
                send(to="pagerduty", subject="CAT-I", body=rendered_sms)
                receipts["pager"] = "paged"
            elif ch in ("slack", "teams"):
                payload = {"text": rendered, "urgency": "critical", "finding_id": finding_id}
                if narrative:
                    payload["narrative"] = narrative
                publish(ch, payload)
                receipts[ch] = "published"
            elif ch == "audit":
                conn.execute(
                    # audit_trail has no resource_type/resource_id/event/detail —
                    # the live vocabulary is event_type/action/actor/details, so
                    # every one of these writes raised and no CAT1 escalation was
                    # ever audited. The resource id moves into details (swp-scan-01).
                    "INSERT INTO audit_trail (event_type, action, actor, details) "
                    "VALUES ('cat1_escalation', 'escalated', 'alert_service', %s)",
                    (json.dumps({"finding_id": finding_id, "rendered": rendered, "recipients": recipients}),),
                )
                conn.commit()
                receipts["audit"] = "inserted"
            else:
                notify(ch, rendered)
                receipts[ch] = "notified"

        return {
            "status": "escalated",
            "finding_id": finding_id,
            "severity": "cat1",
            "sla_hours": sla,
            "open_cat1_count": vars_["open_cat1_count"],
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def dispatch_stig_alert(
    check_id: str,
    workload_id: str,
    channels: Iterable[str],
    auto_create_poam: bool = True,
    ai_narrative: bool = False,
) -> dict:
    """Query STIG check result, render severity-appropriate alert, and dispatch.

    Looks up the STIG check and workload in the DB, selects the appropriate
    severity template, renders a structured alert with remediation guidance,
    and dispatches to the configured channels. Optionally auto-creates a
    POA&M item for open findings. When ``ai_narrative`` is True, attaches a
    best-effort LLM triage narrative (``None`` if the LLM is unavailable);
    the deterministic template output stays authoritative.
    """
    conn = get_connection()
    try:
        # --- DB: fetch check details and workload info ---
        check_row = conn.execute(
            "SELECT check_id, check_name, severity, status, remediation, reference_url, workload_id "
            "FROM govlift_stig_checks WHERE check_id = %s AND workload_id = %s",
            (check_id, workload_id),
        ).fetchone()
        workload_row = conn.execute(
            "SELECT name, classification, owner FROM govlift_workloads WHERE id = %s",
            (workload_id,),
        ).fetchone()
        # The column is weakness_id, not finding_ref — this dedup lookup raised
        # alongside the INSERT below, so it could never suppress a duplicate
        # POA&M even once the write was fixed (swp-scan-01).
        existing_poam = conn.execute(
            "SELECT id FROM poam_items WHERE weakness_id = %s LIMIT 1",
            (check_id,),
        ).fetchone()

        if not check_row:
            return {"status": "error", "reason": f"STIG check {check_id!r} not found for workload {workload_id!r}"}

        stig_sev = STIG_SEVERITY_MAP.get(check_row["severity"] or "III", "CAT III")
        action_map = {
            "open": "Immediate remediation required",
            "not_reviewed": "Review and apply fix within SLA",
            "not_applicable": "Confirm N/A applicability with ISSO",
            "fixed": "Verify fix and collect evidence",
        }

        vars_ = {
            "check_id": check_id,
            "check_name": check_row["check_name"] or "",
            "stig_severity": stig_sev,
            "severity": check_row["severity"] or "III",
            "system_name": (workload_row or {}).get("name", workload_id),
            "workload_id": workload_id,
            "status": check_row["status"] or "open",
            "action": action_map.get(check_row["status"] or "open", "Review required"),
            "remediation": check_row["remediation"] or "See STIG viewer for guidance",
            "reference_url": check_row["reference_url"] or "",
        }

        # --- Render ---
        rendered = Template(STIG_ALERT_TEMPLATE).safe_substitute(vars_)
        _rendered_html = render_template(
            "alerts/stig_finding.html",
            check=check_row,
            workload=workload_row,
            stig_severity=stig_sev,
        )

        # --- AI (optional): synthesize triage narrative; None if unavailable ---
        narrative = _ai_alert_narrative(
            "STIG check finding alert", vars_
        ) if ai_narrative else None

        # --- Auto-create POA&M if needed ---
        poam_id = None
        if auto_create_poam and not existing_poam and check_row["status"] == "open":
            # Four separate reasons this never created a POA&M (swp-scan-01):
            #   - finding_ref/title/workload_id/due_date are not columns; the live
            #     names are weakness_id/weakness_description/project_id/milestone_date
            #   - project_id and source are NOT NULL with no default, so a pure
            #     rename would still have failed
            #   - DATE('now', '+90 days') is SQLite syntax and does not parse on PG
            #   - psycopg2 does not populate lastrowid, so poam_id was never the
            #     new row's id even in the branch that appeared to succeed
            poam_row = conn.execute(
                "INSERT INTO poam_items "
                "(project_id, weakness_id, weakness_description, severity, source, status, milestone_date) "
                "VALUES (%s, %s, %s, %s, 'stig_check', 'open', CURRENT_DATE + INTERVAL '90 days') "
                "RETURNING id",
                (workload_id, check_id, check_row["check_name"], check_row["severity"]),
            ).fetchone()
            poam_id = dict(poam_row).get("id") if poam_row else None
            conn.commit()

        # --- Dispatch ---
        receipts = {}
        for ch in channels:
            if ch in ("email", "smtp"):
                send(
                    to="compliance@icdev.local",
                    subject=f"[{stig_sev}] STIG Finding: {check_id}",
                    body=rendered,
                )
                receipts["email"] = "sent"
            elif ch in ("slack", "teams"):
                payload = {"text": rendered, "check_id": check_id, "severity": stig_sev}
                if narrative:
                    payload["narrative"] = narrative
                publish(ch, payload)
                receipts[ch] = "published"
            elif ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (event_type, action, actor, details) "
                    "VALUES ('stig_alert', 'dispatched', 'alert_service', %s)",
                    (json.dumps({"check_id": check_id, "rendered": rendered}),),
                )
                conn.commit()
                receipts["audit"] = "inserted"
            else:
                emit("stig.alert", {"check_id": check_id, "severity": stig_sev, "message": rendered})
                receipts[ch] = "emitted"

        return {
            "status": "dispatched",
            "check_id": check_id,
            "severity": stig_sev,
            "poam_id": poam_id,
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def send_poam_reminder(
    poam_id: str,
    project_id: str,
    channels: Iterable[str],
    days_threshold: int = 14,
    ai_narrative: bool = False,
) -> dict:
    """Query POA&M item, render deadline reminder, and deliver to owner.

    Fetches the POA&M item and associated finding from the DB, calculates
    days remaining until the scheduled completion date, renders a priority-
    appropriate reminder, and delivers to the item owner and configured
    channels. When ``ai_narrative`` is True, attaches a best-effort LLM
    triage narrative (``None`` if the LLM is unavailable); the deterministic
    template output stays authoritative.
    """
    conn = get_connection()
    try:
        # --- DB: fetch POA&M item + finding + owner contact ---
        poam_row = conn.execute(
            "SELECT id, title, severity, status, due_date, milestone, owner, finding_ref, framework "
            "FROM poam_items WHERE id = %s AND project_id = %s",
            (poam_id, project_id),
        ).fetchone()
        days_row = conn.execute(
            "SELECT CAST(julianday(due_date) - julianday('now') AS INTEGER) as days_remaining "
            "FROM poam_items WHERE id = %s",
            (poam_id,),
        ).fetchone()
        owner_row = conn.execute(
            "SELECT email, name, phone FROM users WHERE username = %s",
            ((poam_row or {}).get("owner", ""),),
        ).fetchone()
        related_findings = conn.execute(
            "SELECT title, severity FROM stig_findings WHERE id = %s LIMIT 1",
            ((poam_row or {}).get("finding_ref", ""),),
        ).fetchone()

        if not poam_row:
            return {"status": "error", "reason": f"POA&M {poam_id!r} not found"}

        days_remaining = int((days_row or {}).get("days_remaining", 0))
        urgency_map = {True: "critical", False: "high"}
        urgency = urgency_map.get(days_remaining in range(days_threshold), "normal") if days_remaining else "high"

        vars_ = {
            "poam_id": poam_id,
            "title": poam_row["title"] or "(untitled)",
            "project_id": project_id,
            "framework": poam_row["framework"] or "general",
            "severity": poam_row["severity"] or "medium",
            "status": poam_row["status"] or "open",
            "due_date": poam_row["due_date"] or "unknown",
            "days_remaining": days_remaining,
            "owner": poam_row["owner"] or "unassigned",
            "milestone": poam_row["milestone"] or "none",
        }

        # --- Render ---
        rendered = Template(POAM_REMINDER_TEMPLATE).safe_substitute(vars_)
        rendered_html = render_template(
            "alerts/poam_reminder.html",
            poam=poam_row,
            days_remaining=days_remaining,
            urgency=urgency,
            owner=owner_row,
            finding=related_findings,
        )
        rendered_brief = render_to_string("alerts/poam_sms.txt", vars_)

        # --- AI (optional): synthesize triage narrative; None if unavailable ---
        narrative = _ai_alert_narrative(
            "POA&M deadline reminder", vars_
        ) if ai_narrative else None

        # --- Deliver ---
        receipts = {}
        owner_email = (owner_row or {}).get("email", "compliance@icdev.local")
        for ch in channels:
            if ch in ("email", "smtp"):
                sendmail(
                    to=owner_email,
                    subject=f"[POA&M REMINDER] {vars_['title']} — {days_remaining}d remaining",
                    html=rendered_html,
                )
                receipts[f"email:{owner_email}"] = "sent"
            elif ch in ("slack", "teams"):
                payload = {"text": rendered, "urgency": urgency, "poam_id": poam_id}
                if narrative:
                    payload["narrative"] = narrative
                dispatch(ch, payload)
                receipts[ch] = "dispatched"
            elif ch == "audit":
                conn.execute(
                    "INSERT INTO audit_trail (event_type, action, actor, details) "
                    "VALUES ('poam_reminder', 'reminder_sent', 'system', %s)",
                    (json.dumps({"poam_id": poam_id, "rendered": rendered}),),
                )
                conn.commit()
                receipts["audit"] = "inserted"
            elif ch == "sms" and urgency == "critical":
                send(to=(owner_row or {}).get("phone", ""), subject="", body=rendered_brief)
                receipts["sms"] = "sent"
            else:
                notify(ch, rendered)
                receipts[ch] = "notified"

        return {
            "status": "delivered",
            "poam_id": poam_id,
            "days_remaining": days_remaining,
            "urgency": urgency,
            "owner": poam_row["owner"],
            "rendered": rendered,
            "narrative": narrative,
            "receipts": receipts,
        }
    finally:
        conn.close()


def _compute_poam_deadline_threshold(cfg: dict | None) -> int:
    """Adaptive POA&M deadline threshold from historical remediation times.

    Config keys:
      enabled: bool
      min_samples: int
      fallback_days_threshold: int
      adaptive_bounds: {"threshold_floor": int, "threshold_ceil": int}
    """
    cfg = cfg or {}
    if not cfg.get("enabled", True):
        return int(cfg.get("fallback_days_threshold", _POAM_DAYS_THRESHOLD))
    min_samples = int(cfg.get("min_samples", 5))
    fallback = int(cfg.get("fallback_days_threshold", _POAM_DAYS_THRESHOLD))
    bounds = cfg.get("adaptive_bounds", {}) or {}
    floor = int(bounds.get("threshold_floor", 3))
    ceil = int(bounds.get("threshold_ceil", 60))
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT AVG(julianday(completed_at) - julianday(detected_at)) as avg_days, COUNT(*) as n "
                "FROM poam_items WHERE status = 'closed' AND completed_at IS NOT NULL"
            ).fetchone()
            avg_days = float((row or {}).get("avg_days", 0.0) or 0.0)
            n = int((row or {}).get("n", 0) or 0)
            if n < min_samples:
                return fallback
            threshold = int(avg_days * 0.5)
            return max(floor, min(ceil, threshold))
        finally:
            conn.close()
    except Exception:
        return fallback


def send_security_alert_digest(
    recipient: str,
    project_id: str | None = None,
    days: int = 7,
    ai_narrative: bool = False,
) -> dict:
    """Fetch recent security alerts, render a digest summary, and deliver.

    Aggregates open CAT-I findings, recent STIG CAT-I/II open checks, and
    POA&M items due within ``days`` days into a single digest and delivers to
    ``recipient``. When ``ai_narrative`` is True, attaches a best-effort LLM
    triage narrative (``None`` if unavailable); the deterministic summary
    remains authoritative.
    """
    conn = get_connection()
    try:
        # --- DB: fetch open CAT-I findings ---
        cat1_rows = conn.execute(
            "SELECT id, title, severity, vid, component, detected_at "
            "FROM stig_findings WHERE severity = 'I' AND status = 'open'"
            + (" AND project_id = ?" if project_id else "")
            + " ORDER BY detected_at DESC LIMIT 20",
            (project_id,) if project_id else (),
        ).fetchall()

        # --- DB: fetch open STIG CAT-I/II checks ---
        stig_rows = conn.execute(
            "SELECT check_id, check_name, severity, status "
            "FROM govlift_stig_checks WHERE severity IN ('I','II') AND status = 'open' "
            "ORDER BY severity, check_id LIMIT 20",
        ).fetchall()

        # --- DB: fetch POA&M items due within the window ---
        poam_rows = conn.execute(
            "SELECT id, title, severity, due_date, owner, status "
            "FROM poam_items WHERE status = 'open' "
            "AND julianday(due_date) - julianday('now') BETWEEN 0 AND ?"
            + (" AND project_id = ?" if project_id else ""),
            (days, project_id) if project_id else (days,),
        ).fetchall()

        cat1_count = len(cat1_rows)
        stig_count = len(stig_rows)
        poam_due_count = len(poam_rows)

        vars_ = {
            "cat1_count": cat1_count,
            "days_window": days,
            "poam_due_count": poam_due_count,
            "project_id": project_id or "all",
            "stig_critical_count": stig_count,
        }

        # --- Render ---
        rendered = render_template(
            "alerts/security_alert_digest.html",
            cat1_findings=cat1_rows,
            stig_checks=stig_rows,
            poam_items=poam_rows,
            days=days,
        )

        # --- AI (optional): synthesize narrative; None if unavailable ---
        narrative = _ai_alert_narrative(
            "security alert digest summary", vars_
        ) if ai_narrative else None

        # --- Deliver ---
        sendmail(
            to=recipient,
            subject=(
                f"[SECURITY DIGEST] {cat1_count} CAT-I findings, "
                f"{poam_due_count} POA&M items due in {days}d"
            ),
            html=rendered,
        )
        payload = {**vars_, "recipient": recipient}
        if narrative:
            payload["narrative"] = narrative
        emit("security.alert_digest_sent", payload)

        return {
            "status": "sent",
            "recipient": recipient,
            "cat1_count": cat1_count,
            "stig_critical_count": stig_count,
            "poam_due_count": poam_due_count,
            "narrative": narrative,
        }
    finally:
        conn.close()
