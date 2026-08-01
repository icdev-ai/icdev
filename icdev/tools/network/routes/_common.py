# CUI // SP-CTI
"""ICDEV Network Design Canvas -- shared route helpers/constants.

Relocated from tools/network/blueprint.py during the cvx-net-01 monolith split.
These are the only factory-local helpers referenced across more than one route
group; single-group helpers stay inside their group module.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from pathlib import Path

from tools.logging.icdev_logger import get_logger
from tools.network.db.init_db import get_connection
from tools.db.storage import sql_placeholder

logger = get_logger("icdev.network")

_NETWORK_DIR = Path(__file__).resolve().parents[1]
_ICDEV_ROOT = _NETWORK_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"


def _nc_save_message(ctx_id: str, role: str, content: str) -> None:
    """Insert a chat message into chat_messages with auto-incrementing turn_number."""
    try:
        msg_id = "ncmsg-" + _uuid.uuid4().hex[:12]
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT MAX(turn_number) FROM chat_messages WHERE context_id={_ph}",
            (ctx_id,),
        ).fetchone()
        turn_number = (row[0] or 0) + 1
        conn.execute(
            "INSERT INTO chat_messages (id, context_id, turn_number, role, content, content_type, created_at) "
            f"VALUES ({_ph}, {_ph}, {_ph}, {_ph}, {_ph}, 'text', {_ph})",
            (msg_id, ctx_id, turn_number, role, content, datetime.utcnow().isoformat()),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("_nc_save_message failed: %s", exc)


_NODE_CLASSIFY_PATTERNS = [
    (r"(?i)(core|edge|border|wan|pe|ce|p\b).*r(outer|tr)", "router"),
    (r"(?i)r(outer|tr)", "router"),
    (r"(?i)(fw|firewall|palo|forti|asa|checkpoint)", "firewall"),
    (r"(?i)(sw|switch).*l3|layer.?3.*sw|dist.*sw|core.*sw", "switch-l3"),
    (r"(?i)(sw|switch|access)", "switch-l2"),
    (r"(?i)(lb|load.?bal|f5|netscaler|a10)", "load-balancer"),
    (r"(?i)(wap|access.?point|ap\d|wifi|wireless)", "wap"),
    (r"(?i)(wlc|wireless.*control)", "wlc"),
    (r"(?i)(srv|server|host|vm\b|esxi|hypervisor)", "server"),
    (r"(?i)(pc|workstation|desktop|laptop|endpoint)", "endpoint-pc"),
    (r"(?i)(phone|voip|sip)", "ip-phone"),
    (r"(?i)(sdwan|sd-wan|vmanage|vedge)", "sdwan-edge"),
    (r"(?i)(mpls.*pe|pe.*router)", "mpls-pe"),
    (r"(?i)(mpls.*p\b|p.*router|provider)", "mpls-p"),
    (r"(?i)(route.?reflect|rr\b)", "route-reflector"),
    (r"(?i)(encrypt|kg-|type.?1|nsa)", "type1-encryptor"),
    (r"(?i)(fips|hsm)", "fips-140-l2"),
    (r"(?i)(siem|splunk|qradar|arcsight)", "siem"),
    (r"(?i)(tap|span|mirror)", "network-tap"),
    (r"(?i)(vpc|aws)", "aws-vpc"),
    (r"(?i)(vnet|azure)", "az-vnet"),
    (r"(?i)(gcp|google.?cloud)", "gcp-vpc"),
    (r"(?i)(internet|cloud|wan|isp)", "cloud"),
    (r"(?i)(patch.?panel|pp\b|mdf|idf)", "patch-panel"),
    (r"(?i)(ups|pdu|power)", "server"),
    (r"(?i)(demarc|demarcation)", "demarc"),
    (r"(?i)(meet.?me|mmr|colo)", "meet-me-room"),
]

def _classify_imported_nodes(graph):
    """Auto-classify imported nodes from generic 'imported' type
    to specific device types using label pattern matching."""
    import re as _re

    for n in graph.get("nodes", []):
        if n.get("type") not in ("imported", "", None):
            continue
        label = n.get("label", "")
        matched = False
        for pattern, dtype in _NODE_CLASSIFY_PATTERNS:
            if _re.search(pattern, label):
                n["type"] = dtype
                matched = True
                break
        if not matched:
            n["type"] = "server"  # safe default
    return graph


_AI_MIGRATION_PLAN_PROMPT = """You are a DoD/government network migration planner. \
You work with any vendor (Cisco, Juniper, Arista, Palo Alto, Fortinet, HPE, Brocade, etc.), \
any device type (routers, switches, firewalls, load balancers, wireless controllers, SD-WAN), \
any ISP or carrier, and any partner network (government, commercial, NIPR, SIPR, or private).

CRITICAL ARCHITECTURE RULE — DoD Cloud Connectivity:
CSPs (AWS GovCloud, Azure Government, GCP, OCI, IBM Cloud, etc.) do NOT connect directly to \
the edge router or to NIPR. In DoD networks, ALL CSP connectivity is routed through DISA BCAP \
(Boundary Cloud Access Point). The topology is always:
  Edge Router → DISA BCAP → CSP
NEVER model a direct edge-router-to-CSP connection. Any migration involving cloud workloads \
must include DISA BCAP as an intermediate node on the north side, and must include DISA \
coordination steps in the relevant phases.

Given a plain-English description of a migration, decompose it into an ordered list of phases \
that are specific to the described devices and connections — do NOT assume any vendor, protocol, \
or peer unless the description explicitly names them.

Output ONLY a valid JSON array — no markdown, no explanation:
[
  {
"phase_num": 1,
"title": "Short imperative title",
"description": "3-4 sentences: what changes, what stays, dependencies, and any coordination required",
"duration_days": 14,
"parallel_run": 0,
"rollback_criteria": "One sentence: condition and steps that trigger rollback",
"maintenance_window": "Sat 02:00-06:00 local time",
"classification": "CUI",
"impact_level": "IL4"
  }
]

Rules:
1. Phase titles must be short imperatives specific to the described devices (e.g. "Stage Cisco ASR Config", "Cut Over ISP BGP", "Migrate VLANs to New Core Switch")
2. Infer phases from the actual topology: north-side partners, south-side peers, protocols (BGP, OSPF, EIGRP, MPLS, etc.) and physical connections (trunk, LAG, port-channel, SFP) mentioned
3. If CSP connectivity is mentioned (cloud workloads, AWS, Azure, GCP, etc.), always model it as going through DISA BCAP — generate a BCAP coordination phase
4. duration_days: realistic estimate in days (minimum 1, typical 7-30 for production cuts)
5. parallel_run: 1 if old and new devices run simultaneously during this phase, else 0
6. classification: PUBLIC | CUI | SECRET | TS — infer from context or default to CUI
7. impact_level: IL2 | IL4 | IL5 | IL6 — infer from context or default to IL4
8. Phases must be ordered so each depends only on prior completed phases
9. Always end with a decommission or final validation phase
10. Minimum 2 phases, maximum 12 phases
11. If partner coordination is needed (ISP, DISA, government agency, carrier), add a dedicated coordination step within the relevant phase description"""
