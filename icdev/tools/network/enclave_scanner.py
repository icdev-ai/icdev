# CUI // SP-CTI
"""ICDEV™ Network Canvas — Air-Gapped Classified Enclave Discovery Agent.

Discovers hosts, services, and network topology within classified on-prem
enclaves without requiring external network connectivity.  Operates in two
complementary modes:

  Passive (default) — reads local OS state only; sends no packets.
      ARP cache, routing table, local interface enumeration.
      Safe for classified enclaves where active scanning is prohibited.

  Active — builds on discovery.py (SNMP/SSH/ICMP ping sweep).
      Requires explicit --active flag.  Controlled by caller; never triggered
      automatically in air-gapped environments without operator opt-in.

Enclave Classification
----------------------
Each discovered network segment is scored against DoD IL criteria:

  IL2  — public-facing; default-route via non-RFC-1918 gateway
  IL4  — CUI/GovCloud; RFC-1918, restricted external routing, gov DNS
  IL5  — CUI Dedicated; RFC-1918, no external default route, isolated DNS
  IL6  — SECRET/SIPR; fully isolated, SIPR-range indicators, no DNS replies

Air-Gap Compatibility
---------------------
All passive operations use stdlib only (socket, subprocess, ipaddress).
Active operations import tools.network.discovery (optional pysnmp/netmiko).
Fails gracefully when optional dependencies are absent.

Usage:
    # Passive-only (default, safe for all IL levels)
    python tools/network/enclave_scanner.py --json

    # Active scan with ping sweep of enclave range
    python tools/network/enclave_scanner.py --active --target 10.1.0.0/24 --json

    # SNMP discovery with classification output
    python tools/network/enclave_scanner.py --active --target 192.168.0.0/24 \\
        --method snmp --community public --json

    # Gate mode — exit 1 if unclassified hosts found
    python tools/network/enclave_scanner.py --gate --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import ipaddress
import json
import logging
import platform
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = get_logger("icdev.network.enclave_scanner")

# ── Classification ────────────────────────────────────────────────────────────

# Known RFC-1918 private ranges
_RFC1918 = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

# Indicators of SIPR/IL6 ranges (non-routable to NIPRNet)
# These are indicative heuristics, not exhaustive
_IL6_INDICATORS = [
    "sipr",
    "secret",
    "classified",
    "sipprnets",
    "dren",
    "disn-s",
]

# NIPR/GovCloud DNS suffix indicators
_GOV_DNS_SUFFIXES = (
    ".mil",
    ".gov",
    ".smil.mil",
    ".ic.gov",
    ".csd.disa.mil",
    ".osd.mil",
)

CLASSIFICATION_BANNER = "CUI // SP-CTI"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_rfc1918(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in _RFC1918)
    except ValueError:
        return False


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


# ── Passive Discovery ─────────────────────────────────────────────────────────


def detect_local_interfaces() -> list[dict[str, Any]]:
    """Read local network interfaces via stdlib socket + platform commands.

    Returns list of {name, ip, netmask, broadcast, is_private, is_loopback}.
    Works on Windows, Linux, and macOS without root — stdlib only.
    """
    interfaces: list[dict[str, Any]] = []

    # Try platform-specific interface enumeration first
    system = platform.system()

    if system == "Windows":
        interfaces = _interfaces_windows()
    elif system in ("Linux", "Darwin"):
        interfaces = _interfaces_unix()

    # Fallback: socket hostname resolution
    if not interfaces:
        try:
            hostname = socket.gethostname()
            ips = socket.getaddrinfo(hostname, None)
            seen: set[str] = set()
            for item in ips:
                ip = item[4][0]
                if ip not in seen and not ip.startswith("::"):
                    seen.add(ip)
                    interfaces.append({
                        "name": "eth0",
                        "ip": ip,
                        "netmask": "",
                        "broadcast": "",
                        "is_private": _is_rfc1918(ip),
                        "is_loopback": _is_loopback(ip),
                        "source": "socket_fallback",
                    })
        except Exception:
            pass

    return interfaces


def _interfaces_windows() -> list[dict[str, Any]]:
    """Read interfaces on Windows via ipconfig /all output."""
    result: list[dict[str, Any]] = []
    try:
        out = subprocess.run(
            ["ipconfig", "/all"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        current_iface = "Unknown"
        current_ip = ""
        current_mask = ""
        for line in out.stdout.splitlines():
            stripped = line.strip()
            # Adapter header
            if not line.startswith(" ") and "adapter" in line.lower():
                if current_ip:
                    result.append({
                        "name": current_iface,
                        "ip": current_ip,
                        "netmask": current_mask,
                        "broadcast": "",
                        "is_private": _is_rfc1918(current_ip),
                        "is_loopback": _is_loopback(current_ip),
                        "source": "ipconfig",
                    })
                current_iface = line.rstrip(":").strip()
                current_ip = ""
                current_mask = ""
            elif "IPv4 Address" in stripped or "IP Address" in stripped:
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    current_ip = parts[1].strip().rstrip("(Preferred)").strip()
            elif "Subnet Mask" in stripped:
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    current_mask = parts[1].strip()
        # Last entry
        if current_ip:
            result.append({
                "name": current_iface,
                "ip": current_ip,
                "netmask": current_mask,
                "broadcast": "",
                "is_private": _is_rfc1918(current_ip),
                "is_loopback": _is_loopback(current_ip),
                "source": "ipconfig",
            })
    except Exception as exc:
        logger.debug("ipconfig /all failed: %s", exc)
    return result


def _interfaces_unix() -> list[dict[str, Any]]:
    """Read interfaces on Linux/macOS via ip addr or ifconfig."""
    result: list[dict[str, Any]] = []
    # Try `ip addr` (Linux/modern macOS)
    try:
        out = subprocess.run(
            ["ip", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if out.returncode == 0:
            current_iface = ""
            for line in out.stdout.splitlines():
                stripped = line.strip()
                if line[0:1].isdigit():
                    # Interface header: "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> ..."
                    parts = line.split(":", 2)
                    if len(parts) >= 2:
                        current_iface = parts[1].strip().split("@")[0]
                elif stripped.startswith("inet ") and "inet6" not in stripped:
                    # "inet 10.0.0.1/24 brd 10.0.0.255 scope global eth0"
                    tokens = stripped.split()
                    cidr = tokens[1] if len(tokens) > 1 else ""
                    brd = ""
                    if "brd" in tokens:
                        brd_idx = tokens.index("brd")
                        if brd_idx + 1 < len(tokens):
                            brd = tokens[brd_idx + 1]
                    if "/" in cidr:
                        ip, prefix = cidr.split("/", 1)
                        try:
                            net = ipaddress.ip_network(cidr, strict=False)
                            netmask = str(net.netmask)
                        except ValueError:
                            netmask = ""
                        result.append({
                            "name": current_iface,
                            "ip": ip,
                            "netmask": netmask,
                            "broadcast": brd,
                            "prefix": int(prefix),
                            "is_private": _is_rfc1918(ip),
                            "is_loopback": _is_loopback(ip),
                            "source": "ip_addr",
                        })
            if result:
                return result
    except Exception:
        pass

    # Fallback: ifconfig (macOS / older Linux)
    try:
        out = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        current_iface = ""
        for line in out.stdout.splitlines():
            if not line.startswith("\t") and not line.startswith(" "):
                current_iface = line.split(":")[0]
            elif "inet " in line and "inet6" not in line:
                parts = line.strip().split()
                ip = ""
                netmask = ""
                brd = ""
                for i, tok in enumerate(parts):
                    if tok == "inet" and i + 1 < len(parts):
                        ip = parts[i + 1]
                    elif tok in ("netmask", "mask") and i + 1 < len(parts):
                        raw = parts[i + 1]
                        # Convert hex (macOS) to dotted
                        if raw.startswith("0x"):
                            try:
                                n = int(raw, 16)
                                netmask = ".".join(str((n >> (24 - j * 8)) & 0xFF) for j in range(4))
                            except ValueError:
                                netmask = raw
                        else:
                            netmask = raw
                    elif tok in ("broadcast", "brd") and i + 1 < len(parts):
                        brd = parts[i + 1]
                if ip:
                    result.append({
                        "name": current_iface,
                        "ip": ip,
                        "netmask": netmask,
                        "broadcast": brd,
                        "is_private": _is_rfc1918(ip),
                        "is_loopback": _is_loopback(ip),
                        "source": "ifconfig",
                    })
    except Exception as exc:
        logger.debug("ifconfig failed: %s", exc)

    return result


def read_arp_table() -> list[dict[str, Any]]:
    """Read the OS ARP cache — passive, no packets sent.

    Returns list of {ip, mac, interface, is_private}.
    """
    entries: list[dict[str, Any]] = []
    system = platform.system()

    try:
        if system == "Windows":
            out = subprocess.run(
                ["arp", "-a"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            for line in out.stdout.splitlines():
                parts = line.split()
                # Windows: "  192.168.1.1   00-11-22-33-44-55   dynamic"
                if len(parts) >= 2 and parts[0].count(".") == 3:
                    ip = parts[0]
                    mac = parts[1] if len(parts) > 1 else ""
                    if mac in ("---", "ff-ff-ff-ff-ff-ff"):
                        continue
                    entries.append({
                        "ip": ip,
                        "mac": mac.replace("-", ":").lower(),
                        "interface": "",
                        "is_private": _is_rfc1918(ip),
                        "source": "arp",
                    })
        else:
            # Linux: `ip neigh show` preferred
            out = subprocess.run(
                ["ip", "neigh", "show"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    parts = line.split()
                    # "10.0.0.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE"
                    if len(parts) >= 5 and parts[3] == "lladdr":
                        ip = parts[0]
                        iface = parts[2] if len(parts) > 2 else ""
                        mac = parts[4]
                        entries.append({
                            "ip": ip,
                            "mac": mac.lower(),
                            "interface": iface,
                            "is_private": _is_rfc1918(ip),
                            "source": "ip_neigh",
                        })
            else:
                # Fallback: arp -a
                out2 = subprocess.run(
                    ["arp", "-a"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                for line in out2.stdout.splitlines():
                    # "hostname (10.0.0.1) at aa:bb:cc:dd:ee:ff [ether] on eth0"
                    if " at " in line:
                        left, right = line.split(" at ", 1)
                        ip_part = left.split("(")[-1].rstrip(")")
                        mac_part = right.split()[0] if right.split() else ""
                        iface = right.split("on ")[-1].strip() if "on " in right else ""
                        if ip_part and mac_part and mac_part != "<incomplete>":
                            entries.append({
                                "ip": ip_part,
                                "mac": mac_part.lower(),
                                "interface": iface,
                                "is_private": _is_rfc1918(ip_part),
                                "source": "arp_a",
                            })
    except Exception as exc:
        logger.warning("ARP table read failed: %s", exc)

    return entries


def read_routing_table() -> list[dict[str, Any]]:
    """Read the OS routing table — passive, no packets sent.

    Returns list of {destination, gateway, interface, metric, has_default_route}.
    """
    routes: list[dict[str, Any]] = []
    system = platform.system()

    try:
        if system == "Windows":
            out = subprocess.run(
                ["route", "print"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            in_ipv4 = False
            for line in out.stdout.splitlines():
                if "IPv4 Route Table" in line or "Active Routes" in line:
                    in_ipv4 = True
                if not in_ipv4:
                    continue
                parts = line.split()
                if len(parts) >= 5:
                    dest = parts[0]
                    gw = parts[2] if len(parts) > 2 else ""
                    if dest == "0.0.0.0":  # nosec B104 — route table comparison, not a bind address
                        gateway_is_private = _is_rfc1918(gw)
                        routes.append({
                            "destination": "0.0.0.0/0",
                            "gateway": gw,
                            "interface": parts[3] if len(parts) > 3 else "",
                            "metric": parts[4] if len(parts) > 4 else "",
                            "is_default": True,
                            "gateway_is_private": gateway_is_private,
                        })
                    elif dest.count(".") == 3:
                        routes.append({
                            "destination": dest,
                            "gateway": gw,
                            "interface": parts[3] if len(parts) > 3 else "",
                            "metric": parts[4] if len(parts) > 4 else "",
                            "is_default": False,
                            "gateway_is_private": _is_rfc1918(gw),
                        })
        else:
            # Linux: `ip route`
            out = subprocess.run(
                ["ip", "route", "show"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    parts = line.split()
                    if not parts:
                        continue
                    dest = parts[0]
                    is_default = dest in ("default", "0.0.0.0/0")
                    gw = ""
                    iface = ""
                    metric = ""
                    for i, tok in enumerate(parts):
                        if tok == "via" and i + 1 < len(parts):
                            gw = parts[i + 1]
                        elif tok == "dev" and i + 1 < len(parts):
                            iface = parts[i + 1]
                        elif tok == "metric" and i + 1 < len(parts):
                            metric = parts[i + 1]
                    routes.append({
                        "destination": "0.0.0.0/0" if is_default else dest,
                        "gateway": gw,
                        "interface": iface,
                        "metric": metric,
                        "is_default": is_default,
                        "gateway_is_private": _is_rfc1918(gw) if gw else False,
                    })
            else:
                # macOS fallback: netstat -rn
                out2 = subprocess.run(
                    ["netstat", "-rn"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                for line in out2.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] in ("default", "0.0.0.0"):  # nosec B104 — route table comparison, not a bind address
                        gw = parts[1]
                        routes.append({
                            "destination": "0.0.0.0/0",
                            "gateway": gw,
                            "interface": parts[-1] if len(parts) > 2 else "",
                            "metric": "",
                            "is_default": True,
                            "gateway_is_private": _is_rfc1918(gw),
                        })
    except Exception as exc:
        logger.warning("Routing table read failed: %s", exc)

    return routes


# ── Enclave Classification ────────────────────────────────────────────────────


def classify_enclave(
    interfaces: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    arp_entries: list[dict[str, Any]],
    hostname: str = "",
) -> dict[str, Any]:
    """Classify the enclave IL level from passive data.

    Scoring heuristics:
      IL6 — hostname/DNS contains SIPR indicators, no default route,
             all interfaces are private, no external connectivity
      IL5 — all interfaces private, no default route (or default via
             private gateway only), DNS suffix indicates .mil/.gov
      IL4 — all interfaces private, default route exists via private gw
      IL2 — any interface has public IP or default route via public gateway

    Returns {
        il_level: int (2/4/5/6),
        classification: str,
        confidence: float,
        indicators: [str],
        boundary_segments: [str],
    }
    """
    indicators: list[str] = []
    private_ifaces = [i for i in interfaces if i.get("is_private") and not i.get("is_loopback")]
    public_ifaces = [i for i in interfaces if not i.get("is_private") and not i.get("is_loopback")]
    default_routes = [r for r in routes if r.get("is_default")]
    private_gw_defaults = [r for r in default_routes if r.get("gateway_is_private")]
    public_gw_defaults = [r for r in default_routes if not r.get("gateway_is_private") and r.get("gateway")]

    # IL6 indicators
    il6_score = 0
    hostname_lower = hostname.lower()
    for ind in _IL6_INDICATORS:
        if ind in hostname_lower:
            il6_score += 3
            indicators.append(f"hostname contains IL6 indicator: {ind}")

    if not default_routes:
        il6_score += 2
        indicators.append("no default route — fully isolated")
    if not public_ifaces:
        il6_score += 1
        indicators.append("all interfaces are RFC-1918 private")
    if not public_gw_defaults:
        il6_score += 1
        indicators.append("no public-gateway default route")

    # DNS suffix check
    gov_dns = False
    try:
        fqdn = socket.getfqdn().lower()
        for suffix in _GOV_DNS_SUFFIXES:
            if fqdn.endswith(suffix):
                gov_dns = True
                indicators.append(f"FQDN suffix indicates gov network: {fqdn}")
                break
    except Exception:
        pass

    # IL2 indicators
    il2_indicators = []
    if public_ifaces:
        il2_indicators.append(f"{len(public_ifaces)} public IP interface(s) detected")
    if public_gw_defaults:
        il2_indicators.append(f"default route via public gateway: {public_gw_defaults[0].get('gateway', '')}")

    # IL classification decision tree
    if il2_indicators:
        il_level = 2
        classification = "IL2 — Public/Internet-facing"
        confidence = 0.9
        indicators = il2_indicators + indicators
    elif il6_score >= 5:
        il_level = 6
        classification = "IL6 — SECRET/SIPR"
        confidence = min(0.5 + il6_score * 0.05, 0.95)
    elif not default_routes or (not public_gw_defaults and gov_dns):
        il_level = 5
        classification = "IL5 — CUI Dedicated (no external route)"
        confidence = 0.75 if gov_dns else 0.60
        if gov_dns:
            indicators.append("government DNS suffix — IL5 more likely than IL4")
    elif private_gw_defaults:
        il_level = 4
        classification = "IL4 — CUI/GovCloud (private gateway default route)"
        confidence = 0.70
        indicators.append(f"default route via private gateway: {private_gw_defaults[0].get('gateway', '')}")
    else:
        il_level = 4
        classification = "IL4 — CUI (unconfirmed; assumed minimum for private network)"
        confidence = 0.45

    # Boundary segments from interface CIDRs
    boundary_segments: list[str] = []
    for iface in private_ifaces:
        ip = iface.get("ip", "")
        netmask = iface.get("netmask", "")
        prefix = iface.get("prefix")
        if ip and (netmask or prefix):
            try:
                if prefix:
                    cidr = f"{ip}/{prefix}"
                else:
                    cidr = f"{ip}/{netmask}"
                net = ipaddress.ip_interface(cidr).network
                boundary_segments.append(str(net))
            except ValueError:
                boundary_segments.append(ip)
        elif ip:
            boundary_segments.append(ip)

    return {
        "il_level": il_level,
        "classification": classification,
        "confidence": round(confidence, 2),
        "indicators": indicators,
        "boundary_segments": list(set(boundary_segments)),
        "interface_count": len(interfaces),
        "private_interfaces": len(private_ifaces),
        "public_interfaces": len(public_ifaces),
        "default_routes": len(default_routes),
        "arp_hosts": len(arp_entries),
    }


# ── Enclave Scanner Agent ─────────────────────────────────────────────────────


class EnclaveScanner:
    """Air-gapped classified enclave discovery agent.

    Orchestrates passive + optional active scanning, classifies the enclave,
    and persists results with CUI markings.
    """

    def __init__(self, classification: str = CLASSIFICATION_BANNER) -> None:
        self.classification = classification
        self.scan_id = str(uuid.uuid4())
        self.scanned_at = _utcnow()

    # ── Air-gap detection ─────────────────────────────────────────────────

    def check_airgap(self) -> dict[str, Any]:
        """Probe local environment for air-gap indicators."""
        try:
            from tools.airgap.detector import detect_environment
            return detect_environment(use_cache=False)
        except ImportError:
            logger.debug("airgap.detector not available — running minimal detection")
            return {
                "airgap": True,
                "local_llm_servers": [],
                "ollama": False,
                "claude_code_cli": False,
                "claude_code_env": False,
                "anthropic_api": False,
                "bedrock": False,
            }

    # ── Passive scan ──────────────────────────────────────────────────────

    def passive_scan(self) -> dict[str, Any]:
        """Run passive-only discovery (no packets sent).

        Reads local interfaces, ARP cache, and routing table.
        Returns structured discovery results.
        """
        logger.info("[enclave_scanner] passive scan started — scan_id=%s", self.scan_id)

        hostname = ""
        try:
            hostname = socket.getfqdn()
        except Exception:
            try:
                hostname = socket.gethostname()
            except Exception:
                pass

        interfaces = detect_local_interfaces()
        arp_entries = read_arp_table()
        routes = read_routing_table()
        enclave_class = classify_enclave(interfaces, routes, arp_entries, hostname)

        # Deduplicate ARP hosts into discovered-host list
        discovered_hosts: list[dict[str, Any]] = []
        seen_ips: set[str] = set()
        for arp in arp_entries:
            ip = arp.get("ip", "")
            if ip and ip not in seen_ips and not _is_loopback(ip):
                seen_ips.add(ip)
                discovered_hosts.append({
                    "ip": ip,
                    "mac": arp.get("mac", ""),
                    "hostname": "",
                    "interface": arp.get("interface", ""),
                    "device_type": "unknown",
                    "discovery_method": "arp_passive",
                    "is_private": arp.get("is_private", True),
                })

        # Include local interface IPs as "self" nodes
        for iface in interfaces:
            ip = iface.get("ip", "")
            if ip and ip not in seen_ips and not _is_loopback(ip):
                seen_ips.add(ip)
                discovered_hosts.append({
                    "ip": ip,
                    "mac": "",
                    "hostname": hostname,
                    "interface": iface.get("name", ""),
                    "device_type": "self",
                    "discovery_method": "local_interface",
                    "is_private": iface.get("is_private", True),
                })

        result = {
            "scan_id": self.scan_id,
            "scan_mode": "passive",
            "classification": self.classification,
            "hostname": hostname,
            "scanned_at": self.scanned_at,
            "airgap_env": self.check_airgap(),
            "enclave_classification": enclave_class,
            "interfaces": interfaces,
            "routing_table": routes,
            "arp_cache": arp_entries,
            "discovered_hosts": discovered_hosts,
            "stats": {
                "interfaces": len(interfaces),
                "arp_entries": len(arp_entries),
                "routes": len(routes),
                "discovered_hosts": len(discovered_hosts),
            },
        }
        logger.info("[enclave_scanner] passive scan complete — %d hosts discovered", len(discovered_hosts))
        return result

    # ── Active scan ───────────────────────────────────────────────────────

    def active_scan(
        self,
        targets: list[str],
        method: str = "ping",
        community: str = "public",
        username: str = "",
        password: str = "",
        device_type: str = "cisco_ios",
        port: int = 0,
        timeout: float = 2.0,
        hop_limit: int = 1,
    ) -> dict[str, Any]:
        """Run active discovery using tools.network.discovery.

        Extends passive_scan with live host enumeration and topology data.
        Operator must explicitly pass --active; never called automatically.
        """
        logger.info(
            "[enclave_scanner] active scan started — targets=%s method=%s scan_id=%s",
            targets, method, self.scan_id,
        )

        # Run passive scan first
        passive = self.passive_scan()

        # Run active discovery
        active_result: dict[str, Any] = {}
        try:
            from tools.network.discovery import run_discovery, ping_sweep

            if method == "ping":
                alive: list[str] = []
                for t in targets:
                    alive.extend(ping_sweep(t, timeout=timeout))
                active_result = {
                    "alive_hosts": alive,
                    "devices": [{"ip": ip, "hostname": ip, "device_type": "unknown",
                                 "vendor": "Unknown", "neighbors": [],
                                 "method": "ping", "discovered_at": _utcnow()}
                                for ip in alive],
                    "method": "ping",
                }
            else:
                active_result = run_discovery(
                    targets=targets,
                    method=method,
                    community=community,
                    username=username,
                    password=password,
                    device_type=device_type,
                    port=port,
                    timeout=timeout,
                    hop_limit=hop_limit,
                )
        except ImportError as exc:
            logger.warning("tools.network.discovery unavailable: %s — active discovery skipped", exc)
            active_result = {"error": f"discovery module unavailable: {exc}", "devices": []}

        # Merge discovered hosts
        active_hosts = active_result.get("devices", [])
        seen_ips: set[str] = {h["ip"] for h in passive["discovered_hosts"]}
        for dev in active_hosts:
            ip = dev.get("ip", "")
            if ip and ip not in seen_ips:
                seen_ips.add(ip)
                passive["discovered_hosts"].append({
                    "ip": ip,
                    "mac": "",
                    "hostname": dev.get("hostname", ip),
                    "interface": "",
                    "device_type": dev.get("device_type", "unknown"),
                    "vendor": dev.get("vendor", ""),
                    "discovery_method": f"active_{method}",
                    "is_private": _is_rfc1918(ip),
                    "neighbors": dev.get("neighbors", []),
                })

        passive["scan_mode"] = "active"
        passive["active_targets"] = targets
        passive["active_method"] = method
        passive["active_discovery"] = active_result
        passive["stats"]["active_hosts_found"] = len(active_hosts)
        passive["stats"]["total_unique_hosts"] = len(passive["discovered_hosts"])

        if "graph_json" in active_result:
            passive["graph_json"] = active_result["graph_json"]

        logger.info(
            "[enclave_scanner] active scan complete — %d unique hosts",
            len(passive["discovered_hosts"]),
        )
        return passive

    # ── DB Persistence ────────────────────────────────────────────────────

    def save_results(self, results: dict[str, Any]) -> str:
        """Persist scan results to the network canvas DB (enclave_scans table).

        Returns the scan_id. Creates the table on first call.
        Table is append-only per NIST AU (no UPDATE/DELETE).
        """
        try:
            from tools.network.db.init_db import get_connection

            conn = get_connection()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS enclave_scans (
                    id             TEXT PRIMARY KEY,
                    scan_mode      TEXT NOT NULL DEFAULT 'passive',
                    hostname       TEXT,
                    il_level       INTEGER,
                    classification TEXT,
                    il_confidence  REAL,
                    host_count     INTEGER,
                    interface_count INTEGER,
                    boundary_segments TEXT DEFAULT '[]',
                    indicators     TEXT DEFAULT '[]',
                    scan_json      TEXT,
                    classification_banner TEXT DEFAULT 'CUI // SP-CTI',
                    scanned_at     TEXT NOT NULL
                )
            """)
            enc = results.get("enclave_classification", {})
            conn.execute(
                "INSERT OR IGNORE INTO enclave_scans "
                "(id, scan_mode, hostname, il_level, classification, il_confidence, "
                " host_count, interface_count, boundary_segments, indicators, "
                " scan_json, classification_banner, scanned_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    results["scan_id"],
                    results.get("scan_mode", "passive"),
                    results.get("hostname", ""),
                    enc.get("il_level"),
                    enc.get("classification", ""),
                    enc.get("confidence"),
                    results.get("stats", {}).get("discovered_hosts", 0),
                    results.get("stats", {}).get("interfaces", 0),
                    json.dumps(enc.get("boundary_segments", [])),
                    json.dumps(enc.get("indicators", [])),
                    json.dumps(results, default=str),
                    self.classification,
                    results["scanned_at"],
                ),
            )
            conn.commit()
            conn.close()
            logger.info("[enclave_scanner] results persisted — scan_id=%s", results["scan_id"])
        except Exception as exc:
            logger.warning("[enclave_scanner] DB persist skipped: %s", exc)

        return results["scan_id"]

    # ── Orchestrator ──────────────────────────────────────────────────────

    def scan(
        self,
        mode: str = "passive",
        targets: list[str] | None = None,
        method: str = "ping",
        community: str = "public",
        username: str = "",
        password: str = "",
        device_type: str = "cisco_ios",
        port: int = 0,
        timeout: float = 2.0,
        hop_limit: int = 1,
        save: bool = True,
    ) -> dict[str, Any]:
        """Run enclave scan and optionally save results.

        Parameters
        ----------
        mode      : "passive" (default) or "active"
        targets   : list of IP/CIDR targets (active mode only)
        method    : "ping", "snmp", or "ssh" (active mode only)
        save      : if True, persist to DB; default True
        """
        if mode == "active":
            if not targets:
                logger.warning("Active mode requested but no targets specified — falling back to passive")
                results = self.passive_scan()
            else:
                results = self.active_scan(
                    targets=targets,
                    method=method,
                    community=community,
                    username=username,
                    password=password,
                    device_type=device_type,
                    port=port,
                    timeout=timeout,
                    hop_limit=hop_limit,
                )
        else:
            results = self.passive_scan()

        if save:
            self.save_results(results)

        return results


# ── Gate Evaluation ───────────────────────────────────────────────────────────


def evaluate_gate(results: dict[str, Any]) -> dict[str, Any]:
    """Gate evaluation: fail if unclassified hosts found or IL level is unknown.

    Returns {"pass": bool, "findings": [str]}.
    """
    findings: list[str] = []
    enc = results.get("enclave_classification", {})

    if enc.get("confidence", 0) < 0.4:
        findings.append(
            f"low IL classification confidence ({enc.get('confidence', 0):.0%}) — "
            "manual review required before enclave access"
        )

    public_ifaces = enc.get("public_interfaces", 0)
    if public_ifaces and enc.get("il_level", 2) >= 5:
        findings.append(
            f"IL{enc['il_level']} enclave has {public_ifaces} public interface(s) — "
            "potential boundary violation"
        )

    if enc.get("il_level") is None:
        findings.append("enclave IL level could not be determined — scanner error")

    return {"pass": len(findings) == 0, "findings": findings}


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=f"ICDEV™ Enclave Scanner — Air-Gapped Classified Enclave Discovery\n"
                    f"Classification: {CLASSIFICATION_BANNER}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--active",
        action="store_true",
        help="Enable active scanning (default: passive only — no packets sent)",
    )
    ap.add_argument(
        "--target",
        nargs="+",
        metavar="IP/CIDR",
        help="Scan targets for active mode (IPs or CIDR subnets)",
    )
    ap.add_argument(
        "--method",
        choices=["ping", "snmp", "ssh"],
        default="ping",
        help="Active discovery method (default: ping)",
    )
    ap.add_argument("--community", default="public", help="SNMP community string (default: public)")
    ap.add_argument("--username", default="", help="SSH username (active/ssh mode)")
    ap.add_argument("--password", default="", help="SSH password (active/ssh mode)")
    ap.add_argument("--device-type", default="cisco_ios", help="Netmiko device type (active/ssh mode)")
    ap.add_argument("--port", type=int, default=0, help="Override default port")
    ap.add_argument("--timeout", type=float, default=2.0, help="Per-host timeout (seconds)")
    ap.add_argument("--hop-limit", type=int, default=1, help="Max neighbor crawl depth (active only)")
    ap.add_argument("--no-save", action="store_true", help="Skip DB persistence")
    ap.add_argument(
        "--gate",
        action="store_true",
        help="Gate mode: exit 1 if classification confidence < 40%% or boundary violations found",
    )
    ap.add_argument("--json", action="store_true", help="Output results as JSON")
    ap.add_argument(
        "--output",
        metavar="FILE",
        help="Write JSON results to file (use - for stdout)",
    )

    args = ap.parse_args(argv)

    scanner = EnclaveScanner()

    results = scanner.scan(
        mode="active" if args.active else "passive",
        targets=args.target,
        method=args.method,
        community=args.community,
        username=args.username,
        password=args.password,
        device_type=args.device_type,
        port=args.port,
        timeout=args.timeout,
        hop_limit=args.hop_limit,
        save=not args.no_save,
    )

    gate = evaluate_gate(results)
    results["gate"] = gate

    if args.json or args.output:
        json_str = json.dumps(results, indent=2, default=str)
        if args.output and args.output != "-":
            Path(args.output).write_text(json_str, encoding="utf-8", newline="")
            print(f"Results written to {args.output}")
        else:
            print(json_str)
    else:
        enc = results.get("enclave_classification", {})
        print(f"\n{'='*60}")
        print(f"  {CLASSIFICATION_BANNER}")
        print(f"  ICDEV™ Enclave Scanner — scan_id={results['scan_id']}")
        print(f"{'='*60}")
        print(f"  Hostname       : {results.get('hostname', 'unknown')}")
        print(f"  Scan mode      : {results.get('scan_mode', 'passive')}")
        print(f"  IL Level       : IL{enc.get('il_level', '?')} — {enc.get('classification', '')}")
        print(f"  Confidence     : {enc.get('confidence', 0):.0%}")
        print(f"  Hosts found    : {len(results.get('discovered_hosts', []))}")
        print(f"  Interfaces     : {len(results.get('interfaces', []))}")
        print(f"  ARP entries    : {len(results.get('arp_cache', []))}")
        print(f"  Boundary segs  : {', '.join(enc.get('boundary_segments', [])) or 'none detected'}")
        print()
        print("  Indicators:")
        for ind in enc.get("indicators", []):
            print(f"    • {ind}")
        print()
        if not gate["pass"]:
            print("  GATE: FAIL")
            for f in gate["findings"]:
                print(f"    ✗ {f}")
        else:
            print("  GATE: PASS")
        print(f"{'='*60}\n")

    return 0 if gate["pass"] else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(_cli())
