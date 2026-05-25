#!/usr/bin/env python3
# CUI // SP-CTI
"""DSOC Demo Seed -- populates dsoc_canvas.db with realistic DDoS & security ops demo data.

Tables seeded:
  dsoc_flowspec_rules (10), dsoc_rtbh_entries (8), dsoc_scrubbing_centers (4),
  dsoc_threats (20), dsoc_mitigations (10), dsoc_bgp_hijacks (6)

Usage:
    python tools/db/seeds/seed_dsoc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_dsoc_demo.py --verify --json
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
        from tools.dsoc_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "dsoc_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn


def _reset_demo_data(conn) -> None:
    for tbl in ("dsoc_mitigations", "dsoc_threats", "dsoc_scrubbing_centers", "dsoc_rtbh_entries", "dsoc_flowspec_rules", "dsoc_bgp_hijacks"):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_FLOWSPEC_RULES = [
    ("FS-DROP-SYNFLOOD", "", "", "tcp", "", "", "", "", "drop", 0, "", "", "64512:666", "active", "RTR-01,RTR-02", "", _ts(0)),
    ("FS-RATE-UDPFLOOD", "", "", "udp", "", "", "", "", "rate-limit", 1000000000, "", "", "64512:667", "active", "RTR-01", "", _ts(2)),
    ("FS-DROP-ICMP", "", "", "icmp", "", "", "", "", "drop", 0, "", "", "64512:668", "active", "RTR-01,RTR-02", "", _ts(4)),
    ("FS-REDIRECT-SCRUB", "192.0.2.0/24", "", "", "", "", "", "", "redirect", 0, "SCRUB-01", "", "64512:669", "active", "RTR-01", "", _ts(6)),
    ("FS-DROP-AMPLIFY", "", "", "udp", "53", "", "", "", "drop", 0, "", "", "64512:670", "active", "RTR-02", "", _ts(8)),
    ("FS-SAMPLE-DDOS", "203.0.113.0/24", "", "", "", "", "", "", "sample", 0, "", "", "64512:671", "active", "RTR-01", "", _ts(10)),
    ("FS-MARK-TOS", "", "", "tcp", "443", "", "", "", "mark", 0, "", "", "64512:672", "active", "RTR-01,RTR-02", "", _ts(12)),
    ("FS-DROP-SPOOF", "", "10.0.0.0/8", "", "", "", "", "", "drop", 0, "", "", "64512:673", "active", "RTR-01", "", _ts(14)),
    ("FS-RATE-SSHFLOOD", "", "", "tcp", "22", "", "", "", "rate-limit", 50000000, "", "", "64512:674", "active", "RTR-02", "", _ts(16)),
    ("FS-EXPIRED-DRAFT", "198.51.100.0/24", "", "", "", "", "", "", "drop", 0, "", "", "64512:675", "expired", "", _ts(18), _ts(48)),
]

_RTBH_ENTRIES = [
    ("192.0.2.128/25", "syn_flood", "system", "active", "64512:666", "192.0.2.1", "RTR-01,RTR-02", "", 60),
    ("203.0.113.64/26", "udp_flood", "system", "active", "64512:667", "192.0.2.1", "RTR-01", "", 120),
    ("198.51.100.0/24", "volumetric_attack", "system", "withdrawn", "64512:668", "192.0.2.1", "RTR-01,RTR-02", _ts(24), 60),
    ("10.0.0.0/8", "spoofed_traffic", "system", "active", "64512:673", "192.0.2.1", "RTR-01", "", 240),
    ("172.16.0.0/12", "amplification", "system", "active", "64512:670", "192.0.2.1", "RTR-02", "", 180),
    ("192.168.0.0/16", "icmp_flood", "system", "active", "64512:668", "192.0.2.1", "RTR-01,RTR-02", "", 90),
    ("0.0.0.0/0", "manual", "noc-smith", "withdrawn", "64512:999", "192.0.2.1", "RTR-01", _ts(36), 30),
    ("198.18.0.0/15", "policy", "system", "active", "64512:999", "192.0.2.1", "RTR-02", "", 1440),
]

_SCRUBBING_CENTERS = [
    ("Scrub-Ashburn-01", "Ashburn", "Arbor Networks", 200.0, 145.5, "operational", "Lumen,ATT", "192.0.2.254/32"),
    ("Scrub-Dallas-01", "Dallas", "F5 Silverline", 150.0, 88.2, "operational", "Zayo,Verizon", "198.51.100.254/32"),
    ("Scrub-London-01", "London", "Arbor Networks", 120.0, 67.0, "degraded", "Lumen,Cogent", "203.0.113.254/32"),
    ("Scrub-Tokyo-01", "Tokyo", "NTT DDoS", 100.0, 42.0, "operational", "NTT", "192.0.2.253/32"),
]

_THREATS = []
for i in range(20):
    _THREATS.append({
        "id": _uid(),
        "source_prefix": f"{random.choice([192,203,198])}.{random.randint(0,255)}.{random.randint(0,255)}.0/24",
        "threat_type": random.choice(["botnet_c2","scanner","amplifier","spoofed_source","known_attacker","tor_exit","vpn_provider","other"]),
        "confidence_pct": round(random.uniform(45.0, 98.0), 1),
        "attack_vector": random.choice(["volumetric","application","protocol","reconnaissance","",""]),
        "feed_source": random.choice(["CIRCL","AlienVault","Abuse.ch","Spamhaus","internal"]),
        "first_seen": _ts(i * 2),
        "last_seen": _ts(i * 2 + random.uniform(0.5, 4)),
        "packets_per_sec": random.randint(1000, 5000000),
        "bits_per_sec": random.randint(1000000, 50000000000),
        "is_active": 1 if random.random() > 0.3 else 0,
        "classification": "CUI",
    })

_MITIGATIONS = []
for i in range(10):
    _MITIGATIONS.append({
        "id": _uid(),
        "mitigation_number": f"MIT-2026-{i+1:03d}",
        "target_prefix": random.choice(["192.0.2.0/24","203.0.113.0/24","198.51.100.0/24","10.0.0.0/8"]),
        "mitigation_type": random.choice(["rtbh","flowspec","scrubbing","acl","hybrid"]),
        "status": random.choice(["active","active","standby","completed","failed"]),
        "attack_type": random.choice(["SYN flood","UDP flood","DNS amplification","ACK flood","HTTP flood",""]),
        "peak_traffic_gbps": round(random.uniform(5.0, 180.0), 1),
        "scrubbing_center_id": random.choice([1,2,3,4]) if random.random() > 0.4 else None,
        "flowspec_rule_id": random.choice([1,2,3,4,5]) if random.random() > 0.5 else None,
        "rtbh_id": random.choice([1,2,3,4,5,6,7,8]) if random.random() > 0.5 else None,
        "started_by": random.choice(["system","noc-smith","noc-jones"]),
        "started_at": _ts(i * 3),
        "ended_at": _ts(i * 3 + random.uniform(0.5, 6)) if random.random() > 0.5 else "",
        "notes": random.choice(["","Auto-triggered by alarm","Manual engagement","Scrubbing center redirected traffic"]),
        "classification": "CUI",
    })

_BGP_HIJACKS = [
    ("HJ-2026-001", "type_0", "open", "192.0.2.0/24", "192.0.2.0/24", 64512, 65535, 2914, "", 85.0, "bgpmon"),
    ("HJ-2026-002", "route_leak", "mitigated", "203.0.113.0/24", "203.0.113.0/24", 64512, 174, 6939, "oops", 92.0, "bgpstream"),
    ("HJ-2026-003", "rpki_invalid", "resolved", "198.51.100.0/24", "198.51.100.0/24", 64512, 13335, 13335, "", 98.0, "rpki-validator"),
    ("HJ-2026-004", "type_1", "under_review", "10.0.0.0/8", "10.0.0.0/8", 64512, 3257, 64512, "", 78.0, "internal"),
    ("HJ-2026-005", "type_2", "false_positive", "172.16.0.0/12", "172.16.0.0/12", 64512, 3356, 64512, "legitimate anycast", 65.0, "noc-smith"),
    ("HJ-2026-006", "unknown", "resolved", "198.18.0.0/15", "198.18.0.0/15", 64512, 9002, 9002, "test prefix leak", 70.0, "bgpmon"),
]


def seed_flowspec_rules(conn) -> int:
    sql = """INSERT OR IGNORE INTO dsoc_flowspec_rules (
        rule_name, destination_prefix, source_prefix, protocol, dst_port, src_port, packet_length,
        dscp, action, rate_limit_bps, redirect_vrf, community, status, applied_routers, expires_at,
        created_by, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _FLOWSPEC_RULES:
        conn.execute(sql, (*row, "system", "CUI", _ts(count), _ts(count)))
        count += 1
    return count


def seed_rtbh_entries(conn) -> int:
    sql = """INSERT OR IGNORE INTO dsoc_rtbh_entries (
        prefix, trigger_reason, triggered_by, status, community, nexthop, applied_routers,
        withdrawn_at, auto_withdraw_minutes, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _RTBH_ENTRIES:
        conn.execute(sql, (*row, "CUI", _ts(count * 2)))
        count += 1
    return count


def seed_scrubbing_centers(conn) -> int:
    sql = """INSERT OR IGNORE INTO dsoc_scrubbing_centers (
        name, location, provider, capacity_gbps, current_load_gbps, status, upstream_links,
        anycast_prefix, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _SCRUBBING_CENTERS:
        conn.execute(sql, (*row, "CUI", _ts(count * 3), _ts(count * 3)))
        count += 1
    return count


def seed_threats(conn) -> int:
    sql = """INSERT OR IGNORE INTO dsoc_threats (
        source_prefix, threat_type, confidence_pct, attack_vector, feed_source, first_seen,
        last_seen, packets_per_sec, bits_per_sec, is_active, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _THREATS:
        conn.execute(sql, tuple(row.values()))
        count += 1
    return count


def seed_mitigations(conn) -> int:
    sql = """INSERT OR IGNORE INTO dsoc_mitigations (
        mitigation_number, target_prefix, mitigation_type, status, attack_type, peak_traffic_gbps,
        scrubbing_center_id, flowspec_rule_id, rtbh_id, started_by, started_at, ended_at, notes, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _MITIGATIONS:
        conn.execute(sql, tuple(row.values()))
        count += 1
    return count


def seed_bgp_hijacks(conn) -> int:
    sql = """INSERT OR IGNORE INTO dsoc_bgp_hijacks (
        hijack_number, hijack_type, status, detected_prefix, expected_prefix, expected_origin_asn,
        observed_origin_asn, peer_asn, route_leak_type, confidence_pct, detection_source, notes,
        classification, detected_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _BGP_HIJACKS:
        conn.execute(sql, (*row, "", "CUI", _ts(count * 4), _ts(count * 4)))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in ("dsoc_flowspec_rules", "dsoc_rtbh_entries", "dsoc_scrubbing_centers", "dsoc_threats", "dsoc_mitigations", "dsoc_bgp_hijacks"):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="DSOC Demo Seed")
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
            "dsoc_flowspec_rules": seed_flowspec_rules(conn),
            "dsoc_rtbh_entries": seed_rtbh_entries(conn),
            "dsoc_scrubbing_centers": seed_scrubbing_centers(conn),
            "dsoc_threats": seed_threats(conn),
            "dsoc_mitigations": seed_mitigations(conn),
            "dsoc_bgp_hijacks": seed_bgp_hijacks(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_dsoc] Seeded {counts}")
            print(f"[seed_dsoc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
