
# [TEMPLATE: CUI // SP-CTI]
"""Automated incident lifecycle management for ICDEV™ SRE module.

Handles incident creation, triage, escalation, resolution, postmortem,
and MTTR statistics tracking.
"""

import argparse
import json
import logging
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

import yaml  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

CONFIG_PATH = BASE_DIR / "args" / "sre_config.yaml"

SEVERITIES = ("sev1", "sev2", "sev3", "sev4")
INCIDENT_STATUSES = ("detected", "triaging", "mitigating", "resolved", "postmortem", "closed")
EVENT_TYPES = ("created", "triaged", "escalated", "mitigated", "resolved", "postmortem_added", "closed", "note_added")


def _load_config() -> dict:
    """Load SRE configuration from args/sre_config.yaml."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_incident_config() -> dict:
    """Get incident-specific config with defaults."""
    cfg = _load_config().get("incidents", {})
    return {
        "auto_create_from_alerts": cfg.get("auto_create_from_alerts", True),
        "auto_escalate_sev1": cfg.get("auto_escalate_sev1", True),
        "escalation_timeout_minutes": cfg.get("escalation_timeout_minutes", 15),
        "postmortem_required_severity": cfg.get("postmortem_required_severity", ["sev1", "sev2"]),
        "mttr_targets": cfg.get(
            "mttr_targets",
            {
                "sev1": 900,
                "sev2": 3600,
                "sev3": 14400,
                "sev4": 86400,
            },
        ),
    }


def init_tables(conn: sqlite3.Connection) -> None:
    """Create SRE incident tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sre_incidents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            severity TEXT NOT NULL CHECK(severity IN ('sev1', 'sev2', 'sev3', 'sev4')),
            status TEXT NOT NULL DEFAULT 'detected' CHECK(status IN ('detected', 'triaging', 'mitigating', 'resolved', 'postmortem', 'closed')),
            service_name TEXT NOT NULL,
            alert_source TEXT,
            alert_id TEXT,
            root_cause TEXT,
            mitigation_steps TEXT,
            timeline_json TEXT NOT NULL DEFAULT '[]',
            assigned_to TEXT,
            escalated INTEGER NOT NULL DEFAULT 0,
            mttr_seconds INTEGER,
            postmortem_url TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            closed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sre_incident_events (
            id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN ('created', 'triaged', 'escalated', 'mitigated', 'resolved', 'postmortem_added', 'closed', 'note_added')),
            description TEXT,
            actor TEXT NOT NULL DEFAULT 'system',
            created_at TEXT NOT NULL,
            FOREIGN KEY (incident_id) REFERENCES sre_incidents(id)
        );
    """)
    conn.commit()


def _add_event(
    conn: sqlite3.Connection, incident_id: str, event_type: str, description: str, actor: str = "system"
) -> str:
    """Add an event to an incident's event log (append-only).

    Returns:
        Event ID.
    """
    event_id = f"iev-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO sre_incident_events
           (id, incident_id, event_type, description, actor, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (event_id, incident_id, event_type, description, actor, now),
    )

    # Also append to timeline_json on the incident
    timeline = conn.execute(
        "SELECT timeline_json FROM sre_incidents WHERE id = %s",
        (incident_id,),
    ).fetchone()

    if timeline:
        tl = json.loads(timeline[0]) if timeline[0] else []
        tl.append(
            {
                "event_id": event_id,
                "event_type": event_type,
                "description": description,
                "actor": actor,
                "timestamp": now,
            }
        )
        conn.execute(
            "UPDATE sre_incidents SET timeline_json = %s WHERE id = %s",
            (json.dumps(tl), incident_id),
        )

    return event_id


def create_incident(title: str, severity: str, service: str, alert_source: str = None) -> dict:
    """Create a new incident.

    Args:
        title: Incident title.
        severity: sev1, sev2, sev3, or sev4.
        service: Affected service name.
        alert_source: What triggered the incident (optional).

    Returns:
        dict with created incident details.
    """
    if severity not in SEVERITIES:
        return {"status": "error", "message": f"Invalid severity: {severity}. Must be one of {SEVERITIES}"}

    cfg = _get_incident_config()
    incident_id = f"inc-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    init_tables(conn)

    initial_timeline = json.dumps(
        [
            {
                "event_type": "created",
                "description": f"Incident created: {title}",
                "actor": "system",
                "timestamp": now,
            }
        ]
    )

    conn.execute(
        """INSERT INTO sre_incidents
           (id, title, severity, status, service_name, alert_source,
            timeline_json, created_at)
           VALUES (%s, %s, %s, 'detected', %s, %s, %s, %s)""",
        (incident_id, title, severity, service, alert_source, initial_timeline, now),
    )

    _add_event(conn, incident_id, "created", f"Incident created: {title} (severity={severity}, service={service})")

    # Auto-escalate sev1 if configured
    escalated = False
    if severity == "sev1" and cfg["auto_escalate_sev1"]:
        conn.execute(
            "UPDATE sre_incidents SET escalated = 1 WHERE id = %s",
            (incident_id,),
        )
        _add_event(conn, incident_id, "escalated", "Auto-escalated: sev1 incident")
        escalated = True

    conn.commit()

    logger.info("Created incident %s: %s (severity=%s)", incident_id, title, severity)
    return {
        "status": "ok",
        "incident_id": incident_id,
        "title": title,
        "severity": severity,
        "service_name": service,
        "incident_status": "detected",
        "escalated": escalated,
    }


def triage_incident(incident_id: str, root_cause: str = None) -> dict:
    """Triage an incident, optionally setting root cause.

    Args:
        incident_id: Incident identifier.
        root_cause: Identified root cause (optional).

    Returns:
        dict with updated incident details.
    """
    conn = get_connection()
    init_tables(conn)

    row = conn.execute(
        "SELECT id, status FROM sre_incidents WHERE id = %s",
        (incident_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Incident not found: {incident_id}"}

    now = datetime.now(timezone.utc).isoformat()

    update_fields = ["status = 'triaging'", "updated_at = ?"]
    params = [now]

    if root_cause:
        update_fields.append("root_cause = ?")
        params.append(root_cause)

    # SQLite doesn't have updated_at column, just use timeline
    conn.execute(
        f"UPDATE sre_incidents SET status = 'triaging'{', root_cause = ?' if root_cause else ''} WHERE id = %s",  # nosec B608
        ([root_cause, incident_id] if root_cause else [incident_id]),
    )

    description = "Incident triaged"
    if root_cause:
        description += f": {root_cause}"
    _add_event(conn, incident_id, "triaged", description)

    conn.commit()

    logger.info("Triaged incident %s", incident_id)
    return {
        "status": "ok",
        "incident_id": incident_id,
        "incident_status": "triaging",
        "root_cause": root_cause,
    }


def escalate_incident(incident_id: str, reason: str) -> dict:
    """Escalate an incident.

    Args:
        incident_id: Incident identifier.
        reason: Reason for escalation.

    Returns:
        dict with updated incident details.
    """
    conn = get_connection()
    init_tables(conn)

    row = conn.execute(
        "SELECT id, status, severity FROM sre_incidents WHERE id = %s",
        (incident_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Incident not found: {incident_id}"}

    conn.execute(
        "UPDATE sre_incidents SET escalated = 1 WHERE id = %s",
        (incident_id,),
    )
    _add_event(conn, incident_id, "escalated", f"Escalated: {reason}")
    conn.commit()

    logger.info("Escalated incident %s: %s", incident_id, reason)
    return {
        "status": "ok",
        "incident_id": incident_id,
        "escalated": True,
        "reason": reason,
    }


def resolve_incident(incident_id: str, mitigation_steps: list) -> dict:
    """Resolve an incident with mitigation steps.

    Args:
        incident_id: Incident identifier.
        mitigation_steps: List of steps taken to mitigate.

    Returns:
        dict with updated incident details including MTTR.
    """
    conn = get_connection()
    init_tables(conn)

    row = conn.execute(
        "SELECT id, status, created_at FROM sre_incidents WHERE id = %s",
        (incident_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Incident not found: {incident_id}"}

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    created_at = datetime.fromisoformat(row[2].replace("Z", "+00:00"))

    # Calculate MTTR
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    mttr_seconds = int((now - created_at).total_seconds())

    conn.execute(
        """UPDATE sre_incidents
           SET status = 'resolved', mitigation_steps = %s, resolved_at = %s,
               mttr_seconds = %s
           WHERE id = %s""",
        (json.dumps(mitigation_steps), now_iso, mttr_seconds, incident_id),
    )

    steps_desc = "; ".join(mitigation_steps) if mitigation_steps else "No steps recorded"
    _add_event(conn, incident_id, "resolved", f"Resolved (MTTR: {mttr_seconds}s). Steps: {steps_desc}")
    conn.commit()

    logger.info("Resolved incident %s (MTTR: %ds)", incident_id, mttr_seconds)
    return {
        "status": "ok",
        "incident_id": incident_id,
        "incident_status": "resolved",
        "mttr_seconds": mttr_seconds,
        "mitigation_steps": mitigation_steps,
    }


def add_postmortem(incident_id: str, url: str, lessons_learned: str) -> dict:
    """Add a postmortem to a resolved incident.

    Args:
        incident_id: Incident identifier.
        url: URL to the postmortem document.
        lessons_learned: Summary of lessons learned.

    Returns:
        dict with updated incident details.
    """
    conn = get_connection()
    init_tables(conn)

    row = conn.execute(
        "SELECT id, status FROM sre_incidents WHERE id = %s",
        (incident_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Incident not found: {incident_id}"}

    conn.execute(
        "UPDATE sre_incidents SET status = 'postmortem', postmortem_url = %s WHERE id = %s",
        (url, incident_id),
    )
    _add_event(conn, incident_id, "postmortem_added", f"Postmortem added: {url}. Lessons: {lessons_learned}")
    conn.commit()

    logger.info("Added postmortem to incident %s", incident_id)
    return {
        "status": "ok",
        "incident_id": incident_id,
        "incident_status": "postmortem",
        "postmortem_url": url,
    }


def close_incident(incident_id: str) -> dict:
    """Close an incident.

    Args:
        incident_id: Incident identifier.

    Returns:
        dict with closed incident details.
    """
    conn = get_connection()
    init_tables(conn)

    row = conn.execute(
        "SELECT id, status FROM sre_incidents WHERE id = %s",
        (incident_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Incident not found: {incident_id}"}

    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "UPDATE sre_incidents SET status = 'closed', closed_at = %s WHERE id = %s",
        (now, incident_id),
    )
    _add_event(conn, incident_id, "closed", "Incident closed")
    conn.commit()

    logger.info("Closed incident %s", incident_id)
    return {
        "status": "ok",
        "incident_id": incident_id,
        "incident_status": "closed",
        "closed_at": now,
    }


def get_incident(incident_id: str) -> dict:
    """Get full details of an incident.

    Args:
        incident_id: Incident identifier.

    Returns:
        dict with full incident details including timeline and events.
    """
    conn = get_connection()
    init_tables(conn)

    cols = [
        "id",
        "title",
        "severity",
        "status",
        "service_name",
        "alert_source",
        "alert_id",
        "root_cause",
        "mitigation_steps",
        "timeline_json",
        "assigned_to",
        "escalated",
        "mttr_seconds",
        "postmortem_url",
        "created_at",
        "resolved_at",
        "closed_at",
    ]

    row = conn.execute(
        f"SELECT {', '.join(cols)} FROM sre_incidents WHERE id = %s",  # nosec B608
        (incident_id,),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"Incident not found: {incident_id}"}

    incident = dict(zip(cols, row))

    # Parse JSON fields
    if incident["mitigation_steps"]:
        incident["mitigation_steps"] = json.loads(incident["mitigation_steps"])
    if incident["timeline_json"]:
        incident["timeline_json"] = json.loads(incident["timeline_json"])

    incident["escalated"] = bool(incident["escalated"])

    # Get events
    events = conn.execute(
        """SELECT id, event_type, description, actor, created_at
           FROM sre_incident_events
           WHERE incident_id = %s
           ORDER BY created_at""",
        (incident_id,),
    ).fetchall()

    event_cols = ["id", "event_type", "description", "actor", "created_at"]
    incident["events"] = [dict(zip(event_cols, e)) for e in events]

    return incident


def list_incidents(status_filter: str = None) -> list:
    """List incidents, optionally filtered by status.

    Args:
        status_filter: If "open", shows detected/triaging/mitigating.
                       If a specific status, filters to that status.

    Returns:
        list of incident dicts.
    """
    conn = get_connection()
    init_tables(conn)

    cols = [
        "id",
        "title",
        "severity",
        "status",
        "service_name",
        "escalated",
        "mttr_seconds",
        "created_at",
        "resolved_at",
    ]

    query = f"SELECT {', '.join(cols)} FROM sre_incidents"  # nosec B608
    params = []

    if status_filter == "open":
        query += " WHERE status IN ('detected', 'triaging', 'mitigating')"
    elif status_filter and status_filter in INCIDENT_STATUSES:
        query += " WHERE status = ?"
        params.append(status_filter)

    query += " ORDER BY CASE severity WHEN 'sev1' THEN 0 WHEN 'sev2' THEN 1 WHEN 'sev3' THEN 2 WHEN 'sev4' THEN 3 END, created_at DESC"

    rows = conn.execute(query, params).fetchall()

    incidents = []
    for row in rows:
        inc = dict(zip(cols, row))
        inc["escalated"] = bool(inc["escalated"])
        incidents.append(inc)

    return incidents


def get_mttr_stats() -> dict:
    """Get MTTR (Mean Time To Resolve) statistics by severity.

    Returns:
        dict with MTTR stats per severity and target comparison.
    """
    conn = get_connection()
    init_tables(conn)

    cfg = _get_incident_config()
    targets = cfg["mttr_targets"]

    stats = {}
    for sev in SEVERITIES:
        rows = conn.execute(
            """SELECT mttr_seconds FROM sre_incidents
               WHERE severity = %s AND mttr_seconds IS NOT NULL
               ORDER BY created_at DESC""",
            (sev,),
        ).fetchall()

        mttr_values = [r[0] for r in rows]

        if mttr_values:
            avg_mttr = sum(mttr_values) / len(mttr_values)
            min_mttr = min(mttr_values)
            max_mttr = max(mttr_values)
            target = targets.get(sev, 0)
            meeting_target = avg_mttr <= target if target > 0 else True

            stats[sev] = {
                "count": len(mttr_values),
                "avg_seconds": round(avg_mttr, 1),
                "min_seconds": min_mttr,
                "max_seconds": max_mttr,
                "target_seconds": target,
                "meeting_target": meeting_target,
            }
        else:
            stats[sev] = {
                "count": 0,
                "avg_seconds": None,
                "target_seconds": targets.get(sev, 0),
                "meeting_target": True,
            }

    # Overall pass/fail
    any_missing_target = any(not s["meeting_target"] for s in stats.values() if s["count"] > 0)

    return {
        "status": "ok",
        "mttr_by_severity": stats,
        "all_targets_met": not any_missing_target,
    }


def check_incident_health() -> dict:
    """Gate check for incident system health.

    Fails if there are open sev1/sev2 incidents or MTTR targets are not met.

    Returns:
        dict with gate result.
    """
    conn = get_connection()
    init_tables(conn)

    # Check for open high-severity incidents
    open_high = conn.execute(
        """SELECT id, title, severity, status, created_at
           FROM sre_incidents
           WHERE severity IN ('sev1', 'sev2')
             AND status IN ('detected', 'triaging', 'mitigating')
           ORDER BY CASE severity WHEN 'sev1' THEN 0 ELSE 1 END""",
    ).fetchall()

    failing = []
    for row in open_high:
        failing.append(
            {
                "incident_id": row[0],
                "title": row[1],
                "severity": row[2],
                "status": row[3],
                "created_at": row[4],
            }
        )

    # Check MTTR targets
    mttr = get_mttr_stats()
    mttr_issues = []
    for sev, data in mttr.get("mttr_by_severity", {}).items():
        if data["count"] > 0 and not data["meeting_target"]:
            mttr_issues.append(
                {
                    "severity": sev,
                    "avg_mttr_seconds": data["avg_seconds"],
                    "target_seconds": data["target_seconds"],
                }
            )

    passed = len(failing) == 0 and len(mttr_issues) == 0
    return {
        "gate": "incident_health",
        "passed": passed,
        "open_high_severity": failing,
        "mttr_issues": mttr_issues,
    }


def main():
    """CLI entry point for incident commander."""
    parser = argparse.ArgumentParser(description="ICDEV™ SRE Incident Commander")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--gate", action="store_true", help="Run gate check (exit 1 if open sev1/sev2 or MTTR issues)")

    # Actions
    parser.add_argument("--create", action="store_true", help="Create a new incident")
    parser.add_argument("--triage", action="store_true", help="Triage an incident")
    parser.add_argument("--escalate", action="store_true", help="Escalate an incident")
    parser.add_argument("--resolve", action="store_true", help="Resolve an incident")
    parser.add_argument("--postmortem", action="store_true", help="Add postmortem to incident")
    parser.add_argument("--close", action="store_true", help="Close an incident")
    parser.add_argument("--get", action="store_true", help="Get incident details")
    parser.add_argument("--list", action="store_true", help="List incidents")
    parser.add_argument("--mttr", action="store_true", help="Show MTTR statistics")

    # Common args
    parser.add_argument("--id", type=str, help="Incident ID")
    parser.add_argument("--title", type=str, help="Incident title")
    parser.add_argument("--severity", type=str, help="Incident severity (sev1-sev4)")
    parser.add_argument("--service", type=str, help="Affected service")
    parser.add_argument("--alert-source", type=str, help="Alert source")
    parser.add_argument("--root-cause", type=str, help="Root cause description")
    parser.add_argument("--reason", type=str, help="Escalation reason")
    parser.add_argument("--steps", type=str, help="Mitigation steps (JSON array)")
    parser.add_argument("--url", type=str, help="Postmortem URL")
    parser.add_argument("--lessons", type=str, help="Lessons learned")
    parser.add_argument("--status", type=str, help="Filter by status (open, detected, resolved, etc.)")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.gate:
        result = check_incident_health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status_str = "PASSED" if result["passed"] else "FAILED"
            print(f"Incident Health Gate: {status_str}")
            if result["open_high_severity"]:
                print("  Open high-severity incidents:")
                for inc in result["open_high_severity"]:
                    print(f"    - [{inc['severity']}] {inc['title']} ({inc['status']})")
            if result["mttr_issues"]:
                print("  MTTR target issues:")
                for issue in result["mttr_issues"]:
                    print(
                        f"    - {issue['severity']}: avg {issue['avg_mttr_seconds']}s > target {issue['target_seconds']}s"
                    )
        if not result["passed"]:
            sys.exit(1)

    elif args.create:
        if not all([args.title, args.severity, args.service]):
            parser.error("--create requires --title, --severity, and --service")
        result = create_incident(args.title, args.severity, args.service, args.alert_source)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Created incident: {result.get('incident_id')} [{result.get('severity')}] {result.get('title')}")
            if result.get("escalated"):
                print("  ** Auto-escalated (sev1)")

    elif args.triage:
        if not args.id:
            parser.error("--triage requires --id")
        result = triage_incident(args.id, args.root_cause)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Triaged incident {args.id}")

    elif args.escalate:
        if not args.id or not args.reason:
            parser.error("--escalate requires --id and --reason")
        result = escalate_incident(args.id, args.reason)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Escalated incident {args.id}: {args.reason}")

    elif args.resolve:
        if not args.id:
            parser.error("--resolve requires --id")
        steps = json.loads(args.steps) if args.steps else []
        result = resolve_incident(args.id, steps)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Resolved incident {args.id} (MTTR: {result.get('mttr_seconds')}s)")

    elif args.postmortem:
        if not all([args.id, args.url]):
            parser.error("--postmortem requires --id and --url")
        result = add_postmortem(args.id, args.url, args.lessons or "")
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Added postmortem to incident {args.id}")

    elif args.close:
        if not args.id:
            parser.error("--close requires --id")
        result = close_incident(args.id)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Closed incident {args.id}")

    elif args.get:
        if not args.id:
            parser.error("--get requires --id")
        result = get_incident(args.id)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Incident {result.get('id')}: {result.get('title')}")
            print(f"  Severity: {result.get('severity')}, Status: {result.get('status')}")
            print(f"  Service: {result.get('service_name')}")
            if result.get("root_cause"):
                print(f"  Root cause: {result.get('root_cause')}")
            if result.get("mttr_seconds"):
                print(f"  MTTR: {result.get('mttr_seconds')}s")

    elif args.list:
        incidents = list_incidents(args.status)
        if args.json:
            print(json.dumps(incidents, indent=2))
        else:
            print(f"Incidents ({len(incidents)}):")
            for inc in incidents:
                esc = " [ESCALATED]" if inc["escalated"] else ""
                print(f"  [{inc['severity']}] {inc['id']} {inc['title']} ({inc['status']}){esc}")

    elif args.mttr:
        result = get_mttr_stats()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("MTTR Statistics:")
            for sev, data in result["mttr_by_severity"].items():
                if data["count"] > 0:
                    met = "MET" if data["meeting_target"] else "MISSED"
                    print(
                        f"  {sev}: avg={data['avg_seconds']}s, target={data['target_seconds']}s [{met}] (n={data['count']})"
                    )
                else:
                    print(f"  {sev}: no resolved incidents")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
