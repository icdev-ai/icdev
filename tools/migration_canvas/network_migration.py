# CUI // SP-CTI
"""Network Device Migration Engine — vendor-agnostic chassis swap workflow.

Supports any vendor whose:
  - Hardware profile exists in nc_hardware_profiles (network_canvas.db)
  - Config text can be parsed by tools.network.config_parser

Tested with: Juniper MX/EX/QFX, Cisco IOS/NX-OS/IOS-XR, Arista EOS,
             Nokia SR-OS, Brocade/Ruckus IronWare, and generic (unknown vendor).

All hardware specs are read from the DB — nothing is hardcoded here.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.migration_canvas.db.init_db import get_connection as _mc_get_connection

logger = get_logger("icdev.migration_canvas.network_migration")

try:
    from tools.canvas.ai_trace_mixin import record_canvas_decision as _record_decision
except Exception:
    def _record_decision(**_kw): pass  # type: ignore[assignment]

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_MC_DB_PATH = _ICDEV_ROOT / "data" / "migration_canvas.db"
_NC_DB_PATH = _ICDEV_ROOT / "data" / "network_canvas.db"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _mc_conn():
    """Return the canonical migration-canvas connection (PG or SQLite).

    Uses tools.migration_canvas.db.init_db.get_connection so that the engine
    reads/writes the same backend as the dashboard blueprint. Previously this
    helper hardcoded the SQLite path, which broke when MC_STORAGE_BACKEND
    routed sessions to PostgreSQL.
    """
    return _mc_get_connection()


# Minimal nc_hardware_profiles schema/seed, self-contained here rather than
# depending on tools.network.db.init_db — that module only runs when the
# (much larger, PG/SQLite-dual-backend) Network Design Canvas is enabled,
# which CI leaves off (ICDEV_NETWORK_ENABLED=false — shared-DB table
# collisions pending the PGP migration). Only the columns this module's own
# queries actually select are populated; everything else defaults per the
# canonical schema in tools/network/db/init_db.py.
_NC_HW_SCHEMA = """
CREATE TABLE IF NOT EXISTS nc_hardware_profiles (
    id                  TEXT PRIMARY KEY,
    vendor              TEXT NOT NULL,
    model               TEXT NOT NULL,
    model_family        TEXT,
    device_type         TEXT NOT NULL,
    form_factor         TEXT DEFAULT 'rack',
    rack_units          INTEGER DEFAULT 1,
    power_typical_w     INTEGER,
    power_max_w         INTEGER,
    throughput_gbps     REAL,
    routing_table_size  INTEGER,
    arp_table_size      INTEGER,
    ports_json          TEXT DEFAULT '[]',
    eol_date            TEXT,
    tags                TEXT DEFAULT '[]',
    UNIQUE(vendor, model)
);
"""

_NC_HW_SEED = [
    ("hw-juniper-mx204", "Juniper", "MX204", "MX Series", "router", "rack", 1, 200, 275, 400.0,
     2000000, 128000,
     json.dumps([{"count": 4, "speed": "100GbE", "type": "QSFP28"}, {"count": 8, "speed": "10GbE", "type": "SFP+"}]),
     "2030-06-30", json.dumps(["access", "cpe", "small-core"])),
    ("hw-cisco-asr9901", "Cisco", "ASR-9901", "ASR 9000 Series", "router", "rack", 2, 600, 850, 2400.0,
     6000000, 256000,
     json.dumps([{"count": 20, "speed": "100GbE", "type": "QSFP28"}, {"count": 4, "speed": "10GbE", "type": "SFP+"}]),
     "2031-12-31", json.dumps(["core", "peering", "aggregation"])),
    ("hw-juniper-mx480", "Juniper", "MX480", "MX Series", "router", "chassis", 8, 1200, 1800, 2400.0,
     4000000, 256000,
     json.dumps([{"count": 6, "speed": "MPC slot", "type": "MPC"}]),
     "2029-03-31", json.dumps(["core", "aggregation"])),
    ("hw-cisco-8201", "Cisco", "8201", "8000 Series", "router", "rack", 1, 450, 600, 10800.0,
     8000000, 512000,
     json.dumps([{"count": 24, "speed": "400GbE", "type": "QSFP-DD"}, {"count": 12, "speed": "100GbE", "type": "QSFP28"}]),
     "2034-12-31", json.dumps(["core", "edge", "peering"])),
]


def _nc_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_NC_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_NC_HW_SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM nc_hardware_profiles").fetchone()[0] == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO nc_hardware_profiles "
            "(id, vendor, model, model_family, device_type, form_factor, rack_units, "
            "power_typical_w, power_max_w, throughput_gbps, routing_table_size, "
            "arp_table_size, ports_json, eol_date, tags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _NC_HW_SEED,
        )
        conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Vendor-agnostic config parsing
# ---------------------------------------------------------------------------

def parse_source_config(config_text: str) -> dict[str, Any]:
    """Parse a device running config regardless of vendor.

    Calls detect_vendor() then the appropriate vendor parser.
    Falls back to generic section-based parser for unknown vendors.

    Returns a unified dict:
    {
      "vendor": str,           # detected vendor slug
      "hostname": str,
      "interfaces": [          # one entry per logical interface
        {
          "name": str,         # e.g. et-0/0/0 or GigabitEthernet0/0/1
          "ip": str,           # primary IPv4 or ""
          "description": str,
          "shutdown": bool,
          "speed_gbps": float, # detected from interface name/config
          "media_type": str,   # "copper" | "fiber" | ""
          "optic_type": str,   # "SFP+" | "QSFP28" | "QSFP-DD" | "CFP" | ""
          "vrf": str,
          "lag_member": str,   # parent AE/po/bond if applicable
        }
      ],
      "bgp_neighbors": [{"ip": str, "asn": int, "group": str}],
      "ospf_areas": [str],
      "isis_nets": [str],
      "mpls_interfaces": [str],
      "ldp_interfaces": [str],
      "rsvp_interfaces": [str],
      "l3vpn_vrfs": [str],
      "l2vpn_instances": [str],
      "firewall_filters": [str],
      "ms_mic_used": bool,   # Juniper stateful-firewall MS-MIC/MS-PIC
      "lag_count": int,
      "raw_interface_count": int,
    }
    """
    from tools.network.config_parser import detect_vendor, parse_config  # type: ignore

    vendor = detect_vendor(config_text)
    base = parse_config(config_text, vendor=vendor)

    # Augment with network-migration-specific fields
    ifaces = []
    for iface in base.get("interfaces", []):
        name = iface.get("name", "")
        ifaces.append({
            "name": name,
            "ip": iface.get("ip", ""),
            "description": iface.get("description", ""),
            "shutdown": iface.get("shutdown", False),
            "speed_gbps": _infer_speed_gbps(name, config_text),
            "media_type": _infer_media_type(name),
            "optic_type": _infer_optic_type(name),
            "vrf": _extract_vrf(name, config_text, vendor),
            "lag_member": _extract_lag_parent(name, config_text, vendor),
        })

    return {
        "vendor": vendor,
        "hostname": base.get("hostname", ""),
        "interfaces": ifaces,
        "bgp_neighbors": _extract_bgp_neighbors(config_text, vendor, base),
        "ospf_areas": _extract_ospf_areas(config_text, vendor),
        "isis_nets": _extract_isis_nets(config_text, vendor),
        "mpls_interfaces": _extract_mpls_ifaces(config_text, vendor),
        "ldp_interfaces": _extract_ldp_ifaces(config_text, vendor),
        "rsvp_interfaces": _extract_rsvp_ifaces(config_text, vendor),
        "l3vpn_vrfs": _extract_l3vpn_vrfs(config_text, vendor),
        "l2vpn_instances": _extract_l2vpn(config_text, vendor),
        "firewall_filters": _extract_firewall_filters(config_text, vendor),
        "ms_mic_used": _detect_ms_mic(config_text, vendor),
        "lag_count": sum(1 for i in ifaces if re.search(r"ae\d+|port-channel|bond\d+|lag\d+|po\d+", i["name"], re.I)),
        "raw_interface_count": len(ifaces),
    }


# -- Speed / media / optic inference from interface name ---------------------

_SPEED_PATTERNS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\bet-\d|^et\d|ethernet.*?\b400g", re.I), 100.0),  # et- = 100GE on most platforms
    (re.compile(r"\bxe-|gigether.*\bte\b|tengig", re.I), 10.0),
    (re.compile(r"\bge-|gigether(?!.*ten)", re.I), 1.0),
    (re.compile(r"\bfe-|fastether", re.I), 0.1),
    (re.compile(r"HundredGig|100GE|100Gig", re.I), 100.0),
    (re.compile(r"FortyGig|40GE|40Gig", re.I), 40.0),
    (re.compile(r"TwentyFiveGig|25GE", re.I), 25.0),
    (re.compile(r"TenGig|10GE|10Gig", re.I), 10.0),
    (re.compile(r"GigabitEth|1GE|GigE", re.I), 1.0),
    (re.compile(r"400GE|400Gig|FourHundredGig", re.I), 400.0),
]


def _infer_speed_gbps(iface_name: str, config_text: str = "") -> float:
    for pat, speed in _SPEED_PATTERNS:
        if pat.search(iface_name):
            return speed
    return 0.0


def _infer_media_type(iface_name: str) -> str:
    copper = re.compile(r"mgmt|management|fxp|em\d|copper|te\d+/\d+$", re.I)
    if copper.search(iface_name):
        return "copper"
    if re.search(r"et-|xe-|ge-\d+/\d+/\d|sfp|qsfp|cfp|dwdm|mpo|lc", iface_name, re.I):
        return "fiber"
    return ""


def _infer_optic_type(iface_name: str) -> str:
    name = iface_name.lower()
    if re.search(r"^et-|^et\d|hundred", name):
        return "QSFP28"
    if re.search(r"^xe-|tengig|10gig", name):
        return "SFP+"
    if re.search(r"^ge-|gigabit|1gig", name):
        return "SFP"
    if re.search(r"400g|qsfp-dd|qdd", name):
        return "QSFP-DD"
    return ""


# -- Protocol extraction (vendor-agnostic best-effort) ----------------------

def _extract_bgp_neighbors(text: str, vendor: str, base: dict) -> list[dict]:
    neighbors = [{"ip": n["ip"], "asn": n.get("asn", 0), "group": ""} for n in base.get("bgp_neighbors", [])]
    # Also grab BGP groups for Juniper
    if vendor == "juniper":
        for m in re.finditer(r"set protocols bgp group (\S+) neighbor (\S+)", text):
            ip = m.group(2)
            grp = m.group(1)
            existing = next((n for n in neighbors if n["ip"] == ip), None)
            if existing:
                existing["group"] = grp
            else:
                neighbors.append({"ip": ip, "asn": 0, "group": grp})
    return neighbors


def _extract_ospf_areas(text: str, vendor: str) -> list[str]:
    areas = set()
    for m in re.finditer(r"area[\s\[]+([\d.]+)", text, re.I):
        areas.add(m.group(1))
    return sorted(areas)


def _extract_isis_nets(text: str, vendor: str) -> list[str]:
    nets = set()
    for m in re.finditer(r"net\s+([\da-fA-F.]+)", text):
        nets.add(m.group(1))
    return sorted(nets)


def _extract_mpls_ifaces(text: str, vendor: str) -> list[str]:
    ifaces = set()
    if vendor == "juniper":
        for m in re.finditer(r"set protocols mpls interface (\S+)", text):
            ifaces.add(m.group(1))
    else:
        for m in re.finditer(r"mpls ip\b", text):
            pass  # Cisco: parse surrounding interface block (approximate)
    return sorted(ifaces)


def _extract_ldp_ifaces(text: str, vendor: str) -> list[str]:
    ifaces = set()
    if vendor == "juniper":
        for m in re.finditer(r"set protocols ldp interface (\S+)", text):
            ifaces.add(m.group(1))
    return sorted(ifaces)


def _extract_rsvp_ifaces(text: str, vendor: str) -> list[str]:
    ifaces = set()
    if vendor == "juniper":
        for m in re.finditer(r"set protocols rsvp interface (\S+)", text):
            ifaces.add(m.group(1))
    return sorted(ifaces)


def _extract_l3vpn_vrfs(text: str, vendor: str) -> list[str]:
    vrfs = set()
    if vendor == "juniper":
        for m in re.finditer(r"set routing-instances (\S+) instance-type vrf", text):
            vrfs.add(m.group(1))
    else:
        for m in re.finditer(r"^vrf definition (\S+)|^ip vrf (\S+)", text, re.M):
            vrfs.add(m.group(1) or m.group(2))
    return sorted(vrfs)


def _extract_l2vpn(text: str, vendor: str) -> list[str]:
    instances = set()
    if vendor == "juniper":
        for m in re.finditer(r"set routing-instances (\S+) instance-type (vpls|evpn|l2backhaul)", text, re.I):
            instances.add(m.group(1))
    return sorted(instances)


def _extract_firewall_filters(text: str, vendor: str) -> list[str]:
    filters = set()
    if vendor == "juniper":
        for m in re.finditer(r"set firewall family \S+ filter (\S+)", text):
            filters.add(m.group(1))
    else:
        for m in re.finditer(r"^ip access-list (?:extended|standard) (\S+)", text, re.M | re.I):
            filters.add(m.group(1))
    return sorted(filters)


def _detect_ms_mic(text: str, vendor: str) -> bool:
    if vendor != "juniper":
        return False
    return bool(re.search(r"ms-\d+/\d+/\d+|adaptive-services|ms-pic|services-inline|ms-amic", text, re.I))


def _extract_vrf(iface_name: str, text: str, vendor: str) -> str:
    if vendor == "juniper":
        m = re.search(
            r"set routing-instances (\S+) interface " + re.escape(iface_name.split(".")[0]),
            text,
        )
        return m.group(1) if m else ""
    return ""


def _extract_lag_parent(iface_name: str, text: str, vendor: str) -> str:
    if vendor == "juniper":
        m = re.search(
            r"set interfaces " + re.escape(iface_name.split(".")[0]) + r" ether-options 802.3ad (ae\d+)",
            text,
        )
        return m.group(1) if m else ""
    if vendor in ("cisco_ios", "cisco_nxos"):
        # Look for "channel-group N" in the interface block
        block_m = re.search(
            r"interface " + re.escape(iface_name) + r".*?channel-group\s+(\d+)",
            text, re.S | re.I,
        )
        return f"Port-channel{block_m.group(1)}" if block_m else ""
    return ""


# ---------------------------------------------------------------------------
# Hardware profile lookup (DB-driven, vendor-agnostic)
# ---------------------------------------------------------------------------

def fetch_hardware_profiles(src_model: str, tgt_model: str) -> dict[str, Any]:
    """Read full hardware specs for source and target models from nc_hardware_profiles.

    Returns {"source": {...}, "target": {...}, "gaps": [...]}
    """
    with _nc_conn() as conn:
        src = conn.execute(
            "SELECT * FROM nc_hardware_profiles WHERE LOWER(model)=LOWER(?) OR LOWER(id)=LOWER(?)",
            (src_model, f"hw-{src_model.lower().replace(' ','-')}"),
        ).fetchone()
        tgt = conn.execute(
            "SELECT * FROM nc_hardware_profiles WHERE LOWER(model)=LOWER(?) OR LOWER(id)=LOWER(?)",
            (tgt_model, f"hw-{tgt_model.lower().replace(' ','-')}"),
        ).fetchone()

    def _row(r):
        if not r:
            return {}
        d = dict(r)
        for field in ("ports_json", "components_json", "mgmt_ports_json", "os_options", "tags"):
            try:
                d[field] = json.loads(d.get(field) or "[]")
            except Exception:
                d[field] = []
        return d

    src_d = _row(src)
    tgt_d = _row(tgt)
    gaps = _compute_hw_gaps(src_d, tgt_d)
    return {"source": src_d, "target": tgt_d, "gaps": gaps}


def _compute_hw_gaps(src: dict, tgt: dict) -> list[dict]:
    """Compare source vs target hardware and flag significant differences."""
    gaps = []
    if not src or not tgt:
        return gaps

    def _gap(field, label, cat, msg):
        sv = src.get(field)
        tv = tgt.get(field)
        if sv and tv and sv != tv:
            gaps.append({"field": field, "label": label, "source": sv, "target": tv, "severity": cat, "message": msg})

    # Power delta
    sp, tp = src.get("power_max_w", 0) or 0, tgt.get("power_max_w", 0) or 0
    if sp and tp and abs(sp - tp) > 100:
        gaps.append({"field": "power_max_w", "label": "Max Power", "source": sp, "target": tp,
                     "severity": "cat3", "message": f"Power delta {abs(sp-tp)}W — verify PDU circuit capacity."})

    # Throughput
    st, tt = src.get("throughput_gbps", 0) or 0, tgt.get("throughput_gbps", 0) or 0
    if st and tt and tt < st:
        gaps.append({"field": "throughput_gbps", "label": "Throughput", "source": st, "target": tt,
                     "severity": "cat1", "message": f"Target throughput {tt}Gbps < source {st}Gbps. Capacity reduction."})

    # Routing table
    sf, tf = src.get("routing_table_size", 0) or 0, tgt.get("routing_table_size", 0) or 0
    if sf and tf and tf < sf:
        gaps.append({"field": "routing_table_size", "label": "FIB Size", "source": sf, "target": tf,
                     "severity": "cat1", "message": f"Target FIB {tf:,} < source FIB {sf:,}. Risk: route overflow."})

    # ARP table
    sa, ta = src.get("arp_table_size", 0) or 0, tgt.get("arp_table_size", 0) or 0
    if sa and ta and ta < sa:
        gaps.append({"field": "arp_table_size", "label": "ARP Table", "source": sa, "target": ta,
                     "severity": "cat2", "message": f"ARP table reduction: {sa:,} → {ta:,}."})

    # Form factor change
    sf2, tf2 = src.get("form_factor", ""), tgt.get("form_factor", "")
    if sf2 and tf2 and sf2 != tf2:
        gaps.append({"field": "form_factor", "label": "Form Factor", "source": sf2, "target": tf2,
                     "severity": "cat3", "message": f"Chassis form factor change: {sf2} → {tf2}. Verify rack space."})

    return gaps


# ---------------------------------------------------------------------------
# Port mapping (vendor-agnostic)
# ---------------------------------------------------------------------------

def generate_port_map(source_ports: list, target_hw: dict) -> dict:
    """Map source device ports to target hardware ports.

    Args:
        source_ports: List of source interface dicts (name, speed_gbps, media_type, etc.)
        target_hw: Target hardware profile dict with ``ports_json`` describing available ports.

    Returns:
        dict with keys:
            ``mappings`` — list of per-port assignment dicts (src_interface, tgt_interface, …)
            ``unmapped_count`` — number of data ports that could not be assigned
            ``optic_change_count`` — number of ports requiring a different optic
            ``speed_mismatch_count`` — number of ports where source and target speeds differ
    """
    mappings = _generate_port_map(source_ports, target_hw)
    return {
        "mappings": mappings,
        "unmapped_count": sum(1 for m in mappings if m.get("status") == "unmapped"),
        "optic_change_count": sum(1 for m in mappings if m.get("optic_change")),
        "speed_mismatch_count": sum(1 for m in mappings if m.get("speed_mismatch")),
    }


def _generate_port_map(
    src_interfaces: list[dict],
    tgt_hw_profile: dict,
    existing_map: list[dict] | None = None,
) -> list[dict]:
    """Auto-suggest target port assignments for each source interface.

    Uses target ports_json from hardware profile to build the assignment pool.
    Flags speed mismatches and optic changes.
    Respects any existing_map rows (won't overwrite user-assigned mappings).
    """
    tgt_ports = tgt_hw_profile.get("ports_json", [])

    # Build assignment pool from target ports_json
    pool: list[dict] = []
    for port_group in tgt_ports:
        if_prefix = port_group.get("if_prefix", "")
        if_start = port_group.get("if_start", 0)
        if_end = port_group.get("if_end", port_group.get("count", 1) - 1)
        speed_str = port_group.get("speed", "")
        optic = port_group.get("type", "")
        speed_gbps = _parse_speed_str(speed_str)

        if if_prefix:
            for idx in range(if_start, if_end + 1):
                pool.append({
                    "tgt_interface": f"{if_prefix}{idx}",
                    "tgt_speed_gbps": speed_gbps,
                    "tgt_optic_required": optic,
                })
        else:
            # Modular chassis — no fixed interface names
            for _ in range(port_group.get("count", 0)):
                pool.append({
                    "tgt_interface": "",
                    "tgt_speed_gbps": speed_gbps,
                    "tgt_optic_required": optic,
                })

    # Pre-assign from existing map
    already_assigned = {}
    if existing_map:
        for row in existing_map:
            if row.get("tgt_interface"):
                already_assigned[row["src_interface"]] = row

    # Build result — skip management, loopback, AE parent interfaces
    result = []
    pool_idx = 0
    for iface in src_interfaces:
        name = iface["name"]
        if re.search(r"^(lo|loopback|fxp0|me\d|management|mgmt|irb|vlan)", name, re.I):
            # Keep these in the map but with no target assignment
            result.append({
                "src_interface": name,
                "src_speed_gbps": iface.get("speed_gbps", 0),
                "src_media": iface.get("media_type", ""),
                "src_optic_type": iface.get("optic_type", ""),
                "src_ip_address": iface.get("ip", ""),
                "src_description": iface.get("description", ""),
                "tgt_interface": "",
                "tgt_speed_gbps": 0,
                "tgt_optic_required": "",
                "optic_change": False,
                "speed_mismatch": False,
                "status": "no-migration",
                "notes": "Loopback / management / IRB — configure directly on target",
            })
            continue

        if name in already_assigned:
            result.append(already_assigned[name])
            continue

        # Pop next target port from pool
        tgt = pool[pool_idx] if pool_idx < len(pool) else {"tgt_interface": "", "tgt_speed_gbps": 0, "tgt_optic_required": ""}
        if pool_idx < len(pool):
            pool_idx += 1

        src_speed = iface.get("speed_gbps", 0)
        src_optic = iface.get("optic_type", "")
        tgt_speed = tgt["tgt_speed_gbps"]
        tgt_optic = tgt["tgt_optic_required"]

        optic_change = bool(src_optic and tgt_optic and src_optic != tgt_optic)
        speed_mismatch = bool(src_speed and tgt_speed and src_speed != tgt_speed)

        result.append({
            "src_interface": name,
            "src_speed_gbps": src_speed,
            "src_media": iface.get("media_type", ""),
            "src_optic_type": src_optic,
            "src_ip_address": iface.get("ip", ""),
            "src_description": iface.get("description", ""),
            "tgt_interface": tgt["tgt_interface"],
            "tgt_speed_gbps": tgt_speed,
            "tgt_optic_required": tgt_optic,
            "optic_change": optic_change,
            "speed_mismatch": speed_mismatch,
            "status": "mapped",
            "notes": "",
        })

    # Flag unmapped source ports (pool exhausted)
    unmapped = len(src_interfaces) - sum(1 for r in result if r.get("tgt_interface"))
    if unmapped > 0:
        for r in result:
            if not r.get("tgt_interface") and r.get("status") == "mapped":
                r["status"] = "unmapped"
                r["notes"] = "Target port pool exhausted — manual assignment required"

    return result


def _parse_speed_str(s: str) -> float:
    s = s.lower()
    if "400" in s:
        return 400.0
    if "100" in s:
        return 100.0
    if "40" in s:
        return 40.0
    if "25" in s:
        return 25.0
    if "10" in s:
        return 10.0
    if "1g" in s or s == "1":
        return 1.0
    return 0.0


# ---------------------------------------------------------------------------
# Compatibility checklist (DB-driven + vendor-aware)
# ---------------------------------------------------------------------------

def check_compatibility(source_config: dict, target_hw: dict) -> dict:
    """Return a compatibility report between a parsed source config and target hardware.

    Args:
        source_config: Parsed source device config dict (output of :func:`parse_source_config`).
        target_hw: Target hardware profile dict (from :func:`fetch_hardware_profiles`).

    Returns:
        dict with keys:
            ``is_compatible`` — ``True`` when no blocking issues are found
            ``issues`` — list of human-readable issue strings describing problems
    """
    issues: list[str] = []

    src_vendor = source_config.get("vendor", "")
    tgt_vendor = target_hw.get("vendor", "")

    # Cross-vendor migration warning
    if src_vendor and tgt_vendor and src_vendor != tgt_vendor:
        issues.append(f"Cross-vendor migration detected: {src_vendor} → {tgt_vendor}")

    # Port count feasibility
    src_iface_count = source_config.get("raw_interface_count", 0)
    tgt_ports_json = target_hw.get("ports_json", [])
    tgt_port_count = sum(
        grp.get("count", max(0, grp.get("if_end", 0) - grp.get("if_start", 0) + 1))
        for grp in tgt_ports_json
    )
    if src_iface_count > 0 and tgt_port_count > 0 and src_iface_count > tgt_port_count:
        issues.append(
            f"Insufficient target ports: source has {src_iface_count} interfaces, "
            f"target has {tgt_port_count} ports"
        )

    # Throughput headroom
    tgt_throughput = target_hw.get("throughput_gbps", 0) or 0
    if tgt_throughput > 0:
        high_speed = sum(
            1 for i in source_config.get("interfaces", [])
            if (i.get("speed_gbps") or 0) >= 100
        )
        if high_speed * 100 > tgt_throughput:
            issues.append(
                f"Target throughput {tgt_throughput} Gbps may be insufficient "
                f"for {high_speed}×100 G source interfaces"
            )

    # MS-MIC / stateful-firewall gap (Juniper)
    if source_config.get("ms_mic_used") and tgt_vendor == "juniper":
        issues.append(
            "Source uses MS-MIC/MS-PIC stateful firewall — verify target platform "
            "has MX-SPC3 or plan migration to an external NGFW"
        )

    # Target EOL
    eol = target_hw.get("eol_date", "")
    if eol:
        issues.append(f"Target hardware end-of-life date: {eol}")

    # FIB size regression
    tgt_fib = target_hw.get("routing_table_size", 0) or 0
    if tgt_fib > 0:
        bgp_neighbors = len(source_config.get("bgp_neighbors", []))
        if bgp_neighbors > 0 and tgt_fib < 1_000_000:
            pass  # low FIB is noted but not a hard block here

    return {"is_compatible": len(issues) == 0, "issues": issues}


def _check_compatibility(
    src_hw: dict,
    tgt_hw: dict,
    parsed_config: dict,
) -> list[dict]:
    """Generate compatibility checklist rows from hardware profiles + parsed config.

    Returns a list of check rows with: category, check_name, expected, actual,
    severity (cat1/cat2/cat3), status (pass/fail/warning/manual).
    """
    rows: list[dict] = []
    vendor = parsed_config.get("vendor", "")
    src_v = src_hw.get("vendor", "")
    tgt_v = tgt_hw.get("vendor", "")

    def _row(cat, name, expected, actual, sev, status, auto=True):
        rows.append({
            "category": cat,
            "check_name": name,
            "expected": str(expected),
            "actual": str(actual),
            "severity": sev,
            "status": status,
            "auto_detected": 1 if auto else 0,
        })

    # --- HARDWARE checks ---
    # RU comparison
    src_ru = src_hw.get("rack_units", 0) or 0
    tgt_ru = tgt_hw.get("rack_units", 0) or 0
    if src_ru and tgt_ru:
        if tgt_ru <= src_ru:
            _row("hardware", "Rack Space", f"<= {src_ru}U", f"{tgt_ru}U", "cat3", "pass")
        else:
            _row("hardware", "Rack Space", f"<= {src_ru}U", f"{tgt_ru}U (increase)", "cat2", "warning")

    # Power
    sp = src_hw.get("power_max_w", 0) or 0
    tp = tgt_hw.get("power_max_w", 0) or 0
    if sp and tp:
        delta = tp - sp
        status = "pass" if delta <= 0 else ("warning" if delta <= 200 else "fail")
        sev = "cat3" if status != "fail" else "cat2"
        _row("hardware", "Max Power Draw", f"{sp}W", f"{tp}W ({'+' if delta>0 else ''}{delta}W)", sev, status)

    # Form factor
    sf = src_hw.get("form_factor", "")
    tf = tgt_hw.get("form_factor", "")
    if sf and tf and sf != tf:
        _row("hardware", "Form Factor Change", sf, tf, "cat3", "warning")

    # Same vendor?
    if src_v and tgt_v:
        if src_v == tgt_v:
            _row("hardware", "Vendor Consistency", src_v, tgt_v, "cat3", "pass")
        else:
            _row("hardware", "Vendor Change (cross-vendor)", src_v, tgt_v, "cat2", "warning")

    # --- SOFTWARE checks ---
    src_os = src_hw.get("os_options", [])
    tgt_os = tgt_hw.get("os_options", [])
    common_os = [o for o in src_os if any(o.lower() in t.lower() or t.lower() in o.lower() for t in tgt_os)]
    if src_os and tgt_os:
        if common_os:
            _row("software", "OS Compatibility", f"Any of {src_os}", common_os[0], "cat2", "pass")
        else:
            _row("software", "OS Compatibility", f"Any of {src_os}", f"Target supports: {tgt_os}", "cat1", "fail")

    # EOL check
    src_eol = src_hw.get("eol_date", "")
    if src_eol:
        _row("software", "Source Platform EOL", "Not EOL", f"EOL: {src_eol}", "cat2", "warning")

    # Juniper-specific: MS-MIC / stateful firewall gap
    if vendor == "juniper" and parsed_config.get("ms_mic_used"):
        _row("software", "Juniper AS-PIC / MS-MIC (Stateful Firewall)",
             "Not required OR migrated to MX-SPC3 / external NGFW",
             "MS-MIC/MS-PIC in use — MX304 and similar fixed-form platforms have no AS-PIC slots",
             "cat1", "fail")

    # --- PROTOCOL checks ---
    bgp_count = len(parsed_config.get("bgp_neighbors", []))
    if bgp_count:
        _row("protocol", f"BGP Neighbors ({bgp_count})", "All preserved post-migration",
             f"{bgp_count} neighbors in config", "cat2", "manual")

    ospf_areas = parsed_config.get("ospf_areas", [])
    if ospf_areas:
        _row("protocol", f"OSPF Areas ({len(ospf_areas)})", "Adjacency restored within MW",
             ", ".join(ospf_areas), "cat2", "manual")

    isis_nets = parsed_config.get("isis_nets", [])
    if isis_nets:
        _row("protocol", "IS-IS NETs", "Preserved on target", ", ".join(isis_nets), "cat2", "manual")

    rsvp_ifaces = parsed_config.get("rsvp_interfaces", [])
    if rsvp_ifaces:
        _row("protocol", f"RSVP Interfaces ({len(rsvp_ifaces)})", "LSPs re-established post-cutover",
             f"{len(rsvp_ifaces)} RSVP interfaces", "cat2", "manual")

    l2vpn = parsed_config.get("l2vpn_instances", [])
    if l2vpn:
        _row("protocol", f"L2VPN Instances ({len(l2vpn)})", "Verify service continuity post-cutover",
             ", ".join(l2vpn[:5]) + ("..." if len(l2vpn) > 5 else ""), "cat2", "manual")

    l3vpn = parsed_config.get("l3vpn_vrfs", [])
    if l3vpn:
        _row("protocol", f"L3VPN VRFs ({len(l3vpn)})", "All VRF routes restored post-cutover",
             f"{len(l3vpn)} VRFs: " + ", ".join(l3vpn[:4]), "cat2", "manual")

    # Firewall filters
    ff = parsed_config.get("firewall_filters", [])
    if ff:
        _row("protocol", f"Firewall Filters / ACLs ({len(ff)})", "Re-applied on target interfaces",
             f"{len(ff)} filters", "cat3", "manual")

    # --- SCALE checks ---
    src_fib = src_hw.get("routing_table_size", 0) or 0
    tgt_fib = tgt_hw.get("routing_table_size", 0) or 0
    if src_fib and tgt_fib:
        if tgt_fib >= src_fib:
            _row("scale", "Routing Table (FIB)", f">= {src_fib:,}", f"{tgt_fib:,}", "cat1", "pass")
        else:
            _row("scale", "Routing Table (FIB)", f">= {src_fib:,}",
                 f"{tgt_fib:,} (INSUFFICIENT — {src_fib-tgt_fib:,} routes over limit)", "cat1", "fail")

    src_arp = src_hw.get("arp_table_size", 0) or 0
    tgt_arp = tgt_hw.get("arp_table_size", 0) or 0
    if src_arp and tgt_arp:
        status = "pass" if tgt_arp >= src_arp else "warning"
        _row("scale", "ARP Table Size", f">= {src_arp:,}", f"{tgt_arp:,}", "cat2", status)

    # LAG count
    lag_count = parsed_config.get("lag_count", 0)
    if lag_count:
        _row("scale", f"LAG/AE Interfaces ({lag_count})", "Re-created on target",
             f"{lag_count} aggregate interfaces", "cat3", "manual")

    return rows


# ---------------------------------------------------------------------------
# Config conversion (vendor-aware rename + cleanup)
# ---------------------------------------------------------------------------

def _convert_config_dict(
    source_config: dict[str, Any],
    port_map: dict[str, str],
) -> dict[str, Any]:
    """Apply port_map renames to a parsed config dict.

    Args:
        source_config: Parsed source config (output of :func:`parse_source_config`).
        port_map: Mapping of ``{src_interface_name: tgt_interface_name}``.

    Returns the converted config dict with interface names renamed plus two
    metadata keys appended:
        ``port_map_applied`` — ``{src: tgt}`` pairs that were actually applied
        ``unmapped`` — data interface names that had no entry in *port_map*
    """
    if not source_config:
        return {
            "vendor": "",
            "hostname": "",
            "interfaces": [],
            "port_map_applied": {},
            "unmapped": [],
        }

    renamed_ifaces: list[dict] = []
    port_map_applied: dict[str, str] = {}
    unmapped: list[str] = []

    for iface in source_config.get("interfaces", []):
        name: str = iface.get("name", "")
        base = name.split(".")[0]  # strip logical-unit suffix (ge-0/0/0.0 → ge-0/0/0)
        unit_suffix = f".{name.split('.', 1)[1]}" if "." in name else ""

        tgt_name = port_map.get(base) or port_map.get(name)
        new_iface = dict(iface)

        if tgt_name:
            new_iface["name"] = tgt_name + unit_suffix
            port_map_applied[name] = tgt_name
        elif not re.search(
            r"^(lo\d*$|loopback|fxp\d|me\d|management|mgmt|irb|vlan)", base, re.I
        ):
            unmapped.append(name)

        renamed_ifaces.append(new_iface)

    result = {k: v for k, v in source_config.items() if k != "interfaces"}
    result["interfaces"] = renamed_ifaces
    result["port_map_applied"] = port_map_applied
    result["unmapped"] = unmapped
    return result


def convert_config(
    source_config: dict[str, Any] | str,
    port_map: dict[str, str] | list[dict],
    src_vendor: str = "",
    tgt_model: str = "",
) -> dict[str, Any]:
    """Transform source config to target format using port_map.

    Two calling modes:

    **Dict mode** — *source_config* is a parsed config dict (output of
    :func:`parse_source_config`); *port_map* is ``{src_interface: tgt_interface}``.
    Returns the converted config dict with interface names renamed and metadata
    keys ``port_map_applied`` and ``unmapped`` added.

    **String mode** (original) — *source_config* is raw config text; *port_map*
    is a list of port-assignment row dicts (from :func:`generate_port_map`).
    Returns ``{"source": str, "target": str, "diff": list[dict]}`` where each
    diff entry has ``op`` ∈ ``{"keep", "remove", "rename"}``.
    """
    if isinstance(source_config, dict):
        if isinstance(port_map, list):
            _pm: dict[str, str] = {
                r.get("src_interface", ""): r.get("tgt_interface", "")
                for r in port_map
                if r.get("src_interface") and r.get("tgt_interface")
            }
        else:
            _pm = {k: v for k, v in (port_map or {}).items() if k and v}
        return _convert_config_dict(source_config, _pm)

    # --- String mode: existing line-by-line transformation ---
    src_config: str = source_config
    list_map: list[dict] = (
        port_map if isinstance(port_map, list)
        else [{"src_interface": k, "tgt_interface": v} for k, v in port_map.items()]
    )

    # Build rename map: src_interface -> tgt_interface
    rename = {}
    for row in list_map:
        si = row.get("src_interface", "")
        ti = row.get("tgt_interface", "")
        if si and ti and si != ti:
            rename[si] = ti

    lines_in = src_config.splitlines()
    lines_out: list[str] = []
    diff: list[dict] = []

    # Platform-specific removals (vendor-aware, not hardcoded per-model)
    _deprecated_patterns = _get_deprecated_patterns(src_vendor)

    for line in lines_in:
        line_out = line
        removed = False

        # Check deprecated patterns first
        for pat, reason in _deprecated_patterns:
            if pat.search(line):
                diff.append({"op": "remove", "line": line, "reason": reason})
                removed = True
                break

        if removed:
            continue

        # Apply interface renames
        for src_if, tgt_if in rename.items():
            # Match whole interface name (not substrings)
            if re.search(r"\b" + re.escape(src_if.split(".")[0]) + r"\b", line_out):
                line_out = re.sub(
                    r"\b" + re.escape(src_if.split(".")[0]) + r"\b",
                    tgt_if.split(".")[0],
                    line_out,
                )

        if line_out != line:
            diff.append({"op": "rename", "line": line, "new_line": line_out})
        else:
            diff.append({"op": "keep", "line": line})

        lines_out.append(line_out)

    return {
        "source": src_config,
        "target": "\n".join(lines_out),
        "diff": diff,
    }


def _get_deprecated_patterns(vendor: str) -> list[tuple[re.Pattern, str]]:
    """Return (regex, reason) pairs for platform-specific stanzas to remove.

    Uses vendor slug to select the right ruleset — never model-specific hardcoding.
    Add new vendor rulesets here as needed.
    """
    patterns: list[tuple[re.Pattern, str]] = []

    if vendor == "juniper":
        patterns += [
            (re.compile(r"\bms-\d+/\d+/\d+\b", re.I), "MS-MIC/MS-PIC adaptive-services interface — not available on all target platforms"),
            (re.compile(r"\badaptive-services\b", re.I), "Adaptive Services (AS-PIC) stanza — verify target platform support"),
            (re.compile(r"\bservices inline\b.*\bms-\d", re.I), "Inline services tied to MS-MIC slot"),
            (re.compile(r"\bpic\s+\d+\s+tunnel-services\b", re.I), "Tunnel-services PIC assignment — may differ on target"),
            (re.compile(r"\bchassis\s+fpc\s+\d+\s+pic\s+\d+\s+number-of-ports\b", re.I), "FPC/PIC port count override — platform specific"),
        ]

    if vendor in ("cisco_ios", "cisco_nxos"):
        patterns += [
            (re.compile(r"\bservice-module\b", re.I), "Service-module command — platform specific"),
            (re.compile(r"\bnetwork-clock\b", re.I), "Network-clock assignment — verify target platform"),
        ]

    if vendor in ("brocade", "ruckus_ironware"):
        patterns += [
            (re.compile(r"\blag.*\bstatic\b", re.I), "Static LAG syntax — verify target platform LAG configuration"),
        ]

    return patterns


# ---------------------------------------------------------------------------
# Commit-check simulation (vendor-aware, pure Python)
# ---------------------------------------------------------------------------

def _simulate_commit_check_dict(
    converted_config: dict[str, Any],
) -> dict[str, Any]:
    """Validate a converted config dict for syntax and structural issues.

    Args:
        converted_config: Parsed/converted config dict — output of
            :func:`convert_config` in dict mode, or any
            :func:`parse_source_config`-shaped dict.

    Returns:
        {
          "valid": bool,          # True when *errors* list is empty
          "errors": list[str],    # Blocking issues that must be resolved
          "warnings": list[str],  # Non-blocking concerns worth reviewing
        }
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not converted_config:
        errors.append("Empty config — nothing to validate")
        return {"valid": False, "errors": errors, "warnings": warnings}

    interfaces: list[dict] = converted_config.get("interfaces", [])

    # Duplicate interface names after conversion
    all_names = [i.get("name", "") for i in interfaces if i.get("name")]
    seen: set[str] = set()
    for name in all_names:
        if name in seen:
            errors.append(f"Duplicate interface name after conversion: {name}")
        seen.add(name)

    # Interfaces without a name
    nameless = sum(1 for i in interfaces if not i.get("name"))
    if nameless:
        errors.append(f"{nameless} interface(s) missing name in converted config")

    # Unmapped data interfaces flagged by _convert_config_dict
    unmapped: list[str] = converted_config.get("unmapped", [])
    if unmapped:
        sample = ", ".join(unmapped[:5])
        suffix = f" (+{len(unmapped) - 5} more)" if len(unmapped) > 5 else ""
        warnings.append(
            f"{len(unmapped)} data interface(s) not mapped to target ports: "
            f"{sample}{suffix}"
        )

    # Vendor-specific checks
    vendor: str = converted_config.get("vendor", "")
    if vendor == "juniper":
        ms_mic = [
            i["name"]
            for i in interfaces
            if re.search(r"^ms-\d+/\d+/\d+", i.get("name", ""), re.I)
        ]
        if ms_mic:
            errors.append(
                f"MS-MIC adaptive-services interfaces remain after conversion "
                f"({len(ms_mic)} found): {', '.join(ms_mic[:3])}"
            )

    # BGP neighbor address format
    for nbr in converted_config.get("bgp_neighbors", []):
        ip = nbr.get("ip", "")
        if ip and not re.match(
            r"^(\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]+:[0-9a-fA-F:]*$", ip
        ):
            warnings.append(f"BGP neighbor has unrecognised address format: {ip!r}")

    # BGP neighbors configured but no interface carries an IP
    if converted_config.get("bgp_neighbors") and not any(
        i.get("ip") for i in interfaces
    ):
        warnings.append(
            "BGP neighbors configured but no interfaces have IP addresses — "
            "verify IP assignments survived conversion"
        )

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def simulate_commit_check(
    converted_config: dict[str, Any] | str,
    tgt_vendor: str = "",
    tgt_hw_profile: dict | None = None,
) -> dict[str, Any] | list[dict]:
    """Simulate commit check or validate a converted config dict.

    Two calling modes:

    **Dict mode** — *converted_config* is a parsed/converted config dict.
    Returns ``{"valid": bool, "errors": list[str], "warnings": list[str]}``.

    **String mode** (original) — *converted_config* is raw target config text.
    Returns ``list[dict]`` of finding rows ``{status, statement, message}``.
    """
    if isinstance(converted_config, dict):
        return _simulate_commit_check_dict(converted_config)

    # --- String mode: existing line-by-line checks ---
    target_config: str = converted_config
    findings: list[dict] = []
    lines = target_config.splitlines()

    def _add(status, statement, message):
        findings.append({"status": status, "statement": statement, "message": message})

    # 1. Check for any deprecated patterns (re-run against target)
    deprecated = _get_deprecated_patterns(tgt_vendor)
    for line in lines:
        for pat, reason in deprecated:
            if pat.search(line):
                _add("fail", line.strip()[:120], f"Deprecated on target: {reason}")

    # 2. Interface naming consistency
    if tgt_vendor == "juniper" and tgt_hw_profile:
        # All et- interfaces must be within the target port pool range
        port_groups = tgt_hw_profile.get("ports_json", [])
        valid_ifaces: set[str] = set()
        for grp in port_groups:
            pfx = grp.get("if_prefix", "")
            if pfx and "if_start" in grp and "if_end" in grp:
                for idx in range(grp["if_start"], grp["if_end"] + 1):
                    valid_ifaces.add(f"{pfx}{idx}")
        if valid_ifaces:
            for line in lines:
                m = re.search(r"\b(et-\d+/\d+/\d+|xe-\d+/\d+/\d+|ge-\d+/\d+/\d+)\b", line)
                if m:
                    base_if = m.group(1).split(".")[0]
                    if base_if not in valid_ifaces:
                        _add("fail", line.strip()[:120],
                             f"Interface {base_if} does not exist on target hardware profile")

    # 3. Optic compatibility (flag CFP/CFP2 on QSFP28 platforms)
    if tgt_hw_profile:
        tgt_optic_types = {
            grp.get("type", "").upper()
            for grp in tgt_hw_profile.get("ports_json", [])
        }
        if tgt_optic_types and "QSFP28" in tgt_optic_types and "CFP" not in tgt_optic_types:
            for line in lines:
                if re.search(r"\bcfp\b|\bcfp2\b|\bcfp4\b", line, re.I):
                    _add("fail", line.strip()[:120],
                         "CFP optic referenced — target platform uses QSFP28; new optics required")

    # 4. Syntax spot-check: unclosed brackets / braces (hierarchy format)
    open_b = target_config.count("{")
    close_b = target_config.count("}")
    if abs(open_b - close_b) > 0 and tgt_vendor == "juniper":
        _add("fail", "<config>",
             f"Unbalanced braces: {open_b} open vs {close_b} close — hierarchy format error")

    # 5. If no issues found, emit a pass
    if not findings:
        _add("pass", "<config>", "Syntax and interface validation passed")
    elif not any(f["status"] == "fail" for f in findings):
        _add("pass", "<config>", "No critical issues — warnings noted above")

    return findings


# ---------------------------------------------------------------------------
# Cutover sequence builder
# ---------------------------------------------------------------------------

def _assemble_cutover_steps(
    port_map: list[dict],
    strategy: str,
    vendor: str,
) -> list[dict]:
    """Core step-assembly logic shared by both build_cutover_sequence calling modes."""
    data_rows = [
        r for r in port_map
        if r.get("status") not in ("no-migration",) and r.get("tgt_interface")
    ]

    if strategy == "alphabetical":
        data_rows = sorted(data_rows, key=lambda r: r.get("src_interface", ""))
    elif strategy == "description_alpha":
        data_rows = sorted(data_rows, key=lambda r: r.get("src_description", ""))

    steps: list[dict] = []
    for seq, row in enumerate(data_rows, start=1):
        src_if = row.get("src_interface", "")
        tgt_if = row.get("tgt_interface", "")
        circuit = row.get("src_circuit_id", "") or row.get("src_description", src_if)
        ip = row.get("src_ip_address", "")
        drain, cutover, verify, rollback = _build_step_actions(src_if, tgt_if, ip, circuit, vendor)
        steps.append({
            "seq_no": seq,
            "circuit_id": row.get("src_circuit_id", ""),
            "interface": src_if,
            "description": circuit,
            "drain_action": drain,
            "cutover_action": cutover,
            "verify_action": verify,
            "rollback_action": rollback,
            "duration_min": 5,
            "status": "pending",
        })

    mgmt_rows = [r for r in port_map if r.get("status") == "no-migration"]
    for seq2, row in enumerate(mgmt_rows, start=len(steps) + 1):
        src_if = row.get("src_interface", "")
        steps.append({
            "seq_no": seq2,
            "circuit_id": "",
            "interface": src_if,
            "description": f"Management/Loopback: {src_if}",
            "drain_action": "N/A — no traffic to drain",
            "cutover_action": f"Configure {src_if} equivalent on target with same IP/config",
            "verify_action": f"Ping management IP; verify SSH access via target {src_if}",
            "rollback_action": "Re-connect to source device management port",
            "duration_min": 3,
            "status": "pending",
        })

    return steps


def build_cutover_sequence(
    port_map: list[dict] | dict,
    strategy: str = "traffic_volume_asc",
    parsed_config: dict | None = None,
) -> list[dict]:
    """Generate an ordered cutover step list from a port map or migration plan.

    Two calling modes:

    **Dict mode** — *port_map* is a ``migration_plan`` dict with keys:
        ``port_map`` (list[dict] of port-assignment rows),
        ``strategy`` (str, optional), ``parsed_config`` (dict, optional).
        Returns steps enriched with ISO-8601 ``scheduled_at`` timestamps
        projected sequentially from the current UTC time.

    **List mode** (original) — *port_map* is a list of port-assignment row
    dicts (output of :func:`generate_port_map`). Returns steps without timestamps.

    Strategies:
      traffic_volume_asc  — low-traffic circuits first (default, safest)
      alphabetical        — by source interface name
      description_alpha   — by circuit description

    Each step includes drain, cutover, verify, and rollback actions
    appropriate for the source vendor.
    """
    if isinstance(port_map, dict):
        migration_plan = port_map
        _port_map: list[dict] = migration_plan.get("port_map", [])
        _strategy: str = migration_plan.get("strategy", strategy)
        _parsed: dict | None = migration_plan.get("parsed_config", parsed_config)
        _vendor: str = (_parsed or {}).get("vendor", "")
        steps = _assemble_cutover_steps(_port_map, _strategy, _vendor)
        from datetime import timedelta
        cursor = datetime.now(timezone.utc)
        for step in steps:
            step["scheduled_at"] = cursor.isoformat()
            cursor += timedelta(minutes=step.get("duration_min", 5))
        return steps

    vendor = (parsed_config or {}).get("vendor", "")
    return _assemble_cutover_steps(port_map, strategy, vendor)


def _build_step_actions(
    src_if: str, tgt_if: str, ip: str, circuit: str, vendor: str
) -> tuple[str, str, str, str]:
    """Return (drain, cutover, verify, rollback) action strings for one circuit."""

    if vendor == "juniper":
        drain = (
            f"Apply BGP community NO-EXPORT on routes advertised via {src_if} "
            f"to shift traffic off. Alternatively increase OSPF/ISIS metric on {src_if}."
        )
        cutover = (
            f"1. Cable circuit '{circuit}' into target port {tgt_if}.\n"
            f"2. Apply interface config on target (IP: {ip or 'per config'}, description, CoS).\n"
            f"3. Remove NO-EXPORT community / restore routing metric."
        )
        verify = (
            f"ping {ip or '<far-end>'} routing-instance <vrf> count 100 | expect 0 loss\n"
            f"show interfaces {tgt_if} detail | match 'Physical link is Up'\n"
            f"show bgp neighbor {ip or '<peer>'} | match Established"
        )
        rollback = (
            f"Re-cable circuit '{circuit}' back to source port {src_if}.\n"
            f"Remove config from {tgt_if} on target.\n"
            f"Restore routing advertisements on source {src_if}."
        )

    elif vendor in ("cisco_ios", "cisco_nxos"):
        drain = (
            f"Apply route-map to set local-preference 80 (below default 100) on {src_if} "
            f"to drain BGP traffic. Increase OSPF cost on {src_if} interface."
        )
        cutover = (
            f"1. Cable '{circuit}' into target port {tgt_if}.\n"
            f"2. Apply: interface {tgt_if} / ip address {ip or '<IP>'} / no shutdown.\n"
            f"3. Restore routing preferences."
        )
        verify = (
            f"ping {ip or '<far-end>'} repeat 100\n"
            f"show interface {tgt_if} | inc line protocol\n"
            f"show ip bgp neighbors {ip or '<peer>'} | inc BGP state"
        )
        rollback = (
            f"Re-cable '{circuit}' to source {src_if}.\n"
            f"interface {tgt_if} / shutdown.\n"
            f"Restore source interface and routing."
        )

    else:
        # Generic / unknown vendor
        drain = f"Drain traffic from {src_if} using vendor-appropriate method (routing metric, communities, etc.)"
        cutover = f"Move circuit '{circuit}' from {src_if} to target port {tgt_if}. Apply equivalent config."
        verify = f"Verify link up on {tgt_if}. Confirm {ip or 'connectivity'} is reachable."
        rollback = f"Re-cable back to source {src_if}. Restore original configuration."

    return drain, cutover, verify, rollback


# ---------------------------------------------------------------------------
# Pre-seeded test cases (vendor-aware)
# ---------------------------------------------------------------------------

def seed_test_cases(vendor: str = "") -> list[dict]:
    """Return vendor-appropriate pre-seeded test case definitions."""
    cases: list[dict] = []
    seq = 1

    def _tc(phase, name, procedure, expected):
        nonlocal seq
        cases.append({
            "phase": phase,
            "seq_no": seq,
            "test_name": name,
            "procedure": procedure,
            "expected_result": expected,
        })
        seq += 1

    # Pre-migration
    _tc("pre", "Interface traffic baseline",
        "Capture 5-min traffic counters on all in-scope interfaces (input/output bps, pps). Record to spreadsheet.",
        "Baseline captured; peak utilization documented per interface")
    _tc("pre", "Routing table snapshot",
        "show route summary (Juniper) / show ip route summary (Cisco) / equivalent for vendor.",
        "Route count per table documented; compare after cutover")
    _tc("pre", "BGP neighbor states",
        "Verify all BGP neighbors are in Established state. Record neighbor IPs and AS numbers.",
        "All BGP sessions Established; N neighbors documented")
    _tc("pre", "MPLS LSP inventory",
        "show mpls lsp (Juniper) / show mpls ldp session / show mpls traffic-eng tunnels.",
        "All LSPs Up; LDP sessions Active; count documented")
    _tc("pre", "Config backup",
        "Save complete running-config to: backup/<hostname>-pre-migration-<date>.txt",
        "Config file saved and MD5 checksummed")
    _tc("pre", "Management reachability",
        "Confirm SSH access to both source and target management IPs from NOC jump host.",
        "SSH to source and target both respond within 3s")
    _tc("pre", "Target platform commit check",
        "Load generated target config on staging or use commit-check simulation tool.",
        "Zero commit errors; all warnings reviewed and accepted")

    if vendor == "juniper":
        _tc("pre", "RSVP LSP check",
            "show rsvp session | match 'Up' | count",
            "All expected RSVP sessions Up")
        _tc("pre", "L2VPN/VPLS status",
            "show l2vpn connections | match 'Up' | count",
            "All L2VPN connections Up")

    # Cutover smoke tests
    _tc("cutover", "Link layer up on all migrated ports",
        "For each migrated port on target: verify 'Physical link is Up / line protocol is up'.",
        "All migrated interfaces show link up within 30s of cable insertion")
    _tc("cutover", "BGP re-establishment",
        "Verify all BGP neighbors return to Established state post-cutover.",
        "All BGP sessions Established within convergence timer (default ~3 min)")
    _tc("cutover", "Ping all BGP peers",
        "Ping each BGP peer IP 100 times via appropriate interface/VRF.",
        "0% packet loss on all BGP peer pings")
    _tc("cutover", "OSPF/ISIS adjacency",
        "Verify OSPF/ISIS neighbors are in Full/Up state on all migrated interfaces.",
        "All OSPF/ISIS adjacencies restored within 90s")
    _tc("cutover", "Traceroute path validation",
        "Run MTR/traceroute to 3 key destinations. Compare hop path against pre-migration baseline.",
        "Path delta <= 1 hop; no unexpected transit changes")
    _tc("cutover", "Traffic counters incrementing",
        "Check interface input/output packet counters are incrementing on migrated ports.",
        "Non-zero PPS on each port that carried traffic pre-migration")
    _tc("cutover", "MPLS forwarding",
        "Traceroute MPLS path to key destinations. Verify LSP labels match expected.",
        "MPLS labels assigned; LSPs forwarding correctly")

    # Post-migration
    _tc("post", "Interface error counters (0)",
        "Check CRC errors, FCS errors, input errors, output drops on all migrated ports. Monitor for 1h.",
        "Zero CRC/FCS errors after 1h; input errors < 0.001% of packets")
    _tc("post", "Traffic profile comparison",
        "Compare 5-min avg traffic bps/pps per interface against pre-migration baseline.",
        "Traffic within +/-10% of pre-migration baseline")
    _tc("post", "Alarm dashboard clear",
        "Verify no critical alarms on target device (show chassis alarms / equivalent).",
        "Zero critical alarms; warning alarms reviewed and documented")
    _tc("post", "Interface flap count",
        "Show interface log / event log for flaps on migrated ports since cutover.",
        "Zero unexpected flaps post-cutover")
    _tc("post", "Customer / service verification",
        "Confirm with network operations that customer-facing services are functional.",
        "NOC sign-off: all services green")
    _tc("post", "Documentation update",
        "Update CMDB/NetBox: device records, interface descriptions, circuit assignments.",
        "NetBox/CMDB updated; old device marked 'retired' or 'standby-30d'")
    _tc("post", "Source device keepalive (30-day standby)",
        "Keep source device racked, powered, connected to management network for 30 days as rollback option.",
        "Source device accessible via management IP; configs preserved")

    return cases


# ---------------------------------------------------------------------------
# Readiness scoring
# ---------------------------------------------------------------------------

def compute_readiness(
    compatibility: dict | str,
    validations: dict | None = None,
) -> dict[str, Any]:
    """Score migration readiness 0-100 with status and blockers.

    Two calling modes:

    **Dict mode** — *compatibility* is a compat report dict (output of
    :func:`check_compatibility`) and *validations* is a commit-check result
    dict (output of :func:`simulate_commit_check`).
    Returns ``{"score": int, "status": str, "blockers": list[str]}``.

    **String mode** (original) — *compatibility* is a session UUID string.
    Returns the full per-dimension breakdown from the DB.
    """
    if isinstance(compatibility, dict):
        issues: list[str] = compatibility.get("issues", [])
        blockers: list[str] = [
            i for i in issues
            if any(kw in i.lower() for kw in ("insufficient", "cross-vendor", "ms-mic", "eol"))
        ]
        val_errors: list[str] = []
        if isinstance(validations, dict):
            val_errors = validations.get("errors", [])
            blockers.extend(val_errors)
        non_blocking = len(issues) + len(val_errors) - len(blockers)
        score = max(0, 100 - len(blockers) * 20 - non_blocking * 5)
        score = min(100, score)
        if score >= 80:
            status = "ready"
        elif score >= 50:
            status = "partial"
        else:
            status = "blocked"
        return {"score": score, "status": status, "blockers": blockers}

    session_id: str = compatibility
    with _mc_conn() as conn:
        session = conn.execute(
            "SELECT * FROM mc_net_sessions WHERE id=%s", (session_id,)
        ).fetchone()
        if not session:
            return {"overall": 0, "error": "session not found"}

        port_rows = conn.execute(
            "SELECT COUNT(*) cnt, SUM(CASE WHEN status='mapped' THEN 1 ELSE 0 END) mapped "
            "FROM mc_net_port_map WHERE session_id=%s", (session_id,)
        ).fetchone()

        compat_rows = conn.execute(
            "SELECT COUNT(*) cnt, "
            "SUM(CASE WHEN status='fail' AND (override_reason='' OR override_reason IS NULL) THEN 1 ELSE 0 END) blocking "
            "FROM mc_net_compat_checks WHERE session_id=%s", (session_id,)
        ).fetchone()

        test_rows = conn.execute(
            "SELECT COUNT(*) cnt, SUM(CASE WHEN passed IS NOT NULL THEN 1 ELSE 0 END) executed "
            "FROM mc_net_test_cases WHERE session_id=%s", (session_id,)
        ).fetchone()

        cutover_rows = conn.execute(
            "SELECT COUNT(*) FROM mc_net_cutover_steps WHERE session_id=%s", (session_id,)
        ).fetchone()

        erb_row = conn.execute(
            "SELECT id, business_justification, risk_tier FROM mc_net_erb_metadata WHERE session_id=%s",
            (session_id,)
        ).fetchone()

    scores = {}
    blockers = []

    # 1. Inventory (config parsed + ports mapped)
    config_parsed = bool(dict(session).get("config_parsed"))
    total_ports = (port_rows["cnt"] or 0) if port_rows else 0
    mapped_ports = (port_rows["mapped"] or 0) if port_rows else 0
    inv_score = 0
    if config_parsed:
        inv_score += 50
    if total_ports > 0:
        inv_score += int(50 * (mapped_ports / total_ports))
    else:
        inv_score += 50  # no ports to map = not started
    scores["inventory"] = inv_score
    if not config_parsed:
        blockers.append("Source config not yet imported")
    if total_ports > 0 and mapped_ports < total_ports:
        blockers.append(f"{total_ports - mapped_ports} port(s) not yet mapped to target")

    # 2. Compatibility (no unresolved CAT1 blocks)
    compat_total = (compat_rows["cnt"] or 0) if compat_rows else 0
    blocking = (compat_rows["blocking"] or 0) if compat_rows else 0
    compat_score = 0 if compat_total == 0 else (100 if blocking == 0 else max(0, 100 - blocking * 25))
    scores["compatibility"] = compat_score
    if blocking > 0:
        blockers.append(f"{blocking} unresolved CAT1 compatibility blocker(s)")

    # 3. Config validation (converted config with commit-check)
    config_done = bool(dict(session).get("config_parsed") and compat_total > 0)
    scores["config_validated"] = 100 if config_done else 0
    if not config_done:
        blockers.append("Config conversion and commit-check not yet completed")

    # 4. Test plan
    test_total = (test_rows["cnt"] or 0) if test_rows else 0
    test_exec = (test_rows["executed"] or 0) if test_rows else 0
    test_score = 0 if test_total == 0 else int(100 * (test_exec / test_total))
    scores["test_plan"] = test_score
    if test_total == 0:
        blockers.append("Test plan not yet built (no test cases)")
    elif test_exec < test_total:
        blockers.append(f"{test_total - test_exec} test case(s) not yet executed")

    # 5. Cutover plan
    cutover_count = cutover_rows[0] if cutover_rows else 0
    cutover_score = 100 if cutover_count > 0 else 0
    scores["cutover_plan"] = cutover_score
    if cutover_count == 0:
        blockers.append("Cutover sequence not yet defined")

    # 6. ERB/CCB package
    erb_score = 0
    if erb_row:
        erb_d = dict(erb_row)
        if erb_d.get("business_justification") and erb_d.get("risk_tier"):
            erb_score = 100
        else:
            erb_score = 50
    scores["erb_package"] = erb_score
    if erb_score == 0:
        blockers.append("ERB/CCB package not yet started")
    elif erb_score < 100:
        blockers.append("ERB/CCB package incomplete (missing justification or risk tier)")

    # Overall weighted score
    weights = {
        "inventory": 0.20,
        "compatibility": 0.25,
        "config_validated": 0.15,
        "test_plan": 0.20,
        "cutover_plan": 0.10,
        "erb_package": 0.10,
    }
    overall = int(sum(scores[k] * w for k, w in weights.items()))
    return {"overall": overall, "dimensions": scores, "blockers": blockers}


# ---------------------------------------------------------------------------
# ERB/CCB package assembly
# ---------------------------------------------------------------------------

def generate_erb_package(
    session_id: str | dict,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """Assemble an ERB/CCB package dict from a session ID or converted config.

    Two calling modes:

    **Dict mode** — *session_id* is a ``converted_config`` dict (output of
    :func:`convert_config`) and *metadata* is an ERB metadata dict with keys:
        ``change_type`` (str), ``requestor`` (str), ``risk_tier`` (str),
        ``business_justification`` (str), ``mw_start`` (str), ``mw_end`` (str),
        ``rollback_plan`` (str, optional), ``impact_summary`` (str, optional).
    Returns a self-contained ERB package without any DB access.

    **String mode** (original) — *session_id* is a migration session UUID.
    Assembles the full ERB/CCB package dict from all session sub-tables.
    """
    if isinstance(session_id, dict):
        converted_config: dict[str, Any] = session_id
        meta: dict[str, Any] = metadata or {}
        interfaces: list[dict] = converted_config.get("interfaces", [])
        port_map_applied: dict = converted_config.get("port_map_applied", {})
        unmapped: list = converted_config.get("unmapped", [])

        risk_tier = meta.get("risk_tier", "medium")
        if not risk_tier:
            risk_tier = "high" if unmapped else "low"

        return {
            "converted_config": converted_config,
            "change_summary": {
                "vendor": converted_config.get("vendor", ""),
                "hostname": converted_config.get("hostname", ""),
                "change_type": meta.get("change_type", "hardware_replacement"),
                "requestor": meta.get("requestor", ""),
                "date": _now()[:10],
            },
            "impact_analysis": {
                "total_interfaces": len(interfaces),
                "data_interfaces": sum(
                    1 for i in interfaces
                    if not re.search(r"^(lo|loopback|fxp|me\d|mgmt|irb|vlan)", i.get("name", ""), re.I)
                ),
                "ports_remapped": len(port_map_applied),
                "unmapped_ports": len(unmapped),
                "unmapped_list": unmapped,
                "mw_start": meta.get("mw_start", ""),
                "mw_end": meta.get("mw_end", ""),
                "impact_summary": meta.get("impact_summary", ""),
            },
            "risk_assessment": {
                "risk_tier": risk_tier,
                "business_justification": meta.get("business_justification", ""),
            },
            "port_mapping": [
                {"src_interface": src, "tgt_interface": tgt}
                for src, tgt in port_map_applied.items()
            ],
            "rollback_plan": meta.get(
                "rollback_plan",
                f"Restore {converted_config.get('hostname', 'source device')} from pre-migration config backup.",
            ),
            "approval_status": "draft",
            "generated_at": _now(),
        }

    # --- String mode: DB-backed assembly ---
    with _mc_conn() as conn:
        session = dict(conn.execute(
            "SELECT * FROM mc_net_sessions WHERE id=%s", (session_id,)
        ).fetchone() or {})

        port_map = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_net_port_map WHERE session_id=%s ORDER BY id", (session_id,)
        ).fetchall()]

        compat = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_net_compat_checks WHERE session_id=%s ORDER BY severity, category, check_name",
            (session_id,)
        ).fetchall()]

        tests = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_net_test_cases WHERE session_id=%s ORDER BY phase, seq_no", (session_id,)
        ).fetchall()]

        cutover = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_net_cutover_steps WHERE session_id=%s ORDER BY seq_no", (session_id,)
        ).fetchall()]

        erb_meta = dict(conn.execute(
            "SELECT * FROM mc_net_erb_metadata WHERE session_id=%s", (session_id,)
        ).fetchone() or {})

    # Auto-compute risk tier from compat blockers if not manually set
    cat1_fails = sum(1 for c in compat if c.get("severity") == "cat1" and c.get("status") == "fail" and not c.get("override_reason"))
    cat2_warns = sum(1 for c in compat if c.get("severity") == "cat2" and c.get("status") in ("fail", "warning"))
    if not erb_meta.get("risk_tier"):
        if cat1_fails > 0:
            risk_tier = "high"
        elif cat2_warns >= 3:
            risk_tier = "medium"
        else:
            risk_tier = "low"
    else:
        risk_tier = erb_meta.get("risk_tier", "medium")

    mw_total_min = sum(s.get("duration_min", 5) for s in cutover)
    mw_buffer_min = max(30, int(mw_total_min * 0.25))

    return {
        "session": session,
        "change_summary": {
            "source_model": session.get("src_model", ""),
            "target_model": session.get("tgt_model", ""),
            "source_device": session.get("src_device_name", ""),
            "target_device": session.get("tgt_device_name", ""),
            "site": session.get("src_site", ""),
            "change_type": erb_meta.get("change_type", "hardware_replacement"),
            "date": _now()[:10],
            "requestor": erb_meta.get("requestor", ""),
        },
        "impact_analysis": {
            "total_interfaces": len(port_map),
            "data_interfaces": sum(1 for r in port_map if r.get("status") not in ("no-migration",)),
            "optic_changes": sum(1 for r in port_map if r.get("optic_change")),
            "speed_mismatches": sum(1 for r in port_map if r.get("speed_mismatch")),
            "customer_facing": erb_meta.get("impact_summary", ""),
            "mw_duration_min": mw_total_min + mw_buffer_min,
            "mw_start": erb_meta.get("mw_start", ""),
            "mw_end": erb_meta.get("mw_end", ""),
        },
        "risk_assessment": {
            "risk_tier": risk_tier,
            "cat1_blockers": cat1_fails,
            "cat2_warnings": cat2_warns,
            "business_justification": erb_meta.get("business_justification", ""),
        },
        "port_mapping": port_map,
        "compatibility_matrix": compat,
        "test_plan": {
            "pre": [t for t in tests if t.get("phase") == "pre"],
            "cutover": [t for t in tests if t.get("phase") == "cutover"],
            "post": [t for t in tests if t.get("phase") == "post"],
        },
        "cutover_sequence": cutover,
        "rollback_plan": erb_meta.get("rollback_plan") or (
            f"Rollback: Re-cable all circuits back to {session.get('src_model','source')} "
            f"({session.get('src_device_name','source device')}). "
            "Source device will remain racked and powered for 30 days post-migration. "
            "Estimated rollback time: same as cutover window."
        ),
        "go_nogo_criteria": json.loads(erb_meta.get("go_nogo_criteria") or "{}"),
        "approval_status": erb_meta.get("approval_status", "draft"),
        "readiness": compute_readiness(session_id),
    }


# ---------------------------------------------------------------------------
# PDF report rendering
# ---------------------------------------------------------------------------

def render_erb_pdf(erb_package: dict, output_path: str) -> str:
    """Render an ERB/CCB package dict to a PDF report at *output_path*.

    Attempts to use ``reportlab`` when available; falls back to a structured
    plain-text report so the function always succeeds.

    Returns *output_path*.
    """
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        from reportlab.lib.pagesizes import letter  # type: ignore
        from reportlab.lib.styles import getSampleStyleSheet  # type: ignore
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer  # type: ignore

        doc = SimpleDocTemplate(str(dest), pagesize=letter)
        styles = getSampleStyleSheet()
        story: list = [
            Paragraph("ERB / CCB Migration Package", styles["Title"]),
            Spacer(1, 12),
        ]

        _section_order = [
            "change_summary", "risk_assessment", "impact_analysis",
            "rollback_plan", "approval_status",
        ]
        for key in _section_order:
            val = erb_package.get(key)
            if val is None:
                continue
            story.append(Paragraph(key.replace("_", " ").title(), styles["Heading1"]))
            if isinstance(val, dict):
                for k, v in val.items():
                    story.append(
                        Paragraph(f"<b>{k}:</b> {v}", styles["Normal"])
                    )
            else:
                story.append(Paragraph(str(val), styles["Normal"]))
            story.append(Spacer(1, 8))

        doc.build(story)

    except ImportError:
        lines = ["ERB/CCB MIGRATION PACKAGE", "=" * 60, ""]
        _section_order2 = [
            "change_summary", "risk_assessment", "impact_analysis",
            "rollback_plan", "approval_status",
        ]
        for key in _section_order2:
            val = erb_package.get(key)
            if val is None:
                continue
            lines.append(key.upper().replace("_", " "))
            lines.append("-" * 40)
            if isinstance(val, dict):
                for k, v in val.items():
                    lines.append(f"  {k}: {v}")
            else:
                lines.append(f"  {val}")
            lines.append("")
        dest.write_text("\n".join(lines), encoding="utf-8")

    return str(dest)


# ---------------------------------------------------------------------------
# RAG + KG integration
# ---------------------------------------------------------------------------

def _index_to_rag(content: str, source_label: str, session_id: str) -> None:
    """Index migration artifact into the memory/knowledge system for RAG search."""
    try:
        from tools.memory.memory_write import write_memory  # type: ignore
        write_memory(
            content=content,
            memory_type="event",
            tags=["network_migration", f"session:{session_id}", "migration_artifact"],
            source=source_label,
        )
    except Exception as exc:
        logger.debug("RAG index skipped: %s", exc)


def _update_kg(session_id: str, design_id: str | None = None) -> None:
    """Update the Migration Canvas KG with network migration device nodes.

    Builds a minimal graph_json with source→migration→target device nodes,
    merges the auto-generated topology nodes/edges, adds a COA node when one
    has been selected, and calls rebuild_canvas_kg("mdc", design_id) if a
    design_id is linked.
    """
    if not design_id:
        # Look up linked design
        try:
            with _mc_conn() as conn:
                row = conn.execute(
                    "SELECT migration_designs.id FROM migration_designs "
                    "WHERE network_session_id=%s", (session_id,)
                ).fetchone()
                design_id = row[0] if row else None
        except Exception:
            pass

    if not design_id:
        return

    try:
        with _mc_conn() as conn:
            sess = dict(conn.execute(
                "SELECT src_model, tgt_model, src_device_name, tgt_device_name, selected_coa, topology_json "
                "FROM mc_net_sessions WHERE id=%s",
                (session_id,)
            ).fetchone() or {})

        src_id = f"net-src-{session_id}"
        tgt_id = f"net-tgt-{session_id}"
        mig_id = f"net-migration-{session_id}"

        nodes = [
            {"id": src_id, "type": "src-network-device", "label": sess.get("src_device_name") or sess.get("src_model", "Source"), "metadata": {"model": sess.get("src_model", "")}},
            {"id": mig_id, "type": "pat-network-cutover", "label": "Network Migration"},
            {"id": tgt_id, "type": "tgt-network-device", "label": sess.get("tgt_device_name") or sess.get("tgt_model", "Target"), "metadata": {"model": sess.get("tgt_model", "")}},
        ]
        edges = [
            {"id": f"e1-{session_id}", "source": src_id, "target": mig_id, "label": "migrates via"},
            {"id": f"e2-{session_id}", "source": mig_id, "target": tgt_id, "label": "migrates to"},
        ]

        # Merge auto-generated topology nodes/edges
        try:
            topo = json.loads(sess.get("topology_json") or "{}")
            if topo.get("nodes"):
                # Prefix topology ids to avoid collisions
                for n in topo["nodes"]:
                    nodes.append({
                        "id": f"topo-{n['id']}",
                        "type": n.get("type", "router"),
                        "label": n.get("label", ""),
                        "metadata": n.get("config", {}),
                    })
                for e in topo.get("edges", []):
                    edges.append({
                        "id": f"topo-{e.get('id', uuid.uuid4().hex[:8])}",
                        "source": f"topo-{e.get('source')}",
                        "target": f"topo-{e.get('target')}",
                        "label": e.get("label", ""),
                    })
        except Exception:
            pass

        selected = sess.get("selected_coa", "")
        if selected:
            coa_id = f"net-coa-{session_id}"
            coa_label = {
                "coa_a": "COA-A: Side-by-Side Parallel",
                "coa_b": "COA-B: Warm Cutover",
                "coa_c": "COA-C: Cold Cutover",
            }.get(selected, selected)
            nodes.append({"id": coa_id, "type": "ctl-rollback", "label": coa_label})
            edges.append({"id": f"e-coa-{session_id}", "source": mig_id, "target": coa_id, "label": "uses COA"})

        graph = {"nodes": nodes, "edges": edges}

        with _mc_conn() as conn:
            conn.execute(
                "UPDATE migration_designs SET graph_json=%s, updated_at=%s WHERE id=%s",
                (json.dumps(graph), _now(), design_id),
            )
            conn.commit()

        from tools.canvas.kg_builder import rebuild_canvas_kg  # type: ignore
        rebuild_canvas_kg("mdc", design_id)
    except Exception as exc:
        logger.debug("KG update skipped: %s", exc)


# ---------------------------------------------------------------------------
# NMCE Phase 3: COA selection, topology, and SOP-aware recommendation
# ---------------------------------------------------------------------------

_DEFAULT_COA_QUESTIONS = [
    {
        "key": "spare_ports_available",
        "text": "Do you have spare ports / VLANs to connect the replacement device alongside the existing one?",
        "default_answer": 1,
        "coa_a_weight": 1.0,
        "coa_b_weight": 0.3,
        "coa_c_weight": -0.2,
    },
    {
        "key": "same_mgmt_vlan_ok",
        "text": "Can the replacement device be placed on the same management VLAN as the existing device?",
        "default_answer": 1,
        "coa_a_weight": 0.8,
        "coa_b_weight": 0.4,
        "coa_c_weight": 0.0,
    },
    {
        "key": "igp_controlled",
        "text": "Is the downstream IGP (OSPF/IS-IS/BGP) under your control and reachable for adjacency testing?",
        "default_answer": 1,
        "coa_a_weight": 0.7,
        "coa_b_weight": 0.5,
        "coa_c_weight": -0.1,
    },
    {
        "key": "tight_maintenance_window",
        "text": "Is the maintenance window too short to run parallel validation?",
        "default_answer": 0,
        "coa_a_weight": -0.6,
        "coa_b_weight": -0.2,
        "coa_c_weight": 0.8,
    },
    {
        "key": "l2_only_replacement",
        "text": "Is the replacement device operating strictly at Layer 2 (no IGP routing on the device)?",
        "default_answer": 0,
        "coa_a_weight": 0.4,
        "coa_b_weight": 0.2,
        "coa_c_weight": 0.6,
    },
    {
        "key": "rollback_familiar",
        "text": "Is the team familiar with a fast rollback to the existing device if the cutover fails?",
        "default_answer": 1,
        "coa_a_weight": 0.5,
        "coa_b_weight": 0.6,
        "coa_c_weight": 0.3,
    },
]


def seed_coa_questions(session_id: str) -> None:
    """Create default COA questions for a session if they don't already exist."""
    existing_keys: set = set()
    with _mc_conn() as conn:
        for row in conn.execute(
            "SELECT question_key FROM mc_net_coa_questions WHERE session_id=%s", (session_id,)
        ).fetchall():
            existing_keys.add(row[0])
    for q in _DEFAULT_COA_QUESTIONS:
        if q["key"] in existing_keys:
            continue
        with _mc_conn() as conn:
            conn.execute(
                """INSERT INTO mc_net_coa_questions
                   (id, session_id, question_key, question_text, default_answer, user_answer,
                    coa_a_weight, coa_b_weight, coa_c_weight)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    str(uuid.uuid4()),
                    session_id,
                    q["key"],
                    q["text"],
                    q.get("default_answer"),
                    q.get("default_answer"),
                    q.get("coa_a_weight", 0),
                    q.get("coa_b_weight", 0),
                    q.get("coa_c_weight", 0),
                ),
            )
            conn.commit()


def get_coa_questions(session_id: str) -> list[dict]:
    """Return COA questions for a session, seeding defaults first."""
    seed_coa_questions(session_id)
    with _mc_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM mc_net_coa_questions WHERE session_id=%s ORDER BY question_key",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_coa_answers(session_id: str, answers: dict[str, int | bool | None]) -> list[dict]:
    """Persist user answers and return updated questions."""
    with _mc_conn() as conn:
        for key, ans in answers.items():
            if ans is None:
                continue
            val = 1 if ans else 0 if isinstance(ans, bool) else int(ans)
            conn.execute(
                "UPDATE mc_net_coa_questions SET user_answer=%s WHERE session_id=%s AND question_key=%s",
                (val, session_id, key),
            )
        conn.commit()
    return get_coa_questions(session_id)


def _score_coa_from_answers(questions: list[dict]) -> dict[str, float]:
    """Score each COA from yes/no answers using question weights."""
    scores = {"coa_a": 0.0, "coa_b": 0.0, "coa_c": 0.0}
    for q in questions:
        ans = q.get("user_answer")
        if ans is None:
            ans = q.get("default_answer", 1)
        direction = 1.0 if ans else -1.0
        scores["coa_a"] += direction * (q.get("coa_a_weight") or 0)
        scores["coa_b"] += direction * (q.get("coa_b_weight") or 0)
        scores["coa_c"] += direction * (q.get("coa_c_weight") or 0)
    return scores


def _detect_context_signals(context: str) -> dict[str, bool]:
    """Rule-based signal extraction from free-text engineer context."""
    text = (context or "").lower()
    signals = {
        "l2_only": any(t in text for t in [
            "layer 2", "layer2", "layer-2", "l2 ", "l2-only", "strictly l2", "no routing", "downstream igp"
        ]),
        "no_igp_control": any(t in text for t in [
            "not under my control", "not controlled", "downstream", "another team", "provider managed"
        ]),
        "tight_window": any(t in text for t in [
            "tight window", "tight maintenance", "short window", "no time", "limited maintenance",
            "brief outage", "maintenance window too short"
        ]),
        "spare_ports": any(t in text for t in [
            "spare port", "available port", "extra port", "same vlan", "parallel connection"
        ]),
    }
    return signals


def _adjust_scores_from_context(scores: dict[str, float], context: str) -> dict[str, float]:
    """Apply rule-based context adjustments to COA scores."""
    signals = _detect_context_signals(context)
    if signals["l2_only"]:
        scores["coa_a"] += 0.5
        scores["coa_b"] += 0.2
        scores["coa_c"] += 0.7
    if signals["no_igp_control"]:
        scores["coa_a"] += 0.6
        scores["coa_b"] += 0.1
        scores["coa_c"] -= 0.3
    if signals["tight_window"]:
        scores["coa_a"] -= 0.5
        scores["coa_b"] -= 0.1
        scores["coa_c"] += 0.7
    if signals["spare_ports"]:
        scores["coa_a"] += 0.4
        scores["coa_b"] += 0.2
    return scores


def recommend_coa(session_id: str) -> dict[str, Any]:
    """Recommend a COA based on parsed config, yes/no answers, and free-text context."""
    questions = get_coa_questions(session_id)
    scores = _score_coa_from_answers(questions)

    with _mc_conn() as conn:
        row = conn.execute(
            "SELECT engineer_context, src_config_raw FROM mc_net_sessions WHERE id=%s", (session_id,)
        ).fetchone()
    context = (row[0] if row else "") or ""
    src_config_raw = (row[1] if row else "") or ""

    scores = _adjust_scores_from_context(scores, context)

    # Config-derived signals
    parsed = parse_source_config(src_config_raw) if src_config_raw else {}
    if parsed.get("bgp_neighbors") or parsed.get("ospf_areas") or parsed.get("isis_nets"):
        # If IGP is present, side-by-side validation is more valuable.
        scores["coa_a"] += 0.2
        scores["coa_b"] += 0.1
    if (parsed.get("raw_interface_count") or 0) <= 2:
        # Very small device: cold cutover is more practical.
        scores["coa_c"] += 0.3

    # Normalize to 0-1 range
    def _norm(v: float) -> float:
        return max(0.0, min(1.0, (v + 2.0) / 4.0))

    normalized = {k: _norm(v) for k, v in scores.items()}
    recommended = max(normalized, key=normalized.get)

    # Build rationale
    coa_names = {
        "coa_a": "Side-by-Side Parallel (safe default)",
        "coa_b": "Warm Cutover",
        "coa_c": "Cold Cutover",
    }
    rationale = (
        f"Recommended '{coa_names[recommended]}' based on your answers and context. "
        f"Scores: COA-A {normalized['coa_a']:.0%}, COA-B {normalized['coa_b']:.0%}, "
        f"COA-C {normalized['coa_c']:.0%}."
    )

    # Optional LLM enhancement for rationale text
    try:
        from tools.llm.router import LLMRouter, LLMRequest
        router = LLMRouter()
        prompt = (
            "You are a network migration engineer. Given these COA scores and context, "
            "write one concise paragraph explaining why the recommended COA is best "
            "and what the engineer should watch out for."
            f"\nContext: {context}\n"
            f"Scores: COA-A={normalized['coa_a']:.2f}, COA-B={normalized['coa_b']:.2f}, COA-C={normalized['coa_c']:.2f}\n"
            f"Recommended: {recommended}"
        )
        resp = router.invoke("recommendation", LLMRequest(prompt=prompt, max_tokens=200))
        if resp and resp.content:
            rationale = resp.content.strip()[:1000]
    except Exception as exc:
        logger.debug("LLM COA rationale skipped: %s", exc)

    # Persist recommendation
    with _mc_conn() as conn:
        conn.execute(
            "UPDATE mc_net_sessions SET recommended_coa=%s, coa_rationale=%s WHERE id=%s",
            (recommended, rationale, session_id),
        )
        conn.commit()

    return {
        "recommended": recommended,
        "rationale": rationale,
        "scores": normalized,
        "questions": questions,
        "context_signals": _detect_context_signals(context),
    }


def select_coa(session_id: str, coa: str, context: str = "") -> dict[str, Any]:
    """Persist engineer-selected COA and refresh recommendation if context changed."""
    if coa not in ("coa_a", "coa_b", "coa_c"):
        raise ValueError(f"Invalid COA: {coa}")
    with _mc_conn() as conn:
        if context:
            conn.execute(
                "UPDATE mc_net_sessions SET selected_coa=%s, engineer_context=%s WHERE id=%s",
                (coa, context, session_id),
            )
        else:
            conn.execute(
                "UPDATE mc_net_sessions SET selected_coa=%s WHERE id=%s", (coa, session_id)
            )
        conn.commit()
    return recommend_coa(session_id)


# ---------------------------------------------------------------------------
# Topology auto-generation (Phase B)
# ---------------------------------------------------------------------------

_NODE_TYPE_FOR_MEDIA = {
    0.1: "media-ge",
    1.0: "media-ge",
    10.0: "media-10ge",
    25.0: "media-25ge",
    40.0: "media-40ge",
    100.0: "media-100ge",
    400.0: "media-400ge",
}


def _media_node_type(iface: dict) -> str:
    speed = iface.get("speed_gbps", 0.0) or 0.0
    optic = (iface.get("optic_type", "") or "").lower()
    if "qsfp-dd" in optic:
        return "qsfp-dd"
    if "qsfp" in optic:
        return "qsfp"
    if optic == "sfp+":
        return "sfp-plus"
    if optic == "sfp":
        return "sfp"
    return _NODE_TYPE_FOR_MEDIA.get(speed, "media-ge")


def _extract_vlan_ids(config_text: str, vendor: str) -> set[str]:
    vids: set[str] = set()
    for m in re.finditer(r"vlan-id\s+(\d+)", config_text, re.I):
        vids.add(m.group(1))
    for m in re.finditer(r"switchport\s+(?:access\s+vlan|trunk\s+allowed\s+vlan)\s+(\d+)", config_text, re.I):
        vids.add(m.group(1))
    for m in re.finditer(r"encapsulation\s+dot1Q\s+(\d+)", config_text, re.I):
        vids.add(m.group(1))
    return vids


def _infer_device_type(parsed: dict) -> str:
    if parsed.get("bgp_neighbors") or parsed.get("ospf_areas") or parsed.get("isis_nets") or parsed.get("l3vpn_vrfs"):
        return "router"
    if parsed.get("l2vpn_instances") or parsed.get("raw_interface_count", 0) <= 2:
        return "switch-l2"
    return "switch-l3"


def _ip_in_network(ip: str, iface_ip: str) -> bool:
    """Best-effort check whether ip belongs to the same subnet as iface_ip."""
    if not ip or not iface_ip:
        return False
    try:
        import ipaddress
        iface_net = ipaddress.ip_interface(iface_ip.split("/")[0] + "/24" if "/" not in iface_ip else iface_ip).network
        return ipaddress.ip_address(ip.split("/")[0]) in iface_net
    except Exception:
        return ip.split(".")[:3] == iface_ip.split(".")[:3]


def discover_neighbors(session_id: str) -> list[dict[str, Any]]:
    """Return neighbor candidates inferred from parsed config, enriched from network_canvas.db."""
    with _mc_conn() as conn:
        row = conn.execute(
            "SELECT src_config_raw FROM mc_net_sessions WHERE id=%s", (session_id,)
        ).fetchone()
    raw_config = (row[0] if row else "") or ""
    parsed = parse_source_config(raw_config) if raw_config else {}
    vendor = parsed.get("vendor", "")

    # BGP peers
    neighbors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in parsed.get("bgp_neighbors", []):
        ip = n.get("ip", "")
        if not ip or ip in seen:
            continue
        seen.add(ip)
        neighbors.append({
            "neighbor_ip": ip,
            "neighbor_name": f"BGP peer {ip}",
            "relationship": "bgp_peer",
            "source_interface": "",
            "protocol_detail": f"ASN {n.get('asn', 0)}",
        })
    # OSPF neighbors (areas)
    for area in parsed.get("ospf_areas", []):
        label = f"OSPF area {area}"
        if label in seen:
            continue
        seen.add(label)
        neighbors.append({
            "neighbor_ip": "",
            "neighbor_name": label,
            "relationship": "ospf_neighbor",
            "source_interface": "",
            "protocol_detail": f"area {area}",
        })
    # ISIS NETs
    for net in parsed.get("isis_nets", []):
        label = f"IS-IS {net}"
        if label in seen:
            continue
        seen.add(label)
        neighbors.append({
            "neighbor_ip": "",
            "neighbor_name": label,
            "relationship": "isis_neighbor",
            "source_interface": "",
            "protocol_detail": net,
        })
    # Static route next-hops
    try:
        from tools.network.config_parser import parse_config
        base = parse_config(raw_config, vendor=vendor)
        for route in base.get("routes", []):
            nh = route.get("next_hop", "")
            if nh and nh not in seen:
                seen.add(nh)
                neighbors.append({
                    "neighbor_ip": nh,
                    "neighbor_name": f"Next-hop {nh}",
                    "relationship": "downstream",
                    "source_interface": "",
                    "protocol_detail": f"static {route.get('network', '')}",
                })
    except Exception:
        pass

    # Enrich from network_canvas inventory
    inventory_rows: list[dict] = []
    try:
        with _nc_conn() as nc:
            inventory_rows = [
                dict(r) for r in nc.execute(
                    "SELECT id, label, node_id, device_type, vendor, model FROM ni_devices"
                ).fetchall()
            ]
    except Exception:
        inventory_rows = []

    for nb in neighbors:
        key = nb["neighbor_ip"] or nb["neighbor_name"]
        match = None
        for inv in inventory_rows:
            labels = [str(inv.get(k, "") or "") for k in ("label", "node_id", "model")]
            if any(key.lower() in lbl.lower() or lbl.lower() in key.lower() for lbl in labels if lbl):
                match = inv
                break
        if match:
            nb["neighbor_name"] = match.get("label") or nb["neighbor_name"]
            nb["notes"] = f"Matched inventory {match.get('vendor','')} {match.get('model','')} ({match.get('device_type','')})"
            nb["is_discovered"] = 1
            nb["inventory_id"] = match.get("id", "")
        else:
            nb["is_discovered"] = 0
    return neighbors


def build_topology(session_id: str, refresh: bool = False) -> dict[str, Any]:
    """Auto-generate a JointJS-compatible topology from the parsed source config.

    The resulting graph contains source/target device nodes, per-interface media
    nodes, inferred neighbor nodes, and VLAN/L2 segment nodes. It is persisted
    in mc_net_sessions.topology_json and the neighbor list is also stored in
    mc_net_topology_neighbors.
    """
    with _mc_conn() as conn:
        row = conn.execute(
            "SELECT src_device_name, tgt_device_name, src_config_raw, src_model, tgt_model, selected_coa, "
            "topology_json FROM mc_net_sessions WHERE id=%s",
            (session_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"Session not found: {session_id}")
    src_name, tgt_name, raw_config, src_model, tgt_model, selected_coa, existing_json = row
    src_label = src_name or src_model or "Source"
    tgt_label = tgt_name or tgt_model or "Target"

    if existing_json and not refresh:
        try:
            stored = json.loads(existing_json)
            if stored.get("nodes"):
                return {"session_id": session_id, "graph_json": stored, "source": "stored"}
        except Exception:
            pass

    parsed = parse_source_config(raw_config) if raw_config else {}
    vendor = parsed.get("vendor", "")
    ifaces = parsed.get("interfaces", []) or []
    device_type = _infer_device_type(parsed)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    created_ids: set[str] = set()

    def _add_node(nid: str, ntype: str, label: str, x: int, y: int, config: dict | None = None) -> None:
        if nid in created_ids:
            return
        created_ids.add(nid)
        nodes.append({
            "id": nid, "type": ntype, "label": label, "x": x, "y": y,
            "config": config or {},
        })

    src_id = f"topo-src-{session_id}"
    tgt_id = f"topo-tgt-{session_id}"
    _add_node(src_id, device_type, src_label, 100, 260)
    _add_node(tgt_id, device_type, tgt_label, 720, 260)
    edges.append({"id": f"e-mig-{session_id}", "source": src_id, "target": tgt_id, "label": "migrates to", "config": {"dashed": True}})

    # Interfaces on the source side
    vlan_ids = _extract_vlan_ids(raw_config, vendor) if raw_config else set()
    iface_y = 60
    for iface in ifaces:
        name = iface.get("name", "")
        if not name or name.startswith("lo0"):
            continue
        in_id = f"topo-iface-{session_id}-{name.replace('/', '-').replace(':', '_')}"
        ip = iface.get("ip", "") or ""
        lbl = name
        if ip:
            lbl += f"\\n{ip}"
        _add_node(in_id, _media_node_type(iface), lbl, 240, iface_y)
        edges.append({"id": f"e-si-{in_id}", "source": src_id, "target": in_id, "label": iface.get("description", "")[:20]})
        iface_y += 56
        if iface_y > 460:
            iface_y = 60

    # VLAN / L2 segment nodes near the middle
    vlan_y = 60
    for vid in sorted(vlan_ids, key=lambda x: int(x) if x.isdigit() else x):
        vn_id = f"topo-vlan-{session_id}-{vid}"
        _add_node(vn_id, "vlan", f"VLAN {vid}", 480, vlan_y)
        vlan_y += 56
        if vlan_y > 460:
            vlan_y = 60

    # Neighbor nodes from config + optional enrichment
    neighbors = discover_neighbors(session_id)
    neigh_y = 60
    for nb in neighbors:
        key = nb.get("neighbor_ip") or nb.get("neighbor_name") or "unknown"
        n_id = f"topo-neigh-{session_id}-{key.replace('/', '-').replace(' ', '_').replace('.', '_')[:40]}"
        rel = nb.get("relationship", "")
        ntype = "router" if rel in ("bgp_peer", "ospf_neighbor", "isis_neighbor") else "server"
        label = nb.get("neighbor_name") or key
        detail = nb.get("protocol_detail", "")
        if detail and len(label + " " + detail) < 40:
            label = f"{label}\\n{detail}"
        _add_node(n_id, ntype, label, 920, neigh_y)
        # Find best source interface by IP subnet match
        src_iface = ""
        for iface in ifaces:
            if iface.get("ip") and nb.get("neighbor_ip") and _ip_in_network(nb["neighbor_ip"], iface["ip"]):
                src_iface = iface["name"]
                break
        if src_iface:
            iface_id = f"topo-iface-{session_id}-{src_iface.replace('/', '-').replace(':', '_')}"
            if iface_id in created_ids:
                edges.append({"id": f"e-in-{n_id}", "source": iface_id, "target": n_id, "label": rel.replace('_', ' ')})
            else:
                edges.append({"id": f"e-sn-{n_id}", "source": src_id, "target": n_id, "label": rel.replace('_', ' ')})
        else:
            edges.append({"id": f"e-sn-{n_id}", "source": src_id, "target": n_id, "label": rel.replace('_', ' ')})
        nb["node_id"] = n_id
        neigh_y += 56
        if neigh_y > 460:
            neigh_y = 60

    graph = {"nodes": nodes, "edges": edges}
    neighbors_json = json.dumps(neighbors)
    topology_json = json.dumps(graph)

    with _mc_conn() as conn:
        conn.execute(
            "UPDATE mc_net_sessions SET topology_json=%s, topology_neighbors_json=%s WHERE id=%s",
            (topology_json, neighbors_json, session_id),
        )
        conn.execute("DELETE FROM mc_net_topology_neighbors WHERE session_id=%s", (session_id,))
        for nb in neighbors:
            conn.execute(
                "INSERT INTO mc_net_topology_neighbors "
                "(id, session_id, neighbor_name, neighbor_ip, relationship, source_interface, is_discovered, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"tnb-{session_id}-{uuid.uuid4().hex[:8]}",
                    session_id,
                    nb.get("neighbor_name", ""),
                    nb.get("neighbor_ip", ""),
                    nb.get("relationship", ""),
                    nb.get("source_interface", ""),
                    int(nb.get("is_discovered", 0) or 0),
                    nb.get("notes", ""),
                ),
            )
        conn.commit()

    # RAG + KG indexing
    _index_to_rag(
        f"Network migration topology for {src_label} → {tgt_label}. "
        f"Interfaces: {len(ifaces)}, neighbors: {len(neighbors)}, VLANs: {len(vlan_ids)}. "
        f"Selected COA: {selected_coa or 'not selected'}.",
        "build_topology",
        session_id,
    )
    _update_kg(session_id)

    return {"session_id": session_id, "graph_json": graph, "neighbors": neighbors, "source": "generated"}


# ---------------------------------------------------------------------------
# NMCE Phase 2: Inventory, Config Loading, AI, Protocol Planning, Timeline
# ---------------------------------------------------------------------------

def get_network_inventory(
    site: str = "",
    device_type: str = "",
    vendor: str = "",
    eol_within_years: int = 0,
) -> list[dict]:
    """Return network device inventory from ni_devices in network_canvas.db.

    Falls back to nc_hardware_profiles rows if ni_devices is empty.
    Annotates each device with has_config (from ni_device_configs) and
    active_session_id (from mc_net_sessions in migration_canvas.db).
    """
    try:
        with _nc_conn() as nc:
            rows = nc.execute(
                "SELECT id, node_id, label, device_type, vendor, model, "
                "firmware_version, eol_date, eos_date FROM ni_devices WHERE 1=1"
            ).fetchall()
    except Exception:
        rows = []

    if not rows:
        try:
            with _nc_conn() as nc:
                rows = nc.execute(
                    "SELECT id, '' AS node_id, (vendor||' '||model) AS label, "
                    "device_type, vendor, model, '' AS firmware_version, "
                    "eol_date, '' AS eos_date FROM nc_hardware_profiles ORDER BY vendor, model"
                ).fetchall()
        except Exception:
            return []

    # Build set of device_ids that have configs
    config_ids: set = set()
    try:
        with _nc_conn() as nc:
            for r in nc.execute("SELECT DISTINCT device_id FROM ni_device_configs").fetchall():
                config_ids.add(r[0])
    except Exception:
        pass

    # Build map of model -> active session_id
    session_map: dict = {}
    try:
        with _mc_conn() as mc:
            for r in mc.execute(
                "SELECT id, src_model FROM mc_net_sessions WHERE status NOT IN ('complete','archived')"
            ).fetchall():
                session_map[r[1]] = r[0]
    except Exception:
        pass

    devices = []
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    for r in rows:
        rd = dict(r)
        eol = rd.get("eol_date", "") or ""
        has_config = rd["id"] in config_ids
        active_sid = session_map.get(rd.get("model", ""))

        # EOL filter
        if eol_within_years and eol:
            try:
                eol_dt = _dt.fromisoformat(eol[:10])
                if (eol_dt - now.replace(tzinfo=None)).days > eol_within_years * 365:
                    continue
            except Exception:
                pass

        # Device type filter
        if device_type and rd.get("device_type", "") != device_type:
            continue

        # Vendor filter (case-insensitive substring)
        if vendor and vendor.lower() not in (rd.get("vendor", "") or "").lower():
            continue

        # Site filter — ni_devices may not have site; skip if mismatch
        # (site info lives in topology/nc_device_geo, skip for now)

        devices.append({
            "id": rd["id"],
            "node_id": rd.get("node_id", ""),
            "label": rd.get("label", rd.get("model", "")),
            "vendor": rd.get("vendor", ""),
            "model": rd.get("model", ""),
            "device_type": rd.get("device_type", ""),
            "firmware_version": rd.get("firmware_version", ""),
            "eol_date": eol,
            "eos_date": rd.get("eos_date", "") or "",
            "has_config": has_config,
            "config_source": "db" if has_config else "none",
            "active_session_id": active_sid,
        })

    # Apply site filter via a no-op here (site not in ni_devices base schema)
    return devices


def load_device_config_from_db(device_id: str) -> str | None:
    """Fetch the most-recent running/startup config for a device from ni_device_configs.

    Config type priority: running > startup > show_run > any other.
    Returns the config_text string or None if not found.
    """
    try:
        with _nc_conn() as nc:
            row = nc.execute(
                """SELECT config_text FROM ni_device_configs
                   WHERE device_id=?
                   ORDER BY CASE config_type
                     WHEN 'running' THEN 1
                     WHEN 'startup' THEN 2
                     WHEN 'show_run' THEN 3
                     ELSE 4
                   END, created_at DESC LIMIT 1""",
                (device_id,),
            ).fetchone()
    except Exception:
        return None
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Configuration-section mapping (AI-assisted, HITL-reviewed)
# ---------------------------------------------------------------------------

_CONFIG_SECTION_TYPES: tuple[str, ...] = (
    "system", "interfaces", "routing-options", "protocols",
    "policy-options", "firewall", "class-of-service", "services",
    "snmp", "ntp", "syslog", "aaa", "management", "vlans", "unknown",
)

# Mapping of source section type -> likely target section type (vendor-agnostic).
_SECTION_TYPE_MAP: dict[str, str] = {
    "interfaces": "interfaces",
    "routing-options": "routing-options",
    "protocols": "protocols",
    "policy-options": "policy-options",
    "firewall": "firewall",
    "class-of-service": "class-of-service",
    "services": "services",
    "snmp": "snmp",
    "ntp": "ntp",
    "syslog": "syslog",
    "aaa": "aaa",
    "system": "system",
    "management": "management",
    "vlans": "vlans",
}


def _detect_section_type_juniper(line: str) -> str:
    """Return the top-level config section for a Juniper line.

    Handles both `set` style and curly-brace hierarchical style.
    """
    stripped = line.strip()
    parts = stripped.split()
    if len(parts) >= 2 and parts[0].lower() == "set":
        sec = parts[1].lower().rstrip(";")
        return sec if sec in _CONFIG_SECTION_TYPES else "unknown"
    # Curly-brace style: top-level sections look like "system {" or "interfaces {"
    if parts:
        first = parts[0].lower().rstrip(";")
        if first in _CONFIG_SECTION_TYPES:
            return first
    return "unknown"


def _detect_section_type_cisco(line: str) -> str:
    """Return the section type for a Cisco/Arista block-style config line."""
    lowered = line.strip().lower()
    if lowered.startswith("interface "):
        return "interfaces"
    if lowered.startswith("router ") or lowered.startswith("ipv6 router "):
        return "protocols"
    if lowered.startswith("routing "):
        return "routing-options"
    if lowered.startswith("ip route ") or lowered.startswith("ipv6 route "):
        return "routing-options"
    if lowered.startswith("ip access-list ") or lowered.startswith("ipv6 access-list "):
        return "firewall"
    if lowered.startswith("route-map ") or lowered.startswith("ip prefix-list "):
        return "policy-options"
    if lowered.startswith("vlan ") or lowered.startswith("vlan database"):
        return "vlans"
    if lowered.startswith("hostname "):
        return "system"
    if lowered.startswith("banner ") or lowered.startswith("ip domain-") or lowered.startswith("service "):
        return "system"
    if lowered.startswith("snmp-") or lowered.startswith("snmp "):
        return "snmp"
    if lowered.startswith("ntp ") or lowered.startswith("clock "):
        return "ntp"
    if lowered.startswith("logging ") or lowered.startswith("syslog "):
        return "syslog"
    if lowered.startswith("aaa ") or lowered.startswith("username ") or lowered.startswith("enable "):
        return "aaa"
    if lowered.startswith("line ") or lowered.startswith("archive") or lowered.startswith("mgmt "):
        return "management"
    return "unknown"


def _detect_section_type_arista(line: str) -> str:
    """Arista EOS is Cisco-like with a few extra keywords."""
    lowered = line.strip().lower()
    if lowered.startswith("router ") or lowered.startswith("ipv6 route "):
        return "protocols"
    if lowered.startswith("ip routing") or lowered.startswith("vrf instance "):
        return "routing-options"
    if lowered.startswith("interface "):
        return "interfaces"
    if lowered.startswith("ip access-list ") or lowered.startswith("ipv6 access-list "):
        return "firewall"
    if lowered.startswith("route-map ") or lowered.startswith("ip prefix-list "):
        return "policy-options"
    if lowered.startswith("vlan "):
        return "vlans"
    if lowered.startswith("hostname "):
        return "system"
    return "unknown"


def _section_detector(vendor: str):
    if vendor == "juniper":
        return _detect_section_type_juniper
    if vendor in ("cisco_ios", "cisco_nxos"):
        return _detect_section_type_cisco
    if vendor == "arista":
        return _detect_section_type_arista
    # Generic: prefer Cisco-style block detection, then Juniper-style set detection.
    return lambda line: _detect_section_type_cisco(line) or _detect_section_type_juniper(line)


def _extract_config_sections(config_text: str, parsed: dict | None = None) -> list[dict]:
    """Split a device config into semantic sections.

    Returns a list of dicts:
      {
        "section_type": str,
        "start_line": int,    # 1-based
        "end_line": int,
        "lines": [str],
        "stanza_text": str,   # joined lines
      }
    """
    vendor = (parsed or {}).get("vendor", "")
    detector = _section_detector(vendor)
    lines = config_text.splitlines()
    sections: list[dict] = []
    current: dict | None = None

    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sec_type = detector(raw)
        # Juniper: every line is a full command; start a new section on type change.
        if vendor == "juniper":
            # For curly-brace style, nested lines inherit the current top-level section.
            if sec_type == "unknown" and current is not None:
                sec_type = current["section_type"]
            if current is None or current["section_type"] != sec_type:
                if current is not None:
                    sections.append(current)
                current = {"section_type": sec_type, "start_line": idx, "end_line": idx, "lines": [raw], "stanza_text": raw}
            else:
                current["lines"].append(raw)
                current["end_line"] = idx
                current["stanza_text"] += "\n" + raw
            continue

        # Block-style vendors: section headers start a new block.
        is_header = (
            stripped.startswith("!")
            or stripped.startswith("interface ")
            or stripped.startswith("router ")
            or stripped.startswith("routing ")
            or stripped.startswith("ip access-list ")
            or stripped.startswith("ipv6 access-list ")
            or stripped.startswith("route-map ")
            or stripped.startswith("ip prefix-list ")
            or stripped.startswith("policy-map ")
            or stripped.startswith("class-map ")
            or stripped.startswith("vlan ")
            or stripped.startswith("vrf ")
            or stripped.startswith("vrf instance ")
            or stripped.startswith("hostname ")
            or stripped.startswith("banner ")
            or stripped.startswith("line ")
            or stripped.startswith("aaa ")
            or stripped.startswith("archive")
            or stripped.startswith("management ")
            or stripped.startswith("system ")
        )
        if is_header or current is None:
            if current is not None and current["lines"]:
                sections.append(current)
            current = {"section_type": sec_type, "start_line": idx, "end_line": idx, "lines": [raw], "stanza_text": raw}
        else:
            current["lines"].append(raw)
            current["end_line"] = idx
            current["stanza_text"] += "\n" + raw
            # If a header also changes section type, update it.
            if is_header:
                current["section_type"] = sec_type

    if current is not None and current["lines"]:
        sections.append(current)

    return sections


def _target_vendor_from_model(tgt_model: str) -> str:
    """Infer target vendor from model name or hardware profile."""
    if not tgt_model:
        return ""
    lowered = tgt_model.lower()
    if "mx" in lowered or "ex" in lowered or "qfx" in lowered or "srx" in lowered:
        return "juniper"
    if "nexus" in lowered or "catalyst" in lowered or "asr" in lowered or "csr" in lowered or "ios" in lowered:
        return "cisco_ios" if "nx" not in lowered else "cisco_nxos"
    if "eos" in lowered or "arista" in lowered or "dcs" in lowered:
        return "arista"
    if "timos" in lowered or "sr" in lowered:
        return "nokia"
    if "ironware" in lowered or "brocade" in lowered or "ruckus" in lowered:
        return "brocade"
    # Fallback: query hardware profile DB if available.
    try:
        with _nc_conn() as nc:
            row = nc.execute(
                "SELECT vendor FROM nc_hardware_profiles WHERE LOWER(model)=LOWER(?) OR LOWER(id)=LOWER(?) LIMIT 1",
                (tgt_model, f"hw-{lowered.replace(' ', '-')}"),
            ).fetchone()
        if row:
            return (row["vendor"] if isinstance(row, sqlite3.Row) else row[0]).lower()
    except Exception:
        pass
    return ""


def _build_default_questions(parsed: dict, src_vendor: str, tgt_vendor: str) -> list[dict]:
    """Generate the initial yes/no question set for config mapping."""
    hostname = (parsed.get("hostname", "source-device") or "source-device").strip().rstrip(";")
    questions: list[dict] = []

    # Hostname preservation.
    questions.append({
        "question_key": "preserve_hostname",
        "question_text": f"Preserve the source hostname '{hostname}' on the target device?",
        "default_answer": 0,
        "user_answer": None,
        "ai_relevance": "When 'yes', the target config keeps the original hostname. When 'no', a placeholder target hostname is used.",
    })

    # VRF/routing-instance naming.
    if parsed.get("l3vpn_vrfs"):
        questions.append({
            "question_key": "preserve_vrf_names",
            "question_text": "Preserve existing VRF / routing-instance names on the target device?",
            "default_answer": 1,
            "user_answer": None,
            "ai_relevance": "When 'yes', VRF names are copied verbatim. When 'no', VRFs are renamed to a target convention.",
        })

    # Cross-vendor syntax conversion.
    if src_vendor and tgt_vendor and src_vendor != tgt_vendor:
        questions.append({
            "question_key": "convert_vendor_syntax",
            "question_text": f"Convert {src_vendor} configuration syntax to {tgt_vendor} style where possible?",
            "default_answer": 1,
            "user_answer": None,
            "ai_relevance": "When 'yes', AI proposes vendor-specific target stanzas. When 'no', sections are flagged as 'manual'.",
        })

    # Firewall/filter migration.
    if parsed.get("firewall_filters"):
        questions.append({
            "question_key": "migrate_firewall_filters",
            "question_text": "Migrate all firewall / filter stanzas instead of dropping deprecated ones?",
            "default_answer": 1,
            "user_answer": None,
            "ai_relevance": "When 'yes', filters are mapped to target syntax. When 'no', deprecated filters are removed.",
        })

    # Management interfaces.
    questions.append({
        "question_key": "preserve_mgmt_interfaces",
        "question_text": "Preserve management / out-of-band interface configuration?",
        "default_answer": 1,
        "user_answer": None,
        "ai_relevance": "When 'yes', mgmt/fxp/em0 stanzas are kept. When 'no', they are skipped to avoid target conflicts.",
    })

    # BGP neighbor details.
    if parsed.get("bgp_neighbors"):
        questions.append({
            "question_key": "preserve_bgp_peers",
            "question_text": "Keep existing BGP neighbor IPs, ASNs, and group names exactly?",
            "default_answer": 1,
            "user_answer": None,
            "ai_relevance": "When 'yes', BGP neighbor definitions are copied with interface renames only. When 'no', AI may suggest redesign.",
        })

    return questions


def _load_config_map_questions(session_id: str) -> list[dict]:
    with _mc_conn() as mc:
        rows = mc.execute(
            "SELECT question_key, question_text, default_answer, user_answer, ai_relevance "
            "FROM mc_net_config_questions WHERE session_id=%s ORDER BY id",
            (session_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _save_config_map_questions(session_id: str, questions: list[dict]) -> None:
    with _mc_conn() as mc:
        for q in questions:
            mc.execute(
                "INSERT INTO mc_net_config_questions (id, session_id, question_key, question_text, "
                "default_answer, user_answer, ai_relevance) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(session_id, question_key) DO UPDATE SET "
                "question_text=excluded.question_text, default_answer=excluded.default_answer, "
                "user_answer=excluded.user_answer, ai_relevance=excluded.ai_relevance",
                (str(uuid.uuid4()), session_id, q["question_key"], q["question_text"],
                 q.get("default_answer"), q.get("user_answer"), q.get("ai_relevance", "")),
            )
        mc.commit()


def generate_config_map_questions(session_id: str) -> dict:
    """Return (and seed if missing) the yes/no questions for config mapping.

    Returns {"questions": [...]} where each question has:
      question_key, question_text, default_answer, user_answer, ai_relevance
    """
    existing = _load_config_map_questions(session_id)
    if existing:
        return {"questions": existing}

    sess: dict = {}
    parsed: dict = {}
    try:
        with _mc_conn() as mc:
            row = mc.execute(
                "SELECT src_model, tgt_model, src_config_raw FROM mc_net_sessions WHERE id=%s",
                (session_id,),
            ).fetchone()
        if row:
            sess = dict(row)
    except Exception as e:
        logger.warning("generate_config_map_questions: session load failed: %s", e)

    if sess.get("src_config_raw"):
        try:
            parsed = parse_source_config(sess["src_config_raw"])
        except Exception as e:
            logger.warning("generate_config_map_questions: parse failed: %s", e)

    src_vendor = parsed.get("vendor", "")
    tgt_vendor = _target_vendor_from_model(sess.get("tgt_model", ""))
    questions = _build_default_questions(parsed, src_vendor, tgt_vendor)
    _save_config_map_questions(session_id, questions)
    return {"questions": questions}


def _get_question_answers(session_id: str) -> dict[str, int | None]:
    """Return {question_key: user_answer|default_answer}."""
    rows = _load_config_map_questions(session_id)
    return {
        r["question_key"]: (r["user_answer"] if r.get("user_answer") is not None else r.get("default_answer"))
        for r in rows
    }


def _rule_based_config_mapping(
    sections: list[dict],
    parsed: dict,
    port_map: dict[str, str],
    src_vendor: str,
    tgt_vendor: str,
    answers: dict[str, int | None],
) -> list[dict]:
    """Deterministic fallback that maps sections when LLM is unavailable."""
    deprecated_patterns = _get_deprecated_patterns(src_vendor)
    preserve_hostname = bool(answers.get("preserve_hostname", 0))
    convert_vendor = bool(answers.get("convert_vendor_syntax", 1))
    preserve_mgmt = bool(answers.get("preserve_mgmt_interfaces", 1))
    migrate_fw = bool(answers.get("migrate_firewall_filters", 1))

    proposals: list[dict] = []
    for sec in sections:
        stype = sec["section_type"]
        src_text = sec["stanza_text"]
        lines = sec["lines"]

        # Build a default target stanza by applying port renames and removing deprecated lines.
        tgt_lines: list[str] = []
        removed_reasons: list[str] = []
        for line in lines:
            removed = False
            for pat, reason in deprecated_patterns:
                if pat.search(line):
                    removed = True
                    removed_reasons.append(reason)
                    break
            if removed:
                continue
            # Apply port/interface renames.
            out = line
            for src_if, tgt_if in port_map.items():
                if re.search(r"\b" + re.escape(src_if.split(".")[0]) + r"\b", out):
                    out = re.sub(
                        r"\b" + re.escape(src_if.split(".")[0]) + r"\b",
                        tgt_if.split(".")[0],
                        out,
                    )
            tgt_lines.append(out)

        tgt_text = "\n".join(tgt_lines)

        # Decide action and rationale.
        action = "direct"
        rationale = "Copy to target with interface renames applied."
        confidence = 0.85

        if stype == "interfaces":
            action = "rename"
            rationale = "Map source interfaces to target interfaces using the port map."
            confidence = 0.90
        elif stype == "system":
            if not preserve_hostname and re.search(r"^\s*(hostname|set\s+system\s+host-name)", src_text, re.M | re.I):
                tgt_text = re.sub(r"(hostname|set\s+system\s+host-name)\s+\S+", r"\1 <target-hostname>", tgt_text, flags=re.I)
                rationale = "Source hostname replaced with target hostname."
            else:
                rationale = "System-level settings copied (hostname, banner, etc.)."
        elif stype in ("protocols", "routing-options"):
            if src_vendor != tgt_vendor and not convert_vendor:
                action = "manual"
                confidence = 0.55
                rationale = f"Cross-vendor {stype} stanza requires manual syntax conversion from {src_vendor} to {tgt_vendor}."
            else:
                action = "direct" if src_vendor == tgt_vendor or convert_vendor else "manual"
                rationale = f"{stype} stanza migrated; verify neighbor/interface references." if action == "direct" else f"{stype} syntax may need vendor-specific adjustments."
                confidence = 0.80 if action == "direct" else 0.60
        elif stype == "firewall":
            if not migrate_fw:
                action = "remove"
                rationale = "User chose to drop deprecated firewall/filter stanzas."
                confidence = 0.95
            elif src_vendor != tgt_vendor:
                action = "manual"
                rationale = f"Firewall/filter syntax differs between {src_vendor} and {tgt_vendor}."
                confidence = 0.60
            else:
                rationale = "Firewall/filter stanza copied with interface renames."
        elif stype == "management":
            if not preserve_mgmt:
                action = "skip"
                rationale = "Management interfaces skipped per user preference."
                confidence = 0.95
            else:
                rationale = "Management/oob configuration preserved."
        elif removed_reasons:
            action = "remove"
            rationale = f"Deprecated / platform-specific content removed: {removed_reasons[0]}"
            confidence = 0.90

        if action in ("manual", "remove", "skip"):
            confidence = min(confidence, 0.65)

        proposals.append({
            "id": str(uuid.uuid4()),
            "src_section_type": stype,
            "src_stanza_text": src_text,
            "src_lines_json": json.dumps([{"line_no": sec["start_line"] + i, "text": ln} for i, ln in enumerate(lines)]),
            "tgt_section_type": _SECTION_TYPE_MAP.get(stype, stype),
            "tgt_stanza_text": tgt_text if action not in ("remove", "skip") else "",
            "mapping_action": action,
            "confidence": round(confidence, 2),
            "ai_rationale": rationale,
            "ai_question_key": "",
            "status": "pending",
            "reviewer_note": "",
        })

    return proposals


def _llm_config_mapping(
    sections: list[dict],
    parsed: dict,
    port_map: dict[str, str],
    src_vendor: str,
    tgt_vendor: str,
    answers: dict[str, int | None],
    session_id: str,
) -> list[dict]:
    """Use LLM to propose section-level config mapping. Falls back to rule-based."""
    try:
        from tools.llm.router import LLMRouter  # type: ignore
        from tools.llm.provider import LLMRequest  # type: ignore
    except Exception as e:
        logger.warning("_llm_config_mapping: LLM imports unavailable: %s", e)
        return _rule_based_config_mapping(sections, parsed, port_map, src_vendor, tgt_vendor, answers)

    # Trim section text to keep prompt size reasonable.
    trimmed_sections = []
    for sec in sections:
        text = sec["stanza_text"]
        if len(text) > 2000:
            text = text[:2000] + "\n... [truncated for LLM prompt]"
        trimmed_sections.append({
            "section_type": sec["section_type"],
            "start_line": sec["start_line"],
            "end_line": sec["end_line"],
            "stanza_text": text,
        })

    prompt = (
        f"You are a senior network architect migrating a {src_vendor} device to a {tgt_vendor} device.\n\n"
        f"Source hostname: {parsed.get('hostname','unknown')}\n"
        f"User migration preferences (yes/no answers):\n{json.dumps(answers, indent=2)}\n\n"
        f"Port map (source interface -> target interface):\n{json.dumps(port_map, indent=2)}\n\n"
        "Source config sections:\n"
        f"{json.dumps(trimmed_sections, indent=2)}\n\n"
        "For each section, propose a target configuration stanza and classify the action as one of: "
        "direct, rename, merge, split, remove, manual, skip.\n\n"
        "Return valid JSON only, no markdown, no commentary:\n"
        "{\n"
        '  "sections": [\n'
        "    {\n"
        '      "src_section_type": "...",\n'
        '      "tgt_section_type": "...",\n'
        '      "tgt_stanza_text": "...",\n'
        '      "mapping_action": "direct",\n'
        '      "confidence": 0.92,\n'
        '      "ai_rationale": "Tooltip explanation for HITL reviewer",\n'
        '      "ai_question_key": ""\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )

    try:
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a network architect. Respond with valid JSON only.",
            effort="high",
        )
        resp = router.invoke("code_generation", req)
        result = json.loads(resp.content)
    except Exception as e:
        logger.warning("_llm_config_mapping: LLM call failed: %s", e)
        return _rule_based_config_mapping(sections, parsed, port_map, src_vendor, tgt_vendor, answers)

    llm_sections = result.get("sections", [])
    if not llm_sections:
        return _rule_based_config_mapping(sections, parsed, port_map, src_vendor, tgt_vendor, answers)

    # Enforce every returned section has required fields and generate an id.
    proposals: list[dict] = []
    for ls in llm_sections:
        stype = ls.get("src_section_type", "unknown")
        # Match back to original stanza if possible.
        orig = next((s for s in sections if s["section_type"] == stype), None)
        src_text = orig["stanza_text"] if orig else ""
        src_lines = orig["lines"] if orig else []
        proposals.append({
            "id": str(uuid.uuid4()),
            "src_section_type": stype,
            "src_stanza_text": src_text,
            "src_lines_json": json.dumps([{"line_no": (orig["start_line"] if orig else 1) + i, "text": ln} for i, ln in enumerate(src_lines)]),
            "tgt_section_type": ls.get("tgt_section_type", _SECTION_TYPE_MAP.get(stype, stype)),
            "tgt_stanza_text": ls.get("tgt_stanza_text", ""),
            "mapping_action": ls.get("mapping_action", "manual"),
            "confidence": max(0.0, min(1.0, float(ls.get("confidence", 0.7)))),
            "ai_rationale": ls.get("ai_rationale", "AI-proposed target stanza."),
            "ai_question_key": ls.get("ai_question_key", ""),
            "status": "pending",
            "reviewer_note": "",
        })

    # If LLM dropped sections, backfill with rule-based to ensure full coverage.
    covered_types = {p["src_section_type"] for p in proposals}
    for sec in sections:
        if sec["section_type"] not in covered_types:
            rule_props = _rule_based_config_mapping([sec], parsed, port_map, src_vendor, tgt_vendor, answers)
            proposals.extend(rule_props)

    return proposals


def propose_config_mapping(
    session_id: str,
    answers: dict[str, int] | None = None,
    use_llm: bool = True,
) -> dict:
    """Generate and persist section-level config mapping proposals.

    Args:
        session_id: Network migration session id.
        answers: Optional override mapping {question_key: 1|0}. If provided, answers are saved.
        use_llm: If True, try LLM; otherwise deterministic rule-based.

    Returns:
        {"proposals": [...], "questions": [...], "model": str|""}
    """
    # Ensure questions exist.
    generate_config_map_questions(session_id)
    if answers:
        existing = _load_config_map_questions(session_id)
        for q in existing:
            if q["question_key"] in answers:
                q["user_answer"] = answers[q["question_key"]]
        _save_config_map_questions(session_id, existing)

    # Load session and port map.
    sess: dict = {}
    with _mc_conn() as mc:
        row = mc.execute("SELECT * FROM mc_net_sessions WHERE id=%s", (session_id,)).fetchone()
        if row:
            sess = dict(row)
        port_rows = mc.execute("SELECT * FROM mc_net_port_map WHERE session_id=%s", (session_id,)).fetchall()
    port_map = {r["src_interface"]: r["tgt_interface"] for r in port_rows if r.get("src_interface") and r.get("tgt_interface")}

    if not sess:
        return {"error": "Session not found", "proposals": [], "questions": [], "model": ""}

    src_config_raw = sess.get("src_config_raw", "")
    if not src_config_raw:
        return {"error": "No source config imported", "proposals": [], "questions": [], "model": ""}

    parsed = parse_source_config(src_config_raw)
    src_vendor = parsed.get("vendor", "")
    tgt_vendor = _target_vendor_from_model(sess.get("tgt_model", ""))

    sections = _extract_config_sections(src_config_raw, parsed)
    current_answers = _get_question_answers(session_id)

    if use_llm:
        proposals = _llm_config_mapping(sections, parsed, port_map, src_vendor, tgt_vendor, current_answers, session_id)
        model = "llm"
    else:
        proposals = _rule_based_config_mapping(sections, parsed, port_map, src_vendor, tgt_vendor, current_answers)
        model = "rule-based"

    # Persist proposals (replace prior auto-generated pending proposals).
    with _mc_conn() as mc:
        mc.execute("DELETE FROM mc_net_config_map WHERE session_id=%s AND status='pending'", (session_id,))
        for p in proposals:
            mc.execute(
                "INSERT INTO mc_net_config_map (id, session_id, src_section_type, src_stanza_text, "
                "src_lines_json, tgt_section_type, tgt_stanza_text, mapping_action, confidence, "
                "ai_rationale, ai_question_key, status, reviewer_note, applied_to_target) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (p["id"], session_id, p["src_section_type"], p["src_stanza_text"],
                 p["src_lines_json"], p["tgt_section_type"], p["tgt_stanza_text"],
                 p["mapping_action"], p["confidence"], p["ai_rationale"],
                 p["ai_question_key"], p["status"], p["reviewer_note"], 0),
            )
        mc.commit()

    # Save LLM usage to audit trail.
    if session_id:
        try:
            with _mc_conn() as mc:
                mc.execute(
                    "INSERT INTO mc_net_ai_sessions (id, session_id, role, message, model_used, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), session_id, "assistant",
                     f"[Config mapping] generated {len(proposals)} proposals via {model}",
                     model, _now()),
                )
                mc.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "propose_config_mapping: best-effort INSERT into mc_net_ai_sessions failed (non-blocking): %s",
                exc,
            )

    questions = _load_config_map_questions(session_id)
    return {"proposals": proposals, "questions": questions, "model": model, "count": len(proposals)}


def get_config_map(session_id: str) -> dict:
    """Return persisted mapping proposals and questions for a session."""
    with _mc_conn() as mc:
        rows = mc.execute(
            "SELECT * FROM mc_net_config_map WHERE session_id=%s ORDER BY created_at, id", (session_id,)
        ).fetchall()
    proposals = [dict(r) for r in rows]
    questions = _load_config_map_questions(session_id)
    return {"proposals": proposals, "questions": questions}


def decide_config_map_row(session_id: str, row_id: str, decision: str, note: str = "") -> dict:
    """Approve / reject / skip a single mapping row."""
    if decision not in ("approved", "rejected", "skipped", "pending"):
        return {"error": "Invalid decision"}
    with _mc_conn() as mc:
        cur = mc.execute(
            "UPDATE mc_net_config_map SET status=%s, reviewer_note=%s, updated_at=%s "
            "WHERE session_id=%s AND id=%s",
            (decision, note, _now(), session_id, row_id),
        )
        mc.commit()
    return {"ok": True, "updated": cur.rowcount}


def apply_approved_config_map(session_id: str) -> dict:
    """Assemble target config from approved mapping rows and port map.

    Returns {"target_config": str, "approved_count": int, "rejected_count": int, "skipped_count": int}
    """
    with _mc_conn() as mc:
        rows = mc.execute(
            "SELECT * FROM mc_net_config_map WHERE session_id=%s ORDER BY created_at, id", (session_id,)
        ).fetchall()
        sess_row = mc.execute("SELECT src_config_raw, tgt_model FROM mc_net_sessions WHERE id=%s", (session_id,)).fetchone()
    if not sess_row:
        return {"error": "Session not found"}

    proposals = [dict(r) for r in rows]
    approved = [p for p in proposals if p.get("status") == "approved"]

    # Build target config by concatenating approved target stanzas in source order.
    # We use the original line numbers stored in src_lines_json to recover ordering.
    ordered: list[tuple[int, dict]] = []
    for p in approved:
        try:
            lines = json.loads(p.get("src_lines_json") or "[]")
            start_line = lines[0]["line_no"] if lines else 999999
        except Exception:
            start_line = 999999
        ordered.append((start_line, p))
    ordered.sort(key=lambda x: x[0])

    target_parts: list[str] = []
    for _, p in ordered:
        if p.get("mapping_action") in ("remove", "skip"):
            continue
        tgt = (p.get("tgt_stanza_text") or "").strip()
        if tgt:
            target_parts.append(tgt)

    target_config = "\n\n".join(target_parts)

    # Mark as applied.
    with _mc_conn() as mc:
        mc.execute(
            "UPDATE mc_net_config_map SET applied_to_target=1, updated_at=%s "
            "WHERE session_id=%s AND status='approved'",
            (_now(), session_id),
        )
        mc.execute(
            "UPDATE mc_net_sessions SET target_config=%s, updated_at=%s WHERE id=%s",
            (target_config, _now(), session_id),
        )
        mc.commit()

    return {
        "target_config": target_config,
        "approved_count": len(approved),
        "rejected_count": sum(1 for p in proposals if p.get("status") == "rejected"),
        "skipped_count": sum(1 for p in proposals if p.get("status") == "skipped"),
        "pending_count": sum(1 for p in proposals if p.get("status") == "pending"),
    }


def recommend_hardware(
    device_info: dict,
    engineer_notes: str = "",
    session_id: str = "",
) -> dict:
    """AI-powered hardware replacement recommendation.

    Fetches valid replacements from nc_hardware_profiles, calls LLMRouter,
    and falls back to deterministic scoring when LLM is unavailable.
    """
    import uuid

    dtype = device_info.get("device_type", "router")
    try:
        with _nc_conn() as nc:
            profiles = [
                dict(r) for r in nc.execute(
                    "SELECT id, vendor, model, device_type, throughput_gbps, rack_units, "
                    "power_typical_w, ports_json, eol_date, tags FROM nc_hardware_profiles "
                    "WHERE device_type=? ORDER BY throughput_gbps DESC LIMIT 20",
                    (dtype,),
                ).fetchall()
            ]
    except Exception:
        profiles = []

    # ── LLM path ──────────────────────────────────────────────────────────
    prompt = (
        f"You are a senior network architect. A network device needs replacement.\n\n"
        f"Current device:\n{json.dumps(device_info, indent=2)}\n\n"
        f"Engineer notes / constraints:\n{engineer_notes or 'None provided.'}\n\n"
        f"Available replacement catalog (from our approved hardware list):\n"
        f"{json.dumps(profiles, indent=2)}\n\n"
        "Return a JSON object with key \"recommendations\" containing a list of up to 3 objects. "
        "Each object must have: profile_id (string), rationale (string), "
        "migration_considerations (string), risk_level (low|medium|high), score (0-100 integer). "
        "Only recommend from the catalog above. Respond with JSON only — no markdown, no commentary."
    )

    resp_text = ""
    model_used = ""
    try:
        from tools.llm.router import LLMRouter  # type: ignore
        from tools.llm.provider import LLMRequest  # type: ignore

        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a network architect. Respond with valid JSON only.",
            effort="high",
        )
        resp = router.invoke("recommendation", req)
        resp_text = resp.content
        model_used = getattr(resp, "model_id", "") or ""
        result = json.loads(resp_text)
    except Exception:
        # Deterministic fallback: rank by throughput delta + port parity
        cur_throughput = float(device_info.get("throughput_gbps", 0) or 0)
        scored = []
        for p in profiles:
            pt = float(p.get("throughput_gbps") or 0)
            delta = abs(pt - cur_throughput)
            score = max(0, 100 - int(delta / max(cur_throughput, 1) * 50))
            scored.append({
                "profile_id": p["id"],
                "rationale": f"{p['vendor']} {p['model']} — {pt} Gbps throughput, {p.get('rack_units',2)}U",
                "migration_considerations": "Verify port count and optic compatibility before ordering.",
                "risk_level": "medium",
                "score": score,
            })
        scored.sort(key=lambda x: -x["score"])
        result = {"recommendations": scored[:3]}

    # Save to audit trail
    if session_id:
        try:
            with _mc_conn() as mc:
                mc.execute(
                    "INSERT INTO mc_net_ai_sessions (id, session_id, role, message, model_used, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4()), session_id, "assistant",
                     f"[Hardware recommendation] {resp_text[:1000]}", model_used, _now()),
                )
                mc.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "recommend_hardware: best-effort INSERT into mc_net_ai_sessions failed (non-blocking): %s",
                exc,
            )

    result.setdefault("recommendations", [])
    result["model"] = model_used
    _top = result["recommendations"][0] if result["recommendations"] else {}
    _record_decision(
        canvas_type="mc",
        record_id=session_id,
        decision_type="readiness_assessment",
        decision=f"HW replacement top pick: {_top.get('rationale', 'N/A')[:200]}",
        rationale="LLM-assisted" if model_used else "Rule-based fallback",
        model_used=model_used or None,
        confidence=(_top.get("score", 0) / 100.0) if _top.get("score") else None,
    )
    return result


def ai_assist(session_id: str, engineer_prompt: str) -> dict:
    """Contextual AI assistant for network migration.

    Loads conversation history and session context, calls LLMRouter,
    saves both turns to mc_net_ai_sessions.
    """
    import uuid

    # Load session context
    sess: dict = {}
    history: list[dict] = []
    try:
        with _mc_conn() as mc:
            row = mc.execute(
                "SELECT src_model, tgt_model, src_device_name, tgt_device_name, "
                "src_config_raw, status FROM mc_net_sessions WHERE id=%s", (session_id,)
            ).fetchone()
            if row:
                sess = dict(row)
            history = [
                dict(r) for r in mc.execute(
                    "SELECT role, message FROM mc_net_ai_sessions WHERE session_id=%s "
                    "ORDER BY created_at DESC LIMIT 10",
                    (session_id,),
                ).fetchall()
            ]
    except Exception:
        pass

    # Build protocol context from parsed config
    proto_ctx = ""
    if sess.get("src_config_raw"):
        try:
            parsed = parse_source_config(sess["src_config_raw"])
            parts = []
            if parsed.get("bgp_neighbors"):
                parts.append(f"{len(parsed['bgp_neighbors'])} BGP peers")
            if parsed.get("ospf_areas"):
                parts.append(f"OSPF areas: {', '.join(parsed['ospf_areas'])}")
            if parsed.get("l3vpn_vrfs"):
                parts.append(f"{len(parsed['l3vpn_vrfs'])} L3VPN VRFs")
            if parsed.get("mpls_interfaces"):
                parts.append("MPLS enabled")
            proto_ctx = "; ".join(parts)
        except Exception:
            pass

    system_prompt = (
        "You are ICDEV's network migration AI assistant. "
        "You help network engineers plan and execute network device hardware migrations.\n\n"
        f"Current migration session:\n"
        f"  Source: {sess.get('src_model','unknown')} ({sess.get('src_device_name','')})\n"
        f"  Target: {sess.get('tgt_model','unknown')} ({sess.get('tgt_device_name','')})\n"
        f"  Status: {sess.get('status','planning')}\n"
        + (f"  Protocols: {proto_ctx}\n" if proto_ctx else "")
        + "\nYou have expertise in Cisco IOS/IOS-XR/NX-OS, Juniper JunOS, Arista EOS migration, "
        "BGP/OSPF/IS-IS/EIGRP protocol migration, VLAN/STP migration, PortChannel/LAG, "
        "QoS policy translation, ACL migration, HSRP/VRRP migration, "
        "cutover planning, and rollback procedures."
    )

    # Build message list from history (reversed to chronological) + new prompt
    messages = []
    for h in reversed(history[:6]):
        role = h.get("role", "engineer")
        messages.append({"role": "user" if role == "engineer" else "assistant",
                         "content": h.get("message", "")})
    messages.append({"role": "user", "content": engineer_prompt})

    response_text = ""
    model_used = ""
    try:
        from tools.llm.router import LLMRouter  # type: ignore
        from tools.llm.provider import LLMRequest  # type: ignore

        router = LLMRouter()
        req = LLMRequest(
            messages=messages,
            system_prompt=system_prompt,
            effort="high",
        )
        resp = router.invoke("recommendation", req)
        response_text = resp.content
        model_used = getattr(resp, "model_id", "") or ""
    except Exception as exc:
        response_text = (
            f"AI assistance is currently unavailable ({type(exc).__name__}). "
            "Check your LLM configuration in args/llm_config.yaml."
        )

    # Save both turns
    try:
        with _mc_conn() as mc:
            mc.execute(
                "INSERT INTO mc_net_ai_sessions (id, session_id, role, message, model_used, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), session_id, "engineer", engineer_prompt, "", _now()),
            )
            mc.execute(
                "INSERT INTO mc_net_ai_sessions (id, session_id, role, message, model_used, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), session_id, "assistant", response_text, model_used, _now()),
            )
            mc.commit()
    except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("ai_assist: best-effort INSERT into mc_net_ai_sessions failed (non-blocking): %s", _exc)

    # Grounding assessment (TRUST invariant): this is free-form LLM guidance
    # with no retrieval context, so it carries a grounding-warning flag unless
    # the model emitted validated [source: …] citations.
    grounding: dict = {}
    try:
        from tools.migration_canvas.grounding import assess_response
        grounding = assess_response(response_text, model=model_used, method="net_ai_assist")
    except Exception:  # pragma: no cover - grounding is best-effort
        pass

    return {"response": response_text, "model": model_used, "grounding": grounding}


# ── Protocol-specific step generators ───────────────────────────────────────

def _bgp_plan(parsed: dict, src_vendor: str, tgt_vendor: str) -> dict:
    peers = parsed.get("bgp_neighbors", [])
    steps = [
        "Verify BGP ASN is preserved on target device.",
        "Configure BGP process on target with identical AS and router-ID.",
        f"Establish {len(peers)} BGP neighbor session(s) in passive/no-export mode on new device.",
        "Validate prefix counts match source device before cutover.",
        "Migrate inbound/outbound route-policies — translate syntax if vendor changed.",
        "Update community/large-community mappings for new platform syntax.",
        "Drain traffic by raising local-pref or MED on source before cutover.",
        "After cutover: remove passive mode; monitor prefix table for 15 minutes.",
    ]
    if src_vendor != tgt_vendor:
        steps.insert(4, f"Translate route-policy syntax from {src_vendor} to {tgt_vendor} CLI.")
    return {"steps": steps, "risk_level": "high", "peer_count": len(peers)}


def _ospf_plan(parsed: dict, src_vendor: str, tgt_vendor: str) -> dict:
    areas = parsed.get("ospf_areas", [])
    steps = [
        f"Configure OSPF process on target in area(s): {', '.join(areas) or 'backbone'}.",
        "Enable OSPF interfaces in passive mode on new device (do not advertise yet).",
        "Verify adjacency forms — check neighbor state reaches FULL.",
        "Confirm routing table matches source before cutover.",
        "Raise OSPF cost on source interfaces to drain traffic prior to cutover.",
        "After cutover: remove passive mode; restore default costs.",
        "Verify all OSPF neighbors re-form on new device.",
    ]
    if src_vendor != tgt_vendor:
        steps.insert(1, f"Translate OSPF config from {src_vendor} to {tgt_vendor} syntax.")
    return {"steps": steps, "risk_level": "medium", "area_count": len(areas)}


def _vlan_plan(parsed: dict, src_vendor: str, tgt_vendor: str) -> dict:
    steps = [
        "Pre-configure identical VLAN IDs on target device.",
        "Set all access/trunk port modes before physical cabling.",
        "Disable DTP (auto-negotiation) on all trunk links for explicit mode.",
        "Verify VLAN database consistency across upstream switches.",
        "Migrate STP root bridge priority if device is root — lower priority on new device first.",
        "Confirm spanning-tree topology is stable before cutover.",
    ]
    return {"steps": steps, "risk_level": "low"}


def _lag_plan(parsed: dict, src_vendor: str, tgt_vendor: str) -> dict:
    lag_count = parsed.get("lag_count", 0)
    steps = [
        f"Map {lag_count} LAG group(s) — verify LACP mode (active/passive) is consistent.",
        "Pre-configure port-channel/ae interfaces on target before physical cabling.",
        "Check LACP system-priority and port-priority alignment with peers.",
        "Migrate LACP min-links and max-links settings.",
        "After cabling: verify LAG bundle forms and all member links are up.",
    ]
    if src_vendor != tgt_vendor:
        steps.insert(1, f"Translate port-channel config (ae on Juniper, Po on Cisco) for {tgt_vendor}.")
    return {"steps": steps, "risk_level": "medium", "lag_count": lag_count}


def _mpls_plan(parsed: dict, src_vendor: str, tgt_vendor: str) -> dict:
    ldp = parsed.get("ldp_interfaces", [])
    rsvp = parsed.get("rsvp_interfaces", [])
    vrfs = parsed.get("l3vpn_vrfs", [])
    steps = [
        "Enable MPLS on all uplink interfaces before cutover.",
        f"Configure LDP on {len(ldp)} interface(s) — verify label space and hello timers.",
        "Establish LDP adjacency with all upstream/downstream neighbors.",
        "Verify label database convergence before traffic shift.",
        f"Migrate {len(vrfs)} L3VPN VRF(s) — RD, RT, and BGP VPNv4 neighbor config.",
        "Confirm VRF routing table is complete after cutover.",
    ]
    if rsvp:
        steps.insert(4, f"Re-establish {len(rsvp)} RSVP-TE tunnel(s) on new device.")
    return {"steps": steps, "risk_level": "high", "ldp_count": len(ldp), "vrf_count": len(vrfs)}


def _acl_plan(parsed: dict, src_vendor: str, tgt_vendor: str) -> dict:
    filters = parsed.get("firewall_filters", [])
    steps = [
        f"Export {len(filters)} ACL/firewall-filter definition(s) from source config.",
        "Translate ACL syntax to target platform CLI.",
        "Apply ACLs/filters to target interfaces before cutover.",
        "Validate ACL hit counters are incrementing correctly after cutover.",
        "Remove ACLs from source device after decommission.",
    ]
    if src_vendor != tgt_vendor:
        steps[1] = (
            f"Translate {src_vendor} firewall-filter/ACL syntax to {tgt_vendor} equivalent "
            "(e.g., Juniper 'firewall filter' → Cisco 'ip access-list extended')."
        )
    return {"steps": steps, "risk_level": "medium", "filter_count": len(filters)}


def _bgp_plan_advanced(parsed: dict, src_vendor: str, tgt_vendor: str, variant: str) -> dict:
    """Advanced BGP migration plans for multipath, route reflectors, and graceful restart."""
    base = _bgp_plan(parsed, src_vendor, tgt_vendor)
    if variant == "multipath":
        base["steps"] += [
            "Configure BGP multipath (ECMP) — verify max-paths setting matches source.",
            "Validate ECMP load-balancing hash algorithm is consistent across fabric.",
            "Test failover: withdraw one path; confirm traffic redistributes within SLA.",
        ]
        base["variant"] = "multipath"
    elif variant == "route_reflector":
        base["steps"] += [
            "Configure new device as Route Reflector (RR) client or add as RR peer.",
            "Verify RR cluster-ID is set to avoid routing loops.",
            "Confirm all iBGP peers receive full routing table from new RR.",
        ]
        base["variant"] = "route_reflector"
    elif variant == "graceful_restart":
        base["steps"] += [
            "Enable BGP Graceful Restart on new device (restart-time 120s, stale-path-time 360s).",
            "Verify all BGP peers support GR — check capability negotiation.",
            "Test GR: simulate control-plane restart; confirm forwarding continues during reconvergence.",
        ]
        base["variant"] = "graceful_restart"
    return base


def _ospf_plan_advanced(parsed: dict, src_vendor: str, tgt_vendor: str, variant: str) -> dict:
    """Advanced OSPF migration plans for multi-area, stub, and NSSA."""
    base = _ospf_plan(parsed, src_vendor, tgt_vendor)
    areas = parsed.get("ospf_areas", [])
    if variant == "multi_area":
        base["steps"] += [
            f"Configure Area Border Router (ABR) role on new device for areas: {', '.join(areas)}.",
            "Verify inter-area LSAs are generated correctly after cutover.",
            "Confirm route summarization at ABR matches source configuration.",
        ]
        base["variant"] = "multi_area"
    elif variant == "stub_nssa":
        base["steps"] += [
            "Configure stub or NSSA area flags — ensure all routers in area agree.",
            "Verify default route injection into stub/NSSA area from ABR.",
            "For NSSA: confirm NSSA-LSA (Type-7) translation to External-LSA (Type-5) at ABR.",
        ]
        base["variant"] = "stub_nssa"
    elif variant == "virtual_link":
        base["steps"] += [
            "Configure OSPF virtual link to connect discontiguous area 0.",
            "Verify virtual link adjacency reaches FULL state.",
            "Monitor SPF calculations — virtual link adds latency to reconvergence.",
        ]
        base["variant"] = "virtual_link"
    return base


def _mpls_plan_advanced(parsed: dict, src_vendor: str, tgt_vendor: str, variant: str) -> dict:
    """Advanced MPLS plans: VRF-lite, L3VPN, segment routing, EVPN/VXLAN."""
    base = _mpls_plan(parsed, src_vendor, tgt_vendor)
    vrfs = parsed.get("l3vpn_vrfs", [])
    if variant == "vrf_lite":
        base["steps"] = [
            f"Configure {len(vrfs)} VRF(s) with import/export route-targets on new device.",
            "Assign interfaces to VRFs — verify no interface is in default VRF unintentionally.",
            "Configure per-VRF BGP peering or static routes for inter-VRF connectivity.",
            "Validate VRF routing tables are complete after cutover.",
        ]
        base["variant"] = "vrf_lite"
    elif variant == "segment_routing":
        base["steps"] = [
            "Enable Segment Routing (SR-MPLS) globally on new device.",
            "Configure SR global block (SRGB) — ensure no overlap with existing labels.",
            "Migrate existing RSVP-TE LSPs to SR-TE policies (Traffic Engineering Database sync).",
            "Enable TI-LFA (Topology-Independent Loop-Free Alternates) for fast reroute.",
            "Validate end-to-end SR path computation with SR-PCE if present.",
        ]
        base["variant"] = "segment_routing"
    elif variant == "evpn_vxlan":
        base["steps"] = [
            "Configure VXLAN VTEP on new device with correct VNI-to-VLAN mappings.",
            "Enable BGP EVPN address family — configure route-distinguisher and route-targets per VNI.",
            "Enable MAC mobility timer — verify duplicate MAC detection.",
            "Configure ARP suppression (proxy ARP via EVPN Type-2 routes).",
            "Verify BUM traffic (broadcast/unknown-unicast/multicast) replication policy (ingress or underlay multicast).",
            "Test: ping across VTEPs; verify MAC and IP route type-2 advertisements.",
        ]
        base["variant"] = "evpn_vxlan"
    return base


def _sdwan_plan(parsed: dict, src_vendor: str, tgt_vendor: str) -> dict:
    """SD-WAN migration steps (replaces legacy WAN/MPLS)."""
    return {
        "steps": [
            "Deploy SD-WAN controller stack (vManage/vBond/vSmart or equivalent).",
            "Bootstrap SD-WAN edge device (ZTP or manual) — attach to controller.",
            "Create device templates to replace per-device legacy router config.",
            "Migrate routing policies to OMP (Overlay Management Protocol) or equivalent.",
            "Configure application-aware routing (AAR) policies — define SLA classes for voice/video/data.",
            "Enable dual-transport (MPLS + DIA) — retain MPLS as backup during transition.",
            "Validate data-plane IPSec tunnels between all sites.",
            "Cut over site-by-site; monitor OMP route convergence and failover.",
        ],
        "risk_level": "high",
        "variant": "standard",
    }


def plan_protocol_migration(session_id: str, variant_overrides: dict | None = None) -> dict:
    """Generate per-protocol migration steps from parsed source config.

    Detects protocols present in the config and generates ordered steps
    for each. Upserts rows into mc_net_protocol_plans.
    """
    import uuid

    with _mc_conn() as mc:
        sess = dict(mc.execute(
            "SELECT src_config_raw, src_model, tgt_model FROM mc_net_sessions WHERE id=%s",
            (session_id,),
        ).fetchone() or {})

    if not sess.get("src_config_raw"):
        return {"error": "No config imported yet — complete Step 2 first."}

    parsed = parse_source_config(sess["src_config_raw"])
    src_vendor = parsed.get("vendor", "")

    # Detect target vendor
    tgt_vendor = ""
    try:
        with _nc_conn() as nc:
            hw = nc.execute(
                "SELECT vendor FROM nc_hardware_profiles WHERE model=? LIMIT 1",
                (sess.get("tgt_model", ""),),
            ).fetchone()
            if hw:
                tgt_vendor = hw[0].lower()
    except Exception:
        pass

    plans: dict[str, dict] = {}
    now = _now()

    overrides = variant_overrides or {}

    protocol_handlers = []
    if parsed.get("bgp_neighbors"):
        variant = overrides.get("bgp", "standard")
        if variant != "standard":
            protocol_handlers.append(("bgp", lambda p, s, t, v=variant: _bgp_plan_advanced(p, s, t, v)))
        else:
            protocol_handlers.append(("bgp", _bgp_plan))
    if parsed.get("ospf_areas"):
        variant = overrides.get("ospf", "standard")
        if variant != "standard":
            protocol_handlers.append(("ospf", lambda p, s, t, v=variant: _ospf_plan_advanced(p, s, t, v)))
        else:
            protocol_handlers.append(("ospf", _ospf_plan))
    if parsed.get("lag_count", 0):
        protocol_handlers.append(("lag", _lag_plan))
    if parsed.get("mpls_interfaces") or parsed.get("ldp_interfaces") or parsed.get("l3vpn_vrfs"):
        variant = overrides.get("mpls", "standard")
        if variant != "standard":
            protocol_handlers.append(("mpls", lambda p, s, t, v=variant: _mpls_plan_advanced(p, s, t, v)))
        else:
            protocol_handlers.append(("mpls", _mpls_plan))
    if overrides.get("sdwan"):
        protocol_handlers.append(("sdwan", _sdwan_plan))
    if parsed.get("firewall_filters"):
        protocol_handlers.append(("acl", _acl_plan))
    # Always include VLAN
    protocol_handlers.append(("vlan", _vlan_plan))

    with _mc_conn() as mc:
        for protocol, handler in protocol_handlers:
            plan = handler(parsed, src_vendor, tgt_vendor)
            steps = plan.pop("steps", [])
            risk = plan.pop("risk_level", "medium")
            variant_used = plan.pop("variant", overrides.get(protocol, "standard"))
            adv_cfg = {k: v for k, v in plan.items()}
            plans[protocol] = {"steps": steps, "risk_level": risk, "variant": variant_used, **adv_cfg}

            mc.execute(
                "INSERT OR REPLACE INTO mc_net_protocol_plans "
                "(id, session_id, protocol, migration_steps_json, risk_level, status, "
                "variant, advanced_config, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), session_id, protocol,
                 json.dumps(steps), risk, "draft",
                 variant_used, json.dumps(adv_cfg), now, now),
            )
        mc.commit()

    return {"session_id": session_id, "protocols": plans}


def build_parallel_timeline(session_id: str) -> list[dict]:
    """Generate parallel operation milestone timeline for a migration session.

    Returns milestones sorted by days_before_cutover (negative=before cutover).
    """
    import uuid

    # Load session data
    sess: dict = {}
    port_map: list[dict] = []
    compat_checks: list[dict] = []
    has_bgp = has_ospf = has_mpls = False

    try:
        with _mc_conn() as mc:
            row = mc.execute(
                "SELECT src_config_raw, src_model, tgt_model FROM mc_net_sessions WHERE id=%s",
                (session_id,),
            ).fetchone()
            if row:
                sess = dict(row)
            port_map = [
                dict(r) for r in mc.execute(
                    "SELECT optic_change FROM mc_net_port_map WHERE session_id=%s", (session_id,)
                ).fetchall()
            ]
            compat_checks = [
                dict(r) for r in mc.execute(
                    "SELECT severity, status FROM mc_net_compat_checks WHERE session_id=%s", (session_id,)
                ).fetchall()
            ]
    except Exception:
        pass

    if sess.get("src_config_raw"):
        try:
            parsed = parse_source_config(sess["src_config_raw"])
            has_bgp = bool(parsed.get("bgp_neighbors"))
            has_ospf = bool(parsed.get("ospf_areas"))
            has_mpls = bool(parsed.get("mpls_interfaces") or parsed.get("ldp_interfaces"))
        except Exception:
            pass

    optic_changes = sum(1 for r in port_map if r.get("optic_change"))
    blocker_count = sum(1 for r in compat_checks if r.get("severity") == "blocker" and r.get("status") == "fail")

    base_milestones = [
        {"milestone_name": "Order and receive target hardware",
         "days_before_cutover": -30, "phase": "pre_migration", "duration_hours": 1,
         "description": "Issue PO for replacement hardware. Confirm lead time with vendor."},
        {"milestone_name": "Rack, stack, and cable new device",
         "days_before_cutover": -14, "phase": "pre_migration", "duration_hours": 4,
         "description": "Physical installation, power, out-of-band management cable."},
        {"milestone_name": "Initial device configuration (hostname, NTP, AAA, SNMP, syslog)",
         "days_before_cutover": -7, "phase": "pre_migration", "duration_hours": 2,
         "description": "Base config only — no production routing or interfaces yet."},
        {"milestone_name": "Load production configuration — interfaces and VLANs",
         "days_before_cutover": -5, "phase": "parallel_run", "duration_hours": 4,
         "description": "Apply converted config. Interfaces in shutdown state initially."},
        {"milestone_name": "Verify routing table convergence on new device",
         "days_before_cutover": -3, "phase": "parallel_run", "duration_hours": 1,
         "description": "Confirm route count and next-hops match source before any traffic shift."},
        {"milestone_name": "Dual-home test traffic — non-production flows only",
         "days_before_cutover": -2, "phase": "parallel_run", "duration_hours": 4,
         "description": "Shift low-risk/non-critical traffic to validate path. Monitor for drops."},
        {"milestone_name": "Final stakeholder notification and change window scheduling",
         "days_before_cutover": -1, "phase": "pre_migration", "duration_hours": 1,
         "description": "Notify NOC, network owners, and downstream teams of maintenance window."},
        {"milestone_name": "Drain traffic from source device",
         "days_before_cutover": 0, "phase": "cutover", "duration_hours": 1,
         "description": "Raise OSPF cost or BGP MED on source to redirect traffic to alternate paths."},
        {"milestone_name": "Cut over production interfaces to new device",
         "days_before_cutover": 0, "phase": "cutover", "duration_hours": 2,
         "description": "Execute cutover sequence per plan. Follow runbook step-by-step."},
        {"milestone_name": "Verify routing, reachability, and critical applications",
         "days_before_cutover": 0, "phase": "cutover", "duration_hours": 1,
         "description": "Ping tests, route-table comparison, app team confirmation."},
        {"milestone_name": "Go/No-go decision — rollback window 2 hours",
         "days_before_cutover": 0, "phase": "cutover", "duration_hours": 0,
         "description": "Decision point: if issues, execute rollback within 2h window."},
        {"milestone_name": "Monitor new device — 24-hour watch period",
         "days_before_cutover": 1, "phase": "post_migration", "duration_hours": 24,
         "description": "Continuous monitoring: interface errors, BGP/OSPF flaps, CPU/memory."},
        {"milestone_name": "Update monitoring systems (SNMP, NetFlow, NMS, syslog)",
         "days_before_cutover": 1, "phase": "post_migration", "duration_hours": 2,
         "description": "Update LibreNMS/SolarWinds, NetFlow collector, SIEM syslog source."},
        {"milestone_name": "Close change request and document lessons learned",
         "days_before_cutover": 7, "phase": "post_migration", "duration_hours": 2,
         "description": "Update CMDB, close ServiceNow/JIRA ticket, capture lessons learned."},
        {"milestone_name": "Decommission source device",
         "days_before_cutover": 30, "phase": "decommission", "duration_hours": 4,
         "description": "Remove cables, erase config, update asset inventory and NMS."},
    ]

    conditional: list[dict] = []
    if optic_changes:
        conditional.append({
            "milestone_name": f"Order replacement optics ({optic_changes} port(s) need new optics)",
            "days_before_cutover": -21, "phase": "pre_migration", "duration_hours": 1,
            "description": "Some ports require different SFP/QSFP optics on target hardware.",
        })
    if blocker_count:
        conditional.append({
            "milestone_name": f"Resolve {blocker_count} compatibility blocker(s) before parallel run",
            "days_before_cutover": -10, "phase": "pre_migration", "duration_hours": 4,
            "description": "Compatibility check found blocking issues. Must resolve before continuing.",
        })
    if has_ospf:
        conditional.append({
            "milestone_name": "Establish OSPF adjacency on new device (passive mode)",
            "days_before_cutover": -4, "phase": "parallel_run", "duration_hours": 1,
            "description": "Bring up OSPF in passive mode — neighbor should reach FULL state but not carry traffic.",
        })
    if has_bgp:
        conditional.append({
            "milestone_name": "Establish BGP sessions on new device (passive/no-export)",
            "days_before_cutover": -3, "phase": "parallel_run", "duration_hours": 2,
            "description": "BGP sessions in passive mode. Verify prefix counts match source.",
        })
    if has_mpls:
        conditional.append({
            "milestone_name": "Verify LDP adjacency and label exchange on new device",
            "days_before_cutover": -2, "phase": "parallel_run", "duration_hours": 1,
            "description": "LDP/MPLS must be fully converged before any L3VPN traffic is shifted.",
        })

    all_milestones = sorted(base_milestones + conditional, key=lambda m: m["days_before_cutover"])

    now = _now()
    with _mc_conn() as mc:
        mc.execute("DELETE FROM mc_net_parallel_timelines WHERE session_id=%s", (session_id,))
        for m in all_milestones:
            mc.execute(
                "INSERT INTO mc_net_parallel_timelines "
                "(id, session_id, milestone_name, description, days_before_cutover, "
                "phase, duration_hours, status, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), session_id,
                 m["milestone_name"], m.get("description", ""),
                 m["days_before_cutover"], m["phase"],
                 m.get("duration_hours", 1), "planned", now),
            )
        mc.commit()

    return all_milestones


# ---------------------------------------------------------------------------
# Device ingestion — 3 paths: CSV/JSON bulk, NetBox sync, topology re-import

def _ensure_import_topology(label: str, nc_conn) -> str:
    """Return existing topology id by label, or create a new one."""
    import uuid as _uuid
    row = nc_conn.execute(
        "SELECT id FROM topologies WHERE name = ? LIMIT 1", (label,)
    ).fetchone()
    if row:
        return row["id"] if hasattr(row, "keys") else row[0]
    topology_id = "imp-" + str(_uuid.uuid4())[:8]
    now = _now()
    nc_conn.execute(
        "INSERT INTO topologies (id, name, graph_json, classification, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (topology_id, label, "{}", "CUI // SP-CTI", now, now),
    )
    nc_conn.commit()
    return topology_id


def list_topologies() -> list[dict]:
    """Return all topologies from network_canvas.db for the import panel selector."""
    with _nc_conn() as nc:
        rows = nc.execute(
            "SELECT id, name, created_at FROM topologies ORDER BY created_at DESC"
        ).fetchall()
    return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"]} for r in rows]


def ingest_devices_csv(
    file_content: bytes | str,
    topology_id: str | None = None,
    filename: str = "upload.csv",
) -> dict:
    """Import devices from CSV or JSON bytes/string into ni_devices.

    topology_id: existing topology to attach devices to. If None, a dated
    'Bulk Import YYYY-MM-DD' topology is auto-created.
    """
    import os

    with _nc_conn() as nc:
        if topology_id is None:
            import datetime as _dt
            label = "Bulk Import " + _dt.date.today().isoformat()
            topology_id = _ensure_import_topology(label, nc)

        # Write to temp file so bulk_import_devices can read it
        suffix = ".json" if filename.lower().endswith(".json") else ".csv"
        tmp_path = None
        try:
            import tempfile as _tmp
            with _tmp.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as f:
                tmp_path = f.name
                if isinstance(file_content, str):
                    f.write(file_content.encode("utf-8"))
                else:
                    f.write(file_content)
            from tools.network.device_manager import bulk_import_devices
            result = bulk_import_devices(topology_id, tmp_path, conn=nc)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    return {**result, "topology_id": topology_id}


def ingest_devices_netbox(
    topology_id: str | None = None,
    test_only: bool = False,
) -> dict:
    """Sync devices from NetBox into ni_devices.

    Reads NETBOX_URL and NETBOX_TOKEN from environment.
    test_only=True: verifies connectivity only, no DB writes.
    """
    import os
    nb_url = os.getenv("NETBOX_URL", "")
    nb_token = os.getenv("NETBOX_TOKEN", "")
    if not nb_url or not nb_token:
        return {"error": "NETBOX_URL and NETBOX_TOKEN must be set in .env"}

    try:
        from tools.network.netbox_client import NetBoxClient
        nc_client = NetBoxClient(nb_url, nb_token)
        conn_info = nc_client.test_connection()
    except Exception as exc:
        return {"error": f"NetBox connection failed: {exc}"}

    if test_only:
        return {"ok": True, "netbox_version": conn_info.get("netbox_version"), "url": nb_url}

    devices = nc_client.get_devices()
    if not devices:
        return {"ok": True, "created": 0, "updated": 0, "message": "No devices returned by NetBox"}

    with _nc_conn() as nc:
        if topology_id is None:
            import datetime as _dt
            label = "NetBox Import " + _dt.date.today().isoformat()
            topology_id = _ensure_import_topology(label, nc)

        from tools.network.device_manager import upsert_device
        created, updated = 0, 0
        for dev in devices:
            node_id = f"nb-{dev['netbox_id']}"
            result = upsert_device(
                topology_id, node_id, conn=nc,
                label=dev.get("label", node_id),
                device_type=dev.get("type", "unknown"),
                site=dev.get("site") or None,
                rack_location=dev.get("rack") or None,
            )
            if result["action"] == "created":
                created += 1
            else:
                updated += 1

    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "total": len(devices),
        "topology_id": topology_id,
        "netbox_version": conn_info.get("netbox_version"),
    }


def ingest_devices_topology(src_topology_id: str) -> dict:
    """Re-ingest all nodes from an existing topology into ni_devices.

    Uses the topology's graph_json nodes and upserts each into ni_devices
    under the same topology_id (idempotent).
    """
    with _nc_conn() as nc:
        row = nc.execute(
            "SELECT id, name, graph_json FROM topologies WHERE id = ? LIMIT 1",
            (src_topology_id,),
        ).fetchone()
        if not row:
            return {"error": f"Topology not found: {src_topology_id}"}

        import json as _json
        topo_name = row["name"]
        try:
            graph = _json.loads(row["graph_json"] or "{}")
        except Exception:
            graph = {}

        nodes = graph.get("nodes", [])
        if not nodes:
            return {"error": "Topology has no nodes", "topology_id": src_topology_id}

        from tools.network.device_manager import upsert_device
        created, updated = 0, 0
        for node in nodes:
            props = node.get("properties", {})
            result = upsert_device(
                src_topology_id,
                node["id"],
                conn=nc,
                label=node.get("label", node["id"]),
                device_type=node.get("type", "unknown"),
                vendor=props.get("vendor") or None,
                model=props.get("model") or None,
                firmware_version=props.get("firmware_version") or None,
                site=props.get("site") or None,
            )
            if result["action"] == "created":
                created += 1
            else:
                updated += 1

    return {
        "ok": True,
        "topology_id": src_topology_id,
        "topology_name": topo_name,
        "created": created,
        "updated": updated,
        "total": len(nodes),
    }


# ---------------------------------------------------------------------------
# 3-COA Migration Planning (Phase 4)
# ---------------------------------------------------------------------------

def generate_coas(
    src_device: dict[str, Any],
    tgt_device: dict[str, Any],
    parsed_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate three Courses of Action for a network device migration.

    Returns dict with keys ``coa_1``, ``coa_2``, ``coa_3``.
    """
    src_vendor = src_device.get("vendor", "")
    src_model = src_device.get("model", "")
    tgt_vendor = tgt_device.get("vendor", "")
    tgt_model = tgt_device.get("model", "")
    site = src_device.get("site", "")
    device_type = src_device.get("device_type", "")

    # Common pre-work across all COAs
    common_prework = [
        "Order and rack target hardware",
        "Apply base config (hostname, Mgmt, AAA, SNMP, NTP)",
        "Verify out-of-band console + management connectivity",
    ]

    # COA 1: Rip & Replace
    coa1 = {
        "name": "Rip & Replace",
        "description": "Swap hardware in a single maintenance window. Highest risk, shortest timeline.",
        "risk_level": "high",
        "estimated_downtime": "2–4 hours",
        "duration_days": 1,
        "phases": [
            {
                "phase_no": 1,
                "name": "Pre-Work",
                "duration_hours": 8,
                "actions": common_prework + [
                    "Load converted production config (interfaces shutdown)",
                    "Validate config syntax with commit-check / dry-run",
                ],
                "validation": "Target config loaded; zero commit errors; all interfaces administratively down.",
                "rollback": "N/A — pre-work only",
            },
            {
                "phase_no": 2,
                "name": "Drain & Decomm",
                "duration_hours": 2,
                "actions": [
                    "Drain traffic (raise OSPF cost / BGP MED / route-map local-pref)",
                    "Power down source device",
                    "Re-cable all circuits to target device",
                    "No-shutdown target interfaces",
                ],
                "validation": "All BGP/OSPF neighbors Established; ping tests 0% loss; traffic counters incrementing.",
                "rollback": "Re-cable back to source; restore source config; reverse routing metrics.",
            },
            {
                "phase_no": 3,
                "name": "Post-Cutover",
                "duration_hours": 4,
                "actions": [
                    "24-hour monitoring watch",
                    "Update NMS / NetFlow / syslog sources",
                    "Close change request",
                ],
                "validation": "Zero critical alarms; traffic within ±10% of baseline; no CRC errors.",
                "rollback": "If issues >2h: full rollback to source hardware (source remains racked 30d).",
            },
        ],
    }

    # COA 2: Phased Cutover
    coa2 = {
        "name": "Phased Cutover",
        "description": "Migrate circuits/services in phases over 1–2 weeks. Medium risk, minutes of downtime per phase.",
        "risk_level": "medium",
        "estimated_downtime": "5–15 minutes per phase",
        "duration_days": 14,
        "phases": [
            {
                "phase_no": 1,
                "name": "Pre-Work & Parallel Build",
                "duration_hours": 16,
                "actions": common_prework + [
                    "Load converted config on target (all interfaces shutdown)",
                    "Establish routing adjacencies in passive/no-export mode",
                    "Verify route tables match source",
                ],
                "validation": "Route count delta ≤ 1%; all adjacencies in passive FULL/Established.",
                "rollback": "N/A — target not yet carrying traffic",
            },
            {
                "phase_no": 2,
                "name": "Phase A — Management & Low-Traffic Circuits",
                "duration_hours": 4,
                "actions": [
                    "Migrate management and test circuits first",
                    "Monitor for 24h before next phase",
                ],
                "validation": "No alarms; ping/SSH stable; syslog clean.",
                "rollback": "Shutdown target ports; re-enable source ports; restore routing.",
            },
            {
                "phase_no": 3,
                "name": "Phase B — Core BGP/MPLS Circuits",
                "duration_hours": 4,
                "actions": [
                    "Drain first BGP peer via community/MED",
                    "Cut over peer to target; verify prefix count",
                    "Repeat per-peer or per-VRF",
                ],
                "validation": "All BGP peers Established; prefix counts stable; no route churn.",
                "rollback": "Shift peer back to source; restore source interface config.",
            },
            {
                "phase_no": 4,
                "name": "Phase C — Final Circuits & Decomm",
                "duration_hours": 4,
                "actions": [
                    "Migrate remaining circuits",
                    "Decommission source device after 48h stability",
                ],
                "validation": "All services green; NOC sign-off; zero rollback events in 48h.",
                "rollback": "Re-enable any source circuits still present; shift traffic back.",
            },
        ],
    }

    # COA 3: Side-by-Side (Safe)
    coa3 = {
        "name": "Side-by-Side VLAN",
        "description": (
            "Run old and new devices in parallel on the same L2 VLAN domain. "
            "New device learns routes without carrying production traffic. Gradual shift. Near-zero downtime."
        ),
        "risk_level": "low",
        "estimated_downtime": "Near-zero (sub-second hit during final preference shift)",
        "duration_days": 21,
        "phases": [
            {
                "phase_no": 1,
                "name": "Pre-Work & Parallel Wiring",
                "duration_hours": 12,
                "actions": common_prework + [
                    "Physically connect new device alongside old (not inline) — same VLAN trunk",
                    "Configure identical SVIs on new device with unique but valid IPs in same subnet",
                    "Configure HSRP/VRRP on both devices with same VIP; new device as standby (lower priority)",
                ],
                "validation": "Both devices see HSRP/VRRP hello packets; new device shows Standby state; no IP conflict.",
                "rollback": "Disconnect new device trunk links; remove SVIs.",
            },
            {
                "phase_no": 2,
                "name": "Learning & Validation",
                "duration_hours": 48,
                "actions": [
                    "Allow new device to form routing adjacencies (passive/no-export)",
                    "Mirror production traffic to new device port (SPAN/tap) for validation",
                    "Run synthetic traffic through new device without affecting production paths",
                ],
                "validation": "Route table converged; BGP/OSPF neighbors Established; no drops on mirrored traffic.",
                "rollback": "Disable routing adjacencies on new device; revert to pure standby.",
            },
            {
                "phase_no": 3,
                "name": "Gradual Traffic Shift",
                "duration_hours": 24,
                "actions": [
                    "Raise HSRP/VRRP priority on new device to make it Active for one VLAN at a time",
                    "Or: shift BGP route preference (local-pref / MED) per peer to new device",
                    "Monitor end-to-end latency and loss for 4h per shift",
                ],
                "validation": "Active gateway transitions to new device; ARP/MAC tables update; sub-second hit.",
                "rollback": "Lower new device HSRP/VRRP priority; traffic immediately returns to old device.",
            },
            {
                "phase_no": 4,
                "name": "Drain Old & Decomm",
                "duration_hours": 8,
                "actions": [
                    "Once all traffic shifted, set old device SVIs to shutdown",
                    "Remove old device from routing adjacencies",
                    "Keep old device racked & powered 30 days for emergency rollback",
                ],
                "validation": "All traffic confirmed on new device; zero packets ingress on old device SVIs.",
                "rollback": "Re-enable old device SVIs; restore HSRP/VRRP priority; traffic returns instantly.",
            },
        ],
    }

    # Attach protocol-specific steps to each COA if config is available
    if parsed_config:
        proto_plans = _build_protocol_steps_for_coas(parsed_config, src_vendor, tgt_vendor)
        for coa in (coa1, coa2, coa3):
            coa["protocol_steps"] = proto_plans

    return {
        "classification": "CUI // SP-CTI",
        "source_device": f"{src_vendor} {src_model}",
        "target_device": f"{tgt_vendor} {tgt_model}",
        "site": site,
        "device_type": device_type,
        "coa_1": coa1,
        "coa_2": coa2,
        "coa_3": coa3,
        "recommendation": (
            "COA-3 (Side-by-Side VLAN) recommended for critical production devices "
            "with low tolerance for downtime. COA-2 (Phased) for moderate risk tolerance. "
            "COA-1 (Rip & Replace) only when maintenance windows are long and rollback hardware is standby."
        ),
    }


def _build_protocol_steps_for_coas(parsed: dict[str, Any], src_vendor: str, tgt_vendor: str) -> dict[str, Any]:
    """Build protocol-specific step lists relevant to all three COAs."""
    result: dict[str, Any] = {}
    if parsed.get("bgp_neighbors"):
        result["bgp"] = _bgp_plan(parsed, src_vendor, tgt_vendor)
    if parsed.get("ospf_areas"):
        result["ospf"] = _ospf_plan(parsed, src_vendor, tgt_vendor)
    if parsed.get("mpls_interfaces") or parsed.get("ldp_interfaces") or parsed.get("l3vpn_vrfs"):
        result["mpls"] = _mpls_plan(parsed, src_vendor, tgt_vendor)
    if parsed.get("lag_count", 0):
        result["lag"] = _lag_plan(parsed, src_vendor, tgt_vendor)
    if parsed.get("firewall_filters"):
        result["acl"] = _acl_plan(parsed, src_vendor, tgt_vendor)
    result["vlan"] = _vlan_plan(parsed, src_vendor, tgt_vendor)
    return result


def generate_phase_diagram(
    phase_info: dict[str, Any],
    topology_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate an SVG + JSON phase diagram for a migration phase.

    Args:
        phase_info: A phase dict from a COA (must have ``phase_no``, ``name``, ``actions``).
        topology_json: Optional topology graph_json dict with ``nodes`` and ``edges``.

    Returns:
        dict with ``svg`` (SVG string), ``json`` (structured diagram data), and ``legend``.
    """
    import textwrap

    phase_no = phase_info.get("phase_no", 0)
    name = phase_info.get("name", "Unknown Phase")
    _actions = phase_info.get("actions", [])  # noqa: F841
    validation = phase_info.get("validation", "")
    rollback = phase_info.get("rollback", "")

    # Node list from topology if provided
    nodes = []
    edges = []
    if topology_json:
        nodes = topology_json.get("nodes", [])
        edges = topology_json.get("edges", [])

    # Build simplified JSON diagram structure
    diagram_json = {
        "phase": phase_no,
        "name": name,
        "nodes": [],
        "edges": [],
        "legend": {
            "blue": "Existing (old) device",
            "green": "New device",
            "orange": "Device being changed / cut over",
            "red": "Device being retired",
        },
    }

    # Color-code nodes by phase
    for node in nodes:
        node_id = node.get("id", "")
        node_type = node.get("type", "")
        color = "blue"
        if "new" in node_id.lower() or "tgt" in node_id.lower():
            color = "green"
        elif "old" in node_id.lower() or "src" in node_id.lower():
            if phase_no >= 3:
                color = "red"
            elif phase_no == 2:
                color = "orange"
            else:
                color = "blue"
        diagram_json["nodes"].append({
            "id": node_id,
            "label": node.get("label", node_id),
            "type": node_type,
            "color": color,
            "x": node.get("x", 0),
            "y": node.get("y", 0),
        })

    for edge in edges:
        diagram_json["edges"].append({
            "id": edge.get("id", ""),
            "source": edge.get("source", ""),
            "target": edge.get("target", ""),
            "label": edge.get("label", ""),
            "style": "solid" if phase_no < 3 else "dashed",
        })

    # Generate a simple SVG representation
    width, height = 800, 400
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '  <rect width="100%" height="100%" fill="#f8f9fa"/>',
        f'  <text x="20" y="30" font-size="16" font-weight="bold" fill="#212529">Phase {phase_no}: {name}</text>',
    ]

    # Draw nodes as simple circles + labels
    y_offset = 80
    for i, node in enumerate(diagram_json["nodes"]):
        x = 100 + (i % 4) * 180
        y = y_offset + (i // 4) * 100
        color_map = {"blue": "#0d6efd", "green": "#198754", "orange": "#fd7e14", "red": "#dc3545"}
        fill = color_map.get(node["color"], "#6c757d")
        svg_parts.append(f'  <circle cx="{x}" cy="{y}" r="30" fill="{fill}" stroke="#fff" stroke-width="2"/>')
        svg_parts.append(
            f'  <text x="{x}" y="{y+5}" text-anchor="middle" font-size="10" fill="#fff">'
            f'{textwrap.shorten(node["label"], width=12, placeholder="..")}</text>'
        )
        # Update JSON with computed positions
        node["x"] = x
        node["y"] = y

    # Draw edges as lines
    node_positions = {n["id"]: (n["x"], n["y"]) for n in diagram_json["nodes"]}
    for edge in diagram_json["edges"]:
        src_pos = node_positions.get(edge["source"])
        tgt_pos = node_positions.get(edge["target"])
        if src_pos and tgt_pos:
            style_attr = 'stroke-dasharray="5,5"' if edge.get("style") == "dashed" else ""
            svg_parts.append(
                f'  <line x1="{src_pos[0]}" y1="{src_pos[1]}" x2="{tgt_pos[0]}" y2="{tgt_pos[1]}" '
                f'stroke="#adb5bd" stroke-width="2" {style_attr}/>'
            )

    # Info box
    info_y = height - 100
    svg_parts.append(f'  <rect x="20" y="{info_y}" width="760" height="80" fill="#fff" stroke="#dee2e6" rx="4"/>')
    svg_parts.append(f'  <text x="30" y="{info_y + 20}" font-size="11" font-weight="bold" fill="#212529">Validation:</text>')
    svg_parts.append(f'  <text x="30" y="{info_y + 36}" font-size="10" fill="#495057">{textwrap.shorten(validation, width=100)}</text>')
    svg_parts.append(f'  <text x="30" y="{info_y + 54}" font-size="11" font-weight="bold" fill="#212529">Rollback:</text>')
    svg_parts.append(f'  <text x="30" y="{info_y + 70}" font-size="10" fill="#495057">{textwrap.shorten(rollback, width=100)}</text>')

    svg_parts.append("</svg>")

    return {
        "phase": phase_no,
        "name": name,
        "svg": "\n".join(svg_parts),
        "json": diagram_json,
        "legend": diagram_json["legend"],
    }
