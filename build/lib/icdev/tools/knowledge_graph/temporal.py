#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Knowledge Graph Temporal Reasoning — date-range queries, evolution, staleness.

Provides temporal analysis over kg_nodes and kg_edges tables:
    - query_time_range: find nodes/edges created within a date window
    - graph_evolution: time-series of graph growth (day/week/month)
    - recent_changes: quick summary of last N days of activity
    - find_stale_entities: flag nodes not updated in N days
    - temporal_diff: compare graph state between two dates

All operations are read-only.  No external dependencies.

Usage:
    python tools/knowledge_graph/temporal.py --range --start 2026-03-01 --end 2026-03-21 --json
    python tools/knowledge_graph/temporal.py --evolution --graph-id <id> --interval day --json
    python tools/knowledge_graph/temporal.py --recent --days 7 --json
    python tools/knowledge_graph/temporal.py --stale --stale-days 90 --json
    python tools/knowledge_graph/temporal.py --diff --graph-id <id> --date-a 2026-03-01 --date-b 2026-03-15 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

ICDEV_DB = BASE_DIR / "data" / "icdev.db"
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(ICDEV_DB)))


# =========================================================================
# DATABASE HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    return conn


def _ensure_tables(conn):
    """Create kg_* tables if they don't exist (for standalone usage)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kg_graphs (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            entity_count INTEGER DEFAULT 0,
            edge_count INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            label TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            embedding BLOB,
            centrality REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS kg_edges (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            source_id TEXT NOT NULL REFERENCES kg_nodes(id),
            target_id TEXT NOT NULL REFERENCES kg_nodes(id),
            relationship TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


# =========================================================================
# DATE HELPERS
# =========================================================================
def _normalize_date(date_str: str) -> str:
    """Normalize a date string to 'YYYY-MM-DD HH:MM:SS' for SQL comparison.

    Accepts:
        YYYY-MM-DD          -> YYYY-MM-DD 00:00:00
        YYYY-MM-DDTHH:MM:SS -> YYYY-MM-DD HH:MM:SS
        YYYY-MM-DD HH:MM:SS -> as-is
    """
    if date_str is None:
        return None
    date_str = date_str.strip().replace("T", " ")
    if len(date_str) == 10:  # YYYY-MM-DD
        return date_str + " 00:00:00"
    return date_str


def _ts_to_str(ts) -> str:
    """Coerce a timestamp value (str or datetime) to 'YYYY-MM-DD HH:MM:SS'."""
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


def _to_iso(ts) -> str:
    """Convert a SQLite timestamp (str or datetime) to ISO-8601 format."""
    if ts is None:
        return None
    s = _ts_to_str(ts)
    return s.strip().replace(" ", "T")


def _date_key(ts_val, interval: str) -> str:
    """Extract a grouping key from a timestamp value (str or datetime).

    interval: 'day' -> YYYY-MM-DD, 'week' -> YYYY-WNN, 'month' -> YYYY-MM
    """
    ts = _ts_to_str(ts_val).strip()[:10]  # YYYY-MM-DD
    if interval == "day":
        return ts
    if interval == "week":
        dt = datetime.strptime(ts, "%Y-%m-%d")
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if interval == "month":
        return ts[:7]  # YYYY-MM
    return ts


# =========================================================================
# CORE FUNCTIONS
# =========================================================================


def query_time_range(
    graph_id: str = None,
    start_date: str = None,
    end_date: str = None,
    entity_type: str = None,
    db_path=None,
) -> Dict[str, Any]:
    """Find nodes created within a date range, with their edges, grouped by date.

    Args:
        graph_id: Optional graph filter.
        start_date: Inclusive lower bound (ISO-8601).
        end_date: Exclusive upper bound (ISO-8601).
        entity_type: Optional entity_type filter.
        db_path: Override database path.

    Returns:
        Dict with status, by_date grouping, total counts.
    """
    conn = _get_db(db_path)
    _ensure_tables(conn)

    # Build node query
    conditions: List[str] = []
    params: List[Any] = []
    if graph_id:
        conditions.append("n.graph_id = ?")
        params.append(graph_id)
    if start_date:
        conditions.append("n.created_at >= ?")
        params.append(_normalize_date(start_date))
    if end_date:
        conditions.append("n.created_at < ?")
        params.append(_normalize_date(end_date))
    if entity_type:
        conditions.append("n.entity_type = ?")
        params.append(entity_type)

    where = " AND ".join(conditions) if conditions else "1=1"

    nodes = conn.execute(
        f"SELECT n.id, n.graph_id, n.label, n.entity_type, n.centrality, "  # nosec B608 -- table/column names are internal constants, not user input
        f"n.created_at FROM kg_nodes n WHERE {where} ORDER BY n.created_at",
        params,
    ).fetchall()

    node_ids = {row["id"] for row in nodes}

    # Fetch edges connected to these nodes
    edges = []
    if node_ids:
        placeholders = ",".join("?" for _ in node_ids)
        edges = conn.execute(
            f"SELECT id, graph_id, source_id, target_id, relationship, "  # nosec B608 -- table/column names are internal constants, not user input
            f"weight, created_at FROM kg_edges "
            f"WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            list(node_ids) + list(node_ids),
        ).fetchall()

    # Group by date (YYYY-MM-DD)
    by_date: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"nodes": [], "edges": []})
    for n in nodes:
        day = _ts_to_str(n["created_at"])[:10] or "unknown"
        by_date[day]["nodes"].append(
            {
                "id": n["id"],
                "graph_id": n["graph_id"],
                "label": n["label"],
                "entity_type": n["entity_type"],
                "centrality": n["centrality"],
                "created_at": _to_iso(n["created_at"]),
            }
        )
    for e in edges:
        day = _ts_to_str(e["created_at"])[:10] or "unknown"
        by_date[day]["edges"].append(
            {
                "id": e["id"],
                "graph_id": e["graph_id"],
                "source_id": e["source_id"],
                "target_id": e["target_id"],
                "relationship": e["relationship"],
                "weight": e["weight"],
                "created_at": _to_iso(e["created_at"]),
            }
        )

    conn.close()

    return {
        "status": "ok",
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "date_count": len(by_date),
        "by_date": dict(by_date),
        "filters": {
            "graph_id": graph_id,
            "start_date": start_date,
            "end_date": end_date,
            "entity_type": entity_type,
        },
    }


def graph_evolution(
    graph_id: str,
    interval: str = "day",
    limit: int = 30,
    db_path=None,
) -> Dict[str, Any]:
    """Show how a graph grew over time as a time series.

    Args:
        graph_id: The graph to analyze.
        interval: Grouping interval — 'day', 'week', or 'month'.
        limit: Maximum number of intervals to return.
        db_path: Override database path.

    Returns:
        Dict with time_series list of {date, nodes_added, edges_added,
        cumulative_nodes, cumulative_edges}.
    """
    if interval not in ("day", "week", "month"):
        return {"status": "error", "error": f"Invalid interval: {interval}. Use day/week/month."}

    conn = _get_db(db_path)
    _ensure_tables(conn)

    nodes = conn.execute(
        "SELECT created_at FROM kg_nodes WHERE graph_id = ? ORDER BY created_at",
        (graph_id,),
    ).fetchall()
    edges = conn.execute(
        "SELECT created_at FROM kg_edges WHERE graph_id = ? ORDER BY created_at",
        (graph_id,),
    ).fetchall()
    conn.close()

    # Count per interval
    node_counts: Dict[str, int] = defaultdict(int)
    edge_counts: Dict[str, int] = defaultdict(int)

    for row in nodes:
        key = _date_key(row["created_at"], interval)
        node_counts[key] += 1
    for row in edges:
        key = _date_key(row["created_at"], interval)
        edge_counts[key] += 1

    all_keys = sorted(set(list(node_counts.keys()) + list(edge_counts.keys())))
    # Apply limit (most recent N intervals)
    if len(all_keys) > limit:
        all_keys = all_keys[-limit:]

    cum_nodes = 0
    cum_edges = 0

    # Count cumulative up to the start of our window
    if len(all_keys) > 0:
        first_key = all_keys[0]
        all_sorted_node_keys = sorted(node_counts.keys())
        all_sorted_edge_keys = sorted(edge_counts.keys())
        for k in all_sorted_node_keys:
            if k < first_key:
                cum_nodes += node_counts[k]
        for k in all_sorted_edge_keys:
            if k < first_key:
                cum_edges += edge_counts[k]

    time_series: List[Dict[str, Any]] = []
    for key in all_keys:
        na = node_counts.get(key, 0)
        ea = edge_counts.get(key, 0)
        cum_nodes += na
        cum_edges += ea
        time_series.append(
            {
                "date": key,
                "nodes_added": na,
                "edges_added": ea,
                "cumulative_nodes": cum_nodes,
                "cumulative_edges": cum_edges,
            }
        )

    return {
        "status": "ok",
        "graph_id": graph_id,
        "interval": interval,
        "total_intervals": len(time_series),
        "time_series": time_series,
    }


def recent_changes(
    graph_id: str = None,
    days: int = 7,
    db_path=None,
) -> Dict[str, Any]:
    """Quick summary of recent graph activity in the last N days.

    Args:
        graph_id: Optional graph filter.
        days: Look-back window in days (default 7).
        db_path: Override database path.

    Returns:
        Dict with nodes/edges added, grouped by entity_type.
    """
    conn = _get_db(db_path)
    _ensure_tables(conn)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    # Nodes
    node_where = "n.created_at >= ?"
    node_params: List[Any] = [cutoff]
    if graph_id:
        node_where += " AND n.graph_id = ?"
        node_params.append(graph_id)

    nodes = conn.execute(
        f"SELECT n.id, n.graph_id, n.label, n.entity_type, n.centrality, "  # nosec B608 -- table/column names are internal constants, not user input
        f"n.created_at FROM kg_nodes n WHERE {node_where} ORDER BY n.created_at DESC",
        node_params,
    ).fetchall()

    # Edges
    edge_where = "e.created_at >= ?"
    edge_params: List[Any] = [cutoff]
    if graph_id:
        edge_where += " AND e.graph_id = ?"
        edge_params.append(graph_id)

    edges = conn.execute(
        f"SELECT e.id, e.graph_id, e.source_id, e.target_id, e.relationship, "  # nosec B608 -- table/column names are internal constants, not user input
        f"e.weight, e.created_at FROM kg_edges e WHERE {edge_where} "
        f"ORDER BY e.created_at DESC",
        edge_params,
    ).fetchall()

    conn.close()

    # Group by entity_type
    by_type: Dict[str, int] = defaultdict(int)
    for n in nodes:
        by_type[n["entity_type"]] += 1

    # Group edges by relationship
    by_relationship: Dict[str, int] = defaultdict(int)
    for e in edges:
        by_relationship[e["relationship"]] += 1

    return {
        "status": "ok",
        "days": days,
        "graph_id": graph_id,
        "cutoff": _to_iso(cutoff),
        "total_nodes_added": len(nodes),
        "total_edges_added": len(edges),
        "nodes_by_entity_type": dict(by_type),
        "edges_by_relationship": dict(by_relationship),
        "nodes": [
            {
                "id": n["id"],
                "graph_id": n["graph_id"],
                "label": n["label"],
                "entity_type": n["entity_type"],
                "created_at": _to_iso(n["created_at"]),
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": e["id"],
                "graph_id": e["graph_id"],
                "source_id": e["source_id"],
                "target_id": e["target_id"],
                "relationship": e["relationship"],
                "created_at": _to_iso(e["created_at"]),
            }
            for e in edges
        ],
    }


def find_stale_entities(
    graph_id: str = None,
    stale_days: int = 90,
    db_path=None,
) -> Dict[str, Any]:
    """Find nodes not updated in the last N days.

    Args:
        graph_id: Optional graph filter.
        stale_days: Age threshold in days (default 90).
        db_path: Override database path.

    Returns:
        Dict with stale nodes sorted oldest first, with age_days.
    """
    conn = _get_db(db_path)
    _ensure_tables(conn)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).strftime("%Y-%m-%d %H:%M:%S")

    where = "n.created_at < ?"
    params: List[Any] = [cutoff]
    if graph_id:
        where += " AND n.graph_id = ?"
        params.append(graph_id)

    nodes = conn.execute(
        f"SELECT n.id, n.graph_id, n.label, n.entity_type, n.centrality, "  # nosec B608 -- table/column names are internal constants, not user input
        f"n.created_at FROM kg_nodes n WHERE {where} ORDER BY n.created_at ASC",
        params,
    ).fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    stale_nodes = []
    by_type: Dict[str, int] = defaultdict(int)

    for n in nodes:
        ts_str = _ts_to_str(n["created_at"]).strip().replace("T", " ")
        try:
            created = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            created = now  # skip unparseable
        age = (now - created).days
        stale_nodes.append(
            {
                "id": n["id"],
                "graph_id": n["graph_id"],
                "label": n["label"],
                "entity_type": n["entity_type"],
                "centrality": n["centrality"],
                "created_at": _to_iso(n["created_at"]),
                "age_days": age,
            }
        )
        by_type[n["entity_type"]] += 1

    return {
        "status": "ok",
        "stale_days_threshold": stale_days,
        "graph_id": graph_id,
        "cutoff": _to_iso(cutoff),
        "total_stale": len(stale_nodes),
        "stale_by_entity_type": dict(by_type),
        "stale_nodes": stale_nodes,
    }


def temporal_diff(
    graph_id: str,
    date_a: str,
    date_b: str,
    db_path=None,
) -> Dict[str, Any]:
    """Compare graph state between two dates — what was added between date_a and date_b.

    Args:
        graph_id: The graph to compare.
        date_a: Earlier date (inclusive, ISO-8601).
        date_b: Later date (exclusive, ISO-8601).
        db_path: Override database path.

    Returns:
        Dict with nodes_added, edges_added, entity_types breakdown.
    """
    norm_a = _normalize_date(date_a)
    norm_b = _normalize_date(date_b)

    conn = _get_db(db_path)
    _ensure_tables(conn)

    nodes = conn.execute(
        "SELECT id, label, entity_type, centrality, created_at "
        "FROM kg_nodes WHERE graph_id = ? AND created_at >= ? AND created_at < ? "
        "ORDER BY created_at",
        (graph_id, norm_a, norm_b),
    ).fetchall()

    edges = conn.execute(
        "SELECT id, source_id, target_id, relationship, weight, created_at "
        "FROM kg_edges WHERE graph_id = ? AND created_at >= ? AND created_at < ? "
        "ORDER BY created_at",
        (graph_id, norm_a, norm_b),
    ).fetchall()

    # Count at date_a (everything before date_a)
    count_before_nodes = conn.execute(
        "SELECT COUNT(*) as cnt FROM kg_nodes WHERE graph_id = ? AND created_at < ?",
        (graph_id, norm_a),
    ).fetchone()["cnt"]
    count_before_edges = conn.execute(
        "SELECT COUNT(*) as cnt FROM kg_edges WHERE graph_id = ? AND created_at < ?",
        (graph_id, norm_a),
    ).fetchone()["cnt"]

    conn.close()

    entity_types: Dict[str, int] = defaultdict(int)
    for n in nodes:
        entity_types[n["entity_type"]] += 1

    relationship_types: Dict[str, int] = defaultdict(int)
    for e in edges:
        relationship_types[e["relationship"]] += 1

    return {
        "status": "ok",
        "graph_id": graph_id,
        "date_a": date_a,
        "date_b": date_b,
        "graph_at_date_a": {
            "nodes": count_before_nodes,
            "edges": count_before_edges,
        },
        "graph_at_date_b": {
            "nodes": count_before_nodes + len(nodes),
            "edges": count_before_edges + len(edges),
        },
        "nodes_added": len(nodes),
        "edges_added": len(edges),
        "entity_types_added": dict(entity_types),
        "relationship_types_added": dict(relationship_types),
        "nodes": [
            {
                "id": n["id"],
                "label": n["label"],
                "entity_type": n["entity_type"],
                "created_at": _to_iso(n["created_at"]),
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": e["id"],
                "source_id": e["source_id"],
                "target_id": e["target_id"],
                "relationship": e["relationship"],
                "created_at": _to_iso(e["created_at"]),
            }
            for e in edges
        ],
    }


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(description="Knowledge Graph Temporal Reasoning")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    # Mode flags
    parser.add_argument("--range", action="store_true", help="Query time range")
    parser.add_argument("--evolution", action="store_true", help="Graph evolution")
    parser.add_argument("--recent", action="store_true", help="Recent changes")
    parser.add_argument("--stale", action="store_true", help="Find stale entities")
    parser.add_argument("--diff", action="store_true", help="Temporal diff")

    # Shared parameters
    parser.add_argument("--graph-id", help="Graph ID filter")
    parser.add_argument("--start", help="Start date (ISO-8601)")
    parser.add_argument("--end", help="End date (ISO-8601)")
    parser.add_argument("--entity-type", help="Entity type filter")
    parser.add_argument(
        "--interval", default="day", choices=["day", "week", "month"], help="Grouping interval for evolution"
    )
    parser.add_argument("--limit", type=int, default=30, help="Max intervals for evolution")
    parser.add_argument("--days", type=int, default=7, help="Look-back days for recent changes")
    parser.add_argument("--stale-days", type=int, default=90, help="Staleness threshold in days")
    parser.add_argument("--date-a", help="Earlier date for diff")
    parser.add_argument("--date-b", help="Later date for diff")

    args = parser.parse_args()

    try:
        if args.range:
            result = query_time_range(
                graph_id=args.graph_id,
                start_date=args.start,
                end_date=args.end,
                entity_type=args.entity_type,
                db_path=args.db_path,
            )
        elif args.evolution:
            if not args.graph_id:
                result = {"status": "error", "error": "--graph-id is required for --evolution"}
            else:
                result = graph_evolution(
                    graph_id=args.graph_id,
                    interval=args.interval,
                    limit=args.limit,
                    db_path=args.db_path,
                )
        elif args.recent:
            result = recent_changes(
                graph_id=args.graph_id,
                days=args.days,
                db_path=args.db_path,
            )
        elif args.stale:
            result = find_stale_entities(
                graph_id=args.graph_id,
                stale_days=args.stale_days,
                db_path=args.db_path,
            )
        elif args.diff:
            if not args.graph_id or not args.date_a or not args.date_b:
                result = {"status": "error", "error": "--graph-id, --date-a, and --date-b are required for --diff"}
            else:
                result = temporal_diff(
                    graph_id=args.graph_id,
                    date_a=args.date_a,
                    date_b=args.date_b,
                    db_path=args.db_path,
                )
        else:
            result = {"status": "error", "error": "Specify one of: --range, --evolution, --recent, --stale, --diff"}

    except FileNotFoundError as exc:
        result = {"status": "error", "error": str(exc)}
    except Exception as exc:
        result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result.get("status") == "error":
            print(f"ERROR: {result['error']}")
            sys.exit(1)
        # Human-readable summary
        if args.range:
            print(f"Nodes: {result['total_nodes']}  Edges: {result['total_edges']}  Dates: {result['date_count']}")
            for day, data in sorted(result.get("by_date", {}).items()):
                print(f"  {day}: {len(data['nodes'])} nodes, {len(data['edges'])} edges")
        elif args.evolution:
            print(f"Graph {result['graph_id']} — {result['total_intervals']} intervals ({result['interval']})")
            for ts in result.get("time_series", []):
                print(
                    f"  {ts['date']}: +{ts['nodes_added']}N +{ts['edges_added']}E "
                    f"(cum {ts['cumulative_nodes']}N {ts['cumulative_edges']}E)"
                )
        elif args.recent:
            print(f"Recent {result['days']}d: {result['total_nodes_added']} nodes, {result['total_edges_added']} edges")
            for etype, cnt in sorted(result.get("nodes_by_entity_type", {}).items()):
                print(f"  {etype}: {cnt}")
        elif args.stale:
            print(f"Stale (>{result['stale_days_threshold']}d): {result['total_stale']} nodes")
            for etype, cnt in sorted(result.get("stale_by_entity_type", {}).items()):
                print(f"  {etype}: {cnt}")
        elif args.diff:
            print(f"Diff {result['date_a']} → {result['date_b']}:")
            print(f"  Before: {result['graph_at_date_a']['nodes']}N {result['graph_at_date_a']['edges']}E")
            print(f"  After:  {result['graph_at_date_b']['nodes']}N {result['graph_at_date_b']['edges']}E")
            print(f"  Added:  {result['nodes_added']}N {result['edges_added']}E")


if __name__ == "__main__":
    main()
