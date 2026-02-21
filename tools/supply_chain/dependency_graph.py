#!/usr/bin/env python3
# CUI // SP-CTI
"""Supply Chain Dependency Graph — build and query dependency graphs using SQL adjacency list.

Provides functions to add vendors and dependencies, build adjacency-list graphs,
trace upstream/downstream relationships via iterative BFS, propagate impact
(severity decays 0.8 per hop), and identify critical-path components.

Tables used:
  - supply_chain_vendors   (id TEXT PK, project_id, vendor_name, vendor_type, ...)
  - supply_chain_dependencies (id INTEGER PK AUTOINCREMENT, project_id, source_type,
        source_id, target_type, target_id, dependency_type, criticality, isa_id, ...)

CLI:
  python tools/supply_chain/dependency_graph.py --project-id <id> --add-vendor ...
  python tools/supply_chain/dependency_graph.py --project-id <id> --add-dep ...
  python tools/supply_chain/dependency_graph.py --project-id <id> --build-graph --json
  python tools/supply_chain/dependency_graph.py --project-id <id> --upstream "comp" --json
  python tools/supply_chain/dependency_graph.py --project-id <id> --downstream "comp" --json
  python tools/supply_chain/dependency_graph.py --project-id <id> --impact "comp" ...
  python tools/supply_chain/dependency_graph.py --project-id <id> --critical-path --json
"""

import argparse
import json
import sqlite3
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Valid enum values matching the DB CHECK constraints
VENDOR_TYPES = ("cots", "gots", "oss", "saas", "paas", "iaas", "contractor", "subcontractor")
RISK_TIERS = ("low", "moderate", "high", "critical")
SECTION_889 = ("compliant", "under_review", "prohibited", "exempt")
SOURCE_TARGET_TYPES = ("project", "system", "component", "vendor", "package")
DEPENDENCY_TYPES = ("depends_on", "supplies", "integrates_with",
                    "data_flows_to", "inherits_ato", "shares_boundary")
CRITICALITY_LEVELS = ("critical", "high", "medium", "low")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Return a sqlite3 connection with Row factory."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _log_audit(conn, project_id, event_type, action, details):
    """Append-only audit trail entry."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, event_type, "icdev-supply-chain-agent", action,
             json.dumps(details) if isinstance(details, dict) else str(details),
             "CUI"),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: audit log failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def add_vendor(project_id, vendor_name, vendor_type, country_of_origin,
               scrm_risk_tier="moderate", section_889_status="compliant",
               db_path=None):
    """Insert a vendor into supply_chain_vendors.

    Args:
        project_id: Project identifier.
        vendor_name: Human-readable vendor name.
        vendor_type: One of VENDOR_TYPES.
        country_of_origin: ISO country code or name.
        scrm_risk_tier: low / moderate / high / critical.
        section_889_status: compliant / under_review / prohibited / exempt.
        db_path: Optional override for the database path.

    Returns:
        dict with vendor_id, vendor_name, scrm_risk_tier.
    """
    if vendor_type not in VENDOR_TYPES:
        raise ValueError(f"vendor_type must be one of {VENDOR_TYPES}, got '{vendor_type}'")
    if scrm_risk_tier not in RISK_TIERS:
        raise ValueError(f"scrm_risk_tier must be one of {RISK_TIERS}, got '{scrm_risk_tier}'")
    if section_889_status not in SECTION_889:
        raise ValueError(f"section_889_status must be one of {SECTION_889}, got '{section_889_status}'")

    vendor_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO supply_chain_vendors
               (id, project_id, vendor_name, vendor_type, country_of_origin,
                scrm_risk_tier, section_889_status, last_assessed, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (vendor_id, project_id, vendor_name, vendor_type,
             country_of_origin, scrm_risk_tier, section_889_status,
             now, now, now),
        )
        conn.commit()
        _log_audit(conn, project_id, "supply_chain_risk_escalated",
                   f"Added vendor: {vendor_name}",
                   {"vendor_id": vendor_id, "vendor_type": vendor_type,
                    "risk_tier": scrm_risk_tier})
        return {
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "scrm_risk_tier": scrm_risk_tier,
        }
    finally:
        conn.close()


def add_dependency(project_id, source_component, target_component,
                   dependency_type, criticality, vendor_id=None,
                   source_type="component", target_type="component",
                   db_path=None):
    """Insert a dependency edge into supply_chain_dependencies.

    The adjacency list uses (source_type, source_id) -> (target_type, target_id).
    By default both sides are 'component'.

    Returns:
        dict with dependency_id, source, target, criticality.
    """
    if dependency_type not in DEPENDENCY_TYPES:
        raise ValueError(f"dependency_type must be one of {DEPENDENCY_TYPES}, got '{dependency_type}'")
    if criticality not in CRITICALITY_LEVELS:
        raise ValueError(f"criticality must be one of {CRITICALITY_LEVELS}, got '{criticality}'")
    if source_type not in SOURCE_TARGET_TYPES:
        raise ValueError(f"source_type must be one of {SOURCE_TARGET_TYPES}")
    if target_type not in SOURCE_TARGET_TYPES:
        raise ValueError(f"target_type must be one of {SOURCE_TARGET_TYPES}")

    conn = _get_connection(db_path)
    try:
        # metadata stores optional vendor link
        metadata = json.dumps({"vendor_id": vendor_id}) if vendor_id else None
        conn.execute(
            """INSERT INTO supply_chain_dependencies
               (project_id, source_type, source_id, target_type, target_id,
                dependency_type, criticality, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, source_type, source_component,
             target_type, target_component, dependency_type,
             criticality, metadata, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        dep_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _log_audit(conn, project_id, "supply_chain_risk_escalated",
                   f"Added dependency: {source_component} -> {target_component}",
                   {"dependency_id": dep_id, "type": dependency_type,
                    "criticality": criticality})
        return {
            "dependency_id": dep_id,
            "source": source_component,
            "target": target_component,
            "criticality": criticality,
        }
    finally:
        conn.close()


def build_graph(project_id, db_path=None):
    """Build an adjacency-list graph from all dependencies for a project.

    Returns:
        dict with project_id, nodes, edges, stats.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT source_type, source_id, target_type, target_id,
                      dependency_type, criticality
               FROM supply_chain_dependencies
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        nodes_set = set()
        edges = []
        critical_count = 0
        for r in rows:
            src = f"{r['source_type']}:{r['source_id']}"
            tgt = f"{r['target_type']}:{r['target_id']}"
            nodes_set.add(src)
            nodes_set.add(tgt)
            edges.append({
                "source": src,
                "target": tgt,
                "dependency_type": r["dependency_type"],
                "criticality": r["criticality"],
            })
            if r["criticality"] == "critical":
                critical_count += 1

        nodes = sorted(nodes_set)
        return {
            "project_id": project_id,
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "critical_paths": critical_count,
            },
        }
    finally:
        conn.close()


def _bfs(project_id, component, direction, db_path=None):
    """Iterative BFS over the dependency graph.

    Args:
        direction: 'upstream' follows edges where component is the *source*
                   (things it depends on), 'downstream' follows edges where
                   component is the *target* (things that depend on it).
    Returns:
        (visited_list, max_depth, critical_count)
    """
    conn = _get_connection(db_path)
    try:
        # Pre-load all edges for the project into adjacency maps
        rows = conn.execute(
            """SELECT source_type, source_id, target_type, target_id, criticality
               FROM supply_chain_dependencies
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        # Build adjacency lists
        # "upstream" of X: X depends_on Y  =>  edge X->Y  => follow source->target
        # "downstream" of X: Y depends_on X => edge Y->X  => follow target->source
        forward = {}   # source -> [target, ...]
        backward = {}  # target -> [source, ...]
        crit_edges = set()

        for r in rows:
            src = f"{r['source_type']}:{r['source_id']}"
            tgt = f"{r['target_type']}:{r['target_id']}"
            forward.setdefault(src, []).append(tgt)
            backward.setdefault(tgt, []).append(src)
            if r["criticality"] == "critical":
                crit_edges.add((src, tgt))

        adj = forward if direction == "upstream" else backward
        start_key = f"component:{component}"

        visited = []
        seen = {start_key}
        queue = deque()
        queue.append((start_key, 0))
        max_depth = 0
        critical_count = 0

        while queue:
            current, depth = queue.popleft()
            for neighbor in adj.get(current, []):
                if neighbor not in seen:
                    seen.add(neighbor)
                    max_depth = max(max_depth, depth + 1)
                    is_crit = ((current, neighbor) in crit_edges
                               or (neighbor, current) in crit_edges)
                    if is_crit:
                        critical_count += 1
                    visited.append({
                        "component": neighbor,
                        "depth": depth + 1,
                        "critical": is_crit,
                    })
                    queue.append((neighbor, depth + 1))

        return visited, max_depth, critical_count
    finally:
        conn.close()


def get_upstream(project_id, component, db_path=None):
    """Find all upstream dependencies (things this component depends on).

    Uses iterative BFS.

    Returns:
        dict with component, upstream list, depth, critical_count.
    """
    visited, max_depth, critical_count = _bfs(
        project_id, component, "upstream", db_path)
    return {
        "component": component,
        "upstream": visited,
        "depth": max_depth,
        "critical_count": critical_count,
    }


def get_downstream(project_id, component, db_path=None):
    """Find all downstream dependents (things that depend on this component).

    Uses iterative BFS.

    Returns:
        dict with component, downstream list, depth, impact_radius.
    """
    visited, max_depth, _ = _bfs(
        project_id, component, "downstream", db_path)
    return {
        "component": component,
        "downstream": visited,
        "depth": max_depth,
        "impact_radius": len(visited),
    }


def propagate_impact(project_id, component, impact_type, severity,
                     db_path=None):
    """Trace downstream impact of a change/vulnerability in a component.

    Severity decays by factor 0.8 per hop. Returns blast radius and
    per-component impact details.

    Args:
        impact_type: e.g. 'vulnerability', 'outage', 'deprecation'.
        severity: 'critical' / 'high' / 'medium' / 'low'.

    Returns:
        dict with source_component, impact_type, severity, affected_components,
        blast_radius, recommendations.
    """
    severity_values = {"critical": 10.0, "high": 7.5, "medium": 5.0, "low": 2.5}
    base_score = severity_values.get(severity, 5.0)
    decay = 0.8

    conn = _get_connection(db_path)
    try:
        # Load all edges
        rows = conn.execute(
            """SELECT source_type, source_id, target_type, target_id, criticality
               FROM supply_chain_dependencies
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        backward = {}  # target -> [source, ...]
        for r in rows:
            src = f"{r['source_type']}:{r['source_id']}"
            tgt = f"{r['target_type']}:{r['target_id']}"
            backward.setdefault(tgt, []).append(src)

        start_key = f"component:{component}"
        affected = []
        seen = {start_key}
        queue = deque()
        queue.append((start_key, 0, base_score))

        while queue:
            current, depth, current_score = queue.popleft()
            for neighbor in backward.get(current, []):
                if neighbor not in seen:
                    seen.add(neighbor)
                    decayed = round(current_score * decay, 2)
                    sev_label = ("critical" if decayed >= 8.0
                                 else "high" if decayed >= 5.5
                                 else "medium" if decayed >= 3.0
                                 else "low")
                    affected.append({
                        "component": neighbor,
                        "hop": depth + 1,
                        "propagated_score": decayed,
                        "propagated_severity": sev_label,
                    })
                    queue.append((neighbor, depth + 1, decayed))

        # Recommendations
        recommendations = []
        if len(affected) > 5:
            recommendations.append(
                "High blast radius detected. Consider isolating this component "
                "behind an abstraction layer.")
        if severity == "critical":
            recommendations.append(
                "Critical severity: initiate incident response within 24 hours.")
        if any(a["propagated_severity"] == "critical" for a in affected):
            recommendations.append(
                "Critical propagated impact reaches downstream components. "
                "Assess downstream ISAs and ATO boundaries.")
        if not recommendations:
            recommendations.append("Impact contained. Monitor for changes.")

        result = {
            "source_component": component,
            "impact_type": impact_type,
            "severity": severity,
            "affected_components": affected,
            "blast_radius": len(affected),
            "recommendations": recommendations,
        }

        _log_audit(conn, project_id, "cve_impact_propagated",
                   f"Impact propagated from {component}",
                   {"blast_radius": len(affected), "severity": severity,
                    "impact_type": impact_type})

        return result
    finally:
        conn.close()


def get_critical_path(project_id, db_path=None):
    """Find components with the highest downstream count (most impactful if compromised).

    Returns:
        dict with project_id and sorted list of components by impact_radius.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT source_type, source_id, target_type, target_id
               FROM supply_chain_dependencies
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        # Gather all unique nodes
        nodes = set()
        backward = {}
        for r in rows:
            src = f"{r['source_type']}:{r['source_id']}"
            tgt = f"{r['target_type']}:{r['target_id']}"
            nodes.add(src)
            nodes.add(tgt)
            backward.setdefault(tgt, []).append(src)

        # For each node, BFS downstream (reverse edges) to count dependents
        results = []
        for node in nodes:
            seen = {node}
            queue = deque([node])
            count = 0
            while queue:
                current = queue.popleft()
                for neighbor in backward.get(current, []):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        count += 1
                        queue.append(neighbor)
            results.append({
                "component": node,
                "downstream_count": count,
                "impact_radius": count,
            })

        results.sort(key=lambda x: x["impact_radius"], reverse=True)
        return {
            "project_id": project_id,
            "critical_components": results,
            "total_components": len(nodes),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Supply Chain Dependency Graph (RICOAS)")
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Vendor operations
    parser.add_argument("--add-vendor", action="store_true",
                        help="Add a new vendor")
    parser.add_argument("--vendor-name", help="Vendor display name")
    parser.add_argument("--vendor-type", choices=VENDOR_TYPES,
                        help="Vendor type")
    parser.add_argument("--country", help="Country of origin")
    parser.add_argument("--risk-tier", choices=RISK_TIERS, default="moderate",
                        help="SCRM risk tier (default: moderate)")
    parser.add_argument("--section-889", choices=SECTION_889,
                        default="compliant",
                        help="Section 889 compliance status")

    # Dependency operations
    parser.add_argument("--add-dep", action="store_true",
                        help="Add a dependency edge")
    parser.add_argument("--source", help="Source component ID")
    parser.add_argument("--target", help="Target component ID")
    parser.add_argument("--dep-type", choices=DEPENDENCY_TYPES,
                        help="Dependency type")
    parser.add_argument("--criticality", choices=CRITICALITY_LEVELS,
                        default="medium", help="Dependency criticality")
    parser.add_argument("--source-type", choices=SOURCE_TARGET_TYPES,
                        default="component", help="Source node type")
    parser.add_argument("--target-type", choices=SOURCE_TARGET_TYPES,
                        default="component", help="Target node type")
    parser.add_argument("--vendor-id", help="Associated vendor ID")

    # Query operations
    parser.add_argument("--build-graph", action="store_true",
                        help="Build full adjacency-list graph")
    parser.add_argument("--upstream", metavar="COMPONENT",
                        help="Get upstream dependencies of component")
    parser.add_argument("--downstream", metavar="COMPONENT",
                        help="Get downstream dependents of component")

    # Impact analysis
    parser.add_argument("--impact", metavar="COMPONENT",
                        help="Propagate impact from component")
    parser.add_argument("--impact-type", default="vulnerability",
                        help="Type of impact (default: vulnerability)")
    parser.add_argument("--severity", choices=("critical", "high", "medium", "low"),
                        default="high", help="Impact severity (default: high)")

    parser.add_argument("--critical-path", action="store_true",
                        help="Find critical-path components by impact radius")

    args = parser.parse_args()

    try:
        result = None

        if args.add_vendor:
            if not all([args.vendor_name, args.vendor_type, args.country]):
                parser.error("--add-vendor requires --vendor-name, --vendor-type, --country")
            result = add_vendor(
                args.project_id, args.vendor_name, args.vendor_type,
                args.country, args.risk_tier, args.section_889)

        elif args.add_dep:
            if not all([args.source, args.target, args.dep_type]):
                parser.error("--add-dep requires --source, --target, --dep-type")
            result = add_dependency(
                args.project_id, args.source, args.target,
                args.dep_type, args.criticality, args.vendor_id,
                args.source_type, args.target_type)

        elif args.build_graph:
            result = build_graph(args.project_id)

        elif args.upstream:
            result = get_upstream(args.project_id, args.upstream)

        elif args.downstream:
            result = get_downstream(args.project_id, args.downstream)

        elif args.impact:
            result = propagate_impact(
                args.project_id, args.impact, args.impact_type, args.severity)

        elif args.critical_path:
            result = get_critical_path(args.project_id)

        else:
            parser.print_help()
            sys.exit(0)

        if result is not None:
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                _print_human(result)

    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_human(data):
    """Pretty-print result dict for human consumption."""
    if "vendor_id" in data and "vendor_name" in data:
        print(f"Vendor added: {data['vendor_name']} [{data['vendor_id']}]")
        print(f"  Risk tier: {data['scrm_risk_tier']}")
    elif "dependency_id" in data:
        print(f"Dependency added: {data['source']} -> {data['target']}")
        print(f"  ID: {data['dependency_id']}  Criticality: {data['criticality']}")
    elif "edges" in data and "nodes" in data:
        stats = data.get("stats", {})
        print(f"Dependency Graph for {data['project_id']}")
        print(f"  Nodes: {stats.get('total_nodes', 0)}")
        print(f"  Edges: {stats.get('total_edges', 0)}")
        print(f"  Critical paths: {stats.get('critical_paths', 0)}")
        for e in data["edges"]:
            print(f"    {e['source']} --[{e['dependency_type']}]--> {e['target']}"
                  f"  ({e['criticality']})")
    elif "upstream" in data:
        print(f"Upstream of {data['component']}  (depth={data['depth']}, "
              f"critical={data['critical_count']})")
        for u in data["upstream"]:
            indent = "  " * u["depth"]
            crit = " [CRITICAL]" if u.get("critical") else ""
            print(f"  {indent}{u['component']} (hop {u['depth']}){crit}")
    elif "downstream" in data:
        print(f"Downstream of {data['component']}  (depth={data['depth']}, "
              f"impact_radius={data['impact_radius']})")
        for d in data["downstream"]:
            indent = "  " * d["depth"]
            print(f"  {indent}{d['component']} (hop {d['depth']})")
    elif "blast_radius" in data and "affected_components" in data:
        print(f"Impact Analysis: {data['source_component']}")
        print(f"  Type: {data['impact_type']}  Severity: {data['severity']}")
        print(f"  Blast radius: {data['blast_radius']}")
        for a in data["affected_components"]:
            print(f"    hop {a['hop']}: {a['component']} "
                  f"(score={a['propagated_score']}, {a['propagated_severity']})")
        print("  Recommendations:")
        for r in data.get("recommendations", []):
            print(f"    - {r}")
    elif "critical_components" in data:
        print(f"Critical Path Analysis: {data['project_id']}  "
              f"({data['total_components']} components)")
        for c in data["critical_components"]:
            print(f"  {c['component']}  downstream={c['downstream_count']}")
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
# CUI // SP-CTI
