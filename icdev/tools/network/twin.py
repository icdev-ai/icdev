# CUI // SP-CTI — NDC Network Digital Twin
"""Network Design Canvas digital twin — snapshot, simulate, and blast-radius analysis.

Implements the Forward Networks-style intent validation pattern described in
docs/briefs/digital-twin-market-canvas-implementation-plan.md (NDC §7).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.network.twin")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nc_conn():
    """Return a connection to the network canvas SQLite database."""
    from tools.network.db.init_db import get_connection
    return get_connection()


def _ensure_snapshots_table(conn) -> None:
    """Create network_twin_snapshots if it doesn't exist yet."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS network_twin_snapshots (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            label TEXT,
            device_count INTEGER DEFAULT 0,
            link_count INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.commit()


def take_snapshot(project_id: str, label: str | None = None) -> dict:
    """Freeze current topology state into network_twin_snapshots (append-only)."""
    conn = _nc_conn()
    _ensure_snapshots_table(conn)

    snap_id = str(uuid.uuid4())
    taken_at = _now()
    label = label or f"snap-{taken_at[:10]}"

    device_count = 0
    link_count = 0
    try:
        row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = %s", (project_id,)
        ).fetchone()
        if row and row["graph_json"]:
            graph = json.loads(row["graph_json"])
            device_count = len(graph.get("nodes", []))
            link_count = len(graph.get("edges", []))
    except Exception:
        pass

    try:
        conn.execute(
            """INSERT INTO network_twin_snapshots
               (id, project_id, label, device_count, link_count, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (snap_id, project_id, label, device_count, link_count, taken_at),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("take_snapshot: best-effort INSERT into network_twin_snapshots failed (non-blocking): %s", exc)

    return {
        "id": snap_id,
        "project_id": project_id,
        "label": label,
        "device_count": device_count,
        "link_count": link_count,
        "created_at": taken_at,
    }


def simulate_delta(
    project_id: str,
    topology_delta: dict,
    intent_rules: list[str] | None = None,
    baseline_snap_id: str | None = None,
) -> dict:
    """Validate a proposed topology delta against intent rules.

    Returns a PASS/WARN/FAIL verdict with per-rule results and compliance findings.
    """
    sim_id = str(uuid.uuid4())
    intent_rules = intent_rules or []

    added_devices = topology_delta.get("add_devices", [])
    removed_devices = topology_delta.get("remove_devices", [])
    added_links = topology_delta.get("add_links", [])
    removed_links = topology_delta.get("remove_links", [])
    acl_changes = topology_delta.get("acl_changes", [])

    # Evaluate intent rules heuristically against the delta
    from tools.network.constants import INTENT_RULES as ALL_RULES
    rule_map = {r["id"]: r for r in ALL_RULES}
    intent_results = []
    compliance_findings = []

    for rule_id in (intent_rules or [r["id"] for r in ALL_RULES]):
        rule = rule_map.get(rule_id, {"id": rule_id, "label": rule_id})
        passed = True
        detail = None

        if rule_id == "no-direct-internet":
            new_types = [d.get("type", "") for d in added_devices]
            if any(t in ("server", "vm", "container") for t in new_types) and not any(t in ("firewall", "waf") for t in new_types):
                passed = False
                detail = f"{len(added_devices)} new node(s) added without co-located firewall/WAF"

        elif rule_id == "acl-compliance":
            risky = [c for c in acl_changes if "permit" in c.get("rule", "").lower() and "any" in c.get("rule", "").lower()]
            if risky:
                passed = False
                detail = f"{len(risky)} overly permissive ACL rule(s) detected"

        elif rule_id == "no-unencrypted":
            for link in added_links:
                proto = str(link.get("protocol", "")).lower()
                if proto in ("http", "telnet", "ftp", "snmpv1", "snmpv2"):
                    passed = False
                    detail = f"Plaintext protocol '{proto}' on link {link.get('src', '?')}→{link.get('dst', '?')}"
                    break

        elif rule_id == "redundancy":
            if removed_links and not added_links:
                passed = False
                detail = f"{len(removed_links)} link(s) removed with no replacement — may create single point of failure"

        intent_results.append({"rule_id": rule_id, "label": rule.get("label", rule_id), "passed": passed, "detail": detail})
        if not passed:
            compliance_findings.append({
                "severity": "high",
                "id": rule_id,
                "title": rule.get("label", rule_id),
                "recommendation": detail or rule.get("desc", ""),
            })

    fails = sum(1 for r in intent_results if not r["passed"])
    verdict = "pass" if fails == 0 else ("warn" if fails <= 1 else "fail")

    return {
        "id": sim_id,
        "simulation_id": sim_id,
        "project_id": project_id,
        "verdict": verdict,
        "topology_delta": {
            "added_devices": added_devices,
            "removed_devices": removed_devices,
            "changed_links": added_links + removed_links,
            "acl_changes": acl_changes,
        },
        "intent_results": intent_results,
        "compliance_findings": compliance_findings,
    }


# Common network abbreviation expansions used during fuzzy node resolution
_NETWORK_ABBREVS = {
    "tgw": "transit gateway",
    "dx": "direct connect",
    "dxgw": "direct connect gateway",
    "alb": "application load balancer",
    "nlb": "network load balancer",
    "gwlb": "gateway load balancer",
    "nfw": "network firewall",
    "fw": "firewall",
    "r53": "route 53",
    "ad": "active directory",
    "kms": "key management service",
    "ct": "cloudtrail",
    "ep": "endpoint",
    "pl": "privatelink",
    "gd": "guardduty",
    "vpc": "virtual private cloud",
    "rtr": "router",
    "sw": "switch",
    "lb": "load balancer",
    "vpn": "virtual private network",
    "wan": "wide area network",
    "lan": "local area network",
    "agg": "aggregation",
    "mgr": "manager",
    "pri": "primary",
    "sec": "secondary",
    "dr": "disaster recovery",
}


def _expand(text: str) -> set[str]:
    """Return tokens from text plus forward abbreviation expansions only.

    Forward only: 'tgw' -> adds 'transit','gateway'. Does NOT reverse-expand
    common words like 'gateway' into every abbreviation that contains it —
    that creates false positives when scoring node matches.
    """
    tokens = set(text.lower().replace("-", " ").replace("_", " ").replace("(", " ").replace(")", " ").split())
    expanded = set(tokens)
    for tok in tokens:
        if tok in _NETWORK_ABBREVS:
            expanded.update(_NETWORK_ABBREVS[tok].split())
    return expanded


def _resolve_node_id(query: str, nodes: dict) -> str | None:
    """Resolve a user query (node ID, label, or natural language) to an actual node ID.

    Tries in order:
      1. Exact ID match
      2. Exact label match (case-insensitive)
      3. Substring match (label contains query or vice versa)
      4. Token overlap with abbreviation expansion
    """
    if not query:
        return None
    q = query.strip()
    q_lower = q.lower()

    # 1. Exact ID match
    if q in nodes:
        return q

    # 2. Exact label match (case-insensitive)
    for nid, node in nodes.items():
        label = (node.get("label") or node.get("name") or nid).lower()
        if label == q_lower:
            return nid

    # 3. Direct substring match
    best_id = None
    best_score = -1
    for nid, node in nodes.items():
        label = (node.get("label") or node.get("name") or nid).lower()
        candidate = f"{nid.lower()} {label}"
        if q_lower in candidate or label in q_lower:
            score = 100 + len(q_lower)  # prefer longer match
            if score > best_score:
                best_score = score
                best_id = nid

    if best_id:
        return best_id

    # 4. Token overlap with abbreviation expansion
    q_tokens = _expand(q)
    for nid, node in nodes.items():
        label = node.get("label") or node.get("name") or nid
        node_tokens = _expand(f"{nid} {label}")
        overlap = len(q_tokens & node_tokens)
        if overlap > best_score:
            best_score = overlap
            best_id = nid

    return best_id if best_score > 0 else None


def blast_radius(
    project_id: str,
    node_id: str,
    topology_delta: dict | None = None,
    baseline_snap_id: str | None = None,
) -> dict:
    """Identify systems impacted by failure or removal of a given device or link.

    node_id may be a raw graph node ID or a natural language label — the function
    resolves it against the topology before querying edges.
    """
    conn = _nc_conn()
    impacted = []
    resolved_id = node_id
    resolved_label = node_id

    try:
        row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = %s", (project_id,)
        ).fetchone()

        if row and row["graph_json"]:
            graph = json.loads(row["graph_json"])
            nodes = {n["id"]: n for n in graph.get("nodes", [])}
            edges = graph.get("edges", [])

            # Resolve natural language / partial label to actual node ID
            matched = _resolve_node_id(node_id, nodes)
            if matched:
                resolved_id = matched
                resolved_label = nodes[matched].get("label") or nodes[matched].get("name") or matched

            # Merge any delta-added links into the edge set
            if topology_delta:
                for link in topology_delta.get("add_links", []):
                    edges.append(link)

            # Find all neighbors directly connected to resolved_id
            neighbors = set()
            for edge in edges:
                src = edge.get("source") or edge.get("src")
                dst = edge.get("target") or edge.get("dst")
                if src == resolved_id and dst:
                    neighbors.add(dst)
                elif dst == resolved_id and src:
                    neighbors.add(src)
            neighbors.discard(None)

            # Build impacted list with real node labels and severities
            for nid in neighbors:
                node = nodes.get(nid, {})
                label = node.get("label") or node.get("name") or nid
                ntype = node.get("type", "")
                if ntype in ("server", "database", "load-balancer"):
                    severity = "critical"
                elif ntype in ("router", "switch-l3", "firewall"):
                    severity = "high"
                else:
                    severity = "medium"
                impacted.append({
                    "id": nid,
                    "severity": severity,
                    "title": label,
                    "recommendation": f"Verify redundant path exists for {label}",
                })

            sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            impacted.sort(key=lambda x: sev_order.get(x["severity"], 9))

    except Exception:
        pass

    total = len(impacted)
    critical = sum(1 for i in impacted if i.get("severity") == "critical")
    slo_risk = "High" if total > 5 else ("Medium" if total > 2 else "Low")

    return {
        "node_id": resolved_id,
        "node_label": resolved_label,
        "impacted_count": total,
        "critical_path_count": critical,
        "slo_risk": slo_risk,
        "impacted_systems": impacted,
    }


def intent_validate(delta: dict, project_id: str | None = None) -> dict:
    """Validate a proposed remediation action against the NDC digital twin.

    Translates a remediation delta (device, action, from_version, to_version)
    into a topology_delta and runs simulate_delta + blast_radius.

    Returns a unified dict with verdict, intent_results, compliance_findings,
    and impacted_systems suitable for consume by remediation_simulator.
    """
    device = delta.get("device", "")
    action = delta.get("action", "")

    topology_delta: dict = {
        "add_devices": [],
        "remove_devices": [],
        "add_links": [],
        "remove_links": [],
        "acl_changes": [],
    }
    if action == "replace":
        topology_delta["remove_devices"] = [{"id": device, "label": device, "type": "unknown"}]
        topology_delta["add_devices"] = [{"id": device, "label": device, "type": "unknown"}]
    elif action == "config_change":
        topology_delta["acl_changes"] = [{"device": device, "rule": "permit any any"}]

    sim_result = simulate_delta(project_id or "_intent_validate", topology_delta)

    impacted_systems: list = []
    if project_id:
        try:
            br = blast_radius(project_id, device, topology_delta=topology_delta)
            impacted_systems = br.get("impacted_systems", [])
        except Exception:
            pass

    return {
        "verdict": sim_result["verdict"],
        "simulation_id": sim_result["id"],
        "intent_results": sim_result["intent_results"],
        "compliance_findings": sim_result["compliance_findings"],
        "impacted_systems": impacted_systems,
    }
