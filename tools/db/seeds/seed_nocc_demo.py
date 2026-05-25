#!/usr/bin/env python3
# CUI // SP-CTI
"""NOCC Demo Seed -- populates noc_canvas.db with realistic NOC operations demo data.

Tables seeded:
  noc_incidents (8), noc_alarms (25), noc_rfcs (5), noc_mops (5),
  noc_maintenance_windows (3), noc_sla_records (12)

Usage:
    python tools/db/seeds/seed_nocc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_nocc_demo.py --verify --json
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

random.seed(42)

_NOW = datetime.now(timezone.utc)
_T0 = _NOW - timedelta(hours=72)


def _ts(offset_hours: float = 0.0) -> str:
    return (_T0 + timedelta(hours=offset_hours)).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _get_conn():
    try:
        from tools.noc_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db(conn)
        return conn
    except Exception:
        db = _ROOT / "data" / "noc_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn


def _reset_demo_data(conn) -> None:
    for tbl in (
        "noc_sla_records",
        "noc_maintenance_windows",
        "noc_mops",
        "noc_rfcs",
        "noc_alarms",
        "noc_incidents",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


# Pre-generate UUIDs so cross-references work
_INCIDENT_IDS = {f"INC-2026-{i:04d}": _uid() for i in range(1, 9)}
_RFC_IDS = {f"RFC-2026-{i:03d}": _uid() for i in range(1, 6)}
_MOP_IDS = {f"MOP-2026-{i:03d}": _uid() for i in range(1, 6)}

_INCIDENTS = [
    (_INCIDENT_IDS["INC-2026-0001"], "INC-2026-0001", "BGP session flap on Core-RTR-01", "p1", "investigating", "CIR-1001", "POP-Ashburn", "Lumen", "upstream peer instability", "", False, 0, "noc-smith", "noc-jones"),
    (_INCIDENT_IDS["INC-2026-0002"], "INC-2026-0002", "Power redundancy loss in DC-2", "p2", "resolved", "CIR-2003", "DC-Dallas", "Zayo", "ATS failure", "switched to secondary feed", True, 45, "noc-jones", "noc-lee"),
    (_INCIDENT_IDS["INC-2026-0003"], "INC-2026-0003", "Latency spike to EU IX", "p3", "open", "CIR-1005", "POP-London", "Cogent", "congestion on LAG member", "", False, 0, "noc-lee", "noc-smith"),
    (_INCIDENT_IDS["INC-2026-0004"], "INC-2026-0004", "Optical BER degradation on OC-192", "p2", "acknowledged", "CIR-3001", "POP-Seattle", "ATT", "fiber bend", "pending maintenance", False, 0, "noc-smith", "noc-jones"),
    (_INCIDENT_IDS["INC-2026-0005"], "INC-2026-0005", "Firewall HA failover", "p3", "resolved", "CIR-4002", "DC-Chicago", "", "HA sync loss", "manual failback completed", False, 12, "noc-jones", "noc-lee"),
    (_INCIDENT_IDS["INC-2026-0006"], "INC-2026-0006", "DDoS alert: UDP flood 12 Gbps", "p1", "resolved", "CIR-5001", "POP-Ashburn", "", "volumetric attack", "scrubbing center engaged", True, 18, "noc-lee", "noc-smith"),
    (_INCIDENT_IDS["INC-2026-0007"], "INC-2026-0007", "SNMP trap storm from edge switches", "p3", "closed", "", "POP-Miami", "", "polling misconfiguration", "adjusted trap threshold", False, 0, "noc-smith", "noc-jones"),
    (_INCIDENT_IDS["INC-2026-0008"], "INC-2026-0008", "Planned maintenance: router firmware", "p4", "closed", "CIR-1001", "POP-Ashburn", "Lumen", "scheduled", "completed", False, 0, "noc-jones", "noc-lee"),
]

_ALARMS = []
_alarm_sources = ["solarwinds", "librenms", "snmp-trap", "syslog", "zabbix"]
_alarm_types = ["interface", "bgp", "circuit", "power", "optical", "cpu", "memory", "temperature", "security"]
_severities = ["critical", "major", "minor", "warning", "warning", "info"]
_devices = [
    ("Core-RTR-01", "10.0.1.1"), ("Core-RTR-02", "10.0.1.2"), ("Dist-SW-Ash", "10.0.2.11"),
    ("Dist-SW-Dal", "10.0.2.21"), ("Edge-FW-01", "10.0.3.1"), ("Edge-FW-02", "10.0.3.2"),
    ("Agg-SW-Sea", "10.0.2.31"), ("Core-RTR-LON", "10.1.1.1"),
]
for i in range(25):
    dev, ip = random.choice(_devices)
    src = random.choice(_alarm_sources)
    typ = random.choice(_alarm_types)
    sev = random.choice(_severities)
    desc = f"{typ.upper()} alarm on {dev}: threshold exceeded"
    _ALARMS.append({
        "id": _uid(),
        "alarm_source": src,
        "source_alarm_id": f"ALM-{1000+i}",
        "severity": sev,
        "alarm_type": typ,
        "device_name": dev,
        "device_ip": ip,
        "circuit_id": f"CIR-{1000 + i % 5}" if random.random() > 0.3 else "",
        "carrier": random.choice(["Lumen", "ATT", "Zayo", "Cogent", ""]),
        "description": desc,
        "raw_payload": json.dumps({"threshold": random.randint(70, 95)}),
        "correlated_incident_id": None,
        "suppressed": False,
        "acknowledged": random.choice([False, False, False, True]),
        "acknowledged_by": "noc-smith" if random.random() > 0.7 else "",
        "acknowledged_at": _ts(i * 1.5) if random.random() > 0.7 else None,
        "cleared": random.choice([False, False, True]),
        "cleared_at": _ts(i * 1.5 + 2) if random.random() > 0.5 else None,
        "classification": "CUI",
        "first_seen": _ts(i * 1.2),
        "last_seen": _ts(i * 1.2 + random.random()),
    })

_RFCS = [
    (_RFC_IDS["RFC-2026-001"], "RFC-2026-001", "Upgrade Core-RTR-01 IOS", "standard", "approved", "medium", "rollback to previous IOS partition", _ts(0), _ts(4), None, None, _MOP_IDS["MOP-2026-001"], "[\"CIR-1001\"]", "[\"POP-Ashburn\"]", "noc-smith", "eng-mgr-brown"),
    (_RFC_IDS["RFC-2026-002"], "RFC-2026-002", "DC-2 ATS replacement", "emergency", "completed", "high", "manual bypass to secondary", _ts(8), _ts(12), _ts(8.5), _ts(11), _MOP_IDS["MOP-2026-002"], "[\"CIR-2003\"]", "[\"DC-Dallas\"]", "noc-jones", "eng-mgr-brown"),
    (_RFC_IDS["RFC-2026-003"], "RFC-2026-003", "Enable BFD on all eBGP peers", "standard", "draft", "low", "disable BFD per peer if issue", _ts(24), _ts(28), None, None, _MOP_IDS["MOP-2026-003"], "[\"CIR-1001\",\"CIR-1005\"]", "[\"POP-Ashburn\",\"POP-London\"]", "noc-lee", None),
    (_RFC_IDS["RFC-2026-004"], "RFC-2026-004", "Optical span re-splice Seattle", "normal", "submitted", "medium", "restore previous splice", _ts(48), _ts(52), None, None, _MOP_IDS["MOP-2026-004"], "[\"CIR-3001\"]", "[\"POP-Seattle\"]", "noc-smith", "eng-mgr-brown"),
    (_RFC_IDS["RFC-2026-005"], "RFC-2026-005", "Firewall policy audit", "standard", "executing", "low", "revert to last known good", _ts(12), _ts(16), _ts(12.5), None, _MOP_IDS["MOP-2026-005"], "[]", "[\"DC-Chicago\"]", "noc-jones", "sec-lead-white"),
]

_MOPS = [
    (_MOP_IDS["MOP-2026-001"], "MOP-2026-001", "IOS upgrade procedure", _RFC_IDS["RFC-2026-001"], json.dumps([
        {"step": 1, "action": "Pre-check: verify current version"},
        {"step": 2, "action": "Download target image"},
        {"step": 3, "action": "Schedule maintenance window"},
        {"step": 4, "action": "Execute reload with new image"},
        {"step": 5, "action": "Post-check: validate BGP sessions"},
    ]), "manual"),
    (_MOP_IDS["MOP-2026-002"], "MOP-2026-002", "ATS replacement hot-swap", _RFC_IDS["RFC-2026-002"], json.dumps([
        {"step": 1, "action": "Isolate failed ATS"},
        {"step": 2, "action": "Engage manual bypass"},
        {"step": 3, "action": "Rack replacement unit"},
        {"step": 4, "action": "Transfer load and verify"},
    ]), "manual"),
    (_MOP_IDS["MOP-2026-003"], "MOP-2026-003", "BFD enable per peer", _RFC_IDS["RFC-2026-003"], json.dumps([
        {"step": 1, "action": "Generate peer list from PeeringDB"},
        {"step": 2, "action": "Push config via NetConf"},
        {"step": 3, "action": "Verify session up with BFD"},
    ]), "ai"),
    (_MOP_IDS["MOP-2026-004"], "MOP-2026-004", "Optical re-splice SOP", _RFC_IDS["RFC-2026-004"], json.dumps([
        {"step": 1, "action": "Coordinate with field engineer"},
        {"step": 2, "action": "Verify OTDR trace post-splice"},
    ]), "manual"),
    (_MOP_IDS["MOP-2026-005"], "MOP-2026-005", "Firewall policy audit checklist", _RFC_IDS["RFC-2026-005"], json.dumps([
        {"step": 1, "action": "Export current rulebase"},
        {"step": 2, "action": "Run compliance scanner"},
        {"step": 3, "action": "Remediate orphaned rules"},
    ]), "ai"),
]

_MAINTENANCE_WINDOWS = [
    (_uid(), "MW-2026-001", "Core-RTR-01 IOS upgrade", _RFC_IDS["RFC-2026-001"], _ts(0), _ts(4), _ts(0), _ts(3.8), "completed", "single-circuit", True, "[\"Customer-A\",\"Customer-B\"]", "[\"CIR-1001\"]"),
    (_uid(), "MW-2026-002", "DC-2 ATS replacement", _RFC_IDS["RFC-2026-002"], _ts(8), _ts(12), _ts(8), _ts(11), "completed", "site", True, "[\"Customer-C\"]", "[\"CIR-2003\"]"),
    (_uid(), "MW-2026-003", "BFD rollout window", _RFC_IDS["RFC-2026-003"], _ts(24), _ts(28), None, None, "scheduled", "multi-circuit", False, "[]", "[\"CIR-1001\",\"CIR-1005\"]"),
]

_SLA_RECORDS = []
_carriers = ["Lumen", "ATT", "Zayo", "Cogent", "Verizon"]
_sla_types = [("uptime", 99.99, 99.95), ("latency_ms", 15.0, 18.5), ("jitter_ms", 2.0, 3.2), ("packet_loss_pct", 0.01, 0.015), ("mttr_min", 60, 45)]
for i in range(12):
    cid = f"CIR-{1000 + i % 6}"
    carrier = _carriers[i % len(_carriers)]
    styp, target, measured = _sla_types[i % len(_sla_types)]
    if styp == "uptime":
        measured = round(random.uniform(99.8, 99.999), 3)
    elif styp == "latency_ms":
        measured = round(random.uniform(10, 25), 1)
    elif styp == "jitter_ms":
        measured = round(random.uniform(1.0, 5.0), 2)
    elif styp == "packet_loss_pct":
        measured = round(random.uniform(0.0, 0.05), 4)
    else:
        measured = random.randint(20, 80)
    breach = measured > target if styp != "uptime" else measured < target
    _SLA_RECORDS.append({
        "id": _uid(),
        "circuit_id": cid,
        "carrier": carrier,
        "customer": f"Customer-{chr(65 + i % 8)}",
        "sla_type": styp,
        "target_value": target,
        "measured_value": measured,
        "measurement_period": "monthly" if i < 6 else "weekly",
        "breach": breach,
        "breach_minutes": random.randint(0, 45) if breach else 0,
        "credit_eligible": breach and random.random() > 0.5,
        "period_start": _ts(i * 6),
        "period_end": _ts(i * 6 + 168),
        "classification": "CUI",
    })


def seed_incidents(conn) -> int:
    sql = """INSERT OR IGNORE INTO noc_incidents (
        id, incident_number, title, severity, status, affected_circuit, affected_site, affected_carrier,
        root_cause, resolution, sla_breach, mttr_minutes, opened_by, assigned_to, classification,
        created_at, resolved_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _INCIDENTS:
        conn.execute(sql, (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9],
            row[10], row[11], row[12], row[13], "CUI",
            _ts(count * 2), _ts(count * 2 + row[11]) if row[11] else None, _ts(count * 2),
        ))
        count += 1
    return count


def seed_alarms(conn) -> int:
    sql = """INSERT OR IGNORE INTO noc_alarms (
        id, alarm_source, source_alarm_id, severity, alarm_type, device_name, device_ip,
        circuit_id, carrier, description, raw_payload, correlated_incident_id, suppressed,
        acknowledged, acknowledged_by, acknowledged_at, cleared, cleared_at, classification,
        first_seen, last_seen
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _ALARMS:
        conn.execute(sql, tuple(row.values()))
        count += 1
    return count


def seed_rfcs(conn) -> int:
    sql = """INSERT OR IGNORE INTO noc_rfcs (
        id, rfc_number, title, change_type, status, risk_level, rollback_plan,
        scheduled_start, scheduled_end, actual_start, actual_end, mop_id,
        affected_circuits, affected_sites, change_owner, approver, classification,
        created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _RFCS:
        conn.execute(sql, (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11],
            row[12], row[13], row[14], row[15], "CUI", _ts(count * 4), _ts(count * 4),
        ))
        count += 1
    return count


def seed_mops(conn) -> int:
    sql = """INSERT OR IGNORE INTO noc_mops (
        id, mop_number, title, rfc_id, steps_json, generated_by, ai_prompt, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _MOPS:
        conn.execute(sql, (
            row[0], row[1], row[2], row[3], row[4], row[5], "", "CUI", _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_maintenance_windows(conn) -> int:
    sql = """INSERT OR IGNORE INTO noc_maintenance_windows (
        id, window_number, title, rfc_id, scheduled_start, scheduled_end, actual_start, actual_end,
        status, impact_scope, notification_sent, affected_customers, affected_circuits, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _MAINTENANCE_WINDOWS:
        conn.execute(sql, (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9],
            row[10], row[11], row[12], "CUI", _ts(count * 8), _ts(count * 8),
        ))
        count += 1
    return count


def seed_sla_records(conn) -> int:
    sql = """INSERT OR IGNORE INTO noc_sla_records (
        id, circuit_id, carrier, customer, sla_type, target_value, measured_value, measurement_period,
        breach, breach_minutes, credit_eligible, period_start, period_end, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _SLA_RECORDS:
        conn.execute(sql, tuple(row.values()))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "noc_incidents", "noc_alarms", "noc_rfcs", "noc_mops",
        "noc_maintenance_windows", "noc_sla_records",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="NOCC Demo Seed")
    parser.add_argument("--reset", action="store_true", help="Clear existing demo data")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verify", action="store_true", help="Only verify counts")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        if args.verify:
            result = verify(conn)
            print(json.dumps(result, indent=2) if args.json else result)
            return

        if args.reset:
            _reset_demo_data(conn)

        counts = {
            "noc_incidents": seed_incidents(conn),
            "noc_alarms": seed_alarms(conn),
            "noc_rfcs": seed_rfcs(conn),
            "noc_mops": seed_mops(conn),
            "noc_maintenance_windows": seed_maintenance_windows(conn),
            "noc_sla_records": seed_sla_records(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_nocc] Seeded {counts}")
            print(f"[seed_nocc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
