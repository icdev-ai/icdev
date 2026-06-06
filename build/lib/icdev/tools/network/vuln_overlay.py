# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Network Canvas — ACAS/Nessus Scan Overlay

Parses Nessus .nessus XML files, stores findings in the network canvas DB,
and provides helpers to match scan hosts to canvas topology nodes by IP/hostname.

Usage:
    from tools.network.vuln_overlay import parse_nessus_file, get_scan_summary

Schema: nc_vuln_scans, nc_vuln_hosts, nc_vuln_findings (see init_db.py)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import ipaddress
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = get_logger("icdev.network.vuln_overlay")

# Severity mapping: Nessus risk_factor / severity integer → label
SEVERITY_LABELS = {
    0: "info",
    1: "low",
    2: "medium",
    3: "high",
    4: "critical",
}
SEVERITY_COLORS = {
    "info": "#95a5a6",
    "low": "#3498db",
    "medium": "#f39c12",
    "high": "#e67e22",
    "critical": "#e94560",
}

# Nessus risk_factor string → severity int
_RISK_TO_INT = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


# ── Parser ─────────────────────────────────────────────────────────────────────


def parse_nessus_file(file_path: str | Path) -> dict[str, Any]:
    """Parse a .nessus XML file and return structured scan data.

    Returns:
        {
          "scan_name": str,
          "policy": str,
          "scan_start": str,    # ISO 8601
          "scan_end": str,
          "hosts": [
            {
              "ip": str,
              "fqdn": str,
              "netbios": str,
              "os": str,
              "counts": {"critical": N, "high": N, "medium": N, "low": N, "info": N},
              "findings": [
                {
                  "plugin_id": str,
                  "plugin_name": str,
                  "severity": int,        # 0–4
                  "severity_label": str,  # info/low/medium/high/critical
                  "risk_factor": str,
                  "cve": str,             # comma-separated CVEs
                  "cvss_base_score": str,
                  "port": str,
                  "protocol": str,
                  "synopsis": str,
                  "description": str,
                  "solution": str,
                  "plugin_output": str,
                }
              ]
            }
          ]
        }
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f".nessus file not found: {path}")

    tree = ET.parse(str(path))  # nosec B314 — parsing trusted internal file uploads
    root = tree.getroot()

    # --- Scan metadata ---
    policy_el = root.find("Policy")
    policy_name = ""
    if policy_el is not None:
        pn = policy_el.find("policyName")
        if pn is not None and pn.text:
            policy_name = pn.text.strip()

    # Scan name from Report/@name
    report_el = root.find("Report")
    scan_name = ""
    scan_start = ""
    scan_end = ""
    if report_el is not None:
        scan_name = report_el.get("name", "")

    hosts: list[dict] = []

    if report_el is None:
        return {
            "scan_name": scan_name,
            "policy": policy_name,
            "scan_start": scan_start,
            "scan_end": scan_end,
            "hosts": hosts,
        }

    for host_el in report_el.findall("ReportHost"):
        host_name = host_el.get("name", "")

        # Host properties (nested <HostProperties><tag name="...">value</tag>)
        props: dict[str, str] = {}
        hp_el = host_el.find("HostProperties")
        if hp_el is not None:
            for tag in hp_el.findall("tag"):
                k = tag.get("name", "")
                props[k] = (tag.text or "").strip()

        ip = props.get("host-ip", host_name)
        fqdn = props.get("host-fqdn", "")
        netbios = props.get("netbios-name", "")
        os_info = props.get("operating-system", "")
        h_start = props.get("HOST_START", "")
        h_end = props.get("HOST_END", "")
        if h_start and not scan_start:
            scan_start = h_start
        if h_end:
            scan_end = h_end

        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        findings: list[dict] = []

        for item_el in host_el.findall("ReportItem"):
            sev_int = int(item_el.get("severity", "0"))
            sev_label = SEVERITY_LABELS.get(sev_int, "info")
            counts[sev_label] += 1

            # CVEs — may be multiple <cve> elements
            cves = [c.text or "" for c in item_el.findall("cve")]
            cve_str = ", ".join(filter(None, cves))

            risk_factor = _text(item_el, "risk_factor") or SEVERITY_LABELS.get(sev_int, "none")

            findings.append(
                {
                    "plugin_id": item_el.get("pluginID", ""),
                    "plugin_name": item_el.get("pluginName", ""),
                    "severity": sev_int,
                    "severity_label": sev_label,
                    "risk_factor": risk_factor,
                    "cve": cve_str,
                    "cvss_base_score": _text(item_el, "cvss_base_score") or "",
                    "port": item_el.get("port", ""),
                    "protocol": item_el.get("protocol", ""),
                    "synopsis": _text(item_el, "synopsis") or "",
                    "description": _text(item_el, "description") or "",
                    "solution": _text(item_el, "solution") or "",
                    "plugin_output": _text(item_el, "plugin_output") or "",
                }
            )

        # Sort: critical → high → medium → low → info
        findings.sort(key=lambda f: (-f["severity"], f["plugin_name"]))

        hosts.append(
            {
                "ip": ip,
                "fqdn": fqdn,
                "netbios": netbios,
                "os": os_info,
                "counts": counts,
                "findings": findings,
            }
        )

    return {
        "scan_name": scan_name,
        "policy": policy_name,
        "scan_start": scan_start,
        "scan_end": scan_end,
        "hosts": hosts,
    }


def _text(el: ET.Element, tag: str) -> str | None:
    """Extract text from a child element, returning None if missing."""
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


# ── DB persistence ─────────────────────────────────────────────────────────────


def save_scan_to_db(conn, topology_id: str, file_name: str, parsed: dict) -> str:
    """Persist a parsed scan to the DB. Returns scan_id."""
    scan_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO nc_vuln_scans "
        "(id, topology_id, scan_name, policy, scan_start, scan_end, "
        "file_name, host_count, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            scan_id,
            topology_id,
            parsed["scan_name"],
            parsed["policy"],
            parsed["scan_start"],
            parsed["scan_end"],
            file_name,
            len(parsed["hosts"]),
            now,
        ),
    )

    for host in parsed["hosts"]:
        host_id = str(uuid.uuid4())
        counts = host["counts"]
        conn.execute(
            "INSERT INTO nc_vuln_hosts "
            "(id, scan_id, ip, fqdn, netbios, os, "
            "cnt_critical, cnt_high, cnt_medium, cnt_low, cnt_info, "
            "node_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?)",
            (
                host_id,
                scan_id,
                host["ip"],
                host["fqdn"],
                host["netbios"],
                host["os"],
                counts["critical"],
                counts["high"],
                counts["medium"],
                counts["low"],
                counts["info"],
                now,
            ),
        )
        for f in host["findings"]:
            conn.execute(
                "INSERT INTO nc_vuln_findings "
                "(id, host_id, scan_id, plugin_id, plugin_name, "
                "severity, severity_label, risk_factor, cve, "
                "cvss_base_score, port, protocol, "
                "synopsis, description, solution, plugin_output) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    host_id,
                    scan_id,
                    f["plugin_id"],
                    f["plugin_name"],
                    f["severity"],
                    f["severity_label"],
                    f["risk_factor"],
                    f["cve"],
                    f["cvss_base_score"],
                    f["port"],
                    f["protocol"],
                    f["synopsis"],
                    f["description"],
                    f["solution"],
                    f["plugin_output"],
                ),
            )

    conn.commit()
    return scan_id


# ── Overlay matching ───────────────────────────────────────────────────────────


def match_hosts_to_nodes(conn, scan_id: str, topology_id: str) -> list[dict]:
    """Match scan hosts to canvas nodes by IP/hostname.

    For each nc_vuln_host in scan_id, tries to find a topology node whose
    label, ip_address, or hostname matches the scan host ip/fqdn/netbios.
    Updates nc_vuln_hosts.node_id when a match is found.

    Returns list of {ip, fqdn, node_id, matched, counts}.
    """
    import json as _json  # noqa: F401 — used below via _json.loads

    # Load topology graph_json
    row = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topology_id,)).fetchone()
    if not row:
        return []

    graph = _json.loads(row["graph_json"] or '{"nodes":[],"edges":[]}')
    nodes = graph.get("nodes", [])

    # Build lookup: normalized IP → node_id, label → node_id
    ip_to_node: dict[str, str] = {}
    name_to_node: dict[str, str] = {}
    for node in nodes:
        nid = node.get("id", "")
        attrs = node.get("attrs", {}) or {}
        label = (attrs.get("label", {}).get("text", "") or "").lower()
        ip = (node.get("data", {}) or {}).get("ip_address", "").strip()
        hostname = (node.get("data", {}) or {}).get("hostname", "").strip().lower()
        if ip:
            ip_to_node[ip] = nid
        if label:
            name_to_node[label] = nid
        if hostname:
            name_to_node[hostname] = nid

    hosts = conn.execute(
        "SELECT id, ip, fqdn, netbios, cnt_critical, cnt_high, "
        "cnt_medium, cnt_low, cnt_info FROM nc_vuln_hosts WHERE scan_id=?",
        (scan_id,),
    ).fetchall()

    results = []
    for host in hosts:
        node_id = None
        host_ip = (host["ip"] or "").strip()
        host_fqdn = (host["fqdn"] or "").lower()
        host_nb = (host["netbios"] or "").lower()

        # Try exact IP match
        if host_ip in ip_to_node:
            node_id = ip_to_node[host_ip]
        # Try subnet containment (node IP may be CIDR)
        if not node_id:
            try:
                scan_addr = ipaddress.ip_address(host_ip)
                for net_str, nid in ip_to_node.items():
                    try:
                        net = ipaddress.ip_network(net_str, strict=False)
                        if scan_addr in net:
                            node_id = nid
                            break
                    except ValueError:
                        pass
            except ValueError:
                pass
        # Try hostname / netbios / fqdn
        if not node_id:
            for key in (host_fqdn, host_nb, host_fqdn.split(".")[0]):
                if key and key in name_to_node:
                    node_id = name_to_node[key]
                    break

        if node_id:
            conn.execute(
                "UPDATE nc_vuln_hosts SET node_id=? WHERE id=?",
                (node_id, host["id"]),
            )

        results.append(
            {
                "ip": host_ip,
                "fqdn": host["fqdn"],
                "node_id": node_id,
                "matched": node_id is not None,
                "counts": {
                    "critical": host["cnt_critical"],
                    "high": host["cnt_high"],
                    "medium": host["cnt_medium"],
                    "low": host["cnt_low"],
                    "info": host["cnt_info"],
                },
            }
        )

    conn.commit()
    return results


def get_scan_summary(conn, scan_id: str) -> dict:
    """Return aggregated vulnerability counts for a scan."""
    row = conn.execute(
        "SELECT scan_name, policy, scan_start, scan_end, file_name, "
        "host_count, created_at FROM nc_vuln_scans WHERE id=?",
        (scan_id,),
    ).fetchone()
    if not row:
        return {}

    agg = conn.execute(
        "SELECT "
        "  SUM(cnt_critical) as critical, "
        "  SUM(cnt_high) as high, "
        "  SUM(cnt_medium) as medium, "
        "  SUM(cnt_low) as low, "
        "  SUM(cnt_info) as info, "
        "  COUNT(*) as host_count, "
        "  SUM(CASE WHEN node_id IS NOT NULL THEN 1 ELSE 0 END) as matched_hosts "
        "FROM nc_vuln_hosts WHERE scan_id=?",
        (scan_id,),
    ).fetchone()

    return {
        "scan_id": scan_id,
        "scan_name": row["scan_name"],
        "policy": row["policy"],
        "scan_start": row["scan_start"],
        "scan_end": row["scan_end"],
        "file_name": row["file_name"],
        "host_count": row["host_count"],
        "created_at": row["created_at"],
        "totals": {
            "critical": agg["critical"] or 0,
            "high": agg["high"] or 0,
            "medium": agg["medium"] or 0,
            "low": agg["low"] or 0,
            "info": agg["info"] or 0,
        },
        "matched_hosts": agg["matched_hosts"] or 0,
    }


def get_overlay_data(conn, scan_id: str) -> list[dict]:
    """Return per-node vulnerability overlay data for the canvas.

    Returns list of {node_id, ip, fqdn, counts, worst_severity, color}.
    Only includes hosts that matched a canvas node.
    """
    rows = conn.execute(
        "SELECT node_id, ip, fqdn, cnt_critical, cnt_high, "
        "cnt_medium, cnt_low, cnt_info "
        "FROM nc_vuln_hosts "
        "WHERE scan_id=? AND node_id IS NOT NULL",
        (scan_id,),
    ).fetchall()

    result = []
    for r in rows:
        counts = {
            "critical": r["cnt_critical"],
            "high": r["cnt_high"],
            "medium": r["cnt_medium"],
            "low": r["cnt_low"],
            "info": r["cnt_info"],
        }
        worst = _worst_severity(counts)
        result.append(
            {
                "node_id": r["node_id"],
                "ip": r["ip"],
                "fqdn": r["fqdn"],
                "counts": counts,
                "worst_severity": worst,
                "color": SEVERITY_COLORS.get(worst, SEVERITY_COLORS["info"]),
            }
        )

    return result


def _worst_severity(counts: dict) -> str:
    """Return the highest severity level present."""
    for label in ("critical", "high", "medium", "low", "info"):
        if counts.get(label, 0) > 0:
            return label
    return "info"


def get_host_findings(conn, scan_id: str, host_ip: str, limit: int = 20) -> list[dict]:
    """Return top N findings for a host in a scan, ordered by severity desc."""
    host = conn.execute(
        "SELECT id FROM nc_vuln_hosts WHERE scan_id=? AND ip=? LIMIT 1",
        (scan_id, host_ip),
    ).fetchone()
    if not host:
        return []

    rows = conn.execute(
        "SELECT plugin_id, plugin_name, severity, severity_label, "
        "risk_factor, cve, cvss_base_score, port, protocol, "
        "synopsis, solution "
        "FROM nc_vuln_findings "
        "WHERE host_id=? "
        "ORDER BY severity DESC, plugin_name "
        "LIMIT ?",
        (host["id"], limit),
    ).fetchall()

    return [dict(r) for r in rows]
