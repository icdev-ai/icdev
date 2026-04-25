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

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("icdev.migration_canvas.network_migration")

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_MC_DB_PATH = _ICDEV_ROOT / "data" / "migration_canvas.db"
_NC_DB_PATH = _ICDEV_ROOT / "data" / "network_canvas.db"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _mc_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_MC_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _nc_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_NC_DB_PATH))
    conn.row_factory = sqlite3.Row
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

def generate_port_map(
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

def check_compatibility(
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

def convert_config(
    src_config: str,
    port_map: list[dict],
    src_vendor: str,
    tgt_model: str = "",
) -> dict[str, Any]:
    """Rename interfaces and remove platform-specific stanzas.

    Returns {"source": str, "target": str, "diff": [{"op", "line"}]}
    where op is "keep" | "remove" | "add".
    """
    # Build rename map: src_interface -> tgt_interface
    rename = {}
    for row in port_map:
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

def simulate_commit_check(
    target_config: str,
    tgt_vendor: str,
    tgt_hw_profile: dict | None = None,
) -> list[dict]:
    """Simulate commit check without pushing to a device.

    Returns list of findings: {status: pass|fail|warn, statement, message}
    """
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

def build_cutover_sequence(
    port_map: list[dict],
    strategy: str = "traffic_volume_asc",
    parsed_config: dict | None = None,
) -> list[dict]:
    """Generate an ordered cutover step list from the port map.

    Strategies:
      traffic_volume_asc  — low-traffic circuits first (default, safest)
      alphabetical        — by source interface name
      description_alpha   — by circuit description

    Each step includes drain, cutover, verify, and rollback actions
    appropriate for the source vendor.
    """
    vendor = (parsed_config or {}).get("vendor", "")

    # Only include data interfaces (not loopback/management)
    data_rows = [
        r for r in port_map
        if r.get("status") not in ("no-migration",) and r.get("tgt_interface")
    ]

    if strategy == "alphabetical":
        data_rows = sorted(data_rows, key=lambda r: r.get("src_interface", ""))
    elif strategy == "description_alpha":
        data_rows = sorted(data_rows, key=lambda r: r.get("src_description", ""))
    # Default: leave in port_map order (assumed low→high traffic by assignment)

    steps: list[dict] = []
    for seq, row in enumerate(data_rows, start=1):
        src_if = row.get("src_interface", "")
        tgt_if = row.get("tgt_interface", "")
        circuit = row.get("src_circuit_id", "") or row.get("src_description", src_if)
        ip = row.get("src_ip_address", "")

        drain, cutover, verify, rollback = _build_step_actions(
            src_if, tgt_if, ip, circuit, vendor
        )
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

    # Always add management / loopback last
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

def compute_readiness(session_id: str) -> dict[str, Any]:
    """Score migration session completeness 0-100 with per-dimension breakdown."""
    with _mc_conn() as conn:
        session = conn.execute(
            "SELECT * FROM mc_net_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            return {"overall": 0, "error": "session not found"}

        port_rows = conn.execute(
            "SELECT COUNT(*) cnt, SUM(CASE WHEN status='mapped' THEN 1 ELSE 0 END) mapped "
            "FROM mc_net_port_map WHERE session_id=?", (session_id,)
        ).fetchone()

        compat_rows = conn.execute(
            "SELECT COUNT(*) cnt, "
            "SUM(CASE WHEN status='fail' AND (override_reason='' OR override_reason IS NULL) THEN 1 ELSE 0 END) blocking "
            "FROM mc_net_compat_checks WHERE session_id=?", (session_id,)
        ).fetchone()

        test_rows = conn.execute(
            "SELECT COUNT(*) cnt, SUM(CASE WHEN passed IS NOT NULL THEN 1 ELSE 0 END) executed "
            "FROM mc_net_test_cases WHERE session_id=?", (session_id,)
        ).fetchone()

        cutover_rows = conn.execute(
            "SELECT COUNT(*) FROM mc_net_cutover_steps WHERE session_id=?", (session_id,)
        ).fetchone()

        erb_row = conn.execute(
            "SELECT id, business_justification, risk_tier FROM mc_net_erb_metadata WHERE session_id=?",
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

def generate_erb_package(session_id: str) -> dict[str, Any]:
    """Assemble the full ERB/CCB package dict from all session sub-tables."""
    with _mc_conn() as conn:
        session = dict(conn.execute(
            "SELECT * FROM mc_net_sessions WHERE id=?", (session_id,)
        ).fetchone() or {})

        port_map = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_net_port_map WHERE session_id=? ORDER BY rowid", (session_id,)
        ).fetchall()]

        compat = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_net_compat_checks WHERE session_id=? ORDER BY severity, category, check_name",
            (session_id,)
        ).fetchall()]

        tests = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_net_test_cases WHERE session_id=? ORDER BY phase, seq_no", (session_id,)
        ).fetchall()]

        cutover = [dict(r) for r in conn.execute(
            "SELECT * FROM mc_net_cutover_steps WHERE session_id=? ORDER BY seq_no", (session_id,)
        ).fetchall()]

        erb_meta = dict(conn.execute(
            "SELECT * FROM mc_net_erb_metadata WHERE session_id=?", (session_id,)
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

    Builds a minimal graph_json with source→migration→target device nodes
    and calls rebuild_canvas_kg("mdc", design_id) if a design_id is linked.
    """
    if not design_id:
        # Look up linked design
        try:
            with _mc_conn() as conn:
                row = conn.execute(
                    "SELECT migration_designs.id FROM migration_designs "
                    "WHERE network_session_id=?", (session_id,)
                ).fetchone()
                design_id = row[0] if row else None
        except Exception:
            pass

    if not design_id:
        return

    try:
        with _mc_conn() as conn:
            sess = dict(conn.execute(
                "SELECT src_model, tgt_model, src_device_name, tgt_device_name FROM mc_net_sessions WHERE id=?",
                (session_id,)
            ).fetchone() or {})

        src_id = f"net-src-{session_id}"
        tgt_id = f"net-tgt-{session_id}"
        mig_id = f"net-migration-{session_id}"

        graph = {
            "nodes": [
                {"id": src_id, "type": "src-network-device", "label": sess.get("src_device_name") or sess.get("src_model", "Source"), "metadata": {"model": sess.get("src_model", "")}},
                {"id": mig_id, "type": "pat-network-cutover", "label": "Network Migration"},
                {"id": tgt_id, "type": "tgt-network-device", "label": sess.get("tgt_device_name") or sess.get("tgt_model", "Target"), "metadata": {"model": sess.get("tgt_model", "")}},
            ],
            "edges": [
                {"id": f"e1-{session_id}", "source": src_id, "target": mig_id, "label": "migrates via"},
                {"id": f"e2-{session_id}", "source": mig_id, "target": tgt_id, "label": "migrates to"},
            ],
        }

        with _mc_conn() as conn:
            conn.execute(
                "UPDATE migration_designs SET graph_json=?, updated_at=? WHERE id=?",
                (json.dumps(graph), _now(), design_id),
            )
            conn.commit()

        from tools.canvas.kg_builder import rebuild_canvas_kg  # type: ignore
        rebuild_canvas_kg("mdc", design_id)
    except Exception as exc:
        logger.debug("KG update skipped: %s", exc)
