#!/usr/bin/env python3
"""Alert correlation engine. Groups related alerts by service and time window,
deduplicates alerts from multiple sources (ELK, Splunk, Prometheus),
and escalates incidents when necessary."""

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_time_window(window: str) -> timedelta:
    """Parse time window string like '5m', '1h', '30s' into timedelta."""
    match = re.match(r'^(\d+)([smhd])$', window)
    if not match:
        raise ValueError(f"Invalid time window: {window}. Use format: 30s, 5m, 1h, 1d")
    value, unit = int(match.group(1)), match.group(2)
    unit_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    return timedelta(**{unit_map[unit]: value})


def _normalize_alert(alert: dict) -> dict:
    """Normalize an alert dict from various sources into a common format."""
    return {
        "id": alert.get("id"),
        "title": alert.get("title") or alert.get("alertname") or alert.get("name") or "",
        "description": alert.get("description") or alert.get("message") or alert.get("summary") or "",
        "severity": (
            alert.get("severity") or alert.get("priority") or "warning"
        ).lower(),
        "source": alert.get("source") or "unknown",
        "service": (
            alert.get("service") or alert.get("job") or
            alert.get("namespace") or alert.get("host") or "unknown"
        ),
        "status": alert.get("status") or "firing",
        "timestamp": alert.get("created_at") or alert.get("timestamp") or alert.get("startsAt") or "",
        "labels": alert.get("labels", {}),
        "raw": alert,
    }


def _similarity(a: str, b: str) -> float:
    """Simple Jaccard similarity on word sets."""
    if not a or not b:
        return 0.0
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------
def correlate(alerts: list, time_window: timedelta = None) -> list:
    """Group related alerts by service and time window.

    Correlation rules:
    1. Same service within time window -> grouped
    2. Similar alert title (>40% word overlap) within time window -> grouped
    3. Same root cause label -> grouped

    Returns:
        List of incident groups, each containing correlated alerts.
    """
    if time_window is None:
        time_window = timedelta(minutes=5)

    normalized = [_normalize_alert(a) for a in alerts]

    # Parse timestamps
    for alert in normalized:
        ts = alert["timestamp"]
        if isinstance(ts, str) and ts:
            try:
                alert["_dt"] = datetime.fromisoformat(
                    ts.replace("Z", "+00:00").replace("+00:00", "")
                )
            except ValueError:
                alert["_dt"] = datetime.utcnow()
        else:
            alert["_dt"] = datetime.utcnow()

    # Sort by timestamp
    normalized.sort(key=lambda a: a["_dt"])

    # Group into incidents
    incidents = []
    used = set()

    for i, alert in enumerate(normalized):
        if i in used:
            continue

        group = {
            "primary_alert": alert,
            "related_alerts": [],
            "services": {alert["service"]},
            "sources": {alert["source"]},
            "start_time": alert["_dt"],
            "end_time": alert["_dt"],
            "max_severity": alert["severity"],
        }
        used.add(i)

        severity_rank = {"critical": 4, "high": 3, "warning": 2, "low": 1, "info": 0}

        for j, other in enumerate(normalized):
            if j in used:
                continue

            time_diff = abs((other["_dt"] - alert["_dt"]).total_seconds())
            if time_diff > time_window.total_seconds():
                continue

            is_related = False

            # Same service
            if other["service"] == alert["service"]:
                is_related = True

            # Similar title
            if _similarity(other["title"], alert["title"]) > 0.4:
                is_related = True

            # Same description keywords
            if _similarity(other["description"], alert["description"]) > 0.5:
                is_related = True

            # Matching labels (e.g., same pod, same namespace)
            shared_labels = set(alert.get("labels", {}).items()) & set(other.get("labels", {}).items())
            if len(shared_labels) >= 2:
                is_related = True

            if is_related:
                group["related_alerts"].append(other)
                group["services"].add(other["service"])
                group["sources"].add(other["source"])
                used.add(j)

                if other["_dt"] > group["end_time"]:
                    group["end_time"] = other["_dt"]
                if other["_dt"] < group["start_time"]:
                    group["start_time"] = other["_dt"]

                # Track max severity
                if severity_rank.get(other["severity"], 0) > severity_rank.get(group["max_severity"], 0):
                    group["max_severity"] = other["severity"]

        incidents.append({
            "incident_id": f"INC-{i+1:04d}",
            "title": alert["title"],
            "severity": group["max_severity"],
            "services": list(group["services"]),
            "sources": list(group["sources"]),
            "alert_count": 1 + len(group["related_alerts"]),
            "start_time": group["start_time"].isoformat(),
            "end_time": group["end_time"].isoformat(),
            "duration_seconds": (group["end_time"] - group["start_time"]).total_seconds(),
            "primary_alert": {
                k: v for k, v in alert.items() if k not in ("_dt", "raw")
            },
            "related_alerts": [
                {k: v for k, v in a.items() if k not in ("_dt", "raw")}
                for a in group["related_alerts"]
            ],
        })

    # Sort by severity then by alert count
    severity_rank = {"critical": 4, "high": 3, "warning": 2, "low": 1, "info": 0}
    incidents.sort(
        key=lambda x: (severity_rank.get(x["severity"], 0), x["alert_count"]),
        reverse=True,
    )

    return incidents


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def deduplicate(alerts: list) -> list:
    """Remove duplicate alerts from multiple sources.
    Two alerts are duplicates if they have:
    - Same service AND same title (or >80% similar title)
    - Within 2 minutes of each other

    Returns:
        Deduplicated list of alerts with source tracking.
    """
    normalized = [_normalize_alert(a) for a in alerts]

    # Parse timestamps
    for alert in normalized:
        ts = alert["timestamp"]
        if isinstance(ts, str) and ts:
            try:
                alert["_dt"] = datetime.fromisoformat(
                    ts.replace("Z", "+00:00").replace("+00:00", "")
                )
            except ValueError:
                alert["_dt"] = datetime.utcnow()
        else:
            alert["_dt"] = datetime.utcnow()

    deduped = []
    used = set()

    for i, alert in enumerate(normalized):
        if i in used:
            continue

        merged = dict(alert)
        merged["duplicate_sources"] = [alert["source"]]
        merged["duplicate_count"] = 1

        for j, other in enumerate(normalized):
            if j in used or j == i:
                continue

            # Check time proximity (2 minutes)
            time_diff = abs((other["_dt"] - alert["_dt"]).total_seconds())
            if time_diff > 120:
                continue

            # Check similarity
            is_dup = False
            if other["service"] == alert["service"]:
                if other["title"] == alert["title"]:
                    is_dup = True
                elif _similarity(other["title"], alert["title"]) > 0.8:
                    is_dup = True

            if is_dup:
                merged["duplicate_sources"].append(other["source"])
                merged["duplicate_count"] += 1
                used.add(j)

                # Keep highest severity
                sev_rank = {"critical": 4, "high": 3, "warning": 2, "low": 1}
                if sev_rank.get(other["severity"], 0) > sev_rank.get(merged["severity"], 0):
                    merged["severity"] = other["severity"]

        used.add(i)
        deduped.append({
            k: v for k, v in merged.items() if k not in ("_dt", "raw")
        })

    return deduped


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------
def escalate(incident: dict, db_path: Path = None) -> dict:
    """Log an escalation for an incident in the audit trail.

    Args:
        incident: Incident dict from correlate()
        db_path: Override database path

    Returns:
        Escalation record.
    """
    conn = _get_db(db_path)
    now = datetime.utcnow().isoformat()

    escalation = {
        "incident_id": incident.get("incident_id", "unknown"),
        "severity": incident.get("severity", "unknown"),
        "title": incident.get("title", "Unknown incident"),
        "services": incident.get("services", []),
        "alert_count": incident.get("alert_count", 0),
        "escalated_at": now,
        "escalation_reason": _determine_escalation_reason(incident),
    }

    try:
        # Record in audit trail
        project_id = None
        if incident.get("services"):
            project_id = incident["services"][0]

        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "self_heal_triggered",
                "alert_correlator",
                f"Escalation: {escalation['title']}",
                json.dumps(escalation),
                "CUI",
            ),
        )

        # Also create an alert record if it doesn't exist
        conn.execute(
            """INSERT INTO alerts
               (project_id, severity, source, title, description, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                incident.get("severity", "warning"),
                "alert_correlator",
                f"[ESCALATED] {escalation['title']}",
                json.dumps(escalation),
                "escalated",
                now,
            ),
        )

        conn.commit()
        escalation["recorded"] = True

    except Exception as e:
        escalation["recorded"] = False
        escalation["error"] = str(e)
    finally:
        conn.close()

    return escalation


def _determine_escalation_reason(incident: dict) -> str:
    """Determine why this incident should be escalated."""
    reasons = []

    severity = incident.get("severity", "").lower()
    if severity == "critical":
        reasons.append("Critical severity alert")

    alert_count = incident.get("alert_count", 0)
    if alert_count > 5:
        reasons.append(f"High alert volume ({alert_count} alerts)")

    services = incident.get("services", [])
    if len(services) > 2:
        reasons.append(f"Multi-service impact ({len(services)} services)")

    duration = incident.get("duration_seconds", 0)
    if duration > 600:
        reasons.append(f"Long duration ({int(duration)}s)")

    return "; ".join(reasons) if reasons else "Standard escalation"


# ---------------------------------------------------------------------------
# Load alerts from database
# ---------------------------------------------------------------------------
def _load_alerts_from_db(
    project_id: str,
    time_window: timedelta,
    db_path: Path = None,
) -> list:
    """Load alerts from the database for a given project and time window."""
    conn = _get_db(db_path)
    try:
        start = (datetime.utcnow() - time_window).isoformat()
        rows = conn.execute(
            """SELECT id, project_id, severity, source, title, description,
                      status, created_at
               FROM alerts
               WHERE project_id = ? AND created_at > ?
               ORDER BY created_at DESC""",
            (project_id, start),
        ).fetchall()

        return [
            {
                "id": r["id"],
                "title": r["title"],
                "description": r["description"],
                "severity": r["severity"],
                "source": r["source"],
                "service": r["project_id"],
                "status": r["status"],
                "created_at": r["created_at"],
                "timestamp": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Alert correlation engine")
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument("--time-window", default="5m", help="Correlation time window (e.g., 5m, 1h)")
    parser.add_argument("--deduplicate", action="store_true", help="Run deduplication")
    parser.add_argument("--escalate-critical", action="store_true", help="Auto-escalate critical incidents")
    parser.add_argument("--alerts-json", help="Path to JSON file with alerts (instead of DB)")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    parser.add_argument("--db-path", help="Database path override")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    time_window = _parse_time_window(args.time_window)

    # Load alerts
    if args.alerts_json:
        with open(args.alerts_json, "r", encoding="utf-8") as f:
            alerts = json.load(f)
    else:
        alerts = _load_alerts_from_db(args.project, time_window, db_path)

    if not alerts:
        print(f"[alert-correlator] No alerts found for {args.project} in the last {args.time_window}")
        return

    print(f"[alert-correlator] Loaded {len(alerts)} alerts for {args.project}")

    # Deduplicate
    if args.deduplicate:
        original_count = len(alerts)
        alerts = deduplicate(alerts)
        print(f"[alert-correlator] Deduplicated: {original_count} -> {len(alerts)} alerts")

    # Correlate
    incidents = correlate(alerts, time_window)
    print(f"[alert-correlator] Correlated into {len(incidents)} incidents")

    # Auto-escalate critical
    escalations = []
    if args.escalate_critical:
        for inc in incidents:
            if inc["severity"] == "critical":
                esc = escalate(inc, db_path)
                escalations.append(esc)
                print(f"[alert-correlator] ESCALATED: {inc['incident_id']} — {inc['title']}")

    result = {
        "project_id": args.project,
        "time_window": args.time_window,
        "analyzed_at": datetime.utcnow().isoformat(),
        "total_alerts": len(alerts),
        "total_incidents": len(incidents),
        "incidents": incidents,
        "escalations": escalations,
    }

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  ALERT CORRELATION — {args.project}")
        print(f"  Window: {args.time_window} | Alerts: {len(alerts)} | Incidents: {len(incidents)}")
        print(f"{'='*60}")

        for inc in incidents:
            sev = inc["severity"].upper()
            print(f"\n  [{sev:>8s}] {inc['incident_id']}: {inc['title']}")
            print(f"           Alerts: {inc['alert_count']} | "
                  f"Services: {', '.join(inc['services'])} | "
                  f"Sources: {', '.join(inc['sources'])}")
            print(f"           Duration: {inc['duration_seconds']:.0f}s | "
                  f"Start: {inc['start_time']}")

            if inc["related_alerts"]:
                print(f"           Related:")
                for ra in inc["related_alerts"][:3]:
                    print(f"             - [{ra['severity']}] {ra['title'][:50]} ({ra['source']})")

        if escalations:
            print(f"\n  ESCALATIONS ({len(escalations)}):")
            for esc in escalations:
                print(f"    {esc['incident_id']}: {esc['title']} — {esc['escalation_reason']}")

        print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
