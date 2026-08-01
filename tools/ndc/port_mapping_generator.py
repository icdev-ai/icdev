#!/usr/bin/env python3
# CUI // SP-CTI
"""NDC Port Mapping Generator — old device ports → new device ports.

Maps interfaces from a source (old/EOL) device to a target replacement device
based on vendor-specific naming conventions and hardware profile port counts.

Usage:
    python tools/ndc/port_mapping_generator.py --device-id dev-edge-wam-pop-ash --json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_NC_DB = BASE_DIR / "data" / "network_canvas.db"


# ── Vendor interface naming conventions ────────────────────────────────────────

_INTERFACE_PATTERNS = {
    "cisco": {
        "gigabit": re.compile(r"interface GigabitEthernet(\d+/\d+|\d+/\d+/\d+|\d+)"),
        "tengig": re.compile(r"interface TenGigabitEthernet(\d+/\d+|\d+/\d+/\d+|\d+)"),
        "fortygig": re.compile(r"interface FortyGigabitEthernet(\d+/\d+|\d+/\d+/\d+|\d+)"),
        "hundredgig": re.compile(r"interface HundredGigabitEthernet(\d+/\d+|\d+/\d+/\d+|\d+)"),
        "port_channel": re.compile(r"interface Port-channel(\d+)"),
        "loopback": re.compile(r"interface Loopback(\d+)"),
        "vlan": re.compile(r"interface Vlan(\d+)"),
        "tunnel": re.compile(r"interface Tunnel(\d+)"),
    },
    "juniper": {
        "ge": re.compile(r"(?:interface\s+)?ge-(\d+/\d+/\d+)"),
        "xe": re.compile(r"(?:interface\s+)?xe-(\d+/\d+/\d+)"),
        "et": re.compile(r"(?:interface\s+)?et-(\d+/\d+/\d+)"),
        "ae": re.compile(r"(?:interface\s+)?ae(\d+)"),
        "lo": re.compile(r"(?:interface\s+)?lo(\d+)"),
        "irb": re.compile(r"(?:interface\s+)?irb\.(\d+)"),
    },
    "arista": {
        "ethernet": re.compile(r"interface Ethernet(\d+)"),
        "management": re.compile(r"interface Management(\d+)"),
        "port_channel": re.compile(r"interface Port-Channel(\d+)"),
        "loopback": re.compile(r"interface Loopback(\d+)"),
        "vlan": re.compile(r"interface Vlan(\d+)"),
    },
    "paloalto": {
        "ethernet": re.compile(r"interface ethernet(\d+/\d+)"),
        "ae": re.compile(r"interface ae(\d+)"),
        "loopback": re.compile(r"interface loopback(\d+)"),
        "tunnel": re.compile(r"interface tunnel\.(\d+)"),
    },
    "fortinet": {
        "port": re.compile(r"interface port(\d+)"),
        "vlan": re.compile(r"interface vlan(\d+)"),
        "loopback": re.compile(r"interface loopback(\d+)"),
    },
}

# Mapping rules: (old_vendor, old_pattern) → (new_vendor, new_prefix, new_number_transform)
_PORT_MAP_RULES = {
    ("cisco", "gigabit"): ("arista", "Ethernet", lambda x: x.replace("0/", "").replace("/", "")),
    ("cisco", "tengig"): ("arista", "Ethernet", lambda x: x.replace("0/", "").replace("/", "")),
    ("cisco", "port_channel"): ("arista", "Port-Channel", lambda x: x),
    ("cisco", "loopback"): ("arista", "Loopback", lambda x: x),
    ("cisco", "vlan"): ("arista", "Vlan", lambda x: x),
    ("juniper", "ge"): ("arista", "Ethernet", lambda x: x.replace("-", "")),
    ("juniper", "xe"): ("arista", "Ethernet", lambda x: x.replace("-", "")),
    ("juniper", "et"): ("arista", "Ethernet", lambda x: x.replace("-", "")),
    ("juniper", "ae"): ("arista", "Port-Channel", lambda x: x),
    ("juniper", "lo"): ("arista", "Loopback", lambda x: x),
    ("juniper", "irb"): ("arista", "Vlan", lambda x: x),
}


def _nc_conn():
    # PG-primary via the Network Canvas helper (NC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.network.db.init_db import get_connection

    return get_connection()


def _get_device(conn: sqlite3.Connection, device_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT id, vendor, model, device_type, site, label FROM ni_devices WHERE id=%s",
        (device_id,),
    ).fetchone()
    return dict(row) if row else None


def _get_config(conn: sqlite3.Connection, device_id: str) -> str:
    row = conn.execute(
        """SELECT config_text FROM ni_device_configs
           WHERE device_id=%s ORDER BY created_at DESC LIMIT 1""",
        (device_id,),
    ).fetchone()
    return row["config_text"] if row and row["config_text"] else ""


def _get_replacement(conn: sqlite3.Connection, device_id: str) -> Optional[Dict[str, Any]]:
    """Fetch the top replacement recommendation."""
    try:
        from tools.ndc.replacement_recommender import recommend_replacement
        rec = recommend_replacement(device_id)
        recs = rec.get("recommendations", [])
        return recs[0] if recs else None
    except Exception:
        return None


def _detect_vendor(vendor: str) -> str:
    v = vendor.lower()
    if "cisco" in v:
        return "cisco"
    if "juniper" in v or "srx" in v or "mx" in v:
        return "juniper"
    if "arista" in v:
        return "arista"
    if "palo" in v:
        return "paloalto"
    if "fortinet" in v or "fortigate" in v:
        return "fortinet"
    return "cisco"


def _extract_interfaces(config_text: str, vendor_key: str) -> List[Dict[str, str]]:
    """Extract interface names from config text."""
    patterns = _INTERFACE_PATTERNS.get(vendor_key, _INTERFACE_PATTERNS["cisco"])
    found = []
    for iface_type, pat in patterns.items():
        for match in pat.finditer(config_text):
            found.append({
                "raw": match.group(0),
                "type": iface_type,
                "number": match.group(1),
                "name": match.group(0).replace("interface ", ""),
            })
    # Deduplicate by name
    seen = set()
    unique = []
    for f in found:
        if f["name"] not in seen:
            seen.add(f["name"])
            unique.append(f)
    return unique


def _map_interface(iface: Dict[str, str], old_vendor: str, new_vendor: str) -> Dict[str, str]:
    """Map a single old interface to new interface naming."""
    key = (old_vendor, iface["type"])
    rule = _PORT_MAP_RULES.get(key)
    if not rule:
        # Generic fallback: strip vendor prefix, keep number
        return {
            "old_port": iface["name"],
            "new_port": f"{iface['type']}{iface['number']}",
            "service": _guess_service(iface["name"]),
            "status": "manual",
        }
    _, new_prefix, transform = rule
    new_num = transform(iface["number"])
    return {
        "old_port": iface["name"],
        "new_port": f"{new_prefix}{new_num}",
        "service": _guess_service(iface["name"]),
        "status": "mapped",
    }


def _guess_service(iface_name: str) -> str:
    """Heuristic service guess from interface name or common patterns."""
    name = iface_name.lower()
    if "loopback" in name:
        return "Router ID / Mgmt"
    if "vlan" in name:
        return "L3 SVI"
    if "port-channel" in name or "ae" in name:
        return "LAG / Trunk"
    if "tunnel" in name:
        return "VPN / GRE"
    if name.endswith("0") or "/0" in name:
        return "WAN / Uplink"
    if name.endswith("1") or "/1" in name:
        return "Core / Downlink"
    if "mgmt" in name or "management" in name:
        return "OOB Management"
    return "Data / Access"


def generate_port_mapping(device_id: str, new_device_id: str | None = None) -> Dict[str, Any]:
    """Generate a port mapping table for a device replacement.

    Args:
        device_id: The old/EOL device ID.
        new_device_id: Optional explicit new device ID. If None, uses top recommendation.

    Returns:
        Dict with old_device, new_device, port_mappings list, conflicts list.
    """
    conn = _nc_conn()
    try:
        old_dev = _get_device(conn, device_id)
        if not old_dev:
            return {"error": f"Device {device_id} not found"}

        old_vendor = _detect_vendor(old_dev.get("vendor", ""))
        config_text = _get_config(conn, device_id)
        ifaces = _extract_interfaces(config_text, old_vendor)

        # Determine target device
        if new_device_id:
            new_dev = _get_device(conn, new_device_id)
        else:
            rec = _get_replacement(conn, device_id)
            if rec:
                new_dev = {
                    "id": rec.get("recommended_model", "unknown"),
                    "vendor": rec.get("recommended_vendor", "Arista"),
                    "model": rec.get("recommended_model", "Unknown"),
                }
            else:
                new_dev = {"id": "auto-recommended", "vendor": "Arista", "model": "7280R3-48S6"}

        new_vendor = _detect_vendor(new_dev.get("vendor", ""))

        mappings = []
        for iface in ifaces:
            mapping = _map_interface(iface, old_vendor, new_vendor)
            mapping["old_device"] = old_dev.get("label", device_id)
            mapping["new_device"] = new_dev.get("model", "unknown")
            mappings.append(mapping)

        # Identify potential conflicts (same new port mapped twice)
        new_ports = {}
        conflicts = []
        for m in mappings:
            np = m["new_port"]
            if np in new_ports:
                conflicts.append({
                    "new_port": np,
                    "old_ports": [new_ports[np], m["old_port"]],
                    "reason": "Duplicate target port",
                })
            else:
                new_ports[np] = m["old_port"]

        return {
            "classification": "CUI // SP-CTI",
            "old_device": {
                "id": old_dev["id"],
                "label": old_dev.get("label"),
                "vendor": old_dev.get("vendor"),
                "model": old_dev.get("model"),
            },
            "new_device": {
                "id": new_dev.get("id"),
                "vendor": new_dev.get("vendor"),
                "model": new_dev.get("model"),
            },
            "port_mappings": mappings,
            "conflicts": conflicts,
            "unmapped_count": sum(1 for m in mappings if m["status"] == "manual"),
            "mapped_count": sum(1 for m in mappings if m["status"] == "mapped"),
            "total_ports": len(mappings),
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="NDC Port Mapping Generator")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--new-device-id", default="")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    result = generate_port_mapping(args.device_id, args.new_device_id or None)
    body = json.dumps(result, indent=2, default=str)
    print(body)


if __name__ == "__main__":
    main()
