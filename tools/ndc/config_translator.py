#!/usr/bin/env python3
# CUI // SP-CTI
"""NDC Config Translator — vendor syntax translation for migration demos.

Translates running-config snippets from one vendor to another using
heuristic pattern matching. Not a full parser — sufficient for demo
side-by-side diff of common routing/switching constructs.

Supported pairs:
  Cisco IOS/IOS-XR → Arista EOS
  Cisco IOS/IOS-XR → Juniper JunOS
  Juniper JunOS → Arista EOS

Usage:
    python tools/ndc/config_translator.py --device-id dev-edge-wam-pop-ash --target-vendor Arista --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_NC_DB = BASE_DIR / "data" / "network_canvas.db"


# ── Translation rule sets ─────────────────────────────────────────────────────

_RULES_CISCO_TO_ARISTA: List[tuple[str, str]] = [
    (r"interface GigabitEthernet", "interface Ethernet"),
    (r"interface TenGigabitEthernet", "interface Ethernet"),
    (r"interface FortyGigabitEthernet", "interface Ethernet"),
    (r"interface HundredGigabitEthernet", "interface Ethernet"),
    (r"interface Port-channel", "interface Port-Channel"),
    (r"interface Loopback", "interface Loopback"),
    (r"interface Vlan", "interface Vlan"),
    (r"interface Tunnel", "interface Tunnel"),
    (r"ip route-cache", "ip hardware-optimized"),
    (r"no ip proxy-arp", "no ip proxy-arp"),
    (r" ip helper-address ", " ip helper-address "),
    (r"ntp server", "ntp server"),
    (r"snmp-server community", "snmp-server community"),
    (r"spanning-tree mode rapid-pvst", "spanning-tree mode rapid-pvst"),
    (r"spanning-tree portfast", "spanning-tree portfast"),
    (r"logging ", "logging "),
    (r"aaa ", "aaa "),
    (r"tacacs-server ", "tacacs-server "),
    (r"radius-server ", "radius-server "),
    (r"router bgp ", "router bgp "),
    (r"router ospf ", "router ospf "),
    (r"router isis ", "router isis "),
    (r"mpls ldp ", "mpls ldp "),
    (r"vrf ", "vrf "),
    (r"ip vrf ", "vrf "),
    (r"ip prefix-list ", "ip prefix-list "),
    (r"route-map ", "route-map "),
    (r"access-list ", "ip access-list "),
    (r"ip access-list ", "ip access-list "),
    (r"banner motd ", "banner motd "),
    (r"hostname ", "hostname "),
    (r"no ip domain-lookup", "no ip domain-lookup"),
]

_RULES_CISCO_TO_JUNIPER: List[tuple[str, str]] = [
    (r"interface GigabitEthernet", "interface ge-"),
    (r"interface TenGigabitEthernet", "interface xe-"),
    (r"interface FortyGigabitEthernet", "interface et-"),
    (r"interface HundredGigabitEthernet", "interface et-"),
    (r"interface Port-channel", "interface ae"),
    (r"interface Loopback", "interface lo"),
    (r"interface Vlan", "interface irb."),
    (r"ip address ", "unit 0 { family inet { address "),
    (r" no shutdown", " } }"),
    (r"router bgp ", "routing-instances { protocols { bgp "),
    (r"router ospf ", "protocols { ospf "),
]

_RULES_JUNIPER_TO_ARISTA: List[tuple[str, str]] = [
    # Interface declarations (JunOS uses "ge-0/0/0 {" without "interface" prefix)
    (r"^\s*ge-(\d+/\d+/\d+)\s*\{", r"interface Ethernet\1 {"),
    (r"^\s*xe-(\d+/\d+/\d+)\s*\{", r"interface Ethernet\1 {"),
    (r"^\s*et-(\d+/\d+/\d+)\s*\{", r"interface Ethernet\1 {"),
    (r"^\s*ae(\d+)\s*\{", r"interface Port-Channel\1 {"),
    (r"^\s*lo(\d+)\s*\{", r"interface Loopback\1 {"),
    (r"^\s*irb\.(\d+)\s*\{", r"interface Vlan\1 {"),
    # Descriptions (strip trailing semicolon)
    (r'\s+description "([^"]+)";', r'  description \1'),
    # Basic protocol flattening hints
    (r"^\s*protocols\s*\{bgp\s*\{", "router bgp {"),
    (r"^\s*protocols\s*\{ospf\s*\{", "router ospf {"),
    (r"^\s*routing-options\s*\{", "! routing-options (flattened)"),
    (r"^\s*policy-options\s*\{", "! policy-options (flattened)"),
    (r"^\s*firewall\s*\{", "! firewall (flattened)"),
    # Braces cleanup
    (r"^\s*}\s*$", "! }"),
]


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


def _detect_vendor(vendor: str) -> str:
    v = vendor.lower()
    if "cisco" in v:
        return "cisco"
    if "juniper" in v or "srx" in v or "mx" in v:
        return "juniper"
    if "arista" in v:
        return "arista"
    return "cisco"


def translate_config(config_text: str, source_vendor: str, target_vendor: str) -> Dict[str, Any]:
    """Translate config text from source vendor to target vendor.

    Returns:
        Dict with translated_lines, notes, confidence score.
    """
    src = _detect_vendor(source_vendor)
    tgt = _detect_vendor(target_vendor)

    if src == tgt:
        return {
            "translated": config_text,
            "lines": [{"old": line, "new": line, "changed": False} for line in config_text.splitlines()],
            "notes": ["Source and target vendor are the same — no translation needed."],
            "confidence": 1.0,
        }

    # Pick rule set
    if src == "cisco" and tgt == "arista":
        rules = _RULES_CISCO_TO_ARISTA
    elif src == "cisco" and tgt == "juniper":
        rules = _RULES_CISCO_TO_JUNIPER
    elif src == "juniper" and tgt == "arista":
        rules = _RULES_JUNIPER_TO_ARISTA
    else:
        return {
            "translated": config_text,
            "lines": [{"old": line, "new": line, "changed": False} for line in config_text.splitlines()],
            "notes": [f"Translation from {src} to {tgt} is not yet implemented — returning original."],
            "confidence": 0.0,
        }

    import re

    lines = config_text.splitlines()
    result_lines = []
    changed_count = 0
    unchanged_count = 0
    notes: List[str] = []

    for line in lines:
        new_line = line
        was_changed = False
        for pattern, replacement in rules:
            if re.search(pattern, line):
                new_line = re.sub(pattern, replacement, line)
                was_changed = True
        if was_changed:
            changed_count += 1
        else:
            unchanged_count += 1
        result_lines.append({"old": line, "new": new_line, "changed": was_changed})

    translated_text = "\n".join(l["new"] for l in result_lines)
    total = len(lines) if lines else 1
    confidence = round(changed_count / total, 2) if total else 0.0

    notes.append(f"Translated {changed_count} of {total} lines ({confidence:.0%} coverage).")
    notes.append("Review all interface numbering, ACL syntax, and routing policy semantics before applying.")

    return {
        "translated": translated_text,
        "lines": result_lines,
        "notes": notes,
        "confidence": confidence,
        "source_vendor": src,
        "target_vendor": tgt,
    }


def generate_config_translation(device_id: str, target_vendor: str | None = None) -> Dict[str, Any]:
    """Full pipeline: fetch device + config, translate, return side-by-side data."""
    conn = _nc_conn()
    try:
        dev = _get_device(conn, device_id)
        if not dev:
            return {"error": f"Device {device_id} not found"}

        src_vendor = dev.get("vendor", "cisco")
        if not target_vendor:
            # Default to Arista for Cisco sources
            target_vendor = "arista" if "cisco" in src_vendor.lower() else "arista"

        config_text = _get_config(conn, device_id)
        if not config_text:
            return {
                "error": f"No configuration found for device {device_id}",
                "device": dev,
            }

        result = translate_config(config_text, src_vendor, target_vendor)
        result["device"] = {
            "id": dev["id"],
            "label": dev.get("label"),
            "vendor": src_vendor,
            "model": dev.get("model"),
            "target_vendor": target_vendor,
        }
        return result
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="NDC Config Translator")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--target-vendor", default="")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    result = generate_config_translation(args.device_id, args.target_vendor or None)
    body = json.dumps(result, indent=2, default=str)
    print(body)


if __name__ == "__main__":
    main()
