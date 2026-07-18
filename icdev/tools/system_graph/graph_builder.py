# CUI // SP-CTI
"""
Federates ICDEV's five graph sources into a single Sigma.js-ready graph payload.

Sources:
  1. awareness_kg   — kg_nodes + kg_edges (Internal Awareness Engine)
  2. canvas_kg      — canvas_kg_nodes + canvas_kg_edges (7 canvases)
  3. kanban_deps    — kanban_tasks + kanban_task_deps
  4. goals          — goals/ directory → inferred tool edges
  5. migrations     — tools/db/migrations/ numbering order chain

Returns node list with pre-computed (x, y) positions from NetworkX spring layout
and community cluster IDs from greedy modularity communities.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import json
import math
import pathlib
import re
import time
from typing import Any

_log = get_logger(__name__)

from .constants import (
    CLUSTER_COLORS,
    LAYOUT_ITERATIONS,
    LAYOUT_K,
    MAX_FULL_NODES,
    NODE_TYPES,
)

_ROOT = pathlib.Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nid(prefix: str, name: str) -> str:
    raw = f"{prefix}::{name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _node(nid: str, label: str, ntype: str, *, source: str,
          props: dict | None = None, health: str = "unknown") -> dict:
    # Normalize any unrecognised entity type to "other"
    if ntype not in NODE_TYPES:
        ntype = "other"
    color = NODE_TYPES.get(ntype, NODE_TYPES["other"])["color"]
    return {
        "id": nid,
        "label": label,
        "type": ntype,
        "color": color,
        "source": source,
        "health": health,
        "properties": props or {},
        "x": 0.0,
        "y": 0.0,
        "cluster_id": -1,
        "cluster_color": "#78909c",
        "size": 6,
    }


def _edge(src: str, dst: str, etype: str = "related") -> dict:
    return {"source": src, "target": dst, "type": etype}


# ---------------------------------------------------------------------------
# Source 1: Internal Awareness KG
# ---------------------------------------------------------------------------

_AWARENESS_GRAPH_ID = "kg-icdev-self-awareness"


def _load_awareness_kg() -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        # Health map from awareness_component_health (latest per node)
        health_map: dict[str, str] = {}
        try:
            hrows = conn.execute(
                "SELECT DISTINCT ON (node_id) node_id, status "
                "FROM awareness_component_health "
                "ORDER BY node_id, probed_at DESC NULLS LAST"
            ).fetchall()
            for hr in hrows:
                hr = dict(hr) if hasattr(hr, "keys") else {"node_id": hr[0], "status": hr[1]}
                raw = (hr.get("status") or "").lower()
                health_map[hr["node_id"]] = "ok" if raw == "pass" else ("error" if raw == "fail" else "unknown")
        except Exception:
            # SQLite fallback (no DISTINCT ON)
            try:
                hrows = conn.execute(
                    "SELECT node_id, status FROM awareness_component_health "
                    "GROUP BY node_id"
                ).fetchall()
                for hr in hrows:
                    hr = dict(hr) if hasattr(hr, "keys") else {"node_id": hr[0], "status": hr[1]}
                    raw = (hr.get("status") or "").lower()
                    health_map[hr["node_id"]] = "ok" if raw == "pass" else ("error" if raw == "fail" else "unknown")
            except Exception:
                pass

        rows = conn.execute(
            "SELECT id, label, entity_type, properties FROM kg_nodes "
            "WHERE graph_id = %s LIMIT 5000",
            (_AWARENESS_GRAPH_ID,)
        ).fetchall()
        for r in rows:
            r = dict(r) if hasattr(r, "keys") else {
                "id": r[0], "label": r[1], "entity_type": r[2], "properties": r[3],
            }
            props: dict = {}
            if r.get("properties"):
                try:
                    props = json.loads(r["properties"]) if isinstance(r["properties"], str) else r["properties"]
                except Exception:
                    pass
            ntype = r.get("entity_type") or "other"
            health = health_map.get(r["id"], "unknown")
            nodes.append(_node(
                r["id"], r.get("label") or r["id"], ntype,
                source="awareness_kg", props=props, health=health,
            ))
        # Load all kg_edges — filter by node membership after (awareness nodes may link via any graph)
        try:
            erows = conn.execute(
                "SELECT source_id, target_id, relationship FROM kg_edges LIMIT 20000"
            ).fetchall()
            for er in erows:
                er = dict(er) if hasattr(er, "keys") else {
                    "source_id": er[0], "target_id": er[1], "relationship": er[2]
                }
                edges.append(_edge(er["source_id"], er["target_id"], er.get("relationship") or "related"))
        except Exception:
            pass
    except Exception:
        pass
    return nodes, edges


# ---------------------------------------------------------------------------
# Source 2: Canvas KG
# ---------------------------------------------------------------------------

def _load_canvas_kg() -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        # Use node_id as the graph node ID (canvas_kg_edges references node_id, not id)
        rows = conn.execute(
            "SELECT node_id, canvas, node_type, label, metadata_json FROM canvas_kg_nodes LIMIT 3000"
        ).fetchall()
        for r in rows:
            r = dict(r) if hasattr(r, "keys") else {
                "node_id": r[0], "canvas": r[1], "node_type": r[2], "label": r[3], "metadata_json": r[4]
            }
            props = {"canvas": r.get("canvas")}
            if r.get("metadata_json"):
                try:
                    props.update(json.loads(r["metadata_json"]))
                except Exception:
                    pass
            ntype = r.get("node_type") or "canvas_module"
            nid = r["node_id"] or r.get("id") or "?"
            nodes.append(_node(nid, r.get("label") or nid, ntype,
                               source="canvas_kg", props=props))
        erows = conn.execute(
            "SELECT source_id, target_id, edge_type FROM canvas_kg_edges LIMIT 5000"
        ).fetchall()
        for er in erows:
            er = dict(er) if hasattr(er, "keys") else {
                "source_id": er[0], "target_id": er[1], "edge_type": er[2]
            }
            edges.append(_edge(er["source_id"], er["target_id"], er.get("edge_type") or "related"))
    except Exception:
        pass
    return nodes, edges


# ---------------------------------------------------------------------------
# Source 3: Kanban DAG
# ---------------------------------------------------------------------------

def _load_kanban_deps() -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, title, status, task_type FROM kanban_tasks WHERE status != 'archived' LIMIT 1000"
        ).fetchall()
        task_ids = set()
        for r in rows:
            r = dict(r) if hasattr(r, "keys") else {
                "id": r[0], "title": r[1], "status": r[2], "task_type": r[3]
            }
            health = {"done": "ok", "in_progress": "warn",
                      "token_exhausted": "error", "failed": "error"}.get(r.get("status") or "", "unknown")
            nid = _nid("kanban", r["id"])
            nodes.append(_node(nid, r.get("title") or r["id"][:20], "kanban_task",
                               source="kanban_deps", props={"status": r.get("status"),
                               "task_type": r.get("task_type"), "task_id": r["id"]},
                               health=health))
            task_ids.add(r["id"])
        # dependency edges (column is depends_on_id, not depends_on_task_id)
        dep_rows = conn.execute(
            "SELECT task_id, depends_on_id FROM kanban_task_deps"
        ).fetchall()
        for dr in dep_rows:
            dr = dict(dr) if hasattr(dr, "keys") else {"task_id": dr[0], "depends_on_id": dr[1]}
            tid = dr.get("task_id") or dr.get("task_id")
            did = dr.get("depends_on_id")
            if tid in task_ids and did in task_ids:
                src = _nid("kanban", did)
                dst = _nid("kanban", tid)
                edges.append(_edge(src, dst, "depends_on"))
    except Exception:
        pass
    return nodes, edges


# ---------------------------------------------------------------------------
# Source 4: Goals directory
# ---------------------------------------------------------------------------

def _load_goals() -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    goals_dir = _ROOT / "goals"
    if not goals_dir.exists():
        return nodes, edges
    tool_re = re.compile(r"python\s+tools/([^\s]+\.py)", re.IGNORECASE)
    for md_file in list(goals_dir.rglob("*.md"))[:200]:
        rel = md_file.relative_to(goals_dir)
        gname = str(rel).replace("\\", "/")
        gnid = _nid("goal", gname)
        nodes.append(_node(gnid, gname, "goal", source="goals",
                           props={"path": gname}))
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            for match in tool_re.findall(content):
                tnid = _nid("tool", match)
                # add tool node if not present (will dedup later)
                nodes.append(_node(tnid, match, "tool", source="goals",
                                   props={"path": f"tools/{match}"}))
                edges.append(_edge(gnid, tnid, "calls"))
        except Exception:
            pass
    return nodes, edges


# ---------------------------------------------------------------------------
# Source 5: Migrations chain
# ---------------------------------------------------------------------------

def _load_migrations() -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    migs_dir = _ROOT / "tools" / "db" / "migrations"
    if not migs_dir.exists():
        return nodes, edges
    mig_dirs = sorted(
        [d for d in migs_dir.iterdir() if d.is_dir() and re.match(r"^\d+", d.name)],
        key=lambda d: int(re.match(r"^(\d+)", d.name).group(1))
    )
    prev_nid = None
    for d in mig_dirs[:300]:
        num = re.match(r"^(\d+)", d.name).group(1)
        label = d.name
        nid = _nid("migration", label)
        nodes.append(_node(nid, label, "migration", source="migrations",
                           props={"number": num, "path": str(d.relative_to(_ROOT))}))
        if prev_nid:
            edges.append(_edge(prev_nid, nid, "migrates"))
        prev_nid = nid
    return nodes, edges


# ---------------------------------------------------------------------------
# Source 6: Codebase structure (routes, blueprints, db_tables, agents)
# ---------------------------------------------------------------------------

def _load_codebase_structure() -> tuple[list[dict], list[dict]]:
    """Scan tools/ for Flask routes, blueprints, DB tables, and agent configs."""
    nodes: list[dict] = []
    edges: list[dict] = []

    route_re   = re.compile(r'@\w*bp\w*\.route\(["\']([^"\']+)["\']')
    bp_re      = re.compile(r'Blueprint\(\s*["\'](\w+)["\']')
    table_re   = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["\`]?(\w+)["\`]?', re.IGNORECASE)
    agent_re   = re.compile(r'["\'](?:name|agent_name|agent_id)["\']:\s*["\']([^"\']{3,60})["\']')

    # ── Routes and blueprints from blueprint.py files ──────────────────────
    for bp_file in list((_ROOT / "tools").rglob("blueprint.py"))[:60]:
        try:
            src = bp_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = str(bp_file.relative_to(_ROOT)).replace("\\", "/")
        # Blueprint node
        for bpname in bp_re.findall(src)[:1]:
            bid = _nid("blueprint", bpname)
            nodes.append(_node(bid, bpname, "blueprint", source="codebase",
                               props={"file": rel}))
        # Route nodes linked to the blueprint
        bm = bp_re.search(src)
        bp_nid = _nid("blueprint", bm.group(1)) if bm else None
        for route_path in route_re.findall(src)[:30]:
            rid = _nid("route", route_path)
            label = route_path if len(route_path) < 50 else route_path[:47] + "…"
            nodes.append(_node(rid, label, "route", source="codebase",
                               props={"path": route_path, "file": rel}))
            if bp_nid:
                edges.append(_edge(bp_nid, rid, "registers"))

    # ── DB tables from migration SQL files ─────────────────────────────────
    migs_dir = _ROOT / "tools" / "db" / "migrations"
    seen_tables: set[str] = set()
    if migs_dir.exists():
        for sql_file in sorted(migs_dir.rglob("*.sql"))[:300]:
            try:
                src = sql_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for tname in table_re.findall(src):
                if tname.lower() in ("table", "exists", "temp") or tname in seen_tables:
                    continue
                seen_tables.add(tname)
                tid = _nid("db_table", tname)
                nodes.append(_node(tid, tname, "db_table", source="codebase",
                                   props={"table": tname,
                                          "migration": sql_file.parent.name}))

    # ── Agents from architecture / agent config files ──────────────────────
    agent_files = list((_ROOT / "tools").rglob("*agent*.py"))[:40]
    agent_files += list((_ROOT / "args").glob("*agent*"))
    agent_files += list((_ROOT / "args").glob("*agents*"))
    seen_agents: set[str] = set()
    for af in agent_files:
        try:
            src = af.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for aname in agent_re.findall(src)[:10]:
            if aname in seen_agents or len(aname) < 4:
                continue
            seen_agents.add(aname)
            aid = _nid("agent", aname)
            nodes.append(_node(aid, aname, "agent", source="codebase",
                               props={"file": str(af.relative_to(_ROOT)).replace("\\", "/")}))

    return nodes, edges


# ---------------------------------------------------------------------------
# Source 7: Network Design Canvas (NDC)
# ---------------------------------------------------------------------------

def _ndc_topology_count() -> int | None:
    """Best-effort live topology count. Returns None if the canvas DB is absent."""
    try:
        from tools.network.db.init_db import get_connection
        conn = get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM topologies").fetchone()
            if row is None:
                return None
            row = dict(row) if hasattr(row, "keys") else {"c": row[0]}
            return int(row.get("c") or 0)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        return None


def _load_ndc() -> tuple[list[dict], list[dict]]:
    """Contribute the Network Design Canvas (NDC) to the federated graph.

    Emits a self-contained NDC subgraph (all target nodes owned here so edges
    always resolve, even when the canvas DB and the codebase source are absent):
      - NDC canvas node
      - grouped "network_canvas tables" DB node (nc_*/ndc_*/ni_*)
      - the /network route surface
      - the Security Design Canvas (SDC) node it couples to via the
        on_ndc_topology_saved assessment hook (tools/network/blueprint.py)

    Read-only and cheap: the only DB touch is an optional COUNT guarded by
    try/except that degrades to a node with no live count when the DB is down.
    """
    nodes: list[dict] = []
    edges: list[dict] = []

    ndc_id = _nid("canvas", "network_canvas")
    tables_id = _nid("db_table", "network_canvas tables")
    route_id = _nid("route", "/network")
    sdc_id = _nid("canvas", "security_canvas")

    topo_count = _ndc_topology_count()
    ndc_props: dict[str, Any] = {
        "canvas": "network_canvas",
        "surface": "/network",
        "table_prefixes": ["nc_", "ndc_", "ni_"],
    }
    if topo_count is not None:
        ndc_props["topology_count"] = topo_count

    nodes.append(_node(ndc_id, "Network Design Canvas", "canvas_module",
                       source="ndc", props=ndc_props, health="ok"))
    nodes.append(_node(tables_id, "network_canvas tables (nc_*/ndc_*/ni_*)",
                       "db_table", source="ndc",
                       props={"prefixes": ["nc_", "ndc_", "ni_"],
                              "backend": "network_canvas"}))
    nodes.append(_node(route_id, "/network", "route", source="ndc",
                       props={"path": "/network", "blueprint": "network_canvas"}))
    nodes.append(_node(sdc_id, "Security Design Canvas", "canvas_module",
                       source="ndc", props={"canvas": "security_canvas",
                                            "surface": "/security"}))

    # NDC canvas → its owned surfaces (always resolve; >=3 edges guaranteed)
    edges.append(_edge(ndc_id, tables_id, "related"))
    edges.append(_edge(ndc_id, route_id, "registers"))
    # NDC → SDC assessment coupling (on_ndc_topology_saved topology-save hook)
    edges.append(_edge(ndc_id, sdc_id, "triggers"))

    # Richer links to the codebase-sourced blueprint nodes when that source is
    # active; filtered out gracefully by _filter_edges if those nodes are absent.
    edges.append(_edge(ndc_id, _nid("blueprint", "network_canvas"), "registers"))
    edges.append(_edge(sdc_id, _nid("blueprint", "security_canvas"), "registers"))

    return nodes, edges


# ---------------------------------------------------------------------------
# Federation + layout
# ---------------------------------------------------------------------------

def _deduplicate(nodes: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for n in nodes:
        if n["id"] not in seen:
            seen[n["id"]] = n
    return list(seen.values())


def _filter_edges(edges: list[dict], valid_ids: set[str]) -> list[dict]:
    return [e for e in edges if e["source"] in valid_ids and e["target"] in valid_ids]


def _apply_layout_and_clusters(nodes: list[dict], edges: list[dict]) -> None:
    """Compute positions (x,y) and cluster IDs using NetworkX. Mutates nodes in place."""
    try:
        import networkx as nx
    except ImportError:
        # Fallback: random circular layout
        n = len(nodes)
        for i, node in enumerate(nodes):
            angle = 2 * math.pi * i / max(n, 1)
            node["x"] = math.cos(angle) * 500
            node["y"] = math.sin(angle) * 500
        return

    G = nx.Graph()
    id_map = {n["id"]: i for i, n in enumerate(nodes)}

    for node in nodes:
        G.add_node(node["id"])
    for edge in edges:
        if edge["source"] in id_map and edge["target"] in id_map:
            G.add_edge(edge["source"], edge["target"])

    # Spring layout with fixed seed for reproducibility
    # Scale to roughly [-1000, 1000] range for Sigma.js
    n_nodes = len(nodes)
    if n_nodes > 1500:
        # Random layout is fast for very large graphs
        pos = nx.random_layout(G, seed=42)
        scale = 2000
    else:
        try:
            pos = nx.spring_layout(G, k=LAYOUT_K / max(n_nodes ** 0.5, 1),
                                   iterations=LAYOUT_ITERATIONS, seed=42, scale=1000)
        except Exception:
            pos = nx.random_layout(G, seed=42)
            scale = 2000
        else:
            scale = 1

    for node in nodes:
        p = pos.get(node["id"], (0, 0))
        node["x"] = float(p[0]) * scale
        node["y"] = float(p[1]) * scale

    # Community detection (greedy modularity)
    try:
        from networkx.algorithms.community import greedy_modularity_communities
        communities = list(greedy_modularity_communities(G))
        community_map: dict[str, int] = {}
        for i, comm in enumerate(communities):
            for nid in comm:
                community_map[nid] = i
        for node in nodes:
            cid = community_map.get(node["id"], -1)
            node["cluster_id"] = cid
            if cid >= 0:
                node["cluster_color"] = CLUSTER_COLORS[cid % len(CLUSTER_COLORS)]
    except Exception:
        pass

    # Node size by degree
    degrees = dict(G.degree())
    for node in nodes:
        deg = degrees.get(node["id"], 0)
        node["size"] = max(4, min(20, 4 + deg * 0.8))


def build_graph(
    sources: list[str] | None = None,
    filter_type: str | None = None,
    filter_cluster: int | None = None,
    filter_health: str | None = None,
    search: str | None = None,
    max_nodes: int = MAX_FULL_NODES,
) -> dict[str, Any]:
    """
    Build and return the full federated graph payload.

    Returns:
        {
            "nodes": [...],
            "edges": [...],
            "stats": { source_counts, total_nodes, total_edges, cluster_count, built_at },
        }
    """
    t0 = time.time()
    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    source_counts: dict[str, int] = {}

    loaders = {
        "awareness_kg":  _load_awareness_kg,
        "canvas_kg":     _load_canvas_kg,
        "kanban_deps":   _load_kanban_deps,
        "goals":         _load_goals,
        "migrations":    _load_migrations,
        "codebase":      _load_codebase_structure,
        "ndc":           _load_ndc,
    }
    active = sources or list(loaders.keys())
    for src in active:
        if src in loaders:
            ns, es = loaders[src]()
            source_counts[src] = len(ns)
            all_nodes.extend(ns)
            all_edges.extend(es)

    # Deduplicate nodes (same ID from multiple sources)
    all_nodes = _deduplicate(all_nodes)

    # Apply filters
    if filter_type:
        all_nodes = [n for n in all_nodes if n["type"] == filter_type]
    if filter_health:
        all_nodes = [n for n in all_nodes if n["health"] == filter_health]
    if search:
        q = search.lower()
        all_nodes = [n for n in all_nodes
                     if q in n["label"].lower() or q in n["type"].lower()]

    # Truncate if massive
    if len(all_nodes) > max_nodes:
        all_nodes = all_nodes[:max_nodes]

    valid_ids = {n["id"] for n in all_nodes}
    all_edges = _filter_edges(all_edges, valid_ids)

    # Compute layout and communities
    _apply_layout_and_clusters(all_nodes, all_edges)

    # Filter by cluster after layout
    if filter_cluster is not None:
        all_nodes = [n for n in all_nodes if n["cluster_id"] == filter_cluster]
        valid_ids = {n["id"] for n in all_nodes}
        all_edges = _filter_edges(all_edges, valid_ids)

    cluster_ids = {n["cluster_id"] for n in all_nodes if n["cluster_id"] >= 0}

    return {
        "nodes": all_nodes,
        "edges": all_edges,
        "stats": {
            "source_counts": source_counts,
            "total_nodes": len(all_nodes),
            "total_edges": len(all_edges),
            "cluster_count": len(cluster_ids),
            "build_ms": round((time.time() - t0) * 1000),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


# ---------------------------------------------------------------------------
# Graceful-degradation helpers for the search endpoint
# ---------------------------------------------------------------------------

_BACKLOG_DIR = _ROOT / ".tmp" / "backlog"


def _save_partial_to_backlog(
    nodes: list[dict],
    edges: list[dict],
    error: str,
    search: str | None,
) -> None:
    """Persist partial search results to .tmp/backlog/ for audit/recovery. Best-effort."""
    try:
        _BACKLOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out = {
            "saved_at": ts,
            "search": search,
            "error": error,
            "partial_nodes": len(nodes),
            "partial_edges": len(edges),
            "nodes": nodes,
            "edges": edges,
        }
        (_BACKLOG_DIR / f"sg-search-{ts}.json").write_text(
            json.dumps(out, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def build_search_fallback(
    search: str,
    sources: list[str] | None = None,
    error: str = "",
) -> dict[str, Any]:
    """
    Fallback used when build_graph() raises mid-run during a search request.

    Loads each source independently (skipping any that fail), applies the
    BM25/substring search filter, saves partial results to .tmp/backlog/, and
    returns a valid JSON-serialisable response with partial=True so callers can
    detect the degraded result.
    """
    t0 = time.time()
    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    source_counts: dict[str, int] = {}
    failed_sources: list[str] = []

    loaders = {
        "awareness_kg": _load_awareness_kg,
        "canvas_kg":    _load_canvas_kg,
        "kanban_deps":  _load_kanban_deps,
        "goals":        _load_goals,
        "migrations":   _load_migrations,
        "codebase":     _load_codebase_structure,
        "ndc":          _load_ndc,
    }
    active = sources or list(loaders.keys())
    for src in active:
        if src not in loaders:
            continue
        try:
            ns, es = loaders[src]()
            source_counts[src] = len(ns)
            all_nodes.extend(ns)
            all_edges.extend(es)
        except Exception as src_exc:
            _log.warning("system-graph fallback: source %r failed: %s", src, src_exc)
            failed_sources.append(src)
            source_counts[src] = 0

    all_nodes = _deduplicate(all_nodes)

    # Substring match (same predicate as the main build path)
    q = search.lower()
    matched_nodes = [
        n for n in all_nodes
        if q in n["label"].lower() or q in n["type"].lower()
    ]
    valid_ids = {n["id"] for n in matched_nodes}
    matched_edges = _filter_edges(all_edges, valid_ids)

    _save_partial_to_backlog(matched_nodes, matched_edges, error, search)

    return {
        "nodes": matched_nodes,
        "edges": matched_edges,
        "stats": {
            "source_counts": source_counts,
            "total_nodes": len(matched_nodes),
            "total_edges": len(matched_edges),
            "cluster_count": 0,
            "build_ms": round((time.time() - t0) * 1000),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "partial": True,
            "failed_sources": failed_sources,
            "error": error,
        },
    }


_detail_cache: dict = {}
_detail_cache_lock = __import__("threading").Lock()
_DETAIL_CACHE_TTL = 300


def _get_or_build_graph() -> dict:
    """Return cached full graph, building it if needed. Shares cache with blueprint layer."""
    import time
    key = "[]"
    with _detail_cache_lock:
        entry = _detail_cache.get(key)
        if entry and (time.time() - entry["ts"]) < _DETAIL_CACHE_TTL:
            return entry["data"]
    data = build_graph()
    with _detail_cache_lock:
        _detail_cache[key] = {"data": data, "ts": __import__("time").time()}
    return data


def get_node_detail(node_id: str) -> dict | None:
    """Return full node info + 1-hop neighbors for sidebar detail panel."""
    graph = _get_or_build_graph()
    node_map = {n["id"]: n for n in graph["nodes"]}
    node = node_map.get(node_id)
    if not node:
        return None

    neighbors = []
    for e in graph["edges"]:
        other_id = None
        direction = None
        if e["source"] == node_id:
            other_id, direction = e["target"], "outbound"
        elif e["target"] == node_id:
            other_id, direction = e["source"], "inbound"
        if other_id and other_id in node_map:
            neighbors.append({
                **node_map[other_id],
                "direction": direction,
                "edge_type": e["type"],
            })

    return {**node, "neighbors": neighbors}
