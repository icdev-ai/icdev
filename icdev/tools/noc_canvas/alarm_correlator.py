# CUI // SP-CTI
"""NOCC alarm correlator — groups alarms into incidents by device/circuit proximity."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_ts(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def correlate_alarms(alarms: list[dict]) -> list[dict]:
    """Group active alarms by device within a 15-minute sliding window.

    Returns list of storm groups: {device_name, alarm_ids, count, first_seen}.
    Groups with fewer than ALARM_STORM_THRESHOLD alarms are excluded.
    """
    from tools.noc_canvas.constants import ALARM_STORM_THRESHOLD, ALARM_STORM_WINDOW_MIN

    by_device: dict[str, list[dict]] = defaultdict(list)
    for alarm in alarms:
        key = alarm.get("device_name") or alarm.get("circuit_id") or "unknown"
        by_device[key].append(alarm)

    storms = []
    window = timedelta(minutes=ALARM_STORM_WINDOW_MIN)
    for device, device_alarms in by_device.items():
        sorted_alarms = sorted(device_alarms, key=lambda a: _parse_ts(a.get("first_seen", "")))
        # Sliding window scan
        i = 0
        while i < len(sorted_alarms):
            anchor = _parse_ts(sorted_alarms[i].get("first_seen", ""))
            group = [sorted_alarms[i]]
            j = i + 1
            while j < len(sorted_alarms):
                if _parse_ts(sorted_alarms[j].get("first_seen", "")) - anchor <= window:
                    group.append(sorted_alarms[j])
                    j += 1
                else:
                    break
            if len(group) >= ALARM_STORM_THRESHOLD:
                storms.append({
                    "device_name": device,
                    "alarm_ids": [a["id"] for a in group],
                    "count": len(group),
                    "first_seen": sorted_alarms[i].get("first_seen", ""),
                })
            i = j if j > i else i + 1

    return storms


def get_active_alarms(conn: Any) -> list[dict]:
    """Return all non-cleared alarms ordered by severity priority then last_seen."""
    severity_order = "CASE severity WHEN 'critical' THEN 0 WHEN 'major' THEN 1 WHEN 'minor' THEN 2 WHEN 'warning' THEN 3 ELSE 4 END"
    try:
        cur = conn.execute(
            f"SELECT * FROM noc_alarms WHERE cleared = FALSE "
            f"ORDER BY {severity_order}, last_seen DESC LIMIT 500"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def get_alarm_summary(conn: Any) -> dict:
    """Return counts of active alarms by severity."""
    summary = {s: 0 for s in ["critical", "major", "minor", "warning", "info"]}
    try:
        rows = conn.execute(
            "SELECT severity, COUNT(*) AS cnt FROM noc_alarms "
            "WHERE cleared = FALSE GROUP BY severity"
        ).fetchall()
        for row in rows:
            sev = row[0] if not hasattr(row, "keys") else row["severity"]
            cnt = row[1] if not hasattr(row, "keys") else row["cnt"]
            if sev in summary:
                summary[sev] = cnt
    except Exception:
        pass
    return summary


def suppress_alarm(conn: Any, alarm_id: str, user_id: str) -> bool:
    try:
        conn.execute(
            "UPDATE noc_alarms SET suppressed = TRUE WHERE id = %s",
            (alarm_id,),
        )
        conn.commit()
        return True
    except Exception:
        try:
            conn.execute(
                "UPDATE noc_alarms SET suppressed = 1 WHERE id = %s",
                (alarm_id,),
            )
            conn.commit()
            return True
        except Exception:
            return False


def acknowledge_alarm(conn: Any, alarm_id: str, user_id: str) -> bool:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for sql in [
        ("UPDATE noc_alarms SET acknowledged = TRUE, acknowledged_by = %s, acknowledged_at = %s WHERE id = %s",
         (user_id, now, alarm_id)),
        ("UPDATE noc_alarms SET acknowledged = 1, acknowledged_by = ?, acknowledged_at = ? WHERE id = ?",
         (user_id, now, alarm_id)),
    ]:
        try:
            conn.execute(sql[0], sql[1])
            conn.commit()
            return True
        except Exception:
            continue
    return False


def create_alarm(conn: Any, alarm_data: dict) -> str:
    """Insert a new alarm record and return its id.

    Ingest is open to arbitrary NMS adapters, but ``noc_alarms`` enforces
    CHECK-constraint enums on ``alarm_source``/``severity``/``alarm_type`` in
    PostgreSQL.  An out-of-enum value (e.g. a new NMS source) would raise a
    CheckViolation → HTTP 500.  Coerce unknown values to the safe catch-all
    defaults ('custom'/'warning'/'interface') and preserve the caller's
    original source in ``raw_payload`` for traceability.  (The SQLite schema
    omits these CHECKs, which is why the PG-only 500 passed local tests.)
    """
    import json
    from datetime import datetime, timezone
    from tools.noc_canvas.constants import (
        ALARM_SEVERITIES,
        ALARM_SOURCES,
        ALARM_TYPES,
    )
    alarm_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    raw_source = alarm_data.get("alarm_source", "custom") or "custom"
    alarm_source = raw_source if raw_source in ALARM_SOURCES else "custom"
    raw_severity = alarm_data.get("severity", "warning") or "warning"
    severity = raw_severity if raw_severity in ALARM_SEVERITIES else "warning"
    raw_type = alarm_data.get("alarm_type", "interface") or "interface"
    alarm_type = raw_type if raw_type in ALARM_TYPES else "interface"

    payload_obj = dict(alarm_data.get("raw_payload", {}) or {})
    if alarm_source != raw_source:
        # Keep the caller's original source identifier for audit/traceability.
        payload_obj.setdefault("_ingest_source", raw_source)
    payload = json.dumps(payload_obj)

    for sql, params in [
        (
            """INSERT INTO noc_alarms
               (id, alarm_source, source_alarm_id, severity, alarm_type,
                device_name, device_ip, circuit_id, carrier, description,
                raw_payload, classification, first_seen, last_seen)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (alarm_id,
             alarm_source,
             alarm_data.get("source_alarm_id", ""),
             severity,
             alarm_type,
             alarm_data.get("device_name", ""),
             alarm_data.get("device_ip", ""),
             alarm_data.get("circuit_id", ""),
             alarm_data.get("carrier", ""),
             alarm_data.get("description", ""),
             payload,
             alarm_data.get("classification", "CUI"),
             now, now),
        ),
        (
            """INSERT INTO noc_alarms
               (id, alarm_source, source_alarm_id, severity, alarm_type,
                device_name, device_ip, circuit_id, carrier, description,
                raw_payload, classification, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (alarm_id,
             alarm_source,
             alarm_data.get("source_alarm_id", ""),
             severity,
             alarm_type,
             alarm_data.get("device_name", ""),
             alarm_data.get("device_ip", ""),
             alarm_data.get("circuit_id", ""),
             alarm_data.get("carrier", ""),
             alarm_data.get("description", ""),
             payload,
             alarm_data.get("classification", "CUI"),
             now, now),
        ),
    ]:
        try:
            conn.execute(sql, params)
            conn.commit()
            return alarm_id
        except Exception:
            continue
    raise RuntimeError("Failed to insert alarm")
