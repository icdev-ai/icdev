# CUI // SP-CTI
"""ICDEV™ Network Canvas — Device Configuration Parser.

Dual-mode parsing:
  1. **Generic text** — section-based splitting for any vendor. Feeds RAG.
  2. **Vendor-specific** — regex extraction of interfaces, ACLs, routes,
     BGP neighbors. Feeds KG nodes.

Supported vendors: Cisco IOS, Cisco NX-OS, Juniper JunOS.
Unknown vendors get generic parsing only.

Air-gap safe: stdlib ``re`` only (no TTP, TextFSM, or external deps).

Usage::

    from tools.network.config_parser import ingest_config, detect_vendor

    vendor = detect_vendor(raw_text)
    result = ingest_config(raw_text, device_ip="10.0.0.1", hostname="R1")
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import re
import uuid
from tools.network.db.init_db import get_connection
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.network.config_parser")


def _sqlite_connection(db_path: str):
    """Open a StorageConnection-wrapped SQLite DB at an explicit path.

    Used only when a caller (e.g. a test) passes an explicit ``db_path``
    override; the default path routes through the canvas ``get_connection()``
    which honours NC_STORAGE_BACKEND (PostgreSQL by default).
    """
    import sqlite3 as _sqlite3

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(path))
    conn.row_factory = _sqlite3.Row
    try:
        from tools.db.storage import StorageConnection

        return StorageConnection(conn, "sqlite")
    except ImportError:
        return conn


# ---------------------------------------------------------------------------
# Vendor detection
# ---------------------------------------------------------------------------

_VENDOR_SIGNATURES = [
    # (pattern, vendor_key)
    (re.compile(r"^!\s*$", re.MULTILINE), "_cisco_candidate"),
    (re.compile(r"\bfeature\s+(vpc|nv overlay|lacp)", re.IGNORECASE), "cisco_nxos"),
    (re.compile(r"\bversion\s+\d+\.\d+\(", re.MULTILINE), "cisco_nxos"),
    (re.compile(r"^hostname\s+\S+", re.MULTILINE), "_cisco_candidate"),
    (re.compile(r"^interface\s+(GigabitEthernet|FastEthernet|Serial|Loopback|Tunnel)", re.MULTILINE), "cisco_ios"),
    (re.compile(r"^interface\s+(Ethernet|port-channel|mgmt|Vlan)\d", re.MULTILINE), "cisco_nxos"),
    (re.compile(r"^set\s+(interfaces|routing-options|security|protocols)\s+", re.MULTILINE), "juniper"),
    (re.compile(r"^(system|interfaces|protocols|security)\s*\{", re.MULTILINE), "juniper"),
    (re.compile(r"## Last commit:", re.MULTILINE), "juniper"),
]


def detect_vendor(raw_text: str) -> str:
    """Heuristic vendor detection from config text.

    Returns:
        'cisco_ios' | 'cisco_nxos' | 'juniper' | 'generic'
    """
    hits = {}  # type: Dict[str, int]
    for pattern, vendor in _VENDOR_SIGNATURES:
        if pattern.search(raw_text):
            hits[vendor] = hits.get(vendor, 0) + 1

    # Resolve cisco candidate
    if "cisco_nxos" in hits:
        return "cisco_nxos"
    if "cisco_ios" in hits:
        return "cisco_ios"
    if "_cisco_candidate" in hits and hits["_cisco_candidate"] >= 2:
        return "cisco_ios"
    if "juniper" in hits:
        return "juniper"
    return "generic"


# ---------------------------------------------------------------------------
# Cisco IOS parser
# ---------------------------------------------------------------------------

_RE_IOS_HOSTNAME = re.compile(r"^hostname\s+(\S+)", re.MULTILINE)
_RE_IOS_INTERFACE = re.compile(
    r"^interface\s+(\S+)\n((?:[ \t]+.*\n)*)",
    re.MULTILINE,
)
_RE_IOS_IP_ADDR = re.compile(r"ip address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)")
_RE_IOS_SHUTDOWN = re.compile(r"^\s*shutdown\s*$", re.MULTILINE)
_RE_IOS_DESCRIPTION = re.compile(r"description\s+(.+)")
_RE_IOS_ACL = re.compile(
    r"^(?:ip\s+)?access-list\s+(?:extended|standard)?\s*(\S+)\n((?:[ \t]+.*\n)*)",
    re.MULTILINE,
)
_RE_IOS_ROUTE = re.compile(
    r"^ip route\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)",
    re.MULTILINE,
)
_RE_IOS_BGP_NEIGHBOR = re.compile(
    r"neighbor\s+(\d+\.\d+\.\d+\.\d+)\s+remote-as\s+(\d+)",
    re.MULTILINE,
)


def parse_cisco_ios(raw_text: str) -> Dict[str, Any]:
    """Parse Cisco IOS running-config.

    Returns:
        {hostname, interfaces: [...], acls: [...], routes: [...], bgp_neighbors: [...]}
    """
    hostname_m = _RE_IOS_HOSTNAME.search(raw_text)
    hostname = hostname_m.group(1) if hostname_m else ""

    interfaces = []  # type: List[Dict[str, Any]]
    for m in _RE_IOS_INTERFACE.finditer(raw_text):
        name = m.group(1)
        body = m.group(2)
        ip_m = _RE_IOS_IP_ADDR.search(body)
        desc_m = _RE_IOS_DESCRIPTION.search(body)
        interfaces.append({
            "name": name,
            "ip": ip_m.group(1) if ip_m else "",
            "mask": ip_m.group(2) if ip_m else "",
            "shutdown": bool(_RE_IOS_SHUTDOWN.search(body)),
            "description": desc_m.group(1).strip() if desc_m else "",
        })

    acls = []  # type: List[Dict[str, Any]]
    for m in _RE_IOS_ACL.finditer(raw_text):
        acls.append({
            "name": m.group(1),
            "entries": [line.strip() for line in m.group(2).splitlines() if line.strip()],
        })

    routes = []  # type: List[Dict[str, Any]]
    for m in _RE_IOS_ROUTE.finditer(raw_text):
        routes.append({
            "network": m.group(1),
            "mask": m.group(2),
            "next_hop": m.group(3),
        })

    bgp_neighbors = []  # type: List[Dict[str, Any]]
    for m in _RE_IOS_BGP_NEIGHBOR.finditer(raw_text):
        bgp_neighbors.append({"ip": m.group(1), "asn": int(m.group(2))})

    return {
        "hostname": hostname,
        "vendor": "cisco_ios",
        "interfaces": interfaces,
        "acls": acls,
        "routes": routes,
        "bgp_neighbors": bgp_neighbors,
    }


# ---------------------------------------------------------------------------
# Cisco NX-OS parser
# ---------------------------------------------------------------------------

_RE_NXOS_VRF = re.compile(r"^vrf context\s+(\S+)", re.MULTILINE)
_RE_NXOS_VPC = re.compile(r"^vpc\s+(\d+)", re.MULTILINE)


def parse_cisco_nxos(raw_text: str) -> Dict[str, Any]:
    """Parse Cisco NX-OS config. Extends IOS patterns for VRF/VPC."""
    base = parse_cisco_ios(raw_text)
    base["vendor"] = "cisco_nxos"

    vrfs = [m.group(1) for m in _RE_NXOS_VRF.finditer(raw_text)]
    vpcs = [int(m.group(1)) for m in _RE_NXOS_VPC.finditer(raw_text)]
    base["vrfs"] = vrfs
    base["vpcs"] = vpcs
    return base


# ---------------------------------------------------------------------------
# Juniper parser
# ---------------------------------------------------------------------------

_RE_JUNIPER_HOSTNAME = re.compile(r"(?:set system host-name|host-name)\s+(\S+)")
_RE_JUNIPER_SET_IFACE = re.compile(
    r"^set interfaces\s+(\S+)\s+unit\s+(\d+)\s+family inet address\s+(\S+)",
    re.MULTILINE,
)
_RE_JUNIPER_SET_ROUTE = re.compile(
    r"^set routing-options static route\s+(\S+)\s+next-hop\s+(\S+)",
    re.MULTILINE,
)
_RE_JUNIPER_SET_POLICY = re.compile(
    r"^set security policies from-zone\s+(\S+)\s+to-zone\s+(\S+)\s+policy\s+(\S+)",
    re.MULTILINE,
)
_RE_JUNIPER_SET_BGP = re.compile(
    r"^set protocols bgp group\s+\S+\s+neighbor\s+(\S+)\s+peer-as\s+(\d+)",
    re.MULTILINE,
)
# Hierarchy format
_RE_JUNIPER_HIER_IFACE = re.compile(
    r"(\S+)\s*\{\s*unit\s+(\d+)\s*\{[^}]*family inet\s*\{[^}]*address\s+(\S+)",
    re.DOTALL,
)

# ── Firewall filters (Junos' ACL equivalent) ────────────────────────────────
# Junos expresses packet filtering as `firewall { family inet { filter NAME {
# term T { from {...} then ...; } } } }`, or flattened as
# `set firewall family inet filter NAME term T ...`. Neither resembles an IOS
# `ip access-list` block, which is why acls was previously always empty here.
# Both forms normalise to the IOS acls shape — {"name", "entries": [str]} —
# with one entry per *term*, a term being Junos' unit of match+action (the
# closest analogue to an IOS ACE).
_RE_JUNIPER_SET_FILTER_TERM = re.compile(
    r"^set firewall\s+(?:family\s+\S+\s+)?filter\s+(\S+)\s+term\s+(\S+)\s+(.+?)\s*$",
    re.MULTILINE,
)
_RE_JUNIPER_HIER_FIREWALL = re.compile(r"^[ \t]*firewall\s*\{", re.MULTILINE)
_RE_JUNIPER_HIER_FILTER = re.compile(r"\bfilter\s+(\S+)\s*\{")
_RE_JUNIPER_HIER_TERM = re.compile(r"\bterm\s+(\S+)\s*\{")


def _brace_block(text: str, open_idx: int) -> str:
    """Return the inner text of the {...} block whose opening brace is at open_idx.

    Junos hierarchy nests arbitrarily (filter > term > from), which a regex
    cannot match. Counts depth instead. Unbalanced input returns the remainder
    rather than raising — a truncated config should degrade, not crash a scan.
    """
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    return text[open_idx + 1:]


def _juniper_set_filters(raw_text: str) -> List[Dict[str, Any]]:
    """Firewall filters from `set`-style config, grouped one entry per term."""
    fragments: Dict[tuple, List[str]] = {}
    term_order: List[tuple] = []
    for m in _RE_JUNIPER_SET_FILTER_TERM.finditer(raw_text):
        key = (m.group(1), m.group(2))
        if key not in fragments:
            fragments[key] = []
            term_order.append(key)
        fragments[key].append(m.group(3).strip())

    acls: Dict[str, List[str]] = {}
    acl_order: List[str] = []
    for filter_name, term_name in term_order:
        if filter_name not in acls:
            acls[filter_name] = []
            acl_order.append(filter_name)
        acls[filter_name].append(
            "term %s %s" % (term_name, "; ".join(fragments[(filter_name, term_name)]))
        )
    return [{"name": name, "entries": acls[name]} for name in acl_order]


def _juniper_hier_filters(raw_text: str) -> List[Dict[str, Any]]:
    """Firewall filters from hierarchy-style config.

    Scoped to inside the `firewall { ... }` block so an interface's
    `family inet { filter { input X; } }` (a filter *application*, not a
    definition) is never mistaken for a filter definition.
    """
    acls: List[Dict[str, Any]] = []
    for fw in _RE_JUNIPER_HIER_FIREWALL.finditer(raw_text):
        fw_block = _brace_block(raw_text, fw.end() - 1)
        for fm in _RE_JUNIPER_HIER_FILTER.finditer(fw_block):
            filter_block = _brace_block(fw_block, fm.end() - 1)
            entries = [
                "term %s %s" % (tm.group(1), " ".join(_brace_block(filter_block, tm.end() - 1).split()))
                for tm in _RE_JUNIPER_HIER_TERM.finditer(filter_block)
            ]
            if entries:
                acls.append({"name": fm.group(1), "entries": entries})
    return acls


def parse_juniper(raw_text: str) -> Dict[str, Any]:
    """Parse Juniper JunOS config (set-style or hierarchy)."""
    hostname_m = _RE_JUNIPER_HOSTNAME.search(raw_text)
    hostname = hostname_m.group(1) if hostname_m else ""

    interfaces = []  # type: List[Dict[str, Any]]
    for m in _RE_JUNIPER_SET_IFACE.finditer(raw_text):
        interfaces.append({
            "name": "%s.%s" % (m.group(1), m.group(2)),
            "ip": m.group(3),
            "mask": "",
            "shutdown": False,
            "description": "",
        })
    # Also try hierarchy format
    for m in _RE_JUNIPER_HIER_IFACE.finditer(raw_text):
        iface_name = "%s.%s" % (m.group(1), m.group(2))
        if not any(i["name"] == iface_name for i in interfaces):
            interfaces.append({
                "name": iface_name,
                "ip": m.group(3),
                "mask": "",
                "shutdown": False,
                "description": "",
            })

    routes = []  # type: List[Dict[str, Any]]
    for m in _RE_JUNIPER_SET_ROUTE.finditer(raw_text):
        routes.append({"network": m.group(1), "mask": "", "next_hop": m.group(2)})

    policies = []  # type: List[Dict[str, Any]]
    for m in _RE_JUNIPER_SET_POLICY.finditer(raw_text):
        policies.append({
            "from_zone": m.group(1),
            "to_zone": m.group(2),
            "name": m.group(3),
        })

    bgp_neighbors = []  # type: List[Dict[str, Any]]
    for m in _RE_JUNIPER_SET_BGP.finditer(raw_text):
        bgp_neighbors.append({"ip": m.group(1), "asn": int(m.group(2))})

    # Firewall filters — Junos' ACL equivalent. Hierarchy wins on name collision
    # (a config carrying both forms for one filter is expressing the same intent
    # twice; the hierarchy form is the fuller rendering).
    acls = _juniper_hier_filters(raw_text)
    seen = {a["name"] for a in acls}
    acls.extend(a for a in _juniper_set_filters(raw_text) if a["name"] not in seen)

    return {
        "hostname": hostname,
        "vendor": "juniper",
        "interfaces": interfaces,
        "acls": acls,
        "policies": policies,
        "routes": routes,
        "bgp_neighbors": bgp_neighbors,
    }


# ---------------------------------------------------------------------------
# Generic parser
# ---------------------------------------------------------------------------

_RE_SECTION_HEADER = re.compile(r"^(\S[^\n]{0,80})$", re.MULTILINE)


def parse_config_generic(raw_text: str) -> Dict[str, Any]:
    """Section-based splitting for unknown vendor configs.

    Splits on lines that start at column 0 and groups indented lines
    under each section header. Good enough for RAG chunking.
    """
    sections = []  # type: List[str]
    current = []  # type: List[str]
    for line in raw_text.splitlines():
        if line and not line[0].isspace() and current:
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))

    return {
        "hostname": "",
        "vendor": "generic",
        "sections": sections,
        "interfaces": [],
        "acls": [],
        "routes": [],
        "bgp_neighbors": [],
    }


# ---------------------------------------------------------------------------
# Unified parse dispatcher
# ---------------------------------------------------------------------------

_VENDOR_PARSERS = {
    "cisco_ios": parse_cisco_ios,
    "cisco_nxos": parse_cisco_nxos,
    "juniper": parse_juniper,
    "generic": parse_config_generic,
}


def parse_config(raw_text: str, vendor: Optional[str] = None) -> Dict[str, Any]:
    """Parse a device config with auto-detection or explicit vendor.

    Args:
        raw_text: Raw configuration text.
        vendor: Force vendor ('cisco_ios', 'cisco_nxos', 'juniper', 'generic').
                If None, auto-detects.

    Returns:
        Parsed dict with hostname, vendor, interfaces, acls, routes, etc.
    """
    if vendor is None:
        vendor = detect_vendor(raw_text)
    parser = _VENDOR_PARSERS.get(vendor, parse_config_generic)
    return parser(raw_text)


# ---------------------------------------------------------------------------
# Config ingestion (persist + trigger RAG/KG)
# ---------------------------------------------------------------------------


def ingest_config(
    raw_text: str,
    device_ip: str = "",
    hostname: str = "",
    topology_id: Optional[str] = None,
    project_id: str = "default",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Ingest a device config: detect vendor, parse, persist, return result.

    Stores raw text in ``nc_collected_configs`` (or ``ni_device_configs``).
    Returns parsed structure for downstream RAG/KG processing.

    Args:
        raw_text: Raw configuration text.
        device_ip: Device IP address (for identification).
        hostname: Device hostname (auto-detected if empty).
        topology_id: Link config to a topology (optional).
        project_id: Project context.
        db_path: Override DB path.

    Returns:
        {config_id, vendor, hostname, interfaces_count, acls_count,
         routes_count, bgp_count, parsed: dict}
    """
    parsed = parse_config(raw_text)
    if not hostname:
        hostname = parsed.get("hostname", "")

    config_id = str(uuid.uuid4())[:12]
    config_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    # Persist to ni_device_configs. Default path routes through the canvas
    # get_connection() (honours NC_STORAGE_BACKEND — PostgreSQL by default);
    # an explicit db_path override routes to the SQLite branch for tests.
    try:
        conn = _sqlite_connection(db_path) if db_path else get_connection()
        conn.execute(
            "INSERT INTO ni_device_configs (id, device_id, config_type, "
            "config_text, config_hash, source, version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                config_id,
                hostname or device_ip or config_id,
                "running",
                raw_text,
                config_hash,
                "ingestion_pipeline",
                1,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to persist config: %s", exc)

    return {
        "config_id": config_id,
        "vendor": parsed.get("vendor", "generic"),
        "hostname": hostname,
        "interfaces_count": len(parsed.get("interfaces", [])),
        "acls_count": len(parsed.get("acls", [])),
        "routes_count": len(parsed.get("routes", [])),
        "bgp_count": len(parsed.get("bgp_neighbors", [])),
        "config_hash": config_hash,
        "parsed": parsed,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(
        description="ICDEV Network Config Parser"
    )
    parser.add_argument("file", help="Config file to parse")
    parser.add_argument("--vendor", help="Force vendor (cisco_ios, cisco_nxos, juniper, generic)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--ingest", action="store_true", help="Persist to DB")
    parser.add_argument("--device-ip", default="", help="Device IP")
    parser.add_argument("--hostname", default="", help="Device hostname")
    parser.add_argument("--topology-id", default=None, help="Topology ID")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    if args.ingest:
        result = ingest_config(
            raw,
            device_ip=args.device_ip,
            hostname=args.hostname,
            topology_id=args.topology_id,
        )
    else:
        result = parse_config(raw, vendor=args.vendor)

    if args.json:
        print(_json.dumps(result, indent=2, default=str))
    else:
        vendor = result.get("vendor", "unknown")
        hostname = result.get("hostname", "")
        print("Vendor:     %s" % vendor)
        print("Hostname:   %s" % hostname)
        print("Interfaces: %d" % len(result.get("interfaces", [])))
        print("ACLs:       %d" % len(result.get("acls", [])))
        print("Routes:     %d" % len(result.get("routes", [])))
        print("BGP peers:  %d" % len(result.get("bgp_neighbors", [])))


if __name__ == "__main__":
    main()
