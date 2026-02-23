#!/usr/bin/env python3
# CUI // SP-CTI
"""Monolith Decomposition Planning Tool for ICDEV DoD Modernization System.

Analyzes legacy monolithic applications, detects bounded contexts via greedy
modularity optimization, suggests microservice boundaries, generates ordered
extraction plans, anti-corruption layers, API facades, effort estimates, and
persists migration plans/tasks to the ICDEV database.

Classification: CUI // SP-CTI
System: ICDEV Intelligent Coding Development Framework
NIST: SC-7 (Boundary Protection), SA-8 (Security Engineering Principles)
"""

import argparse
import json
import sqlite3
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
PATTERNS_PATH = BASE_DIR / "context" / "modernization" / "decomposition_patterns.json"

# ---------------------------------------------------------------------------
# Valid strategy / approach / architecture enums (mirror DB CHECK constraints)
# ---------------------------------------------------------------------------
VALID_STRATEGIES = (
    "rehost", "replatform", "refactor", "rearchitect",
    "repurchase", "retire", "retain", "hybrid",
)
VALID_APPROACHES = (
    "big_bang", "strangler_fig", "parallel_run",
    "blue_green", "canary", "phased",
)
VALID_ARCHITECTURES = (
    "microservices", "modular_monolith", "serverless",
    "event_driven", "layered", "hexagonal",
)

# ---------------------------------------------------------------------------
# Task-type templates per strategy
# ---------------------------------------------------------------------------
STRATEGY_TASK_TEMPLATES = {
    "rehost": [
        ("analyze", "Analyze {name} for containerization", "high", 4),
        ("document", "Document {name} runtime requirements", "medium", 2),
        ("deploy", "Containerize and deploy {name}", "high", 8),
        ("validate", "Validate {name} container deployment", "medium", 4),
    ],
    "replatform": [
        ("analyze", "Analyze {name} platform dependencies", "high", 4),
        ("document", "Document {name} platform migration plan", "medium", 3),
        ("migrate_schema", "Migrate {name} database schema", "high", 8),
        ("deploy", "Containerize and deploy {name}", "high", 6),
        ("validate", "Validate {name} replatform deployment", "medium", 4),
    ],
    "refactor": [
        ("analyze", "Analyze {name} for version/framework upgrade", "high", 4),
        ("upgrade_version", "Upgrade {name} language version", "high", 6),
        ("upgrade_framework", "Upgrade {name} framework", "high", 8),
        ("generate_test", "Generate regression tests for {name}", "medium", 6),
        ("validate", "Validate {name} refactored build", "medium", 4),
    ],
    "retire": [
        ("analyze", "Assess {name} for retirement eligibility", "medium", 2),
        ("document", "Document {name} retirement justification", "high", 3),
        ("decommission", "Decommission {name}", "high", 4),
    ],
    "retain": [
        ("document", "Document {name} retain decision and rationale", "low", 2),
    ],
}


# ===================================================================
# Database helpers
# ===================================================================

def _get_db(db_path=None):
    """Return an sqlite3 connection with Row factory enabled.

    Raises FileNotFoundError if the database does not exist.
    """
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _gen_id(prefix):
    """Generate a prefixed UUID-based identifier."""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# ===================================================================
# Pattern loading
# ===================================================================

def load_decomposition_patterns(patterns_path=None):
    """Load decomposition patterns from the context JSON file.

    Returns a dict keyed by pattern ``id`` for quick lookup.  If the file
    is missing an empty dict is returned with a warning printed to stderr.
    """
    path = Path(patterns_path) if patterns_path else PATTERNS_PATH
    if not path.exists():
        print(f"[WARN] Decomposition patterns not found: {path}", file=sys.stderr)
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    patterns = {}
    for p in data.get("patterns", []):
        patterns[p["id"]] = p
    return patterns


# ===================================================================
# Bounded-context detection (greedy modularity / simplified Louvain)
# ===================================================================

def _build_adjacency(components, dependencies):
    """Build adjacency list and edge-weight lookup from DB rows.

    Returns:
        adj: dict[str, set[str]]  — undirected neighbour sets
        edge_weights: dict[(str,str), float]  — edge weight (sum of dep weights)
        total_weight: float  — sum of all edge weights (m)
        degree: dict[str, float]  — weighted degree per node
    """
    adj = defaultdict(set)
    edge_weights = defaultdict(float)
    degree = defaultdict(float)

    for dep in dependencies:
        src = dep["source_component_id"]
        tgt = dep["target_component_id"]
        w = dep["weight"] if dep["weight"] else 1.0
        # Undirected for modularity
        adj[src].add(tgt)
        adj[tgt].add(src)
        key = tuple(sorted([src, tgt]))
        edge_weights[key] += w
        degree[src] += w
        degree[tgt] += w

    total_weight = sum(edge_weights.values())
    return adj, dict(edge_weights), total_weight, dict(degree)


def _compute_modularity(clusters, edge_weights, degree, m):
    """Compute modularity Q for a given clustering.

    Q = sum_c [ (L_c / m) - (d_c / (2*m))^2 ]

    Where L_c = total edge weight within cluster c,
          d_c = total weighted degree of nodes in cluster c,
          m   = total edge weight.
    """
    if m == 0:
        return 0.0

    # Build reverse map: node -> cluster_id
    node_cluster = {}
    for cid, members in clusters.items():
        for n in members:
            node_cluster[n] = cid

    # Aggregate per-cluster
    L = defaultdict(float)  # internal edges per cluster
    D = defaultdict(float)  # total degree per cluster

    for (u, v), w in edge_weights.items():
        cu = node_cluster.get(u)
        cv = node_cluster.get(v)
        if cu is not None and cv is not None and cu == cv:
            L[cu] += w
        # degree sums
    for node, d in degree.items():
        c = node_cluster.get(node)
        if c is not None:
            D[c] += d

    q = 0.0
    for cid in clusters:
        lc = L.get(cid, 0.0)
        dc = D.get(cid, 0.0)
        q += (lc / m) - (dc / (2.0 * m)) ** 2

    return q


def detect_bounded_contexts(app_id, db_path=None):
    """Cluster legacy components into bounded contexts using greedy modularity.

    Algorithm (simplified Louvain, single pass):
        1. Place each component in its own cluster.
        2. For each component, try moving it to each neighbour's cluster.
        3. Accept the move that yields the largest modularity increase.
        4. Repeat until no move improves Q.

    Returns a list of context dicts:
        {id, name, components: [ids], internal_edges, external_edges,
         cohesion, coupling}
    """
    conn = _get_db(db_path)
    try:
        components = conn.execute(
            "SELECT id, name, component_type, qualified_name "
            "FROM legacy_components WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()
        dependencies = conn.execute(
            "SELECT source_component_id, target_component_id, weight "
            "FROM legacy_dependencies WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()
    finally:
        conn.close()

    if not components:
        return []

    comp_map = {c["id"]: dict(c) for c in components}
    comp_ids = list(comp_map.keys())

    adj, edge_weights, m, degree = _build_adjacency(components, dependencies)

    if m == 0:
        # No edges — every component is its own context
        results = []
        for idx, cid in enumerate(comp_ids):
            c = comp_map[cid]
            results.append({
                "id": f"ctx-{idx}",
                "name": f"{c['component_type']}_{c['name']}",
                "components": [cid],
                "internal_edges": 0,
                "external_edges": 0,
                "cohesion": 0.0,
                "coupling": 0.0,
            })
        return results

    # Initial clustering: each node in its own cluster
    clusters = {cid: {cid} for cid in comp_ids}
    node_cluster = {cid: cid for cid in comp_ids}

    improved = True
    max_iterations = 50
    iteration = 0

    while improved and iteration < max_iterations:
        improved = False
        iteration += 1
        for node in comp_ids:
            current_cluster = node_cluster[node]
            best_cluster = current_cluster
            best_q = _compute_modularity(clusters, edge_weights, degree, m)

            # Candidate clusters: those of neighbours
            neighbour_clusters = set()
            for nb in adj.get(node, set()):
                nc = node_cluster[nb]
                if nc != current_cluster:
                    neighbour_clusters.add(nc)

            for candidate in neighbour_clusters:
                # Tentatively move node
                clusters[current_cluster].discard(node)
                clusters[candidate].add(node)
                node_cluster[node] = candidate

                q = _compute_modularity(clusters, edge_weights, degree, m)
                if q > best_q + 1e-9:
                    best_q = q
                    best_cluster = candidate

                # Revert
                clusters[candidate].discard(node)
                clusters[current_cluster].add(node)
                node_cluster[node] = current_cluster

            if best_cluster != current_cluster:
                # Commit the move
                clusters[current_cluster].discard(node)
                clusters[best_cluster].add(node)
                node_cluster[node] = best_cluster
                # Remove empty clusters
                if not clusters[current_cluster]:
                    del clusters[current_cluster]
                improved = True

    # Build result contexts
    results = []
    for idx, (cid, members) in enumerate(clusters.items()):
        if not members:
            continue

        # Count internal and external edges
        internal = 0
        external = 0
        for (u, v), w in edge_weights.items():
            u_in = u in members
            v_in = v in members
            if u_in and v_in:
                internal += w
            elif u_in or v_in:
                external += w

        total_edges = internal + external
        cohesion = internal / total_edges if total_edges > 0 else 0.0
        coupling = external / total_edges if total_edges > 0 else 0.0

        # Name: dominant component_type + most common package prefix
        types = [comp_map[m_id]["component_type"] for m_id in members if m_id in comp_map]
        type_counts = Counter(types)
        dominant_type = type_counts.most_common(1)[0][0] if type_counts else "module"

        # Extract package prefixes from qualified_name
        prefixes = []
        for m_id in members:
            qn = comp_map.get(m_id, {}).get("qualified_name", "")
            if qn and "." in str(qn):
                parts = str(qn).split(".")
                if len(parts) >= 2:
                    prefixes.append(parts[-2])
                else:
                    prefixes.append(parts[0])
            elif qn:
                prefixes.append(str(qn))
        prefix_counts = Counter(prefixes)
        common_prefix = prefix_counts.most_common(1)[0][0] if prefix_counts else f"context_{idx}"

        context_name = f"{common_prefix}_{dominant_type}_context"

        results.append({
            "id": f"ctx-{idx}",
            "name": context_name,
            "components": sorted(members),
            "internal_edges": internal,
            "external_edges": external,
            "cohesion": round(cohesion, 4),
            "coupling": round(coupling, 4),
        })

    # Sort by size descending
    results.sort(key=lambda x: len(x["components"]), reverse=True)
    return results


# ===================================================================
# Service boundary suggestion
# ===================================================================

def suggest_service_boundaries(app_id, db_path=None):
    """Suggest microservice boundaries based on bounded contexts.

    For each context evaluates:
        - has_apis:       context components own API endpoints
        - owns_data:      context components own distinct DB tables
        - low_coupling:   external deps < 30 % of total deps
        - sufficient_size: context has >= 2 components

    service_readiness = has_apis*0.3 + owns_data*0.3 + low_coupling*0.2 + sufficient_size*0.2

    Returns list sorted by extraction_order (lowest coupling first).
    """
    contexts = detect_bounded_contexts(app_id, db_path)
    if not contexts:
        return []

    conn = _get_db(db_path)
    try:
        # Load APIs
        apis = conn.execute(
            "SELECT id, component_id, method, path FROM legacy_apis "
            "WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()

        # Load DB schemas (distinct tables per component via qualified_name match)
        db_schemas = conn.execute(
            "SELECT id, table_name, column_name FROM legacy_db_schemas "
            "WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()

        # Load components for table ownership heuristic
        components = conn.execute(
            "SELECT id, name, qualified_name FROM legacy_components "
            "WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()
    finally:
        conn.close()

    # Build lookup: component_id -> set of API paths
    comp_apis = defaultdict(list)
    for api in apis:
        comp_apis[api["component_id"]].append({
            "id": api["id"],
            "method": api["method"],
            "path": api["path"],
        })

    # Build lookup: component name (lower) -> set of table names
    # Heuristic: a component "owns" tables whose name includes the component name
    comp_names = {}
    for c in components:
        comp_names[c["id"]] = c["name"].lower().replace("_", "").replace("-", "")

    all_tables = set()
    for s in db_schemas:
        all_tables.add(s["table_name"])

    comp_tables = defaultdict(set)
    for comp_id, cname in comp_names.items():
        for tbl in all_tables:
            tbl_lower = tbl.lower().replace("_", "").replace("-", "")
            # Component owns table if name overlaps significantly
            if cname in tbl_lower or tbl_lower in cname:
                comp_tables[comp_id].add(tbl)

    # Evaluate each context
    boundaries = []
    for ctx in contexts:
        members = set(ctx["components"])

        # APIs owned by this context
        context_apis = []
        for cid in members:
            context_apis.extend(comp_apis.get(cid, []))
        has_apis = 1.0 if len(context_apis) > 0 else 0.0

        # Tables owned by this context
        context_tables = set()
        for cid in members:
            context_tables.update(comp_tables.get(cid, set()))
        owns_data = 1.0 if len(context_tables) > 0 else 0.0

        # Coupling check (< 30 % external)
        total = ctx["internal_edges"] + ctx["external_edges"]
        coupling_ratio = ctx["external_edges"] / total if total > 0 else 0.0
        low_coupling = 1.0 if coupling_ratio < 0.30 else 0.0

        # Sufficient size
        sufficient_size = 1.0 if len(members) >= 2 else 0.0

        readiness = (
            has_apis * 0.3
            + owns_data * 0.3
            + low_coupling * 0.2
            + sufficient_size * 0.2
        )

        # Generate service name from context name
        service_name = ctx["name"].replace("_context", "_service")

        boundaries.append({
            "service_name": service_name,
            "context_id": ctx["id"],
            "components": ctx["components"],
            "apis": context_apis,
            "tables": sorted(context_tables),
            "readiness_score": round(readiness, 4),
            "coupling_ratio": round(coupling_ratio, 4),
            "has_apis": bool(has_apis),
            "owns_data": bool(owns_data),
            "low_coupling": bool(low_coupling),
            "sufficient_size": bool(sufficient_size),
            "extraction_order": 0,  # filled below
        })

    # Sort by coupling_ratio ascending (least coupled first)
    boundaries.sort(key=lambda b: b["coupling_ratio"])
    for idx, b in enumerate(boundaries):
        b["extraction_order"] = idx + 1

    return boundaries


# ===================================================================
# Decomposition plan generation
# ===================================================================

def generate_decomposition_plan(app_id, target_architecture="microservices", db_path=None):
    """Generate an ordered decomposition extraction plan.

    Phases:
        0. Analysis & shared-kernel extraction
        1..N. Service extraction in extraction_order
        N+1. Anti-corruption layer tasks between services
        N+2. Validation & cutover

    Returns a plan dict with phases and tasks.
    """
    boundaries = suggest_service_boundaries(app_id, db_path)
    patterns = load_decomposition_patterns()

    plan = {
        "app_id": app_id,
        "target_architecture": target_architecture,
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "total_services": len(boundaries),
        "phases": [],
        "summary": {},
    }

    task_counter = 0

    # --- Phase 0: Analysis & shared kernel ---
    phase0_tasks = []
    phase0_tasks.append({
        "task_id": f"decomp-task-{task_counter}",
        "task_type": "analyze",
        "title": "Analyze monolith structure and dependency graph",
        "description": "Map all components, dependencies, APIs, and DB schemas. "
                       "Identify shared utilities used across multiple bounded contexts.",
        "priority": "critical",
        "estimated_hours": 8,
    })
    task_counter += 1

    # Identify shared-kernel candidates: components referenced by multiple contexts
    comp_to_contexts = defaultdict(list)
    for b in boundaries:
        for cid in b["components"]:
            comp_to_contexts[cid].append(b["service_name"])
    shared_kernel_comps = [
        cid for cid, ctxs in comp_to_contexts.items() if len(ctxs) > 1
    ]

    if shared_kernel_comps:
        phase0_tasks.append({
            "task_id": f"decomp-task-{task_counter}",
            "task_type": "decompose",
            "title": "Extract shared kernel library",
            "description": (
                f"Extract {len(shared_kernel_comps)} shared components into a "
                "versioned shared kernel library with semantic versioning. "
                "Components: " + ", ".join(shared_kernel_comps[:10])
                + ("..." if len(shared_kernel_comps) > 10 else "")
            ),
            "priority": "high",
            "estimated_hours": max(4, len(shared_kernel_comps) * 2),
        })
        task_counter += 1

    plan["phases"].append({
        "phase": 0,
        "name": "Analysis & Shared Kernel Extraction",
        "tasks": phase0_tasks,
    })

    # --- Phases 1..N: Service extraction (ordered) ---
    for boundary in boundaries:
        phase_tasks = []
        svc = boundary["service_name"]
        order = boundary["extraction_order"]

        # Analyze
        phase_tasks.append({
            "task_id": f"decomp-task-{task_counter}",
            "task_type": "analyze",
            "title": f"Analyze {svc} boundary components",
            "description": f"Deep analysis of {len(boundary['components'])} components "
                           f"in {svc}. Readiness score: {boundary['readiness_score']}.",
            "priority": "high",
            "estimated_hours": 4,
        })
        task_counter += 1

        # Create API
        if boundary["apis"]:
            phase_tasks.append({
                "task_id": f"decomp-task-{task_counter}",
                "task_type": "create_api",
                "title": f"Define API contracts for {svc}",
                "description": f"Define OpenAPI spec for {len(boundary['apis'])} endpoints "
                               f"migrating to {svc}.",
                "priority": "high",
                "estimated_hours": 6,
            })
            task_counter += 1

        # Create ACL
        if boundary["coupling_ratio"] > 0.0:
            phase_tasks.append({
                "task_id": f"decomp-task-{task_counter}",
                "task_type": "create_acl",
                "title": f"Create anti-corruption layer for {svc}",
                "description": f"Build adapter layer to translate between legacy and "
                               f"{svc} domain models. Coupling ratio: "
                               f"{boundary['coupling_ratio']}.",
                "priority": "high",
                "estimated_hours": 8,
            })
            task_counter += 1

        # Extract service
        phase_tasks.append({
            "task_id": f"decomp-task-{task_counter}",
            "task_type": "extract_service",
            "title": f"Extract {svc} from monolith",
            "description": f"Move {len(boundary['components'])} components into "
                           f"standalone {target_architecture} service with its own "
                           f"build and deploy pipeline.",
            "priority": "critical",
            "estimated_hours": max(8, len(boundary["components"]) * 3),
        })
        task_counter += 1

        # Database migration if service owns tables
        if boundary["tables"]:
            phase_tasks.append({
                "task_id": f"decomp-task-{task_counter}",
                "task_type": "migrate_schema",
                "title": f"Migrate database schema for {svc}",
                "description": f"Isolate {len(boundary['tables'])} tables into "
                               f"dedicated database for {svc}: "
                               + ", ".join(boundary["tables"][:5])
                               + ("..." if len(boundary["tables"]) > 5 else ""),
                "priority": "high",
                "estimated_hours": max(4, len(boundary["tables"]) * 2),
            })
            task_counter += 1

        # Generate tests
        phase_tasks.append({
            "task_id": f"decomp-task-{task_counter}",
            "task_type": "generate_test",
            "title": f"Generate integration tests for {svc}",
            "description": f"Create BDD/TDD test suites verifying {svc} correctness "
                           f"against legacy behavior (contract tests + regression).",
            "priority": "high",
            "estimated_hours": 6,
        })
        task_counter += 1

        # Validate
        phase_tasks.append({
            "task_id": f"decomp-task-{task_counter}",
            "task_type": "validate",
            "title": f"Validate {svc} extraction",
            "description": f"Run parallel validation comparing legacy and extracted "
                           f"{svc} responses. Verify data consistency and performance.",
            "priority": "critical",
            "estimated_hours": 4,
        })
        task_counter += 1

        plan["phases"].append({
            "phase": order,
            "name": f"Extract {svc}",
            "service": svc,
            "readiness_score": boundary["readiness_score"],
            "tasks": phase_tasks,
        })

    # --- Final phase: Cross-service ACL + cutover ---
    final_tasks = []
    # ACL tasks between every pair of services that share edges
    for i, b1 in enumerate(boundaries):
        for b2 in boundaries[i + 1:]:
            # Check if they share external edges (simplified: both have coupling)
            if b1["coupling_ratio"] > 0 and b2["coupling_ratio"] > 0:
                final_tasks.append({
                    "task_id": f"decomp-task-{task_counter}",
                    "task_type": "create_acl",
                    "title": f"ACL: {b1['service_name']} <-> {b2['service_name']}",
                    "description": "Create cross-service anti-corruption layer adapters "
                                   "for communication between extracted services.",
                    "priority": "medium",
                    "estimated_hours": 6,
                })
                task_counter += 1

    final_tasks.append({
        "task_id": f"decomp-task-{task_counter}",
        "task_type": "validate",
        "title": "End-to-end system validation",
        "description": "Full integration test of all extracted services with ACLs, "
                       "API gateway routing, and database isolation verified.",
        "priority": "critical",
        "estimated_hours": 12,
    })
    task_counter += 1

    final_tasks.append({
        "task_id": f"decomp-task-{task_counter}",
        "task_type": "cutover",
        "title": "Production cutover and legacy decommission",
        "description": "Switch production traffic to modern services via strangler fig "
                       "facade. Decommission legacy monolith after validation period.",
        "priority": "critical",
        "estimated_hours": 8,
    })
    task_counter += 1

    plan["phases"].append({
        "phase": len(boundaries) + 1,
        "name": "Cross-Service Integration & Cutover",
        "tasks": final_tasks,
    })

    # Summary
    total_hours = sum(
        t["estimated_hours"]
        for phase in plan["phases"]
        for t in phase["tasks"]
    )
    plan["summary"] = {
        "total_phases": len(plan["phases"]),
        "total_tasks": task_counter,
        "total_estimated_hours": total_hours,
        "shared_kernel_components": len(shared_kernel_comps),
        "applicable_patterns": [
            pid for pid in patterns
            if pid in ("ddd_bounded_contexts", "strangler_fig", "anti_corruption_layer",
                       "database_per_service", "shared_kernel")
        ],
    }
    return plan


# ===================================================================
# Anti-corruption layer generation
# ===================================================================

def generate_anti_corruption_layer(app_id, service_boundary, db_path=None):
    """Generate ACL interface skeletons for a service boundary.

    Identifies cross-boundary dependencies and generates adapter interface
    definitions for each.

    Args:
        app_id: Legacy application ID.
        service_boundary: A boundary dict from suggest_service_boundaries.

    Returns dict: {interfaces: [{name, methods, legacy_side, modern_side}]}
    """
    conn = _get_db(db_path)
    try:
        members = set(service_boundary.get("components", []))
        if not members:
            return {"interfaces": []}

        placeholders = ",".join("?" for _ in members)

        # Cross-boundary outgoing deps
        outgoing = conn.execute(
            f"SELECT d.source_component_id, d.target_component_id, "
            f"d.dependency_type, d.weight, "
            f"cs.name AS source_name, ct.name AS target_name, "
            f"ct.qualified_name AS target_qualified "
            f"FROM legacy_dependencies d "
            f"JOIN legacy_components cs ON d.source_component_id = cs.id "
            f"JOIN legacy_components ct ON d.target_component_id = ct.id "
            f"WHERE d.legacy_app_id = ? "
            f"AND d.source_component_id IN ({placeholders}) "
            f"AND d.target_component_id NOT IN ({placeholders})",
            (app_id, *members, *members),
        ).fetchall()

        # Cross-boundary incoming deps
        incoming = conn.execute(
            f"SELECT d.source_component_id, d.target_component_id, "
            f"d.dependency_type, d.weight, "
            f"cs.name AS source_name, ct.name AS target_name, "
            f"cs.qualified_name AS source_qualified "
            f"FROM legacy_dependencies d "
            f"JOIN legacy_components cs ON d.source_component_id = cs.id "
            f"JOIN legacy_components ct ON d.target_component_id = ct.id "
            f"WHERE d.legacy_app_id = ? "
            f"AND d.target_component_id IN ({placeholders}) "
            f"AND d.source_component_id NOT IN ({placeholders})",
            (app_id, *members, *members),
        ).fetchall()

        # Load app info for language hint
        app_row = conn.execute(
            "SELECT primary_language FROM legacy_applications WHERE id = ?",
            (app_id,),
        ).fetchone()
    finally:
        conn.close()

    language = app_row["primary_language"] if app_row else "python"
    svc_name = service_boundary.get("service_name", "unknown_service")

    interfaces = []

    # Group outgoing by target component
    outgoing_grouped = defaultdict(list)
    for dep in outgoing:
        outgoing_grouped[dep["target_component_id"]].append(dep)

    for target_id, deps in outgoing_grouped.items():
        target_name = deps[0]["target_name"]
        target_qual = deps[0]["target_qualified"] or target_name
        dep_types = [d["dependency_type"] for d in deps]

        methods = []
        for dep in deps:
            dtype = dep["dependency_type"]
            dep["source_name"]
            if dtype == "method_call":
                methods.append(f"call_{target_name.lower()}(request)")
            elif dtype == "import":
                methods.append(f"get_{target_name.lower()}_adapter()")
            elif dtype in ("inheritance", "composition"):
                methods.append(f"adapt_{target_name.lower()}()")
            else:
                methods.append(f"translate_{target_name.lower()}(data)")

        # Deduplicate methods
        methods = sorted(set(methods))

        adapter_name = f"{svc_name}To{_to_pascal(target_name)}Adapter"
        interfaces.append({
            "name": adapter_name,
            "methods": methods,
            "legacy_side": target_qual,
            "modern_side": f"{svc_name}.adapters.{adapter_name}",
            "dependency_types": sorted(set(dep_types)),
            "direction": "outgoing",
        })

    # Group incoming by source component
    incoming_grouped = defaultdict(list)
    for dep in incoming:
        incoming_grouped[dep["source_component_id"]].append(dep)

    for source_id, deps in incoming_grouped.items():
        source_name = deps[0]["source_name"]
        source_qual = deps[0]["source_qualified"] or source_name
        dep_types = [d["dependency_type"] for d in deps]

        methods = []
        for dep in deps:
            dtype = dep["dependency_type"]
            tgt_name = dep["target_name"]
            if dtype == "method_call":
                methods.append(f"handle_{tgt_name.lower()}_call(request)")
            elif dtype == "import":
                methods.append(f"expose_{tgt_name.lower()}_interface()")
            else:
                methods.append(f"translate_from_{source_name.lower()}(data)")

        methods = sorted(set(methods))

        adapter_name = f"{_to_pascal(source_name)}To{svc_name}Adapter"
        interfaces.append({
            "name": adapter_name,
            "methods": methods,
            "legacy_side": source_qual,
            "modern_side": f"{svc_name}.adapters.{adapter_name}",
            "dependency_types": sorted(set(dep_types)),
            "direction": "incoming",
        })

    return {
        "service": svc_name,
        "language": language,
        "interface_count": len(interfaces),
        "interfaces": interfaces,
    }


def _to_pascal(name):
    """Convert a snake_case or kebab-case name to PascalCase."""
    return "".join(
        part.capitalize()
        for part in name.replace("-", "_").split("_")
    )


# ===================================================================
# API facade / gateway routing generation
# ===================================================================

def generate_api_facade(app_id, service_boundary=None, db_path=None):
    """Generate API facade routing configuration.

    Maps legacy endpoints to service assignments so an API gateway can
    route traffic to the correct extracted microservice.

    If service_boundary is None, generates routing for ALL boundaries.

    Returns:
        {routes: [{path, method, service, legacy_path}], config: {...}}
    """
    boundaries = (
        [service_boundary] if service_boundary
        else suggest_service_boundaries(app_id, db_path)
    )

    conn = _get_db(db_path)
    try:
        all_apis = conn.execute(
            "SELECT id, component_id, method, path FROM legacy_apis "
            "WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()
    finally:
        conn.close()

    # Build component -> service mapping
    comp_to_service = {}
    for b in boundaries:
        for cid in b["components"]:
            comp_to_service[cid] = b["service_name"]

    routes = []
    unrouted = []
    service_prefixes = defaultdict(set)

    for api in all_apis:
        service = comp_to_service.get(api["component_id"])
        path = api["path"]
        method = api["method"]

        if service:
            routes.append({
                "path": path,
                "method": method,
                "service": service,
                "legacy_path": path,
            })
            # Extract first path segment as prefix
            parts = [p for p in path.split("/") if p]
            if parts:
                service_prefixes[service].add(f"/{parts[0]}")
        else:
            unrouted.append({
                "path": path,
                "method": method,
                "service": "legacy_monolith",
                "legacy_path": path,
            })

    # Build prefix routing config
    prefix_routing = {}
    for svc, prefixes in service_prefixes.items():
        for prefix in sorted(prefixes):
            prefix_routing[prefix] = svc

    return {
        "app_id": app_id,
        "total_routes": len(routes),
        "unrouted_endpoints": len(unrouted),
        "routes": sorted(routes, key=lambda r: r["path"]),
        "unrouted": sorted(unrouted, key=lambda r: r["path"]),
        "prefix_routing": prefix_routing,
        "config": {
            "default_backend": "legacy_monolith",
            "routing_strategy": "path_prefix",
            "health_check_path": "/health",
            "timeout_seconds": 30,
        },
    }


# ===================================================================
# Effort estimation
# ===================================================================

def estimate_decomposition_effort(app_id, db_path=None):
    """Estimate decomposition effort in hours per service.

    Per service:
        base_hours    = sum(component LOC) / 15  (slower than greenfield)
        complexity_factor = avg(cyclomatic_complexity) / 5
        adjusted_hours = base_hours * max(1.0, complexity_factor)
        acl_overhead  = 20 hours per cross-boundary interface
        testing       = 30% of adjusted_hours
        total         = adjusted_hours + acl_overhead + testing

    Returns per-service and total estimates.
    """
    boundaries = suggest_service_boundaries(app_id, db_path)
    if not boundaries:
        return {"services": [], "total_hours": 0, "total_ftes_months": 0}

    conn = _get_db(db_path)
    try:
        components = conn.execute(
            "SELECT id, loc, cyclomatic_complexity FROM legacy_components "
            "WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchall()
    finally:
        conn.close()

    comp_data = {c["id"]: dict(c) for c in components}

    service_estimates = []
    grand_total = 0.0

    for boundary in boundaries:
        members = boundary["components"]
        total_loc = sum(comp_data.get(m, {}).get("loc", 0) or 0 for m in members)
        complexities = [
            comp_data.get(m, {}).get("cyclomatic_complexity", 0) or 0
            for m in members
        ]
        avg_complexity = (
            sum(complexities) / len(complexities) if complexities else 1.0
        )

        base_hours = total_loc / 15.0
        complexity_factor = max(1.0, avg_complexity / 5.0)
        adjusted_hours = base_hours * complexity_factor

        # ACL overhead: count cross-boundary interfaces
        acl = generate_anti_corruption_layer(app_id, boundary, db_path)
        acl_interface_count = acl.get("interface_count", 0)
        acl_overhead = acl_interface_count * 20.0

        # Testing overhead: 30 % of adjusted hours
        testing_hours = adjusted_hours * 0.30

        total = adjusted_hours + acl_overhead + testing_hours

        service_estimates.append({
            "service_name": boundary["service_name"],
            "extraction_order": boundary["extraction_order"],
            "component_count": len(members),
            "total_loc": total_loc,
            "avg_complexity": round(avg_complexity, 2),
            "base_hours": round(base_hours, 1),
            "complexity_factor": round(complexity_factor, 2),
            "adjusted_hours": round(adjusted_hours, 1),
            "acl_interfaces": acl_interface_count,
            "acl_overhead_hours": round(acl_overhead, 1),
            "testing_hours": round(testing_hours, 1),
            "total_hours": round(total, 1),
        })
        grand_total += total

    # FTE-months (assuming 160 hours/month)
    fte_months = grand_total / 160.0 if grand_total > 0 else 0.0

    return {
        "app_id": app_id,
        "services": sorted(service_estimates, key=lambda s: s["extraction_order"]),
        "total_hours": round(grand_total, 1),
        "total_ftes_months": round(fte_months, 1),
        "assumptions": {
            "loc_per_hour": 15,
            "baseline_complexity_divisor": 5,
            "acl_hours_per_interface": 20,
            "testing_overhead_pct": 30,
            "fte_hours_per_month": 160,
        },
    }


# ===================================================================
# Migration plan persistence
# ===================================================================

def create_migration_plan(
    project_id,
    app_id,
    strategy,
    target_lang=None,
    target_framework=None,
    target_db=None,
    target_arch="microservices",
    approach="strangler_fig",
    db_path=None,
):
    """Create a migration plan with tasks in the ICDEV database.

    Generates appropriate tasks based on strategy:
        rehost      — containerize, deploy
        replatform  — containerize, db-migrate, deploy
        refactor    — upgrade-version, upgrade-framework, test per component
        rearchitect — full decomposition tasks from generate_decomposition_plan
        retire      — document, decommission
        retain      — document only

    Returns the plan dict with all tasks.
    """
    if strategy not in VALID_STRATEGIES:
        raise ValueError(
            f"Invalid strategy '{strategy}'. Must be one of: {VALID_STRATEGIES}"
        )
    if approach not in VALID_APPROACHES:
        raise ValueError(
            f"Invalid approach '{approach}'. Must be one of: {VALID_APPROACHES}"
        )
    if target_arch and target_arch not in VALID_ARCHITECTURES:
        raise ValueError(
            f"Invalid architecture '{target_arch}'. Must be one of: {VALID_ARCHITECTURES}"
        )

    conn = _get_db(db_path)
    try:
        # Verify app exists
        app_row = conn.execute(
            "SELECT id, name FROM legacy_applications WHERE id = ?",
            (app_id,),
        ).fetchone()
        if not app_row:
            raise ValueError(f"Legacy application not found: {app_id}")

        app_name = app_row["name"]
        plan_id = _gen_id("mplan-")
        plan_name = f"{strategy}_{app_name}_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        now = datetime.now(timezone.utc).isoformat() + "Z"

        tasks = []

        if strategy == "rearchitect":
            # Full decomposition
            decomp = generate_decomposition_plan(app_id, target_arch, db_path)
            task_order = 0
            prev_task_id = None

            for phase in decomp.get("phases", []):
                for dtask in phase.get("tasks", []):
                    task_id = _gen_id("mtask-")
                    deps = json.dumps([prev_task_id] if prev_task_id else [])
                    tasks.append({
                        "id": task_id,
                        "plan_id": plan_id,
                        "legacy_component_id": None,
                        "task_type": dtask["task_type"],
                        "title": dtask["title"],
                        "description": dtask.get("description", ""),
                        "priority": dtask.get("priority", "medium"),
                        "status": "pending",
                        "estimated_hours": dtask.get("estimated_hours", 4),
                        "dependencies": deps,
                        "created_at": now,
                    })
                    prev_task_id = task_id
                    task_order += 1
        else:
            # Strategy-based tasks per component
            components = conn.execute(
                "SELECT id, name FROM legacy_components WHERE legacy_app_id = ?",
                (app_id,),
            ).fetchall()

            templates = STRATEGY_TASK_TEMPLATES.get(strategy, [])
            if not templates:
                # Fallback: single document task
                templates = [
                    ("document", "Document {name} for {strategy}", "low", 2),
                ]

            prev_task_id = None
            if components:
                for comp in components:
                    for ttype, title_tpl, priority, est_hours in templates:
                        task_id = _gen_id("mtask-")
                        title = title_tpl.format(
                            name=comp["name"],
                            strategy=strategy,
                        )
                        deps = json.dumps([prev_task_id] if prev_task_id else [])
                        tasks.append({
                            "id": task_id,
                            "plan_id": plan_id,
                            "legacy_component_id": comp["id"],
                            "task_type": ttype,
                            "title": title,
                            "description": f"{strategy} task for component {comp['name']}",
                            "priority": priority,
                            "status": "pending",
                            "estimated_hours": est_hours,
                            "dependencies": deps,
                            "created_at": now,
                        })
                        prev_task_id = task_id
            else:
                # No components found — create plan-level tasks
                for ttype, title_tpl, priority, est_hours in templates:
                    task_id = _gen_id("mtask-")
                    title = title_tpl.format(name=app_name, strategy=strategy)
                    deps = json.dumps([prev_task_id] if prev_task_id else [])
                    tasks.append({
                        "id": task_id,
                        "plan_id": plan_id,
                        "legacy_component_id": None,
                        "task_type": ttype,
                        "title": title,
                        "description": f"{strategy} task for application {app_name}",
                        "priority": priority,
                        "status": "pending",
                        "estimated_hours": est_hours,
                        "dependencies": deps,
                        "created_at": now,
                    })
                    prev_task_id = task_id

        total_estimated = sum(t["estimated_hours"] for t in tasks)

        # Insert plan
        conn.execute(
            "INSERT INTO migration_plans "
            "(id, legacy_app_id, plan_name, strategy, target_language, "
            "target_framework, target_database, target_architecture, "
            "migration_approach, total_tasks, status, estimated_hours, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)",
            (
                plan_id, app_id, plan_name, strategy,
                target_lang, target_framework, target_db,
                target_arch, approach,
                len(tasks), total_estimated, now, now,
            ),
        )

        # Insert tasks
        for t in tasks:
            conn.execute(
                "INSERT INTO migration_tasks "
                "(id, plan_id, legacy_component_id, task_type, title, "
                "description, priority, status, estimated_hours, "
                "dependencies, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    t["id"], t["plan_id"], t["legacy_component_id"],
                    t["task_type"], t["title"], t["description"],
                    t["priority"], t["status"], t["estimated_hours"],
                    t["dependencies"], t["created_at"],
                ),
            )

        conn.commit()
    finally:
        conn.close()

    plan_result = {
        "id": plan_id,
        "legacy_app_id": app_id,
        "plan_name": plan_name,
        "strategy": strategy,
        "target_language": target_lang,
        "target_framework": target_framework,
        "target_database": target_db,
        "target_architecture": target_arch,
        "migration_approach": approach,
        "total_tasks": len(tasks),
        "status": "draft",
        "estimated_hours": total_estimated,
        "created_at": now,
        "updated_at": now,
        "tasks": tasks,
    }
    return plan_result


# ===================================================================
# CLI
# ===================================================================

def _format_output(data, as_json=False):
    """Format output for terminal or JSON."""
    if as_json:
        return json.dumps(data, indent=2, default=str)
    return _pretty_print(data)


def _pretty_print(data, indent=0):
    """Recursively pretty-print a dict/list for human-readable terminal output."""
    lines = []
    prefix = "  " * indent

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_pretty_print(value, indent + 1))
            else:
                lines.append(f"{prefix}{key}: {value}")
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            if isinstance(item, dict):
                lines.append(f"{prefix}[{idx}]")
                lines.append(_pretty_print(item, indent + 1))
            else:
                lines.append(f"{prefix}- {item}")
    else:
        lines.append(f"{prefix}{data}")

    return "\n".join(lines)


def main():
    """CLI entry point for monolith decomposition planning."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI — ICDEV Monolith Decomposition Planner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Detect bounded contexts\n"
            "  python monolith_decomposer.py --app-id lapp-abc123 --detect-contexts --json\n"
            "\n"
            "  # Suggest service boundaries\n"
            "  python monolith_decomposer.py --app-id lapp-abc123 --suggest-boundaries\n"
            "\n"
            "  # Create a full rearchitect migration plan\n"
            "  python monolith_decomposer.py --app-id lapp-abc123 \\\n"
            "      --create-plan --project-id proj-xyz \\\n"
            "      --strategy rearchitect --target-arch microservices \\\n"
            "      --target-language python --target-framework fastapi \\\n"
            "      --target-db postgresql --approach strangler_fig --json\n"
            "\n"
            "  # Estimate decomposition effort\n"
            "  python monolith_decomposer.py --app-id lapp-abc123 --estimate-effort\n"
        ),
    )

    parser.add_argument(
        "--app-id", required=True,
        help="Legacy application ID (from legacy_applications table)",
    )
    parser.add_argument(
        "--detect-contexts", action="store_true",
        help="Detect bounded contexts using greedy modularity optimization",
    )
    parser.add_argument(
        "--suggest-boundaries", action="store_true",
        help="Suggest microservice service boundaries",
    )
    parser.add_argument(
        "--generate-plan", action="store_true",
        help="Generate a decomposition plan (does NOT persist to DB)",
    )
    parser.add_argument(
        "--generate-acl", action="store_true",
        help="Generate anti-corruption layer interface skeletons",
    )
    parser.add_argument(
        "--generate-facade", action="store_true",
        help="Generate API facade/gateway routing configuration",
    )
    parser.add_argument(
        "--estimate-effort", action="store_true",
        help="Estimate decomposition effort in hours per service",
    )
    parser.add_argument(
        "--create-plan", action="store_true",
        help="Create and persist a migration plan to the database",
    )
    parser.add_argument(
        "--project-id",
        help="Project ID (required for --create-plan)",
    )
    parser.add_argument(
        "--strategy",
        choices=VALID_STRATEGIES,
        default="rearchitect",
        help="Migration strategy (default: rearchitect)",
    )
    parser.add_argument(
        "--target-arch",
        choices=VALID_ARCHITECTURES,
        default="microservices",
        help="Target architecture (default: microservices)",
    )
    parser.add_argument(
        "--target-language",
        help="Target programming language (e.g., python, java, go)",
    )
    parser.add_argument(
        "--target-framework",
        help="Target framework (e.g., fastapi, spring-boot, gin)",
    )
    parser.add_argument(
        "--target-db",
        help="Target database (e.g., postgresql, dynamodb)",
    )
    parser.add_argument(
        "--approach",
        choices=VALID_APPROACHES,
        default="strangler_fig",
        help="Migration approach (default: strangler_fig)",
    )
    parser.add_argument(
        "--service-index", type=int, default=0,
        help="Service boundary index for --generate-acl / --generate-facade (default: 0)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="output_json",
        help="Output as JSON",
    )
    parser.add_argument(
        "--db-path",
        help="Override database path (default: data/icdev.db)",
    )

    args = parser.parse_args()
    db_path = args.db_path

    # Ensure at least one action is requested
    actions = [
        args.detect_contexts, args.suggest_boundaries,
        args.generate_plan, args.generate_acl,
        args.generate_facade, args.estimate_effort,
        args.create_plan,
    ]
    if not any(actions):
        parser.error(
            "No action specified. Use one of: --detect-contexts, "
            "--suggest-boundaries, --generate-plan, --generate-acl, "
            "--generate-facade, --estimate-effort, --create-plan"
        )

    try:
        if args.detect_contexts:
            result = detect_bounded_contexts(args.app_id, db_path)
            print(_format_output(
                {"bounded_contexts": result, "count": len(result)},
                args.output_json,
            ))

        elif args.suggest_boundaries:
            result = suggest_service_boundaries(args.app_id, db_path)
            print(_format_output(
                {"service_boundaries": result, "count": len(result)},
                args.output_json,
            ))

        elif args.generate_plan:
            result = generate_decomposition_plan(
                args.app_id, args.target_arch, db_path,
            )
            print(_format_output(result, args.output_json))

        elif args.generate_acl:
            boundaries = suggest_service_boundaries(args.app_id, db_path)
            if not boundaries:
                print("No service boundaries found.", file=sys.stderr)
                sys.exit(1)
            idx = min(args.service_index, len(boundaries) - 1)
            result = generate_anti_corruption_layer(
                args.app_id, boundaries[idx], db_path,
            )
            print(_format_output(result, args.output_json))

        elif args.generate_facade:
            boundaries = suggest_service_boundaries(args.app_id, db_path)
            if not boundaries:
                # Generate with empty boundaries (all routes go to legacy)
                result = generate_api_facade(args.app_id, db_path=db_path)
            elif args.service_index >= 0 and args.service_index < len(boundaries):
                result = generate_api_facade(
                    args.app_id, boundaries[args.service_index], db_path,
                )
            else:
                result = generate_api_facade(args.app_id, db_path=db_path)
            print(_format_output(result, args.output_json))

        elif args.estimate_effort:
            result = estimate_decomposition_effort(args.app_id, db_path)
            print(_format_output(result, args.output_json))

        elif args.create_plan:
            if not args.project_id:
                parser.error("--project-id is required for --create-plan")
            result = create_migration_plan(
                project_id=args.project_id,
                app_id=args.app_id,
                strategy=args.strategy,
                target_lang=args.target_language,
                target_framework=args.target_framework,
                target_db=args.target_db,
                target_arch=args.target_arch,
                approach=args.approach,
                db_path=db_path,
            )
            print(_format_output(result, args.output_json))

    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as exc:
        print(f"[DB ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
