#!/usr/bin/env python3
"""
Seed demo showcase data for /network/ and /migration-canvas/network-migration/.

Idempotent: every write is INSERT OR IGNORE / INSERT OR REPLACE keyed on
deterministic IDs, so re-running is a no-op.

Uses the canonical get_connection() abstraction (tools.network.db.init_db and
tools.migration_canvas.db.init_db) so it honors NC_STORAGE_BACKEND /
MC_STORAGE_BACKEND env vars transparently. Never uses raw sqlite3.connect.

Targets
-------
  /network/ (NDC index, tools/network/blueprint.py:181)
    - topologies          (3 enterprise graphs w/ EOL-bearing nodes)
    - nc_project_topologies (link 5 projects to topologies; fix orphan bug)
    - simulation_results  (6 sims)
    - nc_board_reviews    (4 pending)
    - nc_compliance_checks (8 mixed pass/fail)
    - nc_peering_agreements (6 operational, 1 with contract_end < 90d)
    - nc_notifications    (6, 3 unread)
    - nc_audit            (25 diverse action rows)

  /migration-canvas/network-migration/ (NMCE inventory, tools/migration_canvas/blueprint.py:1434)
    - ni_devices          (18 devices, 5 vendors, realistic EOL mix)
    - ni_device_configs   (4 running configs: JunOS, IOS-XE, EOS, PAN-OS)
    - mc_net_sessions     (2 sessions whose src_model matches a device model)
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone

# Canonical connection abstraction — RLS-safe, backend-portable
from tools.network.db.init_db import get_connection as nc_conn
from tools.migration_canvas.db.init_db import get_connection as mc_conn
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ndc.seed_demo_showcase")

NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat()
TODAY = NOW.date()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_count(conn, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def _existing_ids(conn, table: str, id_col: str = "id") -> set:
    try:
        return {r[0] for r in conn.execute(f"SELECT {id_col} FROM {table}").fetchall()}
    except Exception:
        return set()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(d) -> str:
    """Normalize to ISO-8601 string the dashboard can parse."""
    if d is None:
        return None
    if isinstance(d, str):
        return d
    return d.isoformat()


# ---------------------------------------------------------------------------
# 1. Topologies
# ---------------------------------------------------------------------------

def _wam42_graph() -> dict:
    """DEWIE / MX304 WAM edge topology — 18 nodes, EOL-bearing for EOL panel."""
    nodes = [
        # DEWIE MX1003 (EOL'd) + MX304 (replacement)
        {"id": "n-dewie-mx1003", "label": "DEWIE-MX1003", "type": "router", "vendor": "Juniper",
         "model": "MX1003", "x": 80, "y": 120,
         "config": {"vendor": "Juniper", "model": "MX1003", "firmware": "20.4R3-S2.4",
                    "eol_date": "2025-09-30", "eosup_date": "2025-12-31",
                    "mgmt_ip": "10.42.255.1", "site": "WAM-POP-ASH", "role": "edge-router"}},
        {"id": "n-dewie-mx304", "label": "DEWIE-MX304", "type": "router", "vendor": "Juniper",
         "model": "MX304", "x": 320, "y": 120,
         "config": {"vendor": "Juniper", "model": "MX304", "firmware": "23.2R1",
                    "eol_date": "2032-04-30", "eosup_date": "2037-04-30",
                    "mgmt_ip": "10.42.255.2", "site": "WAM-POP-ASH", "role": "edge-router"}},
        # Core / aggregation
        {"id": "n-core-jun-01", "label": "CORE-JUN-01", "type": "router", "vendor": "Juniper",
         "model": "MX480", "x": 560, "y": 80,
         "config": {"vendor": "Juniper", "model": "MX480", "firmware": "21.4R3",
                    "eol_date": "2027-08-15", "eosup_date": "2030-08-15",
                    "mgmt_ip": "10.42.255.10", "site": "WAM-POP-ASH"}},
        {"id": "n-core-jun-02", "label": "CORE-JUN-02", "type": "router", "vendor": "Juniper",
         "model": "MX480", "x": 560, "y": 240,
         "config": {"vendor": "Juniper", "model": "MX480", "firmware": "21.4R3",
                    "eol_date": "2027-08-15", "eosup_date": "2030-08-15",
                    "mgmt_ip": "10.42.255.11", "site": "WAM-POP-ASH"}},
        # Aggregation switches
        {"id": "n-agg-cisco-01", "label": "AGG-C9K-01", "type": "switch", "vendor": "Cisco",
         "model": "Catalyst 9500-48Y4C", "x": 80, "y": 320,
         "config": {"vendor": "Cisco", "model": "Catalyst 9500-48Y4C", "firmware": "17.09.04a",
                    "eol_date": "2026-12-15", "eosup_date": "2028-12-15",
                    "mgmt_ip": "10.42.255.20", "site": "WAM-POP-ASH"}},
        {"id": "n-agg-arista-01", "label": "AGG-ARISTA-01", "type": "switch", "vendor": "Arista",
         "model": "DCS-7280SR3-48YC6", "x": 320, "y": 320,
         "config": {"vendor": "Arista", "model": "DCS-7280SR3-48YC6", "firmware": "4.28.3F",
                    "eol_date": "2029-06-01", "eosup_date": "2032-06-01",
                    "mgmt_ip": "10.42.255.21", "site": "WAM-POP-ASH"}},
        # Firewall / security
        {"id": "n-fw-palo-01", "label": "FW-PALO-PA-01", "type": "firewall", "vendor": "Palo Alto",
         "model": "PA-5440", "x": 560, "y": 400,
         "config": {"vendor": "Palo Alto", "model": "PA-5440", "firmware": "10.2.7",
                    "eol_date": "2027-03-15", "eosup_date": "2030-03-15",
                    "mgmt_ip": "10.42.255.30", "site": "WAM-POP-ASH"}},
        {"id": "n-fw-palo-02", "label": "FW-PALO-PA-02", "type": "firewall", "vendor": "Palo Alto",
         "model": "PA-5440", "x": 800, "y": 400,
         "config": {"vendor": "Palo Alto", "model": "PA-5440", "firmware": "10.2.7",
                    "eol_date": "2027-03-15", "eosup_date": "2030-03-15",
                    "mgmt_ip": "10.42.255.31", "site": "WAM-POP-ASH"}},
        # Load balancer
        {"id": "n-lb-f5-01", "label": "LB-F5-BIGIP-01", "type": "load_balancer", "vendor": "F5",
         "model": "BIG-IP i5800", "x": 800, "y": 80,
         "config": {"vendor": "F5", "model": "BIG-IP i5800", "firmware": "16.1.3.2",
                    "eol_date": "2026-08-30", "eosup_date": "2028-08-30",
                    "mgmt_ip": "10.42.255.40", "site": "WAM-POP-ASH"}},
        # WAPs
        {"id": "n-ap-aruba-01", "label": "AP-ARUBA-505", "type": "access_point", "vendor": "Aruba",
         "model": "AP-505H", "x": 1040, "y": 160,
         "config": {"vendor": "Aruba", "model": "AP-505H", "firmware": "8.10.0.4",
                    "eol_date": "2028-12-01", "eosup_date": "2031-12-01",
                    "mgmt_ip": "10.42.255.50", "site": "WAM-POP-ASH"}},
        # BGP peers (external)
        {"id": "n-bgp-disa", "label": "DISA-BCAP", "type": "external_peer", "vendor": "DISA",
         "model": "BCAP Gateway", "x": 80, "y": -80,
         "config": {"vendor": "DISA", "model": "BCAP Gateway", "eol_date": "",
                    "mgmt_ip": "10.100.42.5", "site": "DISA-BCAP"}},
        {"id": "n-bgp-att", "label": "ISP-ATT", "type": "external_peer", "vendor": "AT&T",
         "model": "AS7018 Transit", "x": 320, "y": -80,
         "config": {"vendor": "AT&T", "model": "AS7018 Transit", "eol_date": "",
                    "mgmt_ip": "192.0.2.1", "site": "Equinix-ASH"}},
        {"id": "n-bgp-ntt", "label": "PEER-NTT", "type": "external_peer", "vendor": "NTT",
         "model": "AS2914 Peering", "x": 560, "y": -80,
         "config": {"vendor": "NTT", "model": "AS2914 Peering", "eol_date": "",
                    "mgmt_ip": "198.32.134.5", "site": "Equinix-ASH"}},
        # Internal hosts
        {"id": "n-host-app-01", "label": "APP-01", "type": "host", "vendor": "Dell",
         "model": "PowerEdge R750", "x": 1040, "y": 320,
         "config": {"vendor": "Dell", "model": "PowerEdge R750", "eol_date": "2030-04-15",
                    "mgmt_ip": "10.42.100.10", "site": "WAM-POP-ASH"}},
        {"id": "n-host-app-02", "label": "APP-02", "type": "host", "vendor": "Dell",
         "model": "PowerEdge R750", "x": 1040, "y": 400,
         "config": {"vendor": "Dell", "model": "PowerEdge R750", "eol_date": "2030-04-15",
                    "mgmt_ip": "10.42.100.11", "site": "WAM-POP-ASH"}},
        {"id": "n-host-db-01", "label": "DB-01", "type": "host", "vendor": "HPE",
         "model": "ProLiant DL380 Gen11", "x": 1040, "y": 480,
         "config": {"vendor": "HPE", "model": "ProLiant DL380 Gen11",
                    "eol_date": "2032-01-20", "mgmt_ip": "10.42.100.20", "site": "WAM-POP-ASH"}},
        # NTP / syslog
        {"id": "n-ntp-01", "label": "NTP-01", "type": "service", "vendor": "Meinberg",
         "model": "LANTIME M1000", "x": 560, "y": 560,
         "config": {"vendor": "Meinberg", "model": "LANTIME M1000",
                    "eol_date": "2029-09-09", "mgmt_ip": "10.42.15.20"}},
        {"id": "n-syslog-01", "label": "SYSLOG-01", "type": "service", "vendor": "Graylog",
         "model": "Appliance X3", "x": 800, "y": 560,
         "config": {"vendor": "Graylog", "model": "Appliance X3",
                    "eol_date": "2030-07-01", "mgmt_ip": "10.42.15.30"}},
    ]
    edges = [
        {"id": "e1", "source": "n-bgp-disa", "target": "n-dewie-mx1003", "type": "ebgp", "label": "DISA BCAP"},
        {"id": "e2", "source": "n-bgp-att", "target": "n-dewie-mx1003", "type": "ebgp", "label": "ATT Transit"},
        {"id": "e3", "source": "n-dewie-mx1003", "target": "n-dewie-mx304", "type": "lag", "label": "ae0 LAG"},
        {"id": "e4", "source": "n-dewie-mx304", "target": "n-core-jun-01", "type": "p2p", "label": "100G"},
        {"id": "e5", "source": "n-dewie-mx304", "target": "n-core-jun-02", "type": "p2p", "label": "100G"},
        {"id": "e6", "source": "n-core-jun-01", "target": "n-agg-cisco-01", "type": "p2p", "label": "40G"},
        {"id": "e7", "source": "n-core-jun-02", "target": "n-agg-arista-01", "type": "p2p", "label": "40G"},
        {"id": "e8", "source": "n-agg-cisco-01", "target": "n-fw-palo-01", "type": "p2p", "label": "25G"},
        {"id": "e9", "source": "n-agg-arista-01", "target": "n-fw-palo-01", "type": "p2p", "label": "25G"},
        {"id": "e10", "source": "n-fw-palo-01", "target": "n-fw-palo-02", "type": "ha", "label": "HA A/P"},
        {"id": "e11", "source": "n-fw-palo-02", "target": "n-lb-f5-01", "type": "p2p", "label": "25G"},
        {"id": "e12", "source": "n-lb-f5-01", "target": "n-ap-aruba-01", "type": "p2p", "label": "10G"},
        {"id": "e13", "source": "n-lb-f5-01", "target": "n-host-app-01", "type": "p2p", "label": "10G"},
        {"id": "e14", "source": "n-lb-f5-01", "target": "n-host-app-02", "type": "p2p", "label": "10G"},
        {"id": "e15", "source": "n-lb-f5-01", "target": "n-host-db-01", "type": "p2p", "label": "10G"},
        {"id": "e16", "source": "n-bgp-ntt", "target": "n-dewie-mx304", "type": "ebgp", "label": "NTT Peering"},
    ]
    return {"nodes": nodes, "edges": edges}


def _campus_lan_graph() -> dict:
    """Campus LAN — 16 nodes, 1 EOL'd Cisco 3850 stack."""
    nodes = [
        {"id": "c-core-01", "label": "CAMPUS-CORE-01", "type": "router", "vendor": "Cisco",
         "model": "Catalyst 9600", "x": 480, "y": 60,
         "config": {"vendor": "Cisco", "model": "Catalyst 9600", "firmware": "17.09.04a",
                    "eol_date": "2029-03-01", "eosup_date": "2032-03-01",
                    "mgmt_ip": "10.50.255.1", "site": "BLDG-1"}},
        {"id": "c-core-02", "label": "CAMPUS-CORE-02", "type": "router", "vendor": "Cisco",
         "model": "Catalyst 9600", "x": 720, "y": 60,
         "config": {"vendor": "Cisco", "model": "Catalyst 9600", "firmware": "17.09.04a",
                    "eol_date": "2029-03-01", "eosup_date": "2032-03-01",
                    "mgmt_ip": "10.50.255.2", "site": "BLDG-1"}},
        {"id": "c-dist-01", "label": "DIST-3850-01", "type": "switch", "vendor": "Cisco",
         "model": "Catalyst 3850-48T", "x": 200, "y": 200,
         "config": {"vendor": "Cisco", "model": "Catalyst 3850-48T", "firmware": "16.12.10",
                    "eol_date": "2026-04-30", "eosup_date": "2027-04-30",
                    "mgmt_ip": "10.50.255.10", "site": "BLDG-1"}},
        {"id": "c-dist-02", "label": "DIST-3850-02", "type": "switch", "vendor": "Cisco",
         "model": "Catalyst 3850-48T", "x": 360, "y": 200,
         "config": {"vendor": "Cisco", "model": "Catalyst 3850-48T", "firmware": "16.12.10",
                    "eol_date": "2026-04-30", "eosup_date": "2027-04-30",
                    "mgmt_ip": "10.50.255.11", "site": "BLDG-1"}},
        {"id": "c-dist-03", "label": "DIST-9300-01", "type": "switch", "vendor": "Cisco",
         "model": "Catalyst 9300-48P", "x": 520, "y": 200,
         "config": {"vendor": "Cisco", "model": "Catalyst 9300-48P", "firmware": "17.09.04a",
                    "eol_date": "2029-09-15", "eosup_date": "2032-09-15",
                    "mgmt_ip": "10.50.255.12", "site": "BLDG-2"}},
        {"id": "c-dist-04", "label": "DIST-9300-02", "type": "switch", "vendor": "Cisco",
         "model": "Catalyst 9300-48P", "x": 680, "y": 200,
         "config": {"vendor": "Cisco", "model": "Catalyst 9300-48P", "firmware": "17.09.04a",
                    "eol_date": "2029-09-15", "eosup_date": "2032-09-15",
                    "mgmt_ip": "10.50.255.13", "site": "BLDG-2"}},
        {"id": "c-fw-fort-01", "label": "FW-FORTIGATE-01", "type": "firewall", "vendor": "Fortinet",
         "model": "FortiGate 600F", "x": 480, "y": 360,
         "config": {"vendor": "Fortinet", "model": "FortiGate 600F", "firmware": "7.4.4",
                    "eol_date": "2028-05-20", "eosup_date": "2031-05-20",
                    "mgmt_ip": "10.50.255.30"}},
        {"id": "c-wlc-01", "label": "WLC-9800-01", "type": "wireless_controller", "vendor": "Cisco",
         "model": "C9800-40-K9", "x": 720, "y": 360,
         "config": {"vendor": "Cisco", "model": "C9800-40-K9", "firmware": "17.09.04a",
                    "eol_date": "2029-12-01", "eosup_date": "2032-12-01",
                    "mgmt_ip": "10.50.255.40"}},
        {"id": "c-ap-01", "label": "AP-9166I-01", "type": "access_point", "vendor": "Cisco",
         "model": "CW9166I", "x": 200, "y": 480,
         "config": {"vendor": "Cisco", "model": "CW9166I", "firmware": "8.10.196.0",
                    "eol_date": "2030-01-15", "mgmt_ip": "10.50.255.50"}},
        {"id": "c-ap-02", "label": "AP-9166I-02", "type": "access_point", "vendor": "Cisco",
         "model": "CW9166I", "x": 360, "y": 480,
         "config": {"vendor": "Cisco", "model": "CW9166I", "firmware": "8.10.196.0",
                    "eol_date": "2030-01-15", "mgmt_ip": "10.50.255.51"}},
        {"id": "c-ap-03", "label": "AP-9166I-03", "type": "access_point", "vendor": "Cisco",
         "model": "CW9166I", "x": 520, "y": 480,
         "config": {"vendor": "Cisco", "model": "CW9166I", "firmware": "8.10.196.0",
                    "eol_date": "2030-01-15", "mgmt_ip": "10.50.255.52"}},
        {"id": "c-ap-04", "label": "AP-9166I-04", "type": "access_point", "vendor": "Cisco",
         "model": "CW9166I", "x": 680, "y": 480,
         "config": {"vendor": "Cisco", "model": "CW9166I", "firmware": "8.10.196.0",
                    "eol_date": "2030-01-15", "mgmt_ip": "10.50.255.53"}},
        {"id": "c-printer-01", "label": "PRINTER-FLOOR-1", "type": "host", "vendor": "HP",
         "model": "LaserJet M751", "x": 80, "y": 360,
         "config": {"vendor": "HP", "model": "LaserJet M751",
                    "eol_date": "2027-11-30", "mgmt_ip": "10.50.100.50"}},
        {"id": "c-phones-01", "label": "PHONES-CUCM", "type": "service", "vendor": "Cisco",
         "model": "CUCM 14", "x": 880, "y": 200,
         "config": {"vendor": "Cisco", "model": "CUCM 14", "eol_date": "2028-10-15",
                    "mgmt_ip": "10.50.100.60"}},
        {"id": "c-vid-01", "label": "VID-CODEC-01", "type": "host", "vendor": "Polycom",
         "model": "Studio X70", "x": 880, "y": 360,
         "config": {"vendor": "Polycom", "model": "Studio X70",
                    "eol_date": "2029-06-15", "mgmt_ip": "10.50.100.70"}},
        {"id": "c-bldg-rtr-01", "label": "BLDG-RTR-01", "type": "router", "vendor": "Cisco",
         "model": "ISR 4451-X", "x": 80, "y": 60,
         "config": {"vendor": "Cisco", "model": "ISR 4451-X", "firmware": "17.09.04a",
                    "eol_date": "2026-11-30", "eosup_date": "2028-11-30",
                    "mgmt_ip": "10.50.255.100", "site": "BLDG-EDGE"}},
    ]
    edges = [
        {"id": "c-e1", "source": "c-bldg-rtr-01", "target": "c-core-01", "type": "p2p", "label": "10G WAN"},
        {"id": "c-e2", "source": "c-core-01", "target": "c-core-02", "type": "vss", "label": "VSS"},
        {"id": "c-e3", "source": "c-core-01", "target": "c-dist-01", "type": "p2p", "label": "10G"},
        {"id": "c-e4", "source": "c-core-01", "target": "c-dist-02", "type": "p2p", "label": "10G"},
        {"id": "c-e5", "source": "c-core-02", "target": "c-dist-03", "type": "p2p", "label": "10G"},
        {"id": "c-e6", "source": "c-core-02", "target": "c-dist-04", "type": "p2p", "label": "10G"},
        {"id": "c-e7", "source": "c-dist-01", "target": "c-fw-fort-01", "type": "p2p"},
        {"id": "c-e8", "source": "c-dist-02", "target": "c-fw-fort-01", "type": "p2p"},
        {"id": "c-e9", "source": "c-dist-03", "target": "c-fw-fort-01", "type": "p2p"},
        {"id": "c-e10", "source": "c-dist-04", "target": "c-fw-fort-01", "type": "p2p"},
        {"id": "c-e11", "source": "c-fw-fort-01", "target": "c-wlc-01", "type": "p2p"},
        {"id": "c-e12", "source": "c-wlc-01", "target": "c-ap-01", "type": "capwap"},
        {"id": "c-e13", "source": "c-wlc-01", "target": "c-ap-02", "type": "capwap"},
        {"id": "c-e14", "source": "c-wlc-01", "target": "c-ap-03", "type": "capwap"},
        {"id": "c-e15", "source": "c-wlc-01", "target": "c-ap-04", "type": "capwap"},
        {"id": "c-e16", "source": "c-dist-01", "target": "c-printer-01", "type": "p2p"},
        {"id": "c-e17", "source": "c-dist-04", "target": "c-phones-01", "type": "p2p"},
        {"id": "c-e18", "source": "c-dist-04", "target": "c-vid-01", "type": "p2p"},
    ]
    return {"nodes": nodes, "edges": edges}


def _ai_ml_fabric_graph() -> dict:
    """AI/ML fabric — 14 nodes, NVIDIA/HPE/Huawei, GPU-heavy, urgent EOL."""
    nodes = [
        {"id": "a-spine-01", "label": "SPINE-NVA-01", "type": "switch", "vendor": "NVIDIA",
         "model": "MSN2010-CB2F", "x": 480, "y": 60,
         "config": {"vendor": "NVIDIA", "model": "MSN2010-CB2F", "firmware": "MLNX-OS 3.10.4010",
                    "eol_date": "2026-06-30", "eosup_date": "2028-06-30",
                    "mgmt_ip": "10.99.255.1", "site": "DC-AI-01"}},
        {"id": "a-spine-02", "label": "SPINE-NVA-02", "type": "switch", "vendor": "NVIDIA",
         "model": "MSN2010-CB2F", "x": 720, "y": 60,
         "config": {"vendor": "NVIDIA", "model": "MSN2010-CB2F", "firmware": "MLNX-OS 3.10.4010",
                    "eol_date": "2026-06-30", "eosup_date": "2028-06-30",
                    "mgmt_ip": "10.99.255.2", "site": "DC-AI-01"}},
        {"id": "a-leaf-01", "label": "LEAF-NVA-01", "type": "switch", "vendor": "NVIDIA",
         "model": "MQM8700-HS2F", "x": 240, "y": 200,
         "config": {"vendor": "NVIDIA", "model": "MQM8700-HS2F", "firmware": "MLNX-OS 3.10.4010",
                    "eol_date": "2027-01-31", "eosup_date": "2029-01-31",
                    "mgmt_ip": "10.99.255.10", "site": "DC-AI-01"}},
        {"id": "a-leaf-02", "label": "LEAF-NVA-02", "type": "switch", "vendor": "NVIDIA",
         "model": "MQM8700-HS2F", "x": 480, "y": 200,
         "config": {"vendor": "NVIDIA", "model": "MQM8700-HS2F", "firmware": "MLNX-OS 3.10.4010",
                    "eol_date": "2027-01-31", "eosup_date": "2029-01-31",
                    "mgmt_ip": "10.99.255.11", "site": "DC-AI-01"}},
        {"id": "a-leaf-03", "label": "LEAF-NVA-03", "type": "switch", "vendor": "NVIDIA",
         "model": "MQM8700-HS2F", "x": 720, "y": 200,
         "config": {"vendor": "NVIDIA", "model": "MQM8700-HS2F", "firmware": "MLNX-OS 3.10.4010",
                    "eol_date": "2027-01-31", "eosup_date": "2029-01-31",
                    "mgmt_ip": "10.99.255.12", "site": "DC-AI-01"}},
        {"id": "a-leaf-04", "label": "LEAF-NVA-04", "type": "switch", "vendor": "NVIDIA",
         "model": "MQM8700-HS2F", "x": 960, "y": 200,
         "config": {"vendor": "NVIDIA", "model": "MQM8700-HS2F", "firmware": "MLNX-OS 3.10.4010",
                    "eol_date": "2027-01-31", "eosup_date": "2029-01-31",
                    "mgmt_ip": "10.99.255.13", "site": "DC-AI-01"}},
        # GPU servers
        {"id": "a-gpu-01", "label": "GPU-NODE-01", "type": "host", "vendor": "HPE",
         "model": "Apollo 6500 Gen10", "x": 240, "y": 400,
         "config": {"vendor": "HPE", "model": "Apollo 6500 Gen10",
                    "eol_date": "2026-09-15", "eosup_date": "2028-09-15",
                    "mgmt_ip": "10.99.100.10", "site": "DC-AI-01",
                    "role": "training-cluster"}},
        {"id": "a-gpu-02", "label": "GPU-NODE-02", "type": "host", "vendor": "HPE",
         "model": "Apollo 6500 Gen10", "x": 480, "y": 400,
         "config": {"vendor": "HPE", "model": "Apollo 6500 Gen10",
                    "eol_date": "2026-09-15", "eosup_date": "2028-09-15",
                    "mgmt_ip": "10.99.100.11", "site": "DC-AI-01"}},
        {"id": "a-gpu-03", "label": "GPU-NODE-03", "type": "host", "vendor": "HPE",
         "model": "Apollo 6500 Gen11", "x": 720, "y": 400,
         "config": {"vendor": "HPE", "model": "Apollo 6500 Gen11",
                    "eol_date": "2029-04-15", "eosup_date": "2032-04-15",
                    "mgmt_ip": "10.99.100.12", "site": "DC-AI-01"}},
        {"id": "a-gpu-04", "label": "GPU-NODE-04", "type": "host", "vendor": "HPE",
         "model": "Apollo 6500 Gen11", "x": 960, "y": 400,
         "config": {"vendor": "HPE", "model": "Apollo 6500 Gen11",
                    "eol_date": "2029-04-15", "eosup_date": "2032-04-15",
                    "mgmt_ip": "10.99.100.13", "site": "DC-AI-01"}},
        # Storage
        {"id": "a-stor-01", "label": "STOR-NVA-01", "type": "host", "vendor": "DDN",
         "model": "AI400X2", "x": 240, "y": 600,
         "config": {"vendor": "DDN", "model": "AI400X2",
                    "eol_date": "2028-03-01", "mgmt_ip": "10.99.100.20", "site": "DC-AI-01"}},
        {"id": "a-stor-02", "label": "STOR-NVA-02", "type": "host", "vendor": "DDN",
         "model": "AI400X2", "x": 720, "y": 600,
         "config": {"vendor": "DDN", "model": "AI400X2",
                    "eol_date": "2028-03-01", "mgmt_ip": "10.99.100.21", "site": "DC-AI-01"}},
        # Mgmt
        {"id": "a-bmc-01", "label": "BMC-NVA-01", "type": "service", "vendor": "NVIDIA",
         "model": "UFM Enterprise", "x": 480, "y": 600,
         "config": {"vendor": "NVIDIA", "model": "UFM Enterprise",
                    "eol_date": "2027-07-01", "mgmt_ip": "10.99.255.40"}},
        {"id": "a-fw-01", "label": "FW-AI-PALO", "type": "firewall", "vendor": "Palo Alto",
         "model": "PA-3440", "x": 960, "y": 600,
         "config": {"vendor": "Palo Alto", "model": "PA-3440", "firmware": "10.2.7",
                    "eol_date": "2027-11-15", "mgmt_ip": "10.99.255.50"}},
    ]
    edges = [
        {"id": "a-e1", "source": "a-leaf-01", "target": "a-spine-01", "type": "p2p", "label": "200G HDR"},
        {"id": "a-e2", "source": "a-leaf-02", "target": "a-spine-01", "type": "p2p", "label": "200G HDR"},
        {"id": "a-e3", "source": "a-leaf-03", "target": "a-spine-02", "type": "p2p", "label": "200G HDR"},
        {"id": "a-e4", "source": "a-leaf-04", "target": "a-spine-02", "type": "p2p", "label": "200G HDR"},
        {"id": "a-e5", "source": "a-gpu-01", "target": "a-leaf-01", "type": "p2p", "label": "400G NDR"},
        {"id": "a-e6", "source": "a-gpu-02", "target": "a-leaf-02", "type": "p2p", "label": "400G NDR"},
        {"id": "a-e7", "source": "a-gpu-03", "target": "a-leaf-03", "type": "p2p", "label": "400G NDR"},
        {"id": "a-e8", "source": "a-gpu-04", "target": "a-leaf-04", "type": "p2p", "label": "400G NDR"},
        {"id": "a-e9", "source": "a-stor-01", "target": "a-leaf-01", "type": "p2p", "label": "200G"},
        {"id": "a-e10", "source": "a-stor-02", "target": "a-leaf-03", "type": "p2p", "label": "200G"},
        {"id": "a-e11", "source": "a-bmc-01", "target": "a-spine-01", "type": "p2p"},
        {"id": "a-e12", "source": "a-fw-01", "target": "a-leaf-04", "type": "p2p"},
    ]
    return {"nodes": nodes, "edges": edges}


TOPOLOGIES = [
    {
        "id": "topo-wam-42",
        "name": "DEWIE WAM POP Ashburn (MX1003 → MX304)",
        "description": "Primary NIPR edge router at WAM-POP-ASH. DEWIE-MX1003 reached EOL 2025-09-30; replacement target MX304. Carries DISA BCAP, AT&T transit, NTT peering.",
        "classification": "CUI // SP-CTI",
        "graph": _wam42_graph,
    },
    {
        "id": "topo-campus-bldg1-001",
        "name": "Campus LAN — BLDG-1 / BLDG-2 (Cisco 9600 + 3850)",
        "description": "Two-building campus with VSS core, four distribution stacks, 9800 WLC, 4 APs, CUCM cluster. Catalyst 3850 stacks approaching EOL Q2 2026.",
        "classification": "CUI // SP-CTI",
        "graph": _campus_lan_graph,
    },
    {
        "id": "topo-aiml-fabric-dc01",
        "name": "AI/ML Fabric — DC-AI-01 (NVIDIA SN2010 + HPE Apollo)",
        "description": "Training fabric: 2× SN2010 spines, 4× MQM8700 leaves, 4× HPE Apollo 6500 GPU nodes, 2× DDN AI400X2 storage. Mix of Gen10 (EOL 2026) and Gen11.",
        "classification": "CUI // SP-CTI",
        "graph": _ai_ml_fabric_graph,
    },
]


def ensure_topologies(conn) -> dict:
    inserted = 0
    skipped = 0
    for t in TOPOLOGIES:
        existing = conn.execute("SELECT id FROM topologies WHERE id = ?", (t["id"],)).fetchone()
        if existing:
            # Keep graph_json updated if it has nodes — only overwrite if empty
            row = conn.execute("SELECT graph_json FROM topologies WHERE id = ?", (t["id"],)).fetchone()
            try:
                gj = json.loads(row["graph_json"] or "{}")
                if not gj.get("nodes"):
                    conn.execute(
                        "UPDATE topologies SET name=?, description=?, graph_json=?, classification=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (t["name"], t["description"], json.dumps(t["graph"]()), t["classification"], t["id"]),
                    )
                    inserted += 1
                else:
                    skipped += 1
            except Exception:
                conn.execute(
                    "UPDATE topologies SET name=?, description=?, graph_json=?, classification=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (t["name"], t["description"], json.dumps(t["graph"]()), t["classification"], t["id"]),
                )
                inserted += 1
        else:
            conn.execute(
                """INSERT INTO topologies (id, name, description, graph_json, template_id, classification, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (t["id"], t["name"], t["description"], json.dumps(t["graph"]()), t["classification"]),
            )
            inserted += 1
    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


# ---------------------------------------------------------------------------
# 2. Project links (orphan fix)
# ---------------------------------------------------------------------------

PROJECT_TOPOLOGY_LINKS = [
    # (project_id_substring, topology_id) — resolved dynamically
    ("proj-dewie-mx304", "topo-wam-42"),
    ("0a076e95", "topo-wam-42"),  # WAN Edge Refresh 2026 (one of the dupes)
    ("73037b7b", "topo-campus-bldg1-001"),  # Core Network Refresh
    ("90784939", "topo-aiml-fabric-dc01"),  # DMZ Redesign
    ("98b5d2f2", "topo-aiml-fabric-dc01"),  # WAN Edge Refresh 2026 (other dupe)
]


def ensure_project_links(conn) -> dict:
    inserted = 0
    for proj_id, topo_id in PROJECT_TOPOLOGY_LINKS:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO nc_project_topologies (project_id, topology_id) VALUES (?, ?)",
                (proj_id, topo_id),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "ensure_project_links: best-effort INSERT into nc_project_topologies failed (non-blocking): %s",
                exc,
            )
    conn.commit()
    return {"inserted": inserted, "skipped": len(PROJECT_TOPOLOGY_LINKS) - inserted}


# ---------------------------------------------------------------------------
# 3. Devices (ni_devices)
# ---------------------------------------------------------------------------

# 18 devices, 5 vendors × 4 types, EOL mix
DEVICES = [
    # (id, topology_id, node_id, label, device_type, vendor, model, fw, eol, eos, site, rack, crit, downstream, notes)
    # DEWIE pair (matches seed_dewie_demo.py IDs)
    ("dev-dewie-mx1003", "topo-wam-42", "n-dewie-mx1003", "DEWIE-MX1003", "router", "Juniper", "MX1003", "20.4R3-S2.4", "2025-09-30", "2025-12-31", "WAM-POP-ASH", "Rack-42-U15", 0.92, 24,
     "Primary edge router for NIPR trunk and DISA BCAP peering. EOL'd 2025-09-30, in tail support.", "edge-router"),
    ("dev-dewie-mx304", "topo-wam-42", "n-dewie-mx304", "DEWIE-MX304", "router", "Juniper", "MX304", "23.2R1", "2032-04-30", "2037-04-30", "WAM-POP-ASH", "Rack-42-U16", 0.10, 0,
     "Replacement target for DEWIE-MX1003. Higher throughput, lower power.", "edge-router"),
    # Additional Juniper — core (urgent EOL 2026)
    ("dev-jun-mx480-01", "topo-wam-42", "n-core-jun-01", "CORE-JUN-01", "router", "Juniper", "MX480", "21.4R3", "2027-08-15", "2030-08-15", "WAM-POP-ASH", "Rack-43-U01", 0.85, 18,
     "Core router — EOL within 24 months. Plan MX304 replacement Q3 2027.", "core-router"),
    ("dev-jun-mx480-02", "topo-wam-42", "n-core-jun-02", "CORE-JUN-02", "router", "Juniper", "MX480", "21.4R3", "2027-08-15", "2030-08-15", "WAM-POP-ASH", "Rack-43-U02", 0.85, 18,
     "Core router — EOL within 24 months.", "core-router"),
    # Cisco aggregation (urgent EOL 2026)
    ("dev-cisco-cat9500-01", "topo-wam-42", "n-agg-cisco-01", "AGG-C9K-01", "switch", "Cisco", "Catalyst 9500-48Y4C", "17.09.04a", "2026-12-15", "2028-12-15", "WAM-POP-ASH", "Rack-44-U03", 0.78, 12,
     "Aggregation switch, EOL within 12 months. Replacement Catalyst 9600 planned.", "aggregation-switch"),
    # Arista aggregation (healthy)
    ("dev-arista-7280-01", "topo-wam-42", "n-agg-arista-01", "AGG-ARISTA-01", "switch", "Arista", "DCS-7280SR3-48YC6", "4.28.3F", "2029-06-01", "2032-06-01", "WAM-POP-ASH", "Rack-44-U04", 0.80, 12,
     "Aggregation switch, healthy EOS runway.", "aggregation-switch"),
    # Palo Alto firewalls (healthy)
    ("dev-palo-pa5440-01", "topo-wam-42", "n-fw-palo-01", "FW-PALO-PA-01", "firewall", "Palo Alto", "PA-5440", "10.2.7", "2027-03-15", "2030-03-15", "WAM-POP-ASH", "Rack-45-U01", 0.95, 30,
     "Perimeter firewall primary, HA paired with PA-02.", "perimeter-fw"),
    ("dev-palo-pa5440-02", "topo-wam-42", "n-fw-palo-02", "FW-PALO-PA-02", "firewall", "Palo Alto", "PA-5440", "10.2.7", "2027-03-15", "2030-03-15", "WAM-POP-ASH", "Rack-45-U02", 0.90, 30,
     "Perimeter firewall standby.", "perimeter-fw"),
    # F5 LB (soon EOL)
    ("dev-f5-bigip-01", "topo-wam-42", "n-lb-f5-01", "LB-F5-BIGIP-01", "load_balancer", "F5", "BIG-IP i5800", "16.1.3.2", "2026-08-30", "2028-08-30", "WAM-POP-ASH", "Rack-45-U05", 0.88, 16,
     "Application load balancer. EOL within 12 months. rSeries replacement target.", "app-lb"),
    # Aruba AP
    ("dev-aruba-ap505-01", "topo-wam-42", "n-ap-aruba-01", "AP-ARUBA-505", "access_point", "Aruba", "AP-505H", "8.10.0.4", "2028-12-01", "2031-12-01", "WAM-POP-ASH", "BLDG-1-FLR2", 0.30, 8,
     "Hospitality AP, healthy.", "wireless-ap"),
    # Campus devices
    ("dev-cisco-cat3850-01", "topo-campus-bldg1-001", "c-dist-01", "DIST-3850-01", "switch", "Cisco", "Catalyst 3850-48T", "16.12.10", "2026-04-30", "2027-04-30", "BLDG-1", "Rack-101-U03", 0.70, 10,
     "Campus distribution switch. EOL in <12 months. Migrate to Catalyst 9300.", "dist-switch"),
    ("dev-cisco-cat3850-02", "topo-campus-bldg1-001", "c-dist-02", "DIST-3850-02", "switch", "Cisco", "Catalyst 3850-48T", "16.12.10", "2026-04-30", "2027-04-30", "BLDG-1", "Rack-101-U04", 0.70, 10,
     "Campus distribution switch. EOL in <12 months.", "dist-switch"),
    ("dev-cisco-cat9600-01", "topo-campus-bldg1-001", "c-core-01", "CAMPUS-CORE-01", "router", "Cisco", "Catalyst 9600", "17.09.04a", "2029-03-01", "2032-03-01", "BLDG-1", "Rack-100-U01", 0.90, 50,
     "Campus core, VSS pair with CORE-02.", "core-router"),
    ("dev-cisco-cat9600-02", "topo-campus-bldg1-001", "c-core-02", "CAMPUS-CORE-02", "router", "Cisco", "Catalyst 9600", "17.09.04a", "2029-03-01", "2032-03-01", "BLDG-1", "Rack-100-U02", 0.90, 50,
     "Campus core, VSS pair with CORE-01.", "core-router"),
    # AI/ML — NVIDIA switches (urgent EOL)
    ("dev-nvidia-sn2010-01", "topo-aiml-fabric-dc01", "a-spine-01", "SPINE-NVA-01", "switch", "NVIDIA", "MSN2010-CB2F", "MLNX-OS 3.10.4010", "2026-06-30", "2028-06-30", "DC-AI-01", "Rack-AI-U01", 0.95, 16,
     "AI fabric spine, EOL in <12 months. Critical for training jobs.", "fabric-spine"),
    ("dev-nvidia-sn2010-02", "topo-aiml-fabric-dc01", "a-spine-02", "SPINE-NVA-02", "switch", "NVIDIA", "MSN2010-CB2F", "MLNX-OS 3.10.4010", "2026-06-30", "2028-06-30", "DC-AI-01", "Rack-AI-U02", 0.95, 16,
     "AI fabric spine, EOL in <12 months.", "fabric-spine"),
    # HPE Apollo GPU node (urgent)
    ("dev-hpe-apollo6500-01", "topo-aiml-fabric-dc01", "a-gpu-01", "GPU-NODE-01", "host", "HPE", "Apollo 6500 Gen10", "", "2026-09-15", "2028-09-15", "DC-AI-01", "Rack-AI-U10", 0.92, 0,
     "Training cluster GPU node. Gen10 reaching EOL Q3 2026.", "gpu-host"),
    # DDN storage (healthy)
    ("dev-ddn-ai400-01", "topo-aiml-fabric-dc01", "a-stor-01", "STOR-NVA-01", "host", "DDN", "AI400X2", "", "2028-03-01", "2031-03-01", "DC-AI-01", "Rack-AI-U20", 0.88, 0,
     "Parallel filesystem storage for training data.", "ai-storage"),
]


def ensure_devices(conn) -> dict:
    inserted = 0
    for d in DEVICES:
        (dev_id, topo_id, node_id, label, dtype, vendor, model, fw, eol, eos, site, rack, crit, ds, notes, role) = d
        props = json.dumps({"role": role})
        try:
            conn.execute(
                """INSERT OR IGNORE INTO ni_devices
                   (id, topology_id, node_id, label, device_type, vendor, model,
                    firmware_version, eol_date, eos_date, site, rack_location,
                    criticality_score, downstream_count, notes, properties_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dev_id, topo_id, node_id, label, dtype, vendor, model, fw, eol, eos, site, rack, crit, ds, notes, props,
                 NOW_ISO, NOW_ISO),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("ensure_devices: best-effort INSERT into ni_devices failed (non-blocking): %s", exc)
    conn.commit()
    return {"inserted": inserted, "skipped": len(DEVICES) - inserted}


# ---------------------------------------------------------------------------
# 4. Device configs
# ---------------------------------------------------------------------------

JUNIPER_DEWIE_CONFIG = """## Last changed: 2025-05-20 14:33:12 UTC by network-ops
## Router: DEWIE-MX1003 (Juniper MX1003)
## Site: WAM-POP-ASH, Rack-42-U15
## Role: Edge Router -- NIPR trunk + DISA BCAP eBGP

system {
    host-name DEWIE-MX1003;
    domain-name wam-pop-ash.mil;
    time-zone UTC;
    name-server {
        10.42.15.10;
        10.42.15.11;
    }
    ntp {
        server 10.42.15.20;
    }
    syslog {
        user * { any emergency; }
        host 10.42.15.30 { any info; }
    }
}

interfaces {
    ge-0/0/0 { description "NIPR Trunk -- North to DISA BCAP"; unit 0 { family inet { address 10.100.42.1/30; } } }
    ge-0/0/1 { description "DISA BCAP eBGP Peer"; unit 0 { family inet { address 10.100.42.5/30; } } }
    xe-0/1/0 { description "LAG Member 1 -- South to Owned Switch"; gigether-options { 802.3ad ae0; } }
    xe-0/1/1 { description "LAG Member 2 -- South to Owned Switch"; gigether-options { 802.3ad ae0; } }
    ae0 {
        description "South LAG -- Owned Switch Cluster";
        aggregated-ether-options { lacp { active; periodic fast; } }
        unit 0 { family inet { address 10.42.16.1/24; } }
    }
    ge-0/0/2 { description "ISP-ATT eBGP Peer"; unit 0 { family inet { address 192.0.2.1/30; } } }
    lo0 { description "Loopback -- Router ID / Mgmt"; unit 0 { family inet { address 10.42.255.1/32 { primary; } } } }
}

routing-options {
    router-id 10.42.255.1;
    autonomous-system 64701;
    forwarding-table { export PFE-LB-OUT; }
}

protocols {
    bgp {
        group DISA-BCAP {
            type external;
            neighbor 10.100.42.6 { peer-as 274; description "DISA BCAP"; authentication-key "$9$5Fkm5FAtxNdw2aZ"; }
        }
        group ATT-TRANSIT {
            type external;
            neighbor 192.0.2.2 { peer-as 7018; description "ATT Transit"; import ATT-IN; export NET-OUT; }
        }
    }
    ospf {
        area 0.0.0.0 {
            interface lo0.0 { passive; }
            interface ae0.0;
        }
    }
}

policy-options {
    policy-statement ATT-IN { term deny-default { from { route-filter 0.0.0.0/0 exact; } then reject; } then accept; }
    policy-statement NET-OUT { term all { then accept; } }
    policy-statement PFE-LB-OUT { term all { then load-balance per-packet; } }
}

firewall {
    filter INPUT { term accept-icmp { from { protocol icmp; } then accept; } term accept-established { from { tcp-established; } then accept; } term drop { then { log; discard; } } }
}
"""

CISCO_AGG_CONFIG = """! Last changed 2025-09-12 08:14:33 by netadmin
! AGG-C9K-01 -- Cisco Catalyst 9500-48Y4C
! IOS-XE 17.09.04a
hostname AGG-C9K-01
!
vrf definition MGMT
 rd 65000:100
 address-family ipv4
  route-target export 65000:100
  route-target import 65000:100
!
interface Loopback0
 ip address 10.42.255.20 255.255.255.255
 ip ospf 1 area 0
!
interface FortyGigabitEthernet1/0/1
 description "Uplink to CORE-JUN-01 xe-0/0/0"
 ip address 10.42.20.1 255.255.255.252
 ip ospf network point-to-point
 ip ospf 1 area 0
!
interface FortyGigabitEthernet1/0/2
 description "Uplink to FW-PALO-PA-01 eth1/1"
 ip address 10.42.30.1 255.255.255.252
 ip ospf network point-to-point
 ip ospf 1 area 0
!
interface TwentyFiveGigE1/0/48
 description "Server-farm downlink"
 switchport mode access
 switchport access vlan 100
 spanning-tree portfast edge
!
router ospf 1
 router-id 10.42.255.20
 passive-interface default
 no passive-interface FortyGigabitEthernet1/0/1
 no passive-interface FortyGigabitEthernet1/0/2
!
ntp server 10.42.15.20
logging host 10.42.15.30
!
end
"""

ARISTA_AGG_CONFIG = """! Arista EOS 4.28.3F
! AGG-ARISTA-01 -- DCS-7280SR3-48YC6
!
hostname AGG-ARISTA-01
!
interface Ethernet1
   description "Uplink to CORE-JUN-02"
   no switchport
   ip address 10.42.21.1/31
   ip ospf network point-to-point
   ip ospf area 0.0.0.0
!
interface Ethernet53
   description "Server-farm downlink"
   switchport
   switchport access vlan 100
!
ip routing
!
router ospf 1
   router-id 10.42.255.21
   passive-interface Loopback0
   network 10.42.21.0/31 area 0.0.0.0
!
management api http-commands
   protocol https
   no shutdown
!
end
"""

PALO_FW_CONFIG = """## Palo Alto PAN-OS 10.2.7
## FW-PALO-PA-01 -- PA-5440 (Primary, HA pair)
##
set deviceconfig system hostname FW-PALO-PA-01
set deviceconfig system ip-address 10.42.255.30
set deviceconfig system netmask 255.255.255.0
set deviceconfig system default-gateway 10.42.30.5
set deviceconfig system dns-setting servers primary 10.42.15.10
set deviceconfig system ntp-servers primary-ntp-server ntp-server-address 10.42.15.20
##
set network interface ethernet ethernet1/1 layer3 ip 10.42.30.6/30
set network interface ethernet ethernet1/1 layer3 lldp-enable yes
set network interface ethernet ethernet1/2 layer3 ip 10.42.40.1/24
##
set network virtual-router default interface [ ethernet1/1 ethernet1/2 ]
##
set network zone trust network [ ethernet1/2 ]
set network zone untrust network [ ethernet1/1 ]
##
set vsys vsys1 zone trust zone-profile dos-protection STRICT
##
set rulebase security rules PERMIT-ICMP from untrust to trust source any destination any application icmp action allow
set rulebase security rules DENY-ALL-LOG from any to any source any destination any application any service any action deny log
##
set deviceconfig high-availability group 1 description "HA Pair with PA-02"
set deviceconfig high-availability group 1 mode active-passive
set deviceconfig high-availability group 1 peer-ip 10.42.255.31
##
commit description "Demo seeder baseline config"
"""

DEVICE_CONFIGS = [
    # (device_id, config_type, source, version, text)
    ("dev-dewie-mx1003", "running", "manual", 1, JUNIPER_DEWIE_CONFIG),
    ("dev-cisco-cat9500-01", "running", "manual", 1, CISCO_AGG_CONFIG),
    ("dev-arista-7280-01", "running", "manual", 1, ARISTA_AGG_CONFIG),
    ("dev-palo-pa5440-01", "running", "manual", 1, PALO_FW_CONFIG),
]


def ensure_device_configs(conn) -> dict:
    inserted = 0
    cfg_id = 0
    for dev_id, cfg_type, source, version, text in DEVICE_CONFIGS:
        cfg_id += 1
        h = _sha256(text)
        # INSERT OR REPLACE keyed on (device_id, config_type, source, version)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO ni_device_configs
                   (id, device_id, config_type, config_text, config_hash, source, version, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (f"cfg-{dev_id}-{cfg_type}-v{version}", dev_id, cfg_type, text, h, source, version, NOW_ISO),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "ensure_device_configs: best-effort INSERT into ni_device_configs failed (non-blocking): %s",
                exc,
            )
    conn.commit()
    return {"inserted": inserted, "skipped": len(DEVICE_CONFIGS) - inserted}


# ---------------------------------------------------------------------------
# 5. Simulation results
# ---------------------------------------------------------------------------

SIMULATIONS = [
    ("sim-wam42-spof-01", "topo-wam-42", "single_point_of_failure",
     "Failure of DEWIE-MX1003 isolates 24 downstream sites."),
    ("sim-wam42-fail-01", "topo-wam-42", "failover_drill",
     "Planned failover to DEWIE-MX304 succeeded in 47s. No packet loss > 5s."),
    ("sim-campus-spof-01", "topo-campus-bldg1-001", "single_point_of_failure",
     "Failure of CAMPUS-CORE-01 cascades to all 4 dist switches (mitigated by VSS)."),
    ("sim-campus-load-01", "topo-campus-bldg1-001", "load_capacity",
     "Peak ingress 47 Gbps — WLC link at 78% utilization."),
    ("sim-aiml-trace-01", "topo-aiml-fabric-dc01", "traceroute",
     "GPU-NODE-01 → STOR-NVA-01: 3-hop, 0.4 ms RTT, no congestion."),
    ("sim-aiml-fail-01", "topo-aiml-fabric-dc01", "failover_drill",
     "SPINE-NVA-01 graceful shutdown rerouted 16 GPU sessions in 12s."),
]


def ensure_simulations(conn) -> dict:
    inserted = 0
    for sim_id, topo_id, sim_type, summary in SIMULATIONS:
        result = json.dumps({
            "summary": summary,
            "ran_by": "demo_seeder",
            "status": "ok",
        })
        try:
            conn.execute(
                """INSERT OR IGNORE INTO simulation_results
                   (id, topology_id, sim_type, input_json, result_json, ran_at)
                   VALUES (?, ?, ?, '{}', ?, ?)""",
                (sim_id, topo_id, sim_type, result, NOW_ISO),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "ensure_simulations: best-effort INSERT into simulation_results failed (non-blocking): %s",
                exc,
            )
    conn.commit()
    return {"inserted": inserted, "skipped": len(SIMULATIONS) - inserted}


# ---------------------------------------------------------------------------
# 6. Board reviews (pending)
# ---------------------------------------------------------------------------

# (id, project_id, board_id, phase, scheduled_date, decision)
BOARD_REVIEWS = [
    ("rev-wam-arb-01", "proj-dewie-mx304", "board-arb", 1, (NOW + timedelta(days=3)).isoformat(), None),
    ("rev-wam-erb-01", "proj-dewie-mx304", "board-erb", 2, (NOW + timedelta(days=10)).isoformat(), None),
    ("rev-campus-erb-01", "73037b7b-cb90-438b-b5d6-920d7dc77681", "board-erb", 1, (NOW + timedelta(days=5)).isoformat(), None),
    ("rev-aiml-ccb-01", "90784939-eeae-473e-afa4-51e6384c9297", "board-ccb", 1, (NOW + timedelta(days=14)).isoformat(), None),
]


def ensure_board_reviews(conn) -> dict:
    inserted = 0
    for rev_id, proj_id, board_id, phase, sched, decision in BOARD_REVIEWS:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO nc_board_reviews
                   (id, project_id, board_id, phase, status, scheduled_date, decision, reviewer_names, package_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, '[]', '{}', ?, ?)""",
                (rev_id, proj_id, board_id, phase, sched, decision, NOW_ISO, NOW_ISO),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "ensure_board_reviews: best-effort INSERT into nc_board_reviews failed (non-blocking): %s",
                exc,
            )
    conn.commit()
    return {"inserted": inserted, "skipped": len(BOARD_REVIEWS) - inserted}


# ---------------------------------------------------------------------------
# 7. Compliance checks
# ---------------------------------------------------------------------------

# (id, topology_id, check_type, passed, failed, findings_count)
COMPLIANCE_CHECKS = [
    ("cc-wam-stig-01", "topo-wam-42", "stig_v3r1", 142, 6, 6),
    ("cc-wam-fedramp-01", "topo-wam-42", "fedramp_high", 78, 18, 18),
    ("cc-campus-stig-01", "topo-campus-bldg1-001", "stig_v3r1", 98, 24, 24),  # 24/122 = 80% — on the boundary
    ("cc-campus-cmmc-01", "topo-campus-bldg1-001", "cmmc_l2", 56, 8, 8),  # 87% pass
    ("cc-aiml-stig-01", "topo-aiml-fabric-dc01", "stig_v3r1", 64, 32, 32),  # 67% — under 80%
    ("cc-aiml-ciso-01", "topo-aiml-fabric-dc01", "ciso_benchmark", 22, 4, 4),
    ("cc-wam-zt-01", "topo-wam-42", "nist_800-207_zt", 38, 2, 2),  # 95% pass
    ("cc-campus-zt-01", "topo-campus-bldg1-001", "nist_800-207_zt", 30, 8, 8),  # 79% — under 80%
]


def ensure_compliance(conn) -> dict:
    inserted = 0
    for cc_id, topo_id, check_type, passed, failed, findings in COMPLIANCE_CHECKS:
        findings_json = json.dumps([{
            "id": f"{cc_id}-f{i}", "severity": ["low", "medium", "high"][i % 3],
            "control": f"AC-{i+1:02d}", "description": f"Finding {i+1} for {check_type}",
        } for i in range(findings)])
        try:
            conn.execute(
                """INSERT OR IGNORE INTO nc_compliance_checks
                   (id, topology_id, check_type, passed, failed, findings_json, ran_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cc_id, topo_id, check_type, passed, failed, findings_json, NOW_ISO),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "ensure_compliance: best-effort INSERT into nc_compliance_checks failed (non-blocking): %s",
                exc,
            )
    conn.commit()
    return {"inserted": inserted, "skipped": len(COMPLIANCE_CHECKS) - inserted}


# ---------------------------------------------------------------------------
# 8. Peering agreements (6 operational, 1 with contract_end < 90d)
# ---------------------------------------------------------------------------

# (id, peer_name, peer_asn, our_asn, peering_type, routing, status, purpose_cat, port, contract_start, contract_end, monthly_cost, sla_latency_ms, sla_uptime_pct, noc_email)
PEERING = [
    ("peer-ntt-001", "NTT", "2914", "64701", "settlement_free", "bgp", "operational", "transit", "100G",
     "2024-01-15", (TODAY + timedelta(days=720)).isoformat(), 18000, 8.5, 99.999, "noc@ntt.com"),
    ("peer-akamai-001", "Akamai", "20940", "64701", "settlement_free", "bgp", "operational", "content_delivery", "100G",
     "2023-08-22", (TODAY + timedelta(days=420)).isoformat(), 14500, 6.0, 99.999, "noc@akamai.com"),
    ("peer-cogent-001", "Cogent", "174", "64701", "paid", "bgp", "operational", "transit", "10G",
     "2024-03-10", (TODAY + timedelta(days=180)).isoformat(), 9500, 18.0, 99.95, "noc@cogent.com"),
    ("peer-cloudflare-001", "Cloudflare", "13335", "64701", "settlement_free", "bgp", "operational", "cloud_onramp", "10G",
     "2024-06-01", (TODAY + timedelta(days=300)).isoformat(), 12000, 4.5, 99.999, "noc@cloudflare.com"),
    ("peer-aws-direct-001", "AWS Direct Connect", "16509", "64701", "paid", "bgp", "operational", "cloud_onramp", "10G",
     "2023-11-15", (TODAY + timedelta(days=60)).isoformat(), 22500, 3.0, 99.99, "dx-noc@amazon.com"),  # <90d — IQE flag
    ("peer-azure-express-001", "Azure ExpressRoute", "12076", "64701", "paid", "bgp", "operational", "cloud_onramp", "10G",
     "2024-09-15", (TODAY + timedelta(days=550)).isoformat(), 21000, 5.0, 99.99, "noc@azure.microsoft.com"),
]


def ensure_peering(conn) -> dict:
    inserted = 0
    for row in PEERING:
        (p_id, peer, asn, our_asn, ptype, routing, status, purpose_cat, port,
         cstart, cend, cost, sla_lat, sla_up, noc_email) = row
        try:
            conn.execute(
                """INSERT OR IGNORE INTO nc_peering_agreements
                   (id, peer_name, peer_asn, our_asn, peering_type, routing_method, status,
                    purpose_category, port_speed, contract_start, contract_end, monthly_cost,
                    sla_latency_ms, sla_uptime_pct, sla_packet_loss, locations, ratio_limit,
                    noc_email, traffic_commit, notes, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p_id, peer, asn, our_asn, ptype, routing, status, purpose_cat, port,
                 cstart, cend, cost, sla_lat, sla_up, 0.001, "[]", "2:1",
                 noc_email, "10 Gbps commit",
                 f"Operational peer for {purpose_cat}; port {port}; expires {cend[:10]}",
                 NOW_ISO, NOW_ISO),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "ensure_peering: best-effort INSERT into nc_peering_agreements failed (non-blocking): %s",
                exc,
            )
    conn.commit()
    return {"inserted": inserted, "skipped": len(PEERING) - inserted}


# ---------------------------------------------------------------------------
# 9. Notifications
# ---------------------------------------------------------------------------

# (id, project_id, event_type, title, body, is_read)
NOTIFICATIONS = [
    ("notif-001", "proj-dewie-mx304", "review_submitted", "ARB review package ready for review",
     "Dewie MX304 ARB package is ready for review. Scheduled for 2026-06-06.", 0),
    ("notif-002", "proj-dewie-mx304", "gate_blocked", "Compliance check below threshold",
     "FedRAMP High compliance check 78/96 = 81.3% — passes the 80% gate, but 18 findings remain.", 0),
    ("notif-003", "90784939-eeae-473e-afa4-51e6384c9297", "phase_changed", "DMZ Redesign moved to Phase 2",
     "Project has been promoted to Phase 2 — detailed design and BOM.", 0),
    ("notif-004", "proj-dewie-mx304", "review_decided", "ARB approved with conditions",
     "Architecture Review Board approved the Dewie MX304 design with 3 conditions.", 1),
    ("notif-005", "73037b7b-cb90-438b-b5d6-920d7dc77681", "review_submitted", "ERB package submitted",
     "Core Network Refresh ERB package submitted for engineering review.", 1),
    ("notif-006", "proj-dewie-mx304", "phase_changed", "Project status: approved → deployed",
     "Project has been deployed to production. All change controls cleared.", 1),
]


def ensure_notifications(conn) -> dict:
    inserted = 0
    for n_id, proj_id, ev, title, body, is_read in NOTIFICATIONS:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO nc_notifications
                   (id, project_id, event_type, title, body, is_read, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (n_id, proj_id, ev, title, body, is_read, NOW_ISO),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "ensure_notifications: best-effort INSERT into nc_notifications failed (non-blocking): %s",
                exc,
            )
    conn.commit()
    return {"inserted": inserted, "skipped": len(NOTIFICATIONS) - inserted}


# ---------------------------------------------------------------------------
# 10. Audit enrichment (diverse action rows)
# ---------------------------------------------------------------------------

# (action, entity_type, entity_id, details, user_id)
AUDIT_ACTIONS = [
    ("topology_created", "topology", "topo-wam-42", "DEWIE WAM topology seeded with 18 nodes, 16 edges", "demo_seeder"),
    ("topology_created", "topology", "topo-campus-bldg1-001", "Campus LAN topology seeded with 16 nodes, 18 edges", "demo_seeder"),
    ("topology_created", "topology", "topo-aiml-fabric-dc01", "AI/ML fabric topology seeded with 14 nodes, 12 edges", "demo_seeder"),
    ("project_linked", "project", "proj-dewie-mx304", "Linked proj-dewie-mx304 → topo-wam-42", "demo_seeder"),
    ("project_linked", "project", "0a076e95-3222-442b-85b8-fd62f648e769", "Linked WAN Edge Refresh 2026 → topo-wam-42", "demo_seeder"),
    ("project_linked", "project", "73037b7b-cb90-438b-b5d6-920d7dc77681", "Linked Core Network Refresh → topo-campus-bldg1-001", "demo_seeder"),
    ("project_linked", "project", "90784939-eeae-473e-afa4-51e6384c9297", "Linked DMZ Redesign → topo-aiml-fabric-dc01", "demo_seeder"),
    ("device_added", "device", "dev-dewie-mx1003", "DEWIE-MX1003 inventory record created (EOL 2025-09-30)", "demo_seeder"),
    ("device_added", "device", "dev-dewie-mx304", "DEWIE-MX304 inventory record created (replacement)", "demo_seeder"),
    ("device_added", "device", "dev-cisco-cat3850-01", "Catalyst 3850 dist-01 inventory (EOL 2026-04-30)", "demo_seeder"),
    ("device_added", "device", "dev-nvidia-sn2010-01", "NVIDIA SN2010 spine-01 inventory (EOL 2026-06-30)", "demo_seeder"),
    ("device_added", "device", "dev-hpe-apollo6500-01", "HPE Apollo 6500 GPU node-01 inventory (EOL 2026-09-15)", "demo_seeder"),
    ("device_added", "device", "dev-f5-bigip-01", "F5 BIG-IP i5800 inventory (EOL 2026-08-30)", "demo_seeder"),
    ("config_uploaded", "config", "cfg-dev-dewie-mx1003-running-v1", "JunOS running config uploaded (SHA-256 verified)", "demo_seeder"),
    ("config_uploaded", "config", "cfg-dev-cisco-cat9500-01-running-v1", "IOS-XE running config uploaded", "demo_seeder"),
    ("config_uploaded", "config", "cfg-dev-arista-7280-01-running-v1", "Arista EOS running config uploaded", "demo_seeder"),
    ("config_uploaded", "config", "cfg-dev-palo-pa5440-01-running-v1", "PAN-OS running config uploaded", "demo_seeder"),
    ("simulation_run", "topology", "topo-wam-42", "single_point_of_failure simulation completed", "demo_seeder"),
    ("simulation_run", "topology", "topo-wam-42", "failover_drill simulation completed", "demo_seeder"),
    ("simulation_run", "topology", "topo-campus-bldg1-001", "load_capacity simulation completed", "demo_seeder"),
    ("simulation_run", "topology", "topo-aiml-fabric-dc01", "traceroute simulation completed", "demo_seeder"),
    ("compliance_check", "topology", "topo-wam-42", "STIG v3r1: 142/148 (95.9%)", "demo_seeder"),
    ("compliance_check", "topology", "topo-aiml-fabric-dc01", "STIG v3r1: 64/96 (66.7%) — BELOW THRESHOLD", "demo_seeder"),
    ("peering_signed", "peering", "peer-aws-direct-001", "AWS Direct Connect agreement operational (contract_end <90d)", "demo_seeder"),
    ("peering_signed", "peering", "peer-azure-express-001", "Azure ExpressRoute agreement operational", "demo_seeder"),
]


def ensure_audit(conn) -> dict:
    inserted = 0
    for action, etype, eid, details, user in AUDIT_ACTIONS:
        try:
            conn.execute(
                """INSERT INTO nc_audit (action, entity_type, entity_id, details, user_id, classification, ts)
                   VALUES (?, ?, ?, ?, ?, 'CUI // SP-CTI', ?)""",
                (action, etype, eid, details, user, NOW_ISO),
            )
            inserted += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("ensure_audit: best-effort INSERT into nc_audit failed (non-blocking): %s", exc)
    conn.commit()
    return {"inserted": inserted, "skipped": 0}


# ---------------------------------------------------------------------------
# 11. Migration sessions (mc_net_sessions in migration_canvas.db)
# ---------------------------------------------------------------------------

# (id, src_model, tgt_model, src_device_name, tgt_device_name, src_site, tgt_site, status, readiness_score)
MIGRATION_SESSIONS = [
    ("mcsess-dewie-mx304-01", "MX1003", "MX304", "DEWIE-MX1003", "DEWIE-MX304", "WAM-POP-ASH", "WAM-POP-ASH", "in_progress", 0.78),
    ("mcsess-cat3850-9300-01", "Catalyst 3850-48T", "Catalyst 9300-48P", "DIST-3850-01", "DIST-9300-01", "BLDG-1", "BLDG-1", "in_progress", 0.65),
]


def ensure_migration_sessions() -> dict:
    inserted = 0
    try:
        with mc_conn() as conn:
            for s_id, src_model, tgt_model, src_name, tgt_name, src_site, tgt_site, status, readiness in MIGRATION_SESSIONS:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO mc_net_sessions
                           (id, src_model, tgt_model, src_device_name, tgt_device_name,
                            src_site, tgt_site, config_parsed, readiness_score, status, classification, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,0,?,?, 'CUI // SP-CTI', ?, ?)""",
                        (s_id, src_model, tgt_model, src_name, tgt_name, src_site, tgt_site,
                         readiness, status, NOW_ISO, NOW_ISO),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0]:
                        inserted += 1
                except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                    logger.warning(
                        "ensure_migration_sessions: best-effort INSERT into mc_net_sessions failed (non-blocking): %s",
                        exc,
                    )
            conn.commit()
    except Exception as e:
        return {"inserted": 0, "skipped": 0, "error": str(e)}
    return {"inserted": inserted, "skipped": len(MIGRATION_SESSIONS) - inserted}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> dict:
    summary: dict = {}

    with nc_conn() as conn:
        summary["topologies"] = ensure_topologies(conn)
        summary["project_links"] = ensure_project_links(conn)
        summary["devices"] = ensure_devices(conn)
        summary["device_configs"] = ensure_device_configs(conn)
        summary["simulations"] = ensure_simulations(conn)
        summary["board_reviews"] = ensure_board_reviews(conn)
        summary["compliance_checks"] = ensure_compliance(conn)
        summary["peering_agreements"] = ensure_peering(conn)
        summary["notifications"] = ensure_notifications(conn)
        summary["audit"] = ensure_audit(conn)

        # Read-back counts for verification
        summary["readback"] = {
            "topologies": _row_count(conn, "topologies"),
            "nc_project_topologies": _row_count(conn, "nc_project_topologies"),
            "ni_devices": _row_count(conn, "ni_devices"),
            "ni_device_configs": _row_count(conn, "ni_device_configs"),
            "simulation_results": _row_count(conn, "simulation_results"),
            "nc_board_reviews": _row_count(conn, "nc_board_reviews"),
            "nc_compliance_checks": _row_count(conn, "nc_compliance_checks"),
            "nc_peering_agreements": _row_count(conn, "nc_peering_agreements"),
            "nc_notifications": _row_count(conn, "nc_notifications"),
            "nc_audit": _row_count(conn, "nc_audit"),
        }

    summary["migration_sessions"] = ensure_migration_sessions()

    try:
        with mc_conn() as conn:
            summary["readback"]["mc_net_sessions"] = _row_count(conn, "mc_net_sessions")
    except Exception:
        pass

    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = ap.parse_args()
    s = run()
    if args.json:
        print(json.dumps(s, indent=2, default=str))
    else:
        print("=== Demo Showcase Seed Summary ===")
        for k, v in s.items():
            if k == "readback":
                print("\nRead-back row counts:")
                for t, c in v.items():
                    print(f"  {t:30} {c}")
            else:
                ins = v.get("inserted", "?")
                skp = v.get("skipped", "?")
                err = v.get("error", "")
                err_s = f"  ERR={err}" if err else ""
                print(f"  {k:25} inserted={ins}  skipped={skp}{err_s}")


if __name__ == "__main__":
    main()
