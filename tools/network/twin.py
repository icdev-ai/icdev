# CUI // SP-CTI — NDC Network Digital Twin
"""Network Design Canvas digital twin — snapshot, simulate, and blast-radius analysis.

Implements the Forward Networks-style intent validation pattern described in
docs/briefs/digital-twin-market-canvas-implementation-plan.md (NDC §7).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone


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
            "SELECT graph_json FROM topologies WHERE id = ?", (project_id,)
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
               VALUES (?, ?, ?, ?, ?, ?)""",
            (snap_id, project_id, label, device_count, link_count, taken_at),
        )
        conn.commit()
    except Exception:
        pass

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


def blast_radius(
    project_id: str,
    node_id: str,
    topology_delta: dict | None = None,
    baseline_snap_id: str | None = None,
) -> dict:
    """Identify systems impacted by failure or removal of a given device or link."""
    conn = _nc_conn()
    impacted = []

    try:
        row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = ?", (project_id,)
        ).fetchone()

        if row and row["graph_json"]:
            graph = json.loads(row["graph_json"])
            nodes = {n["id"]: n for n in graph.get("nodes", [])}
            edges = graph.get("edges", [])

            # Also merge any delta-added links into the edge set
            if topology_delta:
                for link in topology_delta.get("add_links", []):
                    edges.append(link)

            # Find all neighbors directly connected to node_id
            neighbors = set()
            for edge in edges:
                src = edge.get("source") or edge.get("src")
                dst = edge.get("target") or edge.get("dst")
                if src == node_id and dst:
                    neighbors.add(dst)
                elif dst == node_id and src:
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

            # Sort by severity
            sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            impacted.sort(key=lambda x: sev_order.get(x["severity"], 9))

    except Exception:
        pass

    total = len(impacted)
    critical = sum(1 for i in impacted if i.get("severity") == "critical")
    slo_risk = "High" if total > 5 else ("Medium" if total > 2 else "Low")

    return {
        "node_id": node_id,
        "impacted_count": total,
        "critical_path_count": critical,
        "slo_risk": slo_risk,
        "impacted_systems": impacted,
    }
