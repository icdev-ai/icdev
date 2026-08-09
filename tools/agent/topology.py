
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Agent Topology & Dependency Graph — maps relationships between agents,
models, providers, functions, and skills across the ICDEV™ platform.

Reads YAML configs and SQLite data (zero network calls, fully air-gapped).

Key capabilities:
    - Build full dependency graph (agents → functions → models → providers)
    - Detect single points of failure (nodes whose removal disconnects graph)
    - Air-gap path analysis (which functions can run without cloud)
    - Provider dependency report
    - Snapshot comparison over time

Usage:
    python tools/agent/topology.py --build --json
    python tools/agent/topology.py --spof --json
    python tools/agent/topology.py --air-gap-check --json
    python tools/agent/topology.py --providers --json
    python tools/agent/topology.py --snapshot --json
    python tools/agent/topology.py --compare --snap1 "topo-xxx" --snap2 "topo-yyy" --json
    python tools/agent/topology.py --gate
"""

import argparse
import hashlib
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = get_logger("icdev.agent.topology")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# YAML loader (pyyaml preferred, fallback to basic parsing)
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    """Load a YAML file, falling back to empty dict on failure."""
    if not path.exists():
        logger.warning("YAML file not found: %s", path)
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        return yaml.safe_load(text) or {}
    except ImportError:
        logger.warning("pyyaml not installed — cannot parse %s", path)
        return {}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _get_connection():
    """Get DB connection via storage abstraction."""
    from tools.db.storage import get_connection

    return get_connection()


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_topology_snapshots (
    id TEXT PRIMARY KEY,
    snapshot_at TEXT NOT NULL,
    graph_json TEXT NOT NULL,
    node_count INTEGER NOT NULL DEFAULT 0,
    edge_count INTEGER NOT NULL DEFAULT 0,
    single_points_of_failure TEXT NOT NULL DEFAULT '[]',
    health_summary TEXT NOT NULL DEFAULT '{}'
);
"""


def _ensure_table():
    """Create snapshot table if it does not exist."""
    conn = _get_connection()
    try:
        conn.execute(CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Air-gap classification
# ---------------------------------------------------------------------------

# Providers considered fully local / air-gapped
AIR_GAP_PROVIDERS = {"ollama", "vllm", "mistral_vllm"}


def _is_air_gap_provider(provider_type: str, provider_name: str = "") -> bool:
    """Return True if the provider can operate without internet.

    Checks both the provider type and the provider name, since some
    self-hosted providers (e.g. mistral_vllm) use openai_compatible type
    but are actually air-gapped.
    """
    return provider_type in AIR_GAP_PROVIDERS or provider_name in AIR_GAP_PROVIDERS


# ---------------------------------------------------------------------------
# Graph building
# ---------------------------------------------------------------------------


def build_topology() -> dict:
    """Scan configs and build full dependency graph.

    Returns:
        dict with "nodes" and "edges" lists.
    """
    llm_cfg = _load_yaml(BASE_DIR / "args" / "llm_config.yaml")
    agent_cfg = _load_yaml(BASE_DIR / "args" / "agent_config.yaml")

    nodes: List[dict] = []
    edges: List[dict] = []
    seen_nodes: Set[str] = set()

    def _add_node(node: dict):
        if node["id"] not in seen_nodes:
            nodes.append(node)
            seen_nodes.add(node["id"])

    def _add_edge(edge: dict):
        edges.append(edge)

    # -- 1. Providers --
    providers_cfg = llm_cfg.get("providers", {})
    for pname, pdata in providers_cfg.items():
        ptype = pdata.get("type", pname)
        _add_node(
            {
                "id": f"provider:{pname}",
                "type": "provider",
                "provider_type": ptype,
                "air_gap": _is_air_gap_provider(ptype, pname),
            }
        )

    # -- 2. Models --
    models_cfg = llm_cfg.get("models", {})
    for mname, mdata in models_cfg.items():
        provider = mdata.get("provider", "unknown")
        ptype = providers_cfg.get(provider, {}).get("type", provider)
        _add_node(
            {
                "id": f"model:{mname}",
                "type": "model",
                "provider": provider,
                "model_id": mdata.get("model_id", ""),
                "air_gap": _is_air_gap_provider(ptype, provider),
            }
        )
        _add_edge(
            {
                "from": f"model:{mname}",
                "to": f"provider:{provider}",
                "type": "hosted_by",
            }
        )

    # -- 3. Functions (routing) --
    routing_cfg = llm_cfg.get("routing", {})
    for fname, fdata in routing_cfg.items():
        _add_node(
            {
                "id": f"function:{fname}",
                "type": "function",
                "effort": fdata.get("effort", "medium"),
            }
        )
        chain = fdata.get("chain", [])
        for priority, model_name in enumerate(chain, start=1):
            _add_edge(
                {
                    "from": f"function:{fname}",
                    "to": f"model:{model_name}",
                    "type": "routes_to",
                    "priority": priority,
                }
            )

    # -- 4. Two-tier routing overlay --
    two_tier = llm_cfg.get("two_tier", {})
    if two_tier.get("enabled"):
        tier1 = two_tier.get("tier1_model", "")
        tier2 = two_tier.get("tier2_model", "")
        for category in ("planner_functions", "worker_functions", "scanner_functions"):
            tier_type = category.replace("_functions", "")
            for fname in two_tier.get(category, []):
                fid = f"function:{fname}"
                if fid not in seen_nodes:
                    _add_node({"id": fid, "type": "function", "effort": "medium"})
                _add_edge(
                    {
                        "from": fid,
                        "to": f"model:{tier2}" if tier_type == "planner" else f"model:{tier1}",
                        "type": f"two_tier_{tier_type}",
                    }
                )

        # Function model overrides
        overrides = two_tier.get("function_model_overrides", {})
        for fname, model_name in overrides.items():
            fid = f"function:{fname}"
            if fid not in seen_nodes:
                _add_node({"id": fid, "type": "function", "effort": "medium"})
            _add_edge(
                {
                    "from": fid,
                    "to": f"model:{model_name}",
                    "type": "two_tier_override",
                }
            )

    # -- 5. Agents --
    agents_cfg = agent_cfg.get("agents", {})
    for aname, adata in agents_cfg.items():
        agent_id = adata.get("id", aname)
        port = adata.get("port", 0)
        _add_node(
            {
                "id": f"agent:{aname}",
                "type": "agent",
                "agent_id": agent_id,
                "port": port,
                "name": adata.get("name", aname),
                "description": adata.get("description", ""),
                "status": "configured",
            }
        )

        # Link agent to its routing function if one exists
        routing_key = f"agent_{aname}"
        if f"function:{routing_key}" in seen_nodes:
            _add_edge(
                {
                    "from": f"agent:{aname}",
                    "to": f"function:{routing_key}",
                    "type": "uses",
                }
            )

    # -- 6. Skills (scan .claude/commands/) --
    commands_dir = BASE_DIR / ".claude" / "commands"
    if commands_dir.exists():
        for skill_path in sorted(commands_dir.glob("*.md")):
            skill_name = skill_path.stem
            _add_node(
                {
                    "id": f"skill:{skill_name}",
                    "type": "skill",
                    "file": str(skill_path.relative_to(BASE_DIR)),
                }
            )
            # Skills are invoked by the orchestrator
            if "agent:orchestrator" in seen_nodes:
                _add_edge(
                    {
                        "from": "agent:orchestrator",
                        "to": f"skill:{skill_name}",
                        "type": "invokes",
                    }
                )
        # Scan subdirectories (e.g. e2e/)
        for subdir in sorted(commands_dir.iterdir()):
            if subdir.is_dir():
                for skill_path in sorted(subdir.glob("*.md")):
                    skill_name = f"{subdir.name}/{skill_path.stem}"
                    _add_node(
                        {
                            "id": f"skill:{skill_name}",
                            "type": "skill",
                            "file": str(skill_path.relative_to(BASE_DIR)),
                        }
                    )
                    if "agent:orchestrator" in seen_nodes:
                        _add_edge(
                            {
                                "from": "agent:orchestrator",
                                "to": f"skill:{skill_name}",
                                "type": "invokes",
                            }
                        )

    # -- 7. Agent tool implementations (scan tools/agent/) --
    agent_tools_dir = BASE_DIR / "tools" / "agent"
    if agent_tools_dir.exists():
        for py_file in sorted(agent_tools_dir.glob("*.py")):
            if py_file.name.startswith("__"):
                continue
            tool_name = py_file.stem
            tid = f"tool:{tool_name}"
            if tid not in seen_nodes:
                _add_node(
                    {
                        "id": tid,
                        "type": "tool",
                        "file": str(py_file.relative_to(BASE_DIR)),
                    }
                )

    # -- 8. Token usage statistics from DB --
    _enrich_with_usage(nodes, seen_nodes)

    return {"nodes": nodes, "edges": edges}


def _enrich_with_usage(nodes: List[dict], seen_nodes: Set[str]):
    """Add runtime usage data from agent_token_usage table."""
    try:
        conn = _get_connection()
        try:
            cursor = conn.execute(
                "SELECT agent_id, model_id, COUNT(*) as call_count, "
                "SUM(input_tokens) as total_input, SUM(output_tokens) as total_output, "
                "SUM(cost_estimate_usd) as total_cost "
                "FROM agent_token_usage GROUP BY agent_id, model_id"
            )
            rows = cursor.fetchall()
            # Build lookup
            usage_map: Dict[str, list] = defaultdict(list)
            for row in rows:
                agent_id = row[0] if isinstance(row, (list, tuple)) else row["agent_id"]
                model_id = row[1] if isinstance(row, (list, tuple)) else row["model_id"]
                call_count = row[2] if isinstance(row, (list, tuple)) else row["call_count"]
                total_input = row[3] if isinstance(row, (list, tuple)) else row["total_input"]
                total_output = row[4] if isinstance(row, (list, tuple)) else row["total_output"]
                total_cost = row[5] if isinstance(row, (list, tuple)) else row["total_cost"]
                usage_map[agent_id].append(
                    {
                        "model_id": model_id,
                        "call_count": call_count,
                        "total_input_tokens": total_input or 0,
                        "total_output_tokens": total_output or 0,
                        "total_cost_usd": round(total_cost or 0.0, 4),
                    }
                )
            # Attach to agent nodes
            for node in nodes:
                if node["type"] == "agent":
                    aid = node.get("agent_id", "")
                    if aid in usage_map:
                        node["runtime_usage"] = usage_map[aid]
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("Could not enrich with token usage: %s", exc)


# ---------------------------------------------------------------------------
# Graph analysis
# ---------------------------------------------------------------------------


def _build_adjacency(graph: dict) -> Dict[str, Set[str]]:
    """Build undirected adjacency map from graph edges."""
    adj: Dict[str, Set[str]] = defaultdict(set)
    for edge in graph.get("edges", []):
        src, dst = edge["from"], edge["to"]
        adj[src].add(dst)
        adj[dst].add(src)
    # Add isolated nodes
    for node in graph.get("nodes", []):
        if node["id"] not in adj:
            adj[node["id"]] = set()
    return adj


def _count_components(adj: Dict[str, Set[str]], exclude: Optional[str] = None) -> int:
    """Count connected components, optionally excluding one node."""
    visited: Set[str] = set()
    if exclude:
        visited.add(exclude)
    components = 0

    nodes = set(adj.keys())
    if exclude:
        nodes.discard(exclude)

    for start in nodes:
        if start in visited:
            continue
        components += 1
        stack = [start]
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited.add(n)
            for neighbor in adj.get(n, set()):
                if neighbor not in visited and neighbor != exclude:
                    stack.append(neighbor)
    return components


def find_single_points_of_failure(graph: Optional[dict] = None) -> list:
    """Find nodes whose removal increases the number of connected components.

    These are articulation points / cut vertices in the undirected graph.

    Returns:
        List of node IDs that are single points of failure.
    """
    if graph is None:
        graph = build_topology()

    adj = _build_adjacency(graph)
    baseline = _count_components(adj)
    spof = []

    # Only check non-trivial nodes (connected to 2+ other nodes)
    for node_id in list(adj.keys()):
        if len(adj[node_id]) < 2:
            continue
        new_count = _count_components(adj, exclude=node_id)
        if new_count > baseline:
            spof.append(node_id)

    return sorted(spof)


def find_air_gap_paths(graph: Optional[dict] = None) -> dict:
    """For each function, determine if a fully air-gapped execution path exists.

    Returns:
        dict mapping function_id to:
          - air_gap_available: bool
          - air_gap_models: list of local-only models in the chain
          - cloud_only_models: list of cloud-dependent models
          - status: "OK" | "WARNING" | "CRITICAL"
    """
    if graph is None:
        graph = build_topology()

    # Build model -> air_gap lookup
    model_air_gap: Dict[str, bool] = {}
    for node in graph["nodes"]:
        if node["type"] == "model":
            model_air_gap[node["id"]] = node.get("air_gap", False)

    # Build function -> model edges
    func_models: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for edge in graph["edges"]:
        if edge["type"] in (
            "routes_to",
            "two_tier_scanner",
            "two_tier_worker",
            "two_tier_planner",
            "two_tier_override",
        ):
            if edge["from"].startswith("function:"):
                priority = edge.get("priority", 99)
                func_models[edge["from"]].append((edge["to"], priority))

    results = {}
    for node in graph["nodes"]:
        if node["type"] != "function":
            continue
        fid = node["id"]
        models = func_models.get(fid, [])
        air_gap_models = [m for m, _ in models if model_air_gap.get(m, False)]
        cloud_models = [m for m, _ in models if not model_air_gap.get(m, False)]

        if not models:
            status = "CRITICAL"
            has_air_gap = False
        elif air_gap_models:
            status = "OK"
            has_air_gap = True
        else:
            status = "WARNING"
            has_air_gap = False

        results[fid] = {
            "air_gap_available": has_air_gap,
            "air_gap_models": [m.replace("model:", "") for m in air_gap_models],
            "cloud_only_models": [m.replace("model:", "") for m in cloud_models],
            "status": status,
        }

    return results


def get_provider_dependency_report(graph: Optional[dict] = None) -> dict:
    """Which functions depend on which cloud providers.

    Returns:
        dict with:
          - by_provider: {provider: [function_ids]}
          - by_function: {function: [provider_ids]}
          - cloud_only_functions: list of functions with no local fallback
          - fully_local_functions: list of functions with at least one local path
    """
    if graph is None:
        graph = build_topology()

    # model -> provider
    model_provider: Dict[str, str] = {}
    model_air_gap: Dict[str, bool] = {}
    for node in graph["nodes"]:
        if node["type"] == "model":
            model_provider[node["id"]] = node.get("provider", "unknown")
            model_air_gap[node["id"]] = node.get("air_gap", False)

    by_provider: Dict[str, Set[str]] = defaultdict(set)
    by_function: Dict[str, Set[str]] = defaultdict(set)

    for edge in graph["edges"]:
        if edge["from"].startswith("function:") and edge["to"].startswith("model:"):
            fname = edge["from"].replace("function:", "")
            provider = model_provider.get(edge["to"], "unknown")
            by_provider[provider].add(fname)
            by_function[fname].add(provider)

    # Classify functions
    cloud_only = []
    fully_local = []
    for node in graph["nodes"]:
        if node["type"] != "function":
            continue
        fname = node["id"].replace("function:", "")
        providers = by_function.get(fname, set())
        has_local = any(_is_air_gap_provider(p) for p in providers)
        if has_local:
            fully_local.append(fname)
        elif providers:
            cloud_only.append(fname)

    return {
        "by_provider": {k: sorted(v) for k, v in sorted(by_provider.items())},
        "by_function": {k: sorted(v) for k, v in sorted(by_function.items())},
        "cloud_only_functions": sorted(cloud_only),
        "fully_local_functions": sorted(fully_local),
    }


def get_topology_stats(graph: Optional[dict] = None) -> dict:
    """Node/edge counts, type breakdown, and health summary."""
    if graph is None:
        graph = build_topology()

    type_counts: Dict[str, int] = defaultdict(int)
    for node in graph["nodes"]:
        type_counts[node["type"]] += 1

    edge_type_counts: Dict[str, int] = defaultdict(int)
    for edge in graph["edges"]:
        edge_type_counts[edge["type"]] += 1

    air_gap = find_air_gap_paths(graph)
    air_gap_summary = {"OK": 0, "WARNING": 0, "CRITICAL": 0}
    for info in air_gap.values():
        air_gap_summary[info["status"]] += 1

    return {
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "node_types": dict(type_counts),
        "edge_types": dict(edge_type_counts),
        "air_gap_summary": air_gap_summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def snapshot_topology(graph: Optional[dict] = None) -> dict:
    """Save current topology to DB and return snapshot metadata."""
    _ensure_table()

    if graph is None:
        graph = build_topology()

    snap_id = f"topo-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    spof = find_single_points_of_failure(graph)
    stats = get_topology_stats(graph)

    graph_json = json.dumps(graph, default=str)
    graph_hash = hashlib.sha256(graph_json.encode("utf-8")).hexdigest()[:16]

    health = {
        "air_gap_summary": stats["air_gap_summary"],
        "node_types": stats["node_types"],
        "graph_hash": graph_hash,
    }

    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO agent_topology_snapshots "
            "(id, snapshot_at, graph_json, node_count, edge_count, "
            "single_points_of_failure, health_summary) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                snap_id,
                now,
                graph_json,
                stats["node_count"],
                stats["edge_count"],
                json.dumps(spof),
                json.dumps(health),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "snapshot_id": snap_id,
        "snapshot_at": now,
        "node_count": stats["node_count"],
        "edge_count": stats["edge_count"],
        "single_points_of_failure": spof,
        "health_summary": health,
    }


def compare_snapshots(snap_id_1: str, snap_id_2: str) -> dict:
    """Diff two topology snapshots.

    Returns:
        dict with added_nodes, removed_nodes, added_edges, removed_edges,
        and summary statistics.
    """
    _ensure_table()
    conn = _get_connection()
    try:
        row1 = conn.execute(
            "SELECT graph_json, snapshot_at, node_count, edge_count FROM agent_topology_snapshots WHERE id = %s",
            (snap_id_1,),
        ).fetchone()
        row2 = conn.execute(
            "SELECT graph_json, snapshot_at, node_count, edge_count FROM agent_topology_snapshots WHERE id = %s",
            (snap_id_2,),
        ).fetchone()
    finally:
        conn.close()

    if not row1:
        return {"error": f"Snapshot {snap_id_1} not found"}
    if not row2:
        return {"error": f"Snapshot {snap_id_2} not found"}

    def _extract(row):
        if isinstance(row, (list, tuple)):
            return json.loads(row[0]), row[1], row[2], row[3]
        return json.loads(row["graph_json"]), row["snapshot_at"], row["node_count"], row["edge_count"]

    g1, at1, nc1, ec1 = _extract(row1)
    g2, at2, nc2, ec2 = _extract(row2)

    nodes1 = {n["id"] for n in g1.get("nodes", [])}
    nodes2 = {n["id"] for n in g2.get("nodes", [])}

    def _edge_key(e: dict) -> str:
        return f"{e['from']}|{e['to']}|{e['type']}"

    edges1 = {_edge_key(e) for e in g1.get("edges", [])}
    edges2 = {_edge_key(e) for e in g2.get("edges", [])}

    return {
        "snap1": {"id": snap_id_1, "at": at1, "nodes": nc1, "edges": ec1},
        "snap2": {"id": snap_id_2, "at": at2, "nodes": nc2, "edges": ec2},
        "added_nodes": sorted(nodes2 - nodes1),
        "removed_nodes": sorted(nodes1 - nodes2),
        "added_edges": sorted(edges2 - edges1),
        "removed_edges": sorted(edges1 - edges2),
        "node_delta": nc2 - nc1,
        "edge_delta": ec2 - ec1,
    }


# ---------------------------------------------------------------------------
# Gate check
# ---------------------------------------------------------------------------


def gate_check() -> Tuple[bool, dict]:
    """Gate: passes if every function has at least one air-gapped path.

    Returns:
        (passed: bool, report: dict)
    """
    graph = build_topology()
    air_gap = find_air_gap_paths(graph)
    stats = get_topology_stats(graph)

    warnings = []
    criticals = []
    for fid, info in air_gap.items():
        fname = fid.replace("function:", "")
        if info["status"] == "WARNING":
            warnings.append(fname)
        elif info["status"] == "CRITICAL":
            criticals.append(fname)

    passed = len(criticals) == 0
    return passed, {
        "passed": passed,
        "functions_without_air_gap": warnings,
        "functions_without_any_model": criticals,
        "total_functions": sum(1 for n in graph["nodes"] if n["type"] == "function"),
        "air_gap_coverage": stats["air_gap_summary"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Agent Topology & Dependency Graph")
    parser.add_argument("--build", action="store_true", help="Build and display the full topology graph")
    parser.add_argument("--spof", action="store_true", help="Find single points of failure")
    parser.add_argument("--air-gap-check", action="store_true", help="Check air-gap paths for all functions")
    parser.add_argument("--providers", action="store_true", help="Provider dependency report")
    parser.add_argument("--snapshot", action="store_true", help="Save current topology snapshot to DB")
    parser.add_argument("--compare", action="store_true", help="Compare two topology snapshots")
    parser.add_argument("--snap1", type=str, default="", help="First snapshot ID for comparison")
    parser.add_argument("--snap2", type=str, default="", help="Second snapshot ID for comparison")
    parser.add_argument("--stats", action="store_true", help="Show topology statistics")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--gate", action="store_true", help="Gate check — exit 1 if any function has no air-gap path")

    args = parser.parse_args()

    # Default to --build if no action specified
    if not any(
        [args.build, args.spof, args.air_gap_check, args.providers, args.snapshot, args.compare, args.stats, args.gate]
    ):
        args.build = True

    try:
        if args.gate:
            passed, report = gate_check()
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                status = "PASSED" if passed else "FAILED"
                print(f"Topology gate: {status}")
                if report["functions_without_any_model"]:
                    print(f"  CRITICAL — no model: {report['functions_without_any_model']}")
                if report["functions_without_air_gap"]:
                    print(f"  WARNING — cloud-only: {report['functions_without_air_gap']}")
                print(f"  Coverage: {report['air_gap_coverage']}")
            sys.exit(0 if passed else 1)

        if args.build:
            graph = build_topology()
            if args.json:
                print(json.dumps(graph, indent=2, default=str))
            else:
                stats = get_topology_stats(graph)
                print(f"Topology: {stats['node_count']} nodes, {stats['edge_count']} edges")
                print(f"  Types: {stats['node_types']}")
                print(f"  Air-gap: {stats['air_gap_summary']}")

        if args.spof:
            graph = build_topology()
            spof = find_single_points_of_failure(graph)
            if args.json:
                print(json.dumps({"single_points_of_failure": spof}, indent=2))
            else:
                print(f"Single points of failure ({len(spof)}):")
                for node_id in spof:
                    print(f"  - {node_id}")

        if args.air_gap_check:
            graph = build_topology()
            air_gap = find_air_gap_paths(graph)
            if args.json:
                print(json.dumps(air_gap, indent=2))
            else:
                for fid, info in sorted(air_gap.items()):
                    fname = fid.replace("function:", "")
                    status = info["status"]
                    local = ", ".join(info["air_gap_models"]) or "none"
                    print(f"  [{status:8s}] {fname}: local={local}")

        if args.providers:
            report = get_provider_dependency_report()
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print("Provider dependency report:")
                for provider, funcs in report["by_provider"].items():
                    print(f"  {provider}: {len(funcs)} functions")
                if report["cloud_only_functions"]:
                    print(f"  Cloud-only: {report['cloud_only_functions']}")

        if args.snapshot:
            result = snapshot_topology()
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Snapshot saved: {result['snapshot_id']}")
                print(f"  Nodes: {result['node_count']}, Edges: {result['edge_count']}")
                print(f"  SPOFs: {len(result['single_points_of_failure'])}")

        if args.compare:
            if not args.snap1 or not args.snap2:
                print("Error: --compare requires --snap1 and --snap2", file=sys.stderr)
                sys.exit(1)
            diff = compare_snapshots(args.snap1, args.snap2)
            if args.json:
                print(json.dumps(diff, indent=2))
            else:
                if "error" in diff:
                    print(f"Error: {diff['error']}")
                else:
                    print(f"Comparing {args.snap1} vs {args.snap2}:")
                    print(f"  Node delta: {diff['node_delta']:+d}")
                    print(f"  Edge delta: {diff['edge_delta']:+d}")
                    print(f"  Added nodes: {len(diff['added_nodes'])}")
                    print(f"  Removed nodes: {len(diff['removed_nodes'])}")

        if args.stats:
            stats = get_topology_stats()
            if args.json:
                print(json.dumps(stats, indent=2))
            else:
                print("Topology stats:")
                print(f"  Nodes: {stats['node_count']}")
                print(f"  Edges: {stats['edge_count']}")
                print(f"  Types: {stats['node_types']}")
                print(f"  Air-gap: {stats['air_gap_summary']}")

    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
