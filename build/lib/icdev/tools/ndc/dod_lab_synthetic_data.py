"""DoD Lab Synthetic Data Generator — BCAP/NIPR/JWICS Demo Lab.

Generates realistic synthetic telemetry for all 6 demo scenarios:
  - BGP telemetry (ISP latency, peer events, route changes)
  - Syslog / IDS alerts (normal ops + 10 embedded attack events)
  - STIG compliance findings (25 across 7 devices, pre-ZTP baseline)
  - Network traffic flows (200 records, 15 suspicious)
  - Cross-domain transfer requests (30 total, 7 denied)
  - TCCM credential requests (40 total)
  - ISP latency measurements (300 time-series points)
  - AI agent action log

All data uses a fixed seed (42) for reproducibility.
Saves to data/network_canvas.db (network canvas DB).

Usage:
  python tools/ndc/dod_lab_synthetic_data.py [--json] [--reset]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

random.seed(42)

_NOW = datetime.now(timezone.utc)
_T0 = _NOW - timedelta(hours=2)


def _ts(offset_min: float = 0.0) -> str:
    return (_T0 + timedelta(minutes=offset_min)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn():
    """Get network_canvas DB connection (SQLite)."""
    try:
        from tools.network.db.init_db import get_connection
        return get_connection()
    except Exception:
        import sqlite3
        db = _ROOT / "data" / "network_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(db))


# ══════════════════════════════════════════════════════════════════════════════
# Schema creation
# ══════════════════════════════════════════════════════════════════════════════

_DDL = [
    """CREATE TABLE IF NOT EXISTS ndc_bgp_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, router TEXT, peer TEXT, isp TEXT,
        event_type TEXT, bgp_as INTEGER, latency_ms REAL,
        prefix_count INTEGER, med INTEGER, details TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ndc_syslog_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, source_device TEXT, severity TEXT,
        log_prefix TEXT, message TEXT,
        src_ip TEXT, dst_ip TEXT, protocol TEXT, dst_port INTEGER,
        mitre_tactic TEXT, mitre_technique TEXT, action_taken TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ndc_stig_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device TEXT, vuln_id TEXT, severity TEXT, rule_title TEXT,
        finding TEXT, status TEXT, detected_at TEXT,
        remediated_at TEXT, kanban_task_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ndc_traffic_flows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, src_device TEXT, dst_device TEXT,
        src_ip TEXT, dst_ip TEXT, protocol TEXT, port INTEGER,
        bytes_per_sec REAL, latency_ms REAL, classification TEXT,
        bcap_inspected INTEGER, threat_score REAL
    )""",
    """CREATE TABLE IF NOT EXISTS ndc_xd_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, src_ip TEXT, src_device TEXT,
        dst_ip TEXT, dst_device TEXT,
        classification_from TEXT, classification_to TEXT,
        data_type TEXT, status TEXT,
        ai_decision TEXT, ai_reasoning TEXT, approval_time_sec REAL
    )""",
    """CREATE TABLE IF NOT EXISTS ndc_tccm_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, requesting_service TEXT, target_csp TEXT,
        classification TEXT, credential_type TEXT,
        ttl_hours INTEGER, status TEXT, token_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ndc_isp_telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, router TEXT, isp TEXT, interface TEXT,
        latency_ms REAL, jitter_ms REAL, packet_loss_pct REAL,
        bandwidth_mbps REAL, bgp_status TEXT, predicted_failure INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS ndc_agent_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, agent_name TEXT, scenario TEXT,
        trigger_event TEXT, analysis_json TEXT, decision TEXT,
        action_taken TEXT, outcome TEXT, mitre_mapping TEXT,
        kanban_task_id TEXT, llm_model TEXT, response_time_ms INTEGER
    )""",
]


# ══════════════════════════════════════════════════════════════════════════════
# BGP telemetry — ISP link health over 120 minutes
# ══════════════════════════════════════════════════════════════════════════════

def _gen_bgp_events() -> list[tuple]:
    rows = []
    # Normal ATT link — 60 samples every 2 min, latency spikes at T+90min
    for i in range(60):
        t = i * 2.0
        lat = 12.0 + random.gauss(0, 1.5)
        if t >= 90:           # ATT degrading
            lat = 12 + (t - 90) * 12 + random.gauss(0, 5)
        if t >= 98:           # ATT down
            event = "peer_down"
            lat = 9999.0
        elif t >= 90:
            event = "latency_spike"
        else:
            event = "peer_up"
        rows.append((_ts(t), "NIPR-EDGE-1", "203.0.113.2", "ISP-ATT",
                     event, 65101, round(lat, 1), random.randint(118, 122),
                     random.randint(95, 105), json.dumps({"state": event})))
    # ISP-LUMEN — stable, becomes primary at T+98
    for i in range(60):
        t = i * 2.0
        lat = 18.0 + random.gauss(0, 2.0)
        event = "peer_up" if t < 98 else "primary_path"
        rows.append((_ts(t), "NIPR-EDGE-1", "198.51.100.2", "ISP-LUMEN",
                     event, 65102, round(lat, 1), random.randint(93, 97),
                     random.randint(98, 102), json.dumps({"state": event})))
    # ISP-VERIZON — NIPR-EDGE-2, always stable
    for i in range(60):
        t = i * 2.0
        lat = 15.0 + random.gauss(0, 1.0)
        rows.append((_ts(t), "NIPR-EDGE-2", "192.0.2.2", "ISP-VERIZON",
                     "peer_up", 65103, round(lat, 1), random.randint(108, 112),
                     random.randint(98, 102), json.dumps({"state": "peer_up"})))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Syslog events — 200 records with 10 embedded attacks
# ══════════════════════════════════════════════════════════════════════════════

_NORMAL_MSGS = [
    ("NIPR-EDGE-1",  "info",    "",          "OSPF: neighbor 10.10.0.2 state Full", "", "", "", 0),
    ("NIPR-CORE-RTR","info",    "",          "BGP: table updated, 120 prefixes", "", "", "", 0),
    ("BCAP-VDSS-FW", "info",    "",          "Firewall: established session 10.30.1.10->10.100.1.50:443", "10.30.1.10", "10.100.1.50", "tcp", 443),
    ("ONPREM-DC1-RTR","info",   "",          "OSPF: DR election complete on ether1", "", "", "", 0),
    ("NIPR-EDGE-2",  "info",    "",          "NTP: sync OK to 10.99.0.2", "", "", "", 0),
    ("BCAP-VDMS",    "info",    "",          "Policy route: 10.30.1.10 → AWS-IL4 (mark=IL4)", "10.30.1.10", "10.100.1.50", "tcp", 443),
    ("JWICS-EDGE",   "info",    "",          "JWICS: session closed cleanly", "", "", "", 0),
    ("BCAP-VDSS-IDS","warning", "",          "High connection rate from 10.30.1.0/24 — monitoring", "10.30.1.50", "10.100.0.0", "tcp", 443),
    ("NIPR-CORE-RTR","info",    "",          "Route: 0.0.0.0/0 via 10.10.0.1 installed", "", "", "", 0),
    ("MGMT-JUMPHOST", "info",   "",          "SSH login: admin from 10.99.0.1", "", "", "", 22),
]

_ATTACKS = [
    (28.0,  "BCAP-VDSS-IDS", "critical", "IDS-SQL-INJECT: ",
     "SQL injection: union select * from users", "172.16.99.5", "10.100.1.50", "tcp", 80,
     "Initial Access", "T1190", "BLOCKED"),
    (35.0,  "BCAP-VDSS-IDS", "critical", "IDS-XSS: ",
     "XSS payload detected: <script>document.cookie</script>", "172.16.99.6", "10.100.1.50", "tcp", 443,
     "Execution", "T1059", "BLOCKED"),
    (42.0,  "BCAP-VDSS-IDS", "critical", "IDS-EXFIL: ",
     "Exfil pattern: base64decode in URI parameter", "10.30.1.99", "198.51.100.100", "tcp", 443,
     "Exfiltration", "T1048", "BLOCKED"),
    (51.0,  "BCAP-VDSS-FW",  "warning",  "VDSS default deny: ",
     "Port scan detected: 254 ports in 30s", "192.168.50.10", "10.20.0.0", "tcp", 0,
     "Discovery", "T1046", "BLOCKED"),
    (58.0,  "NIPR-EDGE-1",   "critical", "IDS-RATELIMIT: ",
     "Brute-force SSH: 500 attempts in 60s", "203.0.113.100", "10.10.0.1", "tcp", 22,
     "Credential Access", "T1110", "BLOCKED"),
    (62.0,  "BCAP-VDSS-IDS", "critical", "IDS-SQL-INJECT: ",
     "SQL injection repeat (same source): drop table sessions", "172.16.99.5", "10.100.1.50", "tcp", 80,
     "Initial Access", "T1190", "BLOCKED"),
    (67.0,  "BCAP-VDSS-IDS", "critical", "IDS-CMDINJ: ",
     "Command injection: ;cat /etc/passwd", "172.16.99.5", "10.100.1.50", "tcp", 80,
     "Execution", "T1059.004", "BLOCKED"),
    (72.0,  "BCAP-VDSS-IDS", "critical", "IDS-EXFIL: ",
     "Staged exfil: wget http://172.16.99.5:8080/data.enc", "10.30.1.10", "172.16.99.5", "tcp", 8080,
     "Exfiltration", "T1041", "BLOCKED"),
    (75.0,  "ONPREM-DC1-RTR","warning",  "XD-DENIED: ",
     "Lateral movement: ONPREM-WORKSTA attempting JWICS access directly", "10.30.1.20", "192.168.100.10", "tcp", 443,
     "Lateral Movement", "T1021", "BLOCKED"),
    (80.0,  "BCAP-VDSS-FW",  "critical", "VDSS default deny: ",
     "Privilege escalation attempt via SNMP write community", "10.30.1.99", "10.20.0.2", "udp", 161,
     "Privilege Escalation", "T1548", "BLOCKED"),
]


def _gen_syslog() -> list[tuple]:
    rows = []
    normal_times = sorted(random.uniform(0, 120) for _ in range(190))
    for t in normal_times:
        m = random.choice(_NORMAL_MSGS)
        rows.append((_ts(t), m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], "", "", "LOGGED"))
    for a in _ATTACKS:
        rows.append((_ts(a[0]), a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11]))
    rows.sort(key=lambda r: r[0])
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# STIG findings — 25 findings across 7 devices (pre-ZTP baseline)
# ══════════════════════════════════════════════════════════════════════════════

_STIG_DATA = [
    # device, vuln_id, severity, rule_title, finding
    ("NIPR-EDGE-1", "V-220518", "cat1",
     "Telnet must be disabled",
     "Telnet service is enabled on NIPR-EDGE-1 — cleartext credential exposure"),
    ("NIPR-EDGE-1", "V-220522", "cat2",
     "SSH must use strong crypto only",
     "SSH strong-crypto=no (weak ciphers DES, RC4 allowed)"),
    ("NIPR-EDGE-1", "V-220530", "cat3",
     "NTP must be configured to a DoD time source",
     "NTP not configured — clock drift > 5 min"),

    ("NIPR-EDGE-2", "V-220518", "cat1",
     "Telnet must be disabled",
     "Telnet enabled on NIPR-EDGE-2"),
    ("NIPR-EDGE-2", "V-220545", "cat2",
     "SNMP community string must not be default",
     "SNMP community = 'public' (default)"),

    ("NIPR-CORE-RTR", "V-220601", "cat1",
     "FTP service must be disabled",
     "FTP service active on NIPR-CORE-RTR"),
    ("NIPR-CORE-RTR", "V-220518", "cat1",
     "Telnet must be disabled",
     "Telnet enabled on NIPR-CORE-RTR"),
    ("NIPR-CORE-RTR", "V-220570", "cat2",
     "Firewall default-deny policy required",
     "No default-deny rule — traffic permitted by default"),
    ("NIPR-CORE-RTR", "V-220580", "cat3",
     "Syslog must be forwarded to SIEM",
     "Syslog remote action not configured"),

    ("BCAP-VDSS-FW", "V-220605", "cat1",
     "VDSS inbound default-deny must be enforced",
     "Missing explicit deny rule on ether1 inbound chain"),
    ("BCAP-VDSS-FW", "V-220610", "cat2",
     "All management access must be from OOB MGMT network",
     "SSH accessible from any 10.0.0.0/8 (should be 10.99.0.0/30 only)"),
    ("BCAP-VDSS-FW", "V-220615", "cat3",
     "Firewall logging must capture connection state",
     "Established/related sessions not logged"),

    ("BCAP-VDSS-IDS", "V-220620", "cat2",
     "IDS signatures must be updated within 24h of release",
     "L7 protocol signatures last updated 72h ago"),
    ("BCAP-VDSS-IDS", "V-220625", "cat3",
     "IDS alerts must be forwarded to SOC within 60s",
     "SIEM syslog action not configured on BCAP-VDSS-IDS"),

    ("BCAP-VDMS", "V-220630", "cat1",
     "Credential access to CSPs must route through TCCM",
     "Direct IAM key found in BCAP-VDMS config — bypasses TCCM"),
    ("BCAP-VDMS", "V-220635", "cat2",
     "Policy routing must enforce IL-level workload isolation",
     "IL4 and IL5 traffic not segregated — mangle rules missing"),
    ("BCAP-VDMS", "V-220640", "cat2",
     "VDMS must validate source classification before routing to CSP",
     "No source classification check before AWS/Azure routing"),
    ("BCAP-VDMS", "V-220645", "cat3",
     "VDMS config changes must be logged",
     "Configuration change logging not enabled"),

    ("ONPREM-DC1-RTR", "V-220518", "cat1",
     "Telnet must be disabled",
     "Telnet enabled — critical on device with JWICS cross-domain interface"),
    ("ONPREM-DC1-RTR", "V-220650", "cat1",
     "Cross-domain guard must use explicit allowlist only",
     "XD-Guard permits 10.30.1.0/24 subnet (should be specific /32 hosts)"),
    ("ONPREM-DC1-RTR", "V-220655", "cat2",
     "Cross-domain interface must log all denied traffic",
     "JWICS deny log prefix not configured"),
    ("ONPREM-DC1-RTR", "V-220660", "cat2",
     "JWICS interface must block all multicast",
     "Multicast not explicitly blocked on ether3"),
    ("ONPREM-DC1-RTR", "V-220665", "cat3",
     "Route to JWICS must not appear in NIPR routing table",
     "192.168.100.0/24 visible in NIPR routing table via redistribution"),

    ("JWICS-EDGE", "V-220670", "cat1",
     "JWICS must have no default route toward NIPR",
     "Default route 0.0.0.0/0 via 10.40.0.1 found — violates JWICS isolation"),
    ("JWICS-EDGE", "V-220675", "cat2",
     "JWICS NTP must use internal Stratum-1 only",
     "JWICS-EDGE NTP pointing to 10.99.0.2 (NIPR MGMT) — must use 192.168.100.10"),
]


def _gen_stig_findings() -> list[tuple]:
    rows = []
    for i, (dev, vid, sev, title, finding) in enumerate(_STIG_DATA):
        status = "open"
        detected = _ts(-random.uniform(10, 40))
        rows.append((dev, vid, sev, title, finding, status, detected, None, None))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Traffic flows — 200 flows (185 normal, 15 suspicious)
# ══════════════════════════════════════════════════════════════════════════════

_NORMAL_FLOWS = [
    ("ONPREM-WORKSTA", "AWS-GOVCLOUD-IL4", "10.30.1.20", "10.100.1.50", "tcp", 443, 2400.0, 15.2, "NIPR→CSP", 1, 0.02),
    ("ONPREM-WORKSTA", "AZURE-GOV-IL5",    "10.30.1.20", "10.200.1.50", "tcp", 443, 1800.0, 22.1, "NIPR→CSP", 1, 0.01),
    ("ONPREM-SRV1",    "AWS-GOVCLOUD-IL4", "10.30.1.10", "10.100.1.100","tcp", 443, 8500.0, 14.8, "NIPR→CSP", 1, 0.03),
    ("ONPREM-SRV2",    "AZURE-GOV-IL5",    "10.30.1.11", "10.200.1.100","tcp", 8443,12000.0,20.5, "NIPR→CSP", 1, 0.02),
    ("MGMT-JUMPHOST",  "NIPR-EDGE-1",      "10.99.0.2",  "10.10.0.1",   "tcp", 22,   512.0,  2.1, "MGMT",    0, 0.00),
    ("MGMT-JUMPHOST",  "BCAP-VDSS-FW",     "10.99.0.2",  "10.20.0.2",   "tcp", 22,   512.0,  3.5, "MGMT",    0, 0.00),
    ("ONPREM-SRV1",    "BCAP-TCCM",        "10.30.1.10", "10.20.3.2",   "tcp", 443,  256.0,  5.0, "NIPR",    1, 0.00),
    ("JWICS-SRV1",     "JWICS-SRV2",       "192.168.100.10","192.168.100.11","tcp",5432,45000.0,0.5,"JWICS",  0, 0.00),
]

_SUSPICIOUS_FLOWS = [
    ("ONPREM-WORKSTA", "AWS-GOVCLOUD-IL4", "10.30.1.20", "10.100.1.50", "tcp", 8080, 250000.0, 150.5, "NIPR→CSP", 1, 0.92),
    ("ONPREM-SRV2",    "ISP-ATT",         "10.30.1.11", "203.0.113.100","tcp", 4444, 88000.0,  22.0, "NIPR",    1, 0.88),
    ("ONPREM-WORKSTA", "JWICS-SRV1",      "10.30.1.20", "192.168.100.10","tcp",443,  1200.0,   5.0, "XD-DENY", 0, 0.99),
    ("ONPREM-SRV1",    "ISP-LUMEN",       "10.30.1.10", "198.51.100.200","udp",53,  95000.0,   3.0, "NIPR",    1, 0.75),
    ("MGMT-JUMPHOST",  "JWICS-EDGE",      "10.99.0.2",  "10.40.0.2",   "tcp",  23,    512.0,   2.0, "MGMT",    0, 0.95),
    ("ONPREM-SRV2",    "BCAP-VDSS-FW",    "10.30.1.11", "10.20.0.2",   "tcp", 161,    64.0,   1.5, "NIPR",    1, 0.70),
    ("ONPREM-WORKSTA", "AZURE-GOV-IL5",   "10.30.1.20", "10.200.1.50", "tcp", 3389,  4096.0,  20.0, "NIPR→CSP",1, 0.85),
    ("ONPREM-SRV1",    "AWS-GOVCLOUD-IL4","10.30.1.10", "10.100.1.200","tcp",  21,    256.0,  14.0, "NIPR→CSP",1, 0.80),
    ("ONPREM-WORKSTA", "NIPR-CORE-RTR",   "10.30.1.20", "10.10.0.2",   "icmp", 0,  12000.0,   1.0, "NIPR",    0, 0.60),
    ("JWICS-SRV2",     "NIPR-CORE-RTR",   "192.168.100.11","10.10.0.2","tcp",  22,    512.0,   4.0, "JWICS→NIPR",0,0.97),
    ("ONPREM-SRV2",    "ISP-ATT",         "10.30.1.11", "203.0.113.200","tcp",1337,  2048.0,  18.0, "NIPR",    1, 0.91),
    ("ONPREM-WORKSTA", "BCAP-VDMS",       "10.30.1.20", "10.20.2.2",   "tcp",  80,   1024.0,   5.0, "NIPR",    1, 0.78),
    ("ONPREM-SRV1",    "JWICS-SRV2",      "10.30.1.10", "192.168.100.11","tcp",8443, 4096.0,   6.0, "XD-DENY", 0, 0.96),
    ("MGMT-JUMPHOST",  "AWS-GOVCLOUD-IL4","10.99.0.2",  "10.100.1.50", "tcp", 3306,   256.0,  15.0, "MGMT→CSP",1, 0.82),
    ("ONPREM-SRV2",    "BCAP-TCCM",       "10.30.1.11", "10.20.3.2",   "tcp", 443, 128000.0,   4.0, "NIPR",    1, 0.65),
]


def _gen_traffic_flows() -> list[tuple]:
    rows = []
    for i in range(185):
        f = random.choice(_NORMAL_FLOWS)
        t = random.uniform(0, 120)
        bps = f[6] + random.gauss(0, f[6] * 0.1)
        lat = f[7] + random.gauss(0, f[7] * 0.05)
        rows.append((_ts(t), f[0], f[1], f[2], f[3], f[4], f[5],
                     round(max(0, bps), 1), round(max(0, lat), 1),
                     f[8], f[9], f[10]))
    for f in _SUSPICIOUS_FLOWS:
        t = random.uniform(20, 100)
        rows.append((_ts(t), f[0], f[1], f[2], f[3], f[4], f[5],
                     round(f[6], 1), round(f[7], 1), f[8], f[9], f[10]))
    rows.sort(key=lambda r: r[0])
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Cross-domain transfer requests
# ══════════════════════════════════════════════════════════════════════════════

_XD_APPROVED_REASONS = [
    "Source in approved list, destination accredited, classification appropriate, business hours",
    "Valid TCCM token presented, data type cleared for transfer, no anomalies detected",
    "Approved source host, IL-appropriate destination, transfer within SLA window",
]
_XD_DENIED_REASONS = [
    "Source IP 10.30.1.20 (ONPREM-WORKSTA) NOT in approved source list",
    "Destination 192.168.100.11 (JWICS-SRV2) not in approved destination list for this source",
    "Data classification UNCLASSIFIED does not meet JWICS minimum (SECRET)",
    "Transfer requested outside approved window (02:30 UTC — window is 08:00-20:00 UTC)",
    "Source certificate expired 3 days ago — transfer denied pending renewal",
    "Anomaly: same source made 5 requests in 60s — rate limit triggered",
    "Data payload size 2.1GB exceeds single-transfer limit of 500MB for this classification",
]


def _gen_xd_requests() -> list[tuple]:
    rows = []
    approved_src = [("10.30.1.10", "ONPREM-SRV1")]
    denied_src   = [("10.30.1.20", "ONPREM-WORKSTA"), ("10.30.1.11", "ONPREM-SRV2"),
                    ("10.30.1.99", "ONPREM-UNKNOWN"), ("10.99.0.2", "MGMT-JUMPHOST")]
    data_types   = ["SITREP", "INTEL-REPORT", "COMMS-LOG", "IMAGERY", "SIGINT", "HUMINT"]

    for i in range(23):
        src_ip, src_dev = random.choice(approved_src)
        t = random.uniform(5, 115)
        rows.append((_ts(t), src_ip, src_dev, "192.168.100.10", "JWICS-SRV1",
                     "CUI", "SECRET//SCI", random.choice(data_types),
                     "approved", "APPROVED", random.choice(_XD_APPROVED_REASONS),
                     round(random.uniform(8, 45), 1)))
    for i in range(7):
        src_ip, src_dev = random.choice(denied_src)
        t = random.uniform(5, 115)
        rows.append((_ts(t), src_ip, src_dev, "192.168.100.10", "JWICS-SRV1",
                     "CUI", "SECRET//SCI", random.choice(data_types),
                     "denied", "DENIED", _XD_DENIED_REASONS[i],
                     round(random.uniform(2, 8), 1)))
    rows.sort(key=lambda r: r[0])
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# TCCM credential requests
# ══════════════════════════════════════════════════════════════════════════════

def _gen_tccm_requests() -> list[tuple]:
    rows = []
    services  = ["ONPREM-SRV1", "ONPREM-SRV2", "BCAP-VDMS", "AWS-GOVCLOUD-IL4", "AZURE-GOV-IL5"]
    csps      = ["AWS-GOVCLOUD-IL4", "AZURE-GOV-IL5"]
    cred_types= ["sts-assume-role", "managed-identity-token", "spiffe-svid", "oauth2-client-creds"]

    for i in range(35):
        t = random.uniform(0, 120)
        svc = random.choice(services)
        token = uuid.uuid4().hex[:16]
        rows.append((_ts(t), svc, random.choice(csps), "CUI",
                     random.choice(cred_types), random.choice([1, 4, 8]),
                     "issued", token))
    for i in range(3):
        t = random.uniform(0, 120)
        rows.append((_ts(t), "ONPREM-WORKSTA", random.choice(csps), "CUI",
                     "sts-assume-role", 1, "denied", ""))
    for i in range(2):
        t = random.uniform(0, 120)
        token = uuid.uuid4().hex[:16]
        rows.append((_ts(t), "ONPREM-SRV1", random.choice(csps), "CUI",
                     "managed-identity-token", 4, "revoked", token))
    rows.sort(key=lambda r: r[0])
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# ISP latency measurements — 300 time-series points
# ══════════════════════════════════════════════════════════════════════════════

def _gen_isp_telemetry() -> list[tuple]:
    rows = []
    for i in range(100):
        t = i * 1.2
        # ATT — degrades at T+90
        lat_att = 12.0 + random.gauss(0, 1.5)
        pred_att = 0
        if t >= 85:
            lat_att = 12 + (t - 85) * 14 + random.gauss(0, 6)
            pred_att = 1 if t >= 87 else 0
        bgp_att = "up" if t < 98 else "down"
        loss_att = 0.0 if t < 90 else min(100.0, (t - 90) * 4)
        rows.append((_ts(t), "NIPR-EDGE-1", "ISP-ATT", "ether1",
                     round(lat_att, 1), round(abs(random.gauss(0, 1)), 1),
                     round(loss_att, 1), round(random.uniform(980, 1020), 1),
                     bgp_att, pred_att))
        # LUMEN — stable
        rows.append((_ts(t), "NIPR-EDGE-1", "ISP-LUMEN", "ether2",
                     round(18.0 + random.gauss(0, 2), 1), round(abs(random.gauss(0, 1.5)), 1),
                     0.0, round(random.uniform(480, 520), 1), "up", 0))
        # VERIZON — stable
        rows.append((_ts(t), "NIPR-EDGE-2", "ISP-VERIZON", "ether1",
                     round(15.0 + random.gauss(0, 1), 1), round(abs(random.gauss(0, 0.8)), 1),
                     0.0, round(random.uniform(990, 1010), 1), "up", 0))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def generate(reset: bool = False) -> dict:
    conn = _get_conn()
    counts: dict[str, int] = {}
    try:
        for ddl in _DDL:
            conn.execute(ddl)
        conn.commit()

        tables = {
            "ndc_bgp_events":    ("timestamp,router,peer,isp,event_type,bgp_as,latency_ms,prefix_count,med,details", _gen_bgp_events()),
            "ndc_syslog_events": ("timestamp,source_device,severity,log_prefix,message,src_ip,dst_ip,protocol,dst_port,mitre_tactic,mitre_technique,action_taken", _gen_syslog()),
            "ndc_stig_findings": ("device,vuln_id,severity,rule_title,finding,status,detected_at,remediated_at,kanban_task_id", _gen_stig_findings()),
            "ndc_traffic_flows": ("timestamp,src_device,dst_device,src_ip,dst_ip,protocol,port,bytes_per_sec,latency_ms,classification,bcap_inspected,threat_score", _gen_traffic_flows()),
            "ndc_xd_requests":   ("timestamp,src_ip,src_device,dst_ip,dst_device,classification_from,classification_to,data_type,status,ai_decision,ai_reasoning,approval_time_sec", _gen_xd_requests()),
            "ndc_tccm_requests": ("timestamp,requesting_service,target_csp,classification,credential_type,ttl_hours,status,token_id", _gen_tccm_requests()),
            "ndc_isp_telemetry": ("timestamp,router,isp,interface,latency_ms,jitter_ms,packet_loss_pct,bandwidth_mbps,bgp_status,predicted_failure", _gen_isp_telemetry()),
        }

        for table, (cols, rows) in tables.items():
            if reset:
                conn.execute(f"DELETE FROM {table}")
            placeholders = ",".join("?" * len(cols.split(",")))
            conn.executemany(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", rows)
            counts[table] = len(rows)

        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "counts": counts,
            "total": sum(counts.values()),
            "window": f"{_T0.strftime('%H:%M')} → {_NOW.strftime('%H:%M')} UTC (120 min)"}


def main() -> None:
    parser = argparse.ArgumentParser(description="DoD Lab Synthetic Data Generator")
    parser.add_argument("--json",  action="store_true")
    parser.add_argument("--reset", action="store_true", help="Clear existing data before insert")
    args = parser.parse_args()
    result = generate(args.reset)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Generated {result['total']} records across {len(result['counts'])} tables")
        for t, c in result["counts"].items():
            print(f"  {t:30s}: {c:4d} rows")


if __name__ == "__main__":
    main()
