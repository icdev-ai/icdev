#!/usr/bin/env python3
# CUI // SP-CTI
"""Documentation Generator for ICDEV Legacy Code Modernization.

Generates structured documentation from legacy code analysis data stored in
icdev.db. Produces Markdown documents with CUI markings for API docs, data
dictionaries, component documentation, dependency maps, and tech debt reports.

All generated documents include CUI // SP-CTI banners at top and bottom as
required for Controlled Unclassified Information handling.

Usage:
    python tools/modernization/doc_generator.py --app-id APP-001 --output-dir /tmp/docs --type all
    python tools/modernization/doc_generator.py --app-id APP-001 --output-dir /tmp/docs --type api
    python tools/modernization/doc_generator.py --app-id APP-001 --output-dir /tmp/docs --type all --json
"""

import argparse
import json
import os
import sqlite3
import sys
import textwrap
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

CUI_BANNER = "CUI // SP-CTI"
CUI_HEADER = f"<!-- {CUI_BANNER} -->"
CUI_FOOTER = f"<!-- {CUI_BANNER} -->"

# Dependency type abbreviations for the dependency matrix
DEP_TYPE_ABBREV = {
    "import": "I",
    "inheritance": "H",
    "composition": "C",
    "method_call": "M",
    "injection": "J",
    "aggregation": "A",
    "field_access": "F",
    "annotation": "@",
    "event": "E",
    "database": "D",
    "api_call": "P",
    "file_io": "O",
    "message_queue": "Q",
    "external_service": "X",
}

# Complexity rating thresholds
COMPLEXITY_RATINGS = [
    (5.0, "Low"),
    (10.0, "Medium"),
    (20.0, "High"),
    (float("inf"), "Very High"),
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None):
    """Return a sqlite3 connection with Row factory for dict-like access.

    Args:
        db_path: Optional override for the database path.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_app(conn, app_id):
    """Fetch a legacy application record by ID.

    Args:
        conn: Database connection.
        app_id: The legacy_applications.id value.

    Returns:
        dict of the application row.

    Raises:
        ValueError: If the application is not found.
    """
    row = conn.execute(
        "SELECT * FROM legacy_applications WHERE id = ?", (app_id,)
    ).fetchone()
    if not row:
        raise ValueError(
            f"Legacy application '{app_id}' not found in database."
        )
    return dict(row)


def _complexity_rating(score):
    """Return a human-readable complexity rating string.

    Args:
        score: Cyclomatic complexity score (float).

    Returns:
        One of 'Low', 'Medium', 'High', 'Very High'.
    """
    for threshold, label in COMPLEXITY_RATINGS:
        if score < threshold:
            return label
    return "Very High"


def _health_indicator(coupling, cohesion, complexity):
    """Compute a health indicator for a component.

    Health is derived from a weighted combination of coupling (lower is
    better), cohesion (higher is better), and complexity (lower is better).

    Args:
        coupling: Coupling score (0.0 - 1.0+).
        cohesion: Cohesion score (0.0 - 1.0, higher = better).
        complexity: Cyclomatic complexity score.

    Returns:
        Tuple of (label, emoji-free symbol) e.g. ('Healthy', '[OK]').
    """
    # Normalize: coupling 0-1 (lower=better), cohesion 0-1 (higher=better),
    # complexity mapped to 0-1 scale (lower=better)
    c_norm = min(coupling, 1.0)
    h_norm = max(min(cohesion, 1.0), 0.0)
    x_norm = min(complexity / 30.0, 1.0)

    # Score: 0 = worst, 1 = best
    score = (1.0 - c_norm) * 0.3 + h_norm * 0.3 + (1.0 - x_norm) * 0.4

    if score >= 0.7:
        return "Healthy", "[OK]"
    elif score >= 0.4:
        return "Warning", "[!!]"
    else:
        return "Critical", "[XX]"


def _abbreviate(name, max_len=15):
    """Abbreviate a name to fit within max_len characters.

    Args:
        name: The full name string.
        max_len: Maximum length (default 15).

    Returns:
        Truncated name with '..' suffix if it exceeded max_len.
    """
    if not name:
        return ""
    if len(name) <= max_len:
        return name
    return name[: max_len - 2] + ".."


def _ensure_dir(output_dir):
    """Ensure the output directory exists, creating it if necessary.

    Args:
        output_dir: Path to the output directory.

    Returns:
        Path object for the directory.
    """
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_doc(filepath, content):
    """Write a document with CUI banners to the given filepath.

    Args:
        filepath: Path to write the file.
        content: The markdown content (CUI banners are added automatically).

    Returns:
        The Path object for the written file.
    """
    path = Path(filepath)
    full_content = f"{CUI_HEADER}\n\n{content}\n\n{CUI_FOOTER}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(full_content)
    return path


def _now_iso():
    """Return the current UTC datetime as an ISO-formatted string."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 1. API Documentation
# ---------------------------------------------------------------------------

def generate_api_docs(app_id, output_dir, db_path=None):
    """Generate OpenAPI-like API documentation in Markdown.

    Queries all legacy_apis for the given app_id, groups endpoints by path
    prefix, and produces a comprehensive Markdown document with method,
    path, handler, parameters, request body, response type, and auth info.

    Args:
        app_id: The legacy application ID.
        output_dir: Directory to write the output file.
        db_path: Optional database path override.

    Returns:
        str: Path to the generated api_documentation.md file.
    """
    conn = _get_db(db_path)
    try:
        app = _get_app(conn, app_id)
        apis = conn.execute(
            "SELECT * FROM legacy_apis WHERE legacy_app_id = ? ORDER BY path, method",
            (app_id,),
        ).fetchall()
        apis = [dict(r) for r in apis]
    finally:
        conn.close()

    out_dir = _ensure_dir(output_dir)

    # Group APIs by path prefix (first two segments: /api/resource)
    grouped = defaultdict(list)
    for api in apis:
        parts = api["path"].strip("/").split("/")
        if len(parts) >= 2:
            prefix = "/" + "/".join(parts[:2])
        elif len(parts) == 1:
            prefix = "/" + parts[0]
        else:
            prefix = "/"
        grouped[prefix].append(api)

    # Build document
    lines = []
    lines.append(f"# API Documentation: {app['name']}")
    lines.append("")
    lines.append(f"**Application ID:** {app_id}")
    lines.append(f"**Generated:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total Endpoints:** {len(apis)}")
    methods_count = defaultdict(int)
    auth_count = 0
    for api in apis:
        methods_count[api.get("method", "UNKNOWN")] += 1
        if api.get("auth_required"):
            auth_count += 1
    lines.append(f"- **Authenticated Endpoints:** {auth_count}")
    lines.append(f"- **Unauthenticated Endpoints:** {len(apis) - auth_count}")
    lines.append(f"- **API Groups:** {len(grouped)}")
    lines.append("")
    lines.append("### Methods Distribution")
    lines.append("")
    lines.append("| Method | Count |")
    lines.append("|--------|-------|")
    for method in sorted(methods_count.keys()):
        lines.append(f"| {method} | {methods_count[method]} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Endpoint groups
    for prefix in sorted(grouped.keys()):
        endpoints = grouped[prefix]
        lines.append(f"## {prefix}")
        lines.append("")

        for ep in endpoints:
            method = ep.get("method", "UNKNOWN")
            path = ep.get("path", "")
            handler = ep.get("handler_function", "N/A")
            auth = "Yes" if ep.get("auth_required") else "No"

            lines.append(f"### `{method} {path}`")
            lines.append("")
            lines.append(f"- **Handler:** `{handler}`")
            lines.append(f"- **Authentication Required:** {auth}")

            # Response type
            resp = ep.get("response_type")
            if resp:
                lines.append(f"- **Response Type:** `{resp}`")

            lines.append("")

            # Parameters
            params_raw = ep.get("parameters")
            if params_raw:
                try:
                    params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
                except (json.JSONDecodeError, TypeError):
                    params = None

                if params and isinstance(params, list):
                    lines.append("**Parameters:**")
                    lines.append("")
                    lines.append("| Name | Type | In | Required | Description |")
                    lines.append("|------|------|----|----------|-------------|")
                    for p in params:
                        if isinstance(p, dict):
                            pname = p.get("name", "")
                            ptype = p.get("type", "string")
                            pin = p.get("in", "query")
                            preq = "Yes" if p.get("required") else "No"
                            pdesc = p.get("description", "")
                            lines.append(f"| `{pname}` | `{ptype}` | {pin} | {preq} | {pdesc} |")
                    lines.append("")
                elif params and isinstance(params, dict):
                    lines.append("**Parameters:**")
                    lines.append("")
                    lines.append("| Name | Type | Required | Description |")
                    lines.append("|------|------|----------|-------------|")
                    for pname, pinfo in params.items():
                        if isinstance(pinfo, dict):
                            ptype = pinfo.get("type", "string")
                            preq = "Yes" if pinfo.get("required") else "No"
                            pdesc = pinfo.get("description", "")
                        else:
                            ptype = str(pinfo)
                            preq = "No"
                            pdesc = ""
                        lines.append(f"| `{pname}` | `{ptype}` | {preq} | {pdesc} |")
                    lines.append("")

            # Request body
            body_raw = ep.get("request_body")
            if body_raw:
                try:
                    body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
                    body_str = json.dumps(body, indent=2)
                except (json.JSONDecodeError, TypeError):
                    body_str = str(body_raw)

                lines.append("**Request Body:**")
                lines.append("")
                lines.append("```json")
                lines.append(body_str)
                lines.append("```")
                lines.append("")

            lines.append("---")
            lines.append("")

    if not apis:
        lines.append("*No API endpoints discovered for this application.*")
        lines.append("")

    content = "\n".join(lines)
    filepath = out_dir / "api_documentation.md"
    _write_doc(filepath, content)
    return str(filepath)


# ---------------------------------------------------------------------------
# 2. Data Dictionary
# ---------------------------------------------------------------------------

def generate_data_dictionary(app_id, output_dir, db_path=None):
    """Generate a data dictionary from discovered database schemas.

    Queries all legacy_db_schemas for the given app_id, groups by table,
    documents each column, and maps foreign key relationships.

    Args:
        app_id: The legacy application ID.
        output_dir: Directory to write the output file.
        db_path: Optional database path override.

    Returns:
        str: Path to the generated data_dictionary.md file.
    """
    conn = _get_db(db_path)
    try:
        app = _get_app(conn, app_id)
        schemas = conn.execute(
            "SELECT * FROM legacy_db_schemas WHERE legacy_app_id = ? "
            "ORDER BY schema_name, table_name, column_name",
            (app_id,),
        ).fetchall()
        schemas = [dict(r) for r in schemas]
    finally:
        conn.close()

    out_dir = _ensure_dir(output_dir)

    # Group by schema_name.table_name
    tables = OrderedDict()
    relationships = []
    db_types = set()

    for col in schemas:
        schema = col.get("schema_name", "public")
        table = col.get("table_name", "unknown")
        key = f"{schema}.{table}"
        db_types.add(col.get("db_type", "unknown"))

        if key not in tables:
            tables[key] = {
                "schema": schema,
                "table": table,
                "db_type": col.get("db_type", "unknown"),
                "columns": [],
            }
        tables[key]["columns"].append(col)

        # Track FK relationships
        if col.get("is_foreign_key") and col.get("foreign_table"):
            relationships.append({
                "from_table": table,
                "from_column": col.get("column_name"),
                "to_table": col.get("foreign_table"),
                "to_column": col.get("foreign_column", "id"),
                "schema": schema,
            })

    # Build document
    lines = []
    lines.append(f"# Data Dictionary: {app['name']}")
    lines.append("")
    lines.append(f"**Application ID:** {app_id}")
    lines.append(f"**Generated:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total Tables:** {len(tables)}")
    total_cols = sum(len(t["columns"]) for t in tables.values())
    lines.append(f"- **Total Columns:** {total_cols}")
    lines.append(f"- **Database Type(s):** {', '.join(sorted(db_types))}")
    lines.append(f"- **Foreign Key Relationships:** {len(relationships)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Table of contents
    lines.append("## Tables")
    lines.append("")
    for idx, (key, tinfo) in enumerate(tables.items(), 1):
        col_count = len(tinfo["columns"])
        pk_count = sum(1 for c in tinfo["columns"] if c.get("is_primary_key"))
        fk_count = sum(1 for c in tinfo["columns"] if c.get("is_foreign_key"))
        lines.append(
            f"{idx}. **{tinfo['table']}** ({tinfo['schema']}) "
            f"- {col_count} columns, {pk_count} PK, {fk_count} FK"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Table details
    for key, tinfo in tables.items():
        lines.append(f"### {tinfo['table']}")
        lines.append("")
        lines.append(f"- **Schema:** {tinfo['schema']}")
        lines.append(f"- **Database:** {tinfo['db_type']}")
        lines.append("")
        lines.append("| Column | Data Type | Nullable | PK | FK | Default | Constraints |")
        lines.append("|--------|-----------|----------|----|----|---------|-------------|")

        for col in tinfo["columns"]:
            cname = col.get("column_name", "")
            dtype = col.get("data_type", "")
            nullable = "Yes" if col.get("is_nullable") else "No"
            pk = "PK" if col.get("is_primary_key") else ""
            fk_ref = ""
            if col.get("is_foreign_key") and col.get("foreign_table"):
                fk_col = col.get("foreign_column", "id")
                fk_ref = f"FK -> {col['foreign_table']}.{fk_col}"
            default = col.get("default_value", "") or ""
            constraints = col.get("constraints", "") or ""
            lines.append(
                f"| `{cname}` | `{dtype}` | {nullable} | {pk} | {fk_ref} | {default} | {constraints} |"
            )

        lines.append("")
        lines.append("---")
        lines.append("")

    # Relationships section
    if relationships:
        lines.append("## Relationships")
        lines.append("")
        lines.append("| From Table | From Column | To Table | To Column | Schema |")
        lines.append("|------------|-------------|----------|-----------|--------|")
        for rel in relationships:
            lines.append(
                f"| {rel['from_table']} | {rel['from_column']} "
                f"| {rel['to_table']} | {rel['to_column']} | {rel['schema']} |"
            )
        lines.append("")
    else:
        lines.append("## Relationships")
        lines.append("")
        lines.append("*No foreign key relationships discovered.*")
        lines.append("")

    if not schemas:
        lines.clear()
        lines.append(f"# Data Dictionary: {app['name']}")
        lines.append("")
        lines.append(f"**Application ID:** {app_id}")
        lines.append(f"**Generated:** {_now_iso()}")
        lines.append(f"**Classification:** {CUI_BANNER}")
        lines.append("")
        lines.append("*No database schemas discovered for this application.*")
        lines.append("")

    content = "\n".join(lines)
    filepath = out_dir / "data_dictionary.md"
    _write_doc(filepath, content)
    return str(filepath)


# ---------------------------------------------------------------------------
# 3. Component Documentation
# ---------------------------------------------------------------------------

def generate_component_docs(app_id, output_dir, db_path=None):
    """Generate component documentation from legacy analysis.

    Queries all legacy_components for the given app_id, groups by component
    type, and documents each component with metrics, complexity ratings,
    and health indicators.

    Args:
        app_id: The legacy application ID.
        output_dir: Directory to write the output file.
        db_path: Optional database path override.

    Returns:
        str: Path to the generated component_documentation.md file.
    """
    conn = _get_db(db_path)
    try:
        app = _get_app(conn, app_id)
        components = conn.execute(
            "SELECT * FROM legacy_components WHERE legacy_app_id = ? "
            "ORDER BY component_type, name",
            (app_id,),
        ).fetchall()
        components = [dict(r) for r in components]
    finally:
        conn.close()

    out_dir = _ensure_dir(output_dir)

    # Group by component_type
    by_type = OrderedDict()
    for comp in components:
        ctype = comp.get("component_type", "unknown")
        if ctype not in by_type:
            by_type[ctype] = []
        by_type[ctype].append(comp)

    # Build document
    lines = []
    lines.append(f"# Component Documentation: {app['name']}")
    lines.append("")
    lines.append(f"**Application ID:** {app_id}")
    lines.append(f"**Generated:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary statistics
    total_loc = sum(c.get("loc", 0) for c in components)
    avg_complexity = 0.0
    if components:
        avg_complexity = sum(c.get("cyclomatic_complexity", 0) for c in components) / len(components)

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total Components:** {len(components)}")
    lines.append(f"- **Component Types:** {len(by_type)}")
    lines.append(f"- **Total Lines of Code:** {total_loc:,}")
    lines.append(f"- **Average Complexity:** {avg_complexity:.1f} ({_complexity_rating(avg_complexity)})")
    lines.append("")

    # Health overview
    health_counts = {"Healthy": 0, "Warning": 0, "Critical": 0}
    for comp in components:
        label, _ = _health_indicator(
            comp.get("coupling_score", 0),
            comp.get("cohesion_score", 0),
            comp.get("cyclomatic_complexity", 0),
        )
        health_counts[label] += 1

    lines.append("### Health Overview")
    lines.append("")
    lines.append("| Status | Count | Percentage |")
    lines.append("|--------|-------|------------|")
    for status in ["Healthy", "Warning", "Critical"]:
        count = health_counts[status]
        pct = (count / len(components) * 100) if components else 0
        lines.append(f"| {status} | {count} | {pct:.1f}% |")
    lines.append("")

    # Type distribution
    lines.append("### Component Type Distribution")
    lines.append("")
    lines.append("| Type | Count | Total LOC |")
    lines.append("|------|-------|-----------|")
    for ctype, comps in by_type.items():
        type_loc = sum(c.get("loc", 0) for c in comps)
        lines.append(f"| {ctype} | {len(comps)} | {type_loc:,} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Detailed component tables by type
    for ctype, comps in by_type.items():
        lines.append(f"## {ctype.replace('_', ' ').title()} Components")
        lines.append("")
        lines.append(
            "| Name | File Path | LOC | Complexity | Rating | "
            "Coupling | Cohesion | Deps In | Deps Out | Health |"
        )
        lines.append(
            "|------|-----------|-----|------------|--------|"
            "----------|----------|---------|----------|--------|"
        )

        for comp in comps:
            name = comp.get("name", "")
            fpath = comp.get("file_path", "")
            loc = comp.get("loc", 0)
            cx = comp.get("cyclomatic_complexity", 0)
            rating = _complexity_rating(cx)
            coupling = comp.get("coupling_score", 0)
            cohesion = comp.get("cohesion_score", 0)
            deps_in = comp.get("dependencies_in", 0)
            deps_out = comp.get("dependencies_out", 0)
            _, symbol = _health_indicator(coupling, cohesion, cx)

            lines.append(
                f"| `{name}` | `{fpath}` | {loc:,} | {cx:.1f} | {rating} | "
                f"{coupling:.2f} | {cohesion:.2f} | {deps_in} | {deps_out} | {symbol} |"
            )

        lines.append("")

        # Per-type summary
        type_avg_cx = sum(c.get("cyclomatic_complexity", 0) for c in comps) / len(comps) if comps else 0
        type_avg_coupling = sum(c.get("coupling_score", 0) for c in comps) / len(comps) if comps else 0
        type_avg_cohesion = sum(c.get("cohesion_score", 0) for c in comps) / len(comps) if comps else 0

        lines.append(f"**{ctype} Summary:**")
        lines.append(f"- Average Complexity: {type_avg_cx:.1f} ({_complexity_rating(type_avg_cx)})")
        lines.append(f"- Average Coupling: {type_avg_coupling:.2f}")
        lines.append(f"- Average Cohesion: {type_avg_cohesion:.2f}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Component details with properties
    has_properties = [c for c in components if c.get("properties")]
    if has_properties:
        lines.append("## Component Properties")
        lines.append("")
        for comp in has_properties:
            name = comp.get("name", "")
            qname = comp.get("qualified_name", name)
            lines.append(f"### {name}")
            lines.append(f"- **Qualified Name:** `{qname}`")
            try:
                props = json.loads(comp["properties"]) if isinstance(comp["properties"], str) else comp["properties"]
                if isinstance(props, dict):
                    for pk, pv in props.items():
                        lines.append(f"- **{pk}:** {pv}")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"- **Raw Properties:** {comp['properties']}")
            lines.append("")

    if not components:
        lines.clear()
        lines.append(f"# Component Documentation: {app['name']}")
        lines.append("")
        lines.append(f"**Application ID:** {app_id}")
        lines.append(f"**Generated:** {_now_iso()}")
        lines.append(f"**Classification:** {CUI_BANNER}")
        lines.append("")
        lines.append("*No components discovered for this application.*")
        lines.append("")

    content = "\n".join(lines)
    filepath = out_dir / "component_documentation.md"
    _write_doc(filepath, content)
    return str(filepath)


# ---------------------------------------------------------------------------
# 4. Dependency Map
# ---------------------------------------------------------------------------

def generate_dependency_map(app_id, output_dir, db_path=None):
    """Generate a text-based dependency matrix and list view.

    Queries all legacy_dependencies for the given app_id, builds an
    adjacency matrix using abbreviated component names, and identifies
    dependency hotspots and potential circular dependencies.

    Args:
        app_id: The legacy application ID.
        output_dir: Directory to write the output file.
        db_path: Optional database path override.

    Returns:
        str: Path to the generated dependency_map.md file.
    """
    conn = _get_db(db_path)
    try:
        app = _get_app(conn, app_id)

        deps = conn.execute(
            "SELECT d.*, "
            "  sc.name AS source_name, sc.component_type AS source_type, "
            "  tc.name AS target_name, tc.component_type AS target_type "
            "FROM legacy_dependencies d "
            "LEFT JOIN legacy_components sc ON d.source_component_id = sc.id "
            "LEFT JOIN legacy_components tc ON d.target_component_id = tc.id "
            "WHERE d.legacy_app_id = ? "
            "ORDER BY sc.name, tc.name",
            (app_id,),
        ).fetchall()
        deps = [dict(r) for r in deps]

        # Get all component names for the matrix
        components = conn.execute(
            "SELECT id, name FROM legacy_components WHERE legacy_app_id = ? ORDER BY name",
            (app_id,),
        ).fetchall()
        comp_map = {row["id"]: row["name"] for row in components}
    finally:
        conn.close()

    out_dir = _ensure_dir(output_dir)

    # Build adjacency data
    # adj[source_id][target_id] = set of dep types
    adj = defaultdict(lambda: defaultdict(set))
    involved_ids = set()
    in_degree = defaultdict(int)
    out_degree = defaultdict(int)

    for dep in deps:
        src = dep.get("source_component_id", "")
        tgt = dep.get("target_component_id", "")
        dtype = dep.get("dependency_type", "unknown")
        if src and tgt:
            adj[src][tgt].add(dtype)
            involved_ids.add(src)
            involved_ids.add(tgt)
            out_degree[src] += 1
            in_degree[tgt] += 1

    # Ordered list of component IDs that participate in dependencies
    ordered_ids = sorted(involved_ids, key=lambda cid: comp_map.get(cid, cid))
    abbrev_names = {cid: _abbreviate(comp_map.get(cid, cid)) for cid in ordered_ids}

    # Detect circular dependencies (simple: A->B and B->A)
    circular = []
    checked = set()
    for src in adj:
        for tgt in adj[src]:
            if tgt in adj and src in adj[tgt]:
                pair = tuple(sorted([src, tgt]))
                if pair not in checked:
                    checked.add(pair)
                    circular.append((
                        comp_map.get(src, src),
                        comp_map.get(tgt, tgt),
                    ))

    # Build document
    lines = []
    lines.append(f"# Dependency Map: {app['name']}")
    lines.append("")
    lines.append(f"**Application ID:** {app_id}")
    lines.append(f"**Generated:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total Dependencies:** {len(deps)}")
    lines.append(f"- **Components Involved:** {len(involved_ids)}")
    lines.append(f"- **Circular Dependencies Detected:** {len(circular)}")
    lines.append("")

    # Dependency type legend
    lines.append("### Dependency Type Legend")
    lines.append("")
    lines.append("| Abbreviation | Type |")
    lines.append("|-------------|------|")
    for dtype, abbr in sorted(DEP_TYPE_ABBREV.items(), key=lambda x: x[1]):
        lines.append(f"| {abbr} | {dtype} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Most coupled components (by total degree)
    total_degree = defaultdict(int)
    for cid in involved_ids:
        total_degree[cid] = in_degree.get(cid, 0) + out_degree.get(cid, 0)

    top_coupled = sorted(total_degree.items(), key=lambda x: x[1], reverse=True)[:10]

    if top_coupled:
        lines.append("## Most Coupled Components")
        lines.append("")
        lines.append("| Rank | Component | In | Out | Total |")
        lines.append("|------|-----------|-----|-----|-------|")
        for rank, (cid, total) in enumerate(top_coupled, 1):
            cname = comp_map.get(cid, cid)
            lines.append(
                f"| {rank} | `{cname}` | {in_degree.get(cid, 0)} "
                f"| {out_degree.get(cid, 0)} | {total} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    # Dependency hotspots (components with high fan-out)
    hotspots = sorted(out_degree.items(), key=lambda x: x[1], reverse=True)[:10]
    if hotspots:
        lines.append("## Dependency Hotspots (High Fan-Out)")
        lines.append("")
        lines.append("| Component | Outgoing Dependencies | Targets |")
        lines.append("|-----------|----------------------|---------|")
        for cid, count in hotspots:
            targets = [comp_map.get(t, t) for t in adj.get(cid, {})]
            target_str = ", ".join(sorted(targets)[:5])
            if len(targets) > 5:
                target_str += f" (+{len(targets) - 5} more)"
            cname = comp_map.get(cid, cid)
            lines.append(f"| `{cname}` | {count} | {target_str} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Circular dependencies
    if circular:
        lines.append("## Circular Dependencies")
        lines.append("")
        lines.append("The following component pairs have bidirectional dependencies:")
        lines.append("")
        lines.append("| Component A | Component B |")
        lines.append("|-------------|-------------|")
        for a, b in circular:
            lines.append(f"| `{a}` | `{b}` |")
        lines.append("")
        lines.append(
            "> Circular dependencies increase coupling and make refactoring "
            "more difficult. Consider introducing interfaces or mediator patterns."
        )
        lines.append("")
        lines.append("---")
        lines.append("")

    # Dependency Matrix (ASCII table)
    if ordered_ids and len(ordered_ids) <= 30:
        lines.append("## Dependency Matrix")
        lines.append("")
        lines.append(
            "Rows = source components, Columns = target components. "
            "Cell values indicate dependency type abbreviations."
        )
        lines.append("")

        # Build the matrix as a fixed-width ASCII table
        col_width = 17
        header_pad = " " * col_width
        # Column headers
        header_line = header_pad + " | "
        header_line += " | ".join(
            abbrev_names[cid].ljust(col_width) for cid in ordered_ids
        )
        lines.append("```")
        lines.append(header_line)
        sep = "-" * col_width + "-+-" + "-+-".join(
            "-" * col_width for _ in ordered_ids
        )
        lines.append(sep)

        for src_id in ordered_ids:
            row_label = abbrev_names[src_id].ljust(col_width)
            cells = []
            for tgt_id in ordered_ids:
                if src_id == tgt_id:
                    cells.append(".".center(col_width))
                elif tgt_id in adj.get(src_id, {}):
                    types = adj[src_id][tgt_id]
                    abbrs = "".join(sorted(
                        DEP_TYPE_ABBREV.get(t, "?") for t in types
                    ))
                    cells.append(abbrs.center(col_width))
                else:
                    cells.append(" " * col_width)
            lines.append(row_label + " | " + " | ".join(cells))

        lines.append("```")
        lines.append("")
    elif ordered_ids:
        lines.append("## Dependency Matrix")
        lines.append("")
        lines.append(
            f"*Matrix view omitted: {len(ordered_ids)} components exceed "
            "the 30-component limit for readable ASCII matrices. "
            "See the list view below.*"
        )
        lines.append("")

    lines.append("---")
    lines.append("")

    # List view (always included)
    lines.append("## Dependency List")
    lines.append("")
    if deps:
        lines.append("| Source | Target | Type | Weight |")
        lines.append("|--------|--------|------|--------|")
        for dep in deps:
            src_name = dep.get("source_name", dep.get("source_component_id", "?"))
            tgt_name = dep.get("target_name", dep.get("target_component_id", "?"))
            dtype = dep.get("dependency_type", "unknown")
            weight = dep.get("weight", 1.0)
            lines.append(f"| `{src_name}` | `{tgt_name}` | {dtype} | {weight:.1f} |")
        lines.append("")
    else:
        lines.append("*No dependencies discovered for this application.*")
        lines.append("")

    content = "\n".join(lines)
    filepath = out_dir / "dependency_map.md"
    _write_doc(filepath, content)
    return str(filepath)


# ---------------------------------------------------------------------------
# 5. Tech Debt Report
# ---------------------------------------------------------------------------

def generate_tech_debt_report(app_id, output_dir, db_path=None):
    """Generate a technical debt analysis report.

    Ranks components by tech debt contribution using a composite score of
    complexity * LOC * coupling, identifies debt hotspots, and provides
    estimated remediation hours per category.

    Args:
        app_id: The legacy application ID.
        output_dir: Directory to write the output file.
        db_path: Optional database path override.

    Returns:
        str: Path to the generated tech_debt_report.md file.
    """
    conn = _get_db(db_path)
    try:
        app = _get_app(conn, app_id)
        components = conn.execute(
            "SELECT * FROM legacy_components WHERE legacy_app_id = ? ORDER BY name",
            (app_id,),
        ).fetchall()
        components = [dict(r) for r in components]
    finally:
        conn.close()

    out_dir = _ensure_dir(output_dir)

    # Calculate tech debt score for each component
    # Debt = cyclomatic_complexity * LOC * (1 + coupling_score)
    # This captures complex, large, tightly-coupled components as high debt
    for comp in components:
        cx = comp.get("cyclomatic_complexity", 0)
        loc = comp.get("loc", 0)
        coupling = comp.get("coupling_score", 0)
        comp["debt_score"] = cx * loc * (1.0 + coupling)

        # Estimated remediation hours:
        # Base: 0.5 hours per 100 LOC * complexity_factor
        complexity_factor = 1.0
        if cx >= 20:
            complexity_factor = 3.0
        elif cx >= 10:
            complexity_factor = 2.0
        elif cx >= 5:
            complexity_factor = 1.5
        comp["remediation_hours"] = (loc / 100.0) * 0.5 * complexity_factor

    # Sort by debt_score descending
    ranked = sorted(components, key=lambda c: c.get("debt_score", 0), reverse=True)

    # Build document
    lines = []
    lines.append(f"# Technical Debt Report: {app['name']}")
    lines.append("")
    lines.append(f"**Application ID:** {app_id}")
    lines.append(f"**Generated:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Application-level metrics
    lines.append("## Application Overview")
    lines.append("")
    lines.append(f"- **Application:** {app.get('name', 'N/A')}")
    lines.append(f"- **Language:** {app.get('primary_language', 'N/A')} {app.get('language_version', '')}")
    lines.append(f"- **Framework:** {app.get('framework', 'N/A')} {app.get('framework_version', '')}")
    lines.append(f"- **Total LOC:** {app.get('loc_total', 0):,}")
    lines.append(f"- **Code LOC:** {app.get('loc_code', 0):,}")
    lines.append(f"- **File Count:** {app.get('file_count', 0):,}")
    lines.append(f"- **Complexity Score:** {app.get('complexity_score', 0):.1f}")
    lines.append(f"- **Maintainability Index:** {app.get('maintainability_index', 0):.1f}")
    lines.append(f"- **Estimated Tech Debt (app-level):** {app.get('tech_debt_hours', 0):.1f} hours")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Overall debt summary
    total_debt_hours = sum(c.get("remediation_hours", 0) for c in components)
    total_debt_score = sum(c.get("debt_score", 0) for c in components)

    lines.append("## Debt Summary")
    lines.append("")
    lines.append(f"- **Total Components Analyzed:** {len(components)}")
    lines.append(f"- **Total Estimated Remediation Hours:** {total_debt_hours:.1f}")
    lines.append(f"- **Aggregate Debt Score:** {total_debt_score:,.0f}")
    lines.append("")

    # Debt severity breakdown
    severity_counts = {"Very High": 0, "High": 0, "Medium": 0, "Low": 0}
    for comp in components:
        cx = comp.get("cyclomatic_complexity", 0)
        severity_counts[_complexity_rating(cx)] += 1

    lines.append("### Complexity Distribution")
    lines.append("")
    lines.append("| Complexity | Count | Percentage |")
    lines.append("|------------|-------|------------|")
    for sev in ["Very High", "High", "Medium", "Low"]:
        count = severity_counts[sev]
        pct = (count / len(components) * 100) if components else 0
        lines.append(f"| {sev} | {count} | {pct:.1f}% |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Top 10 debt hotspots
    top10 = ranked[:10]
    if top10:
        lines.append("## Top 10 Debt Hotspots")
        lines.append("")
        lines.append(
            "| Rank | Component | Type | LOC | Complexity | Coupling | "
            "Debt Score | Est. Hours |"
        )
        lines.append(
            "|------|-----------|------|-----|------------|----------|"
            "------------|------------|"
        )
        for rank, comp in enumerate(top10, 1):
            lines.append(
                f"| {rank} | `{comp.get('name', '')}` "
                f"| {comp.get('component_type', '')} "
                f"| {comp.get('loc', 0):,} "
                f"| {comp.get('cyclomatic_complexity', 0):.1f} "
                f"| {comp.get('coupling_score', 0):.2f} "
                f"| {comp.get('debt_score', 0):,.0f} "
                f"| {comp.get('remediation_hours', 0):.1f} |"
            )
        lines.append("")

        # Top 10 contributes what %
        top10_debt = sum(c.get("debt_score", 0) for c in top10)
        pct = (top10_debt / total_debt_score * 100) if total_debt_score else 0
        lines.append(
            f"> The top 10 components account for **{pct:.1f}%** of total "
            f"technical debt ({top10_debt:,.0f} of {total_debt_score:,.0f})."
        )
        lines.append("")
        lines.append("---")
        lines.append("")

    # Debt distribution by component type
    type_debt = defaultdict(lambda: {"count": 0, "debt_score": 0, "hours": 0, "loc": 0})
    for comp in components:
        ctype = comp.get("component_type", "unknown")
        type_debt[ctype]["count"] += 1
        type_debt[ctype]["debt_score"] += comp.get("debt_score", 0)
        type_debt[ctype]["hours"] += comp.get("remediation_hours", 0)
        type_debt[ctype]["loc"] += comp.get("loc", 0)

    if type_debt:
        lines.append("## Debt Distribution by Component Type")
        lines.append("")
        lines.append("| Type | Components | Total LOC | Debt Score | Est. Hours | Avg Debt/Component |")
        lines.append("|------|------------|-----------|------------|------------|-------------------|")
        for ctype in sorted(type_debt.keys(), key=lambda t: type_debt[t]["debt_score"], reverse=True):
            td = type_debt[ctype]
            avg_debt = td["debt_score"] / td["count"] if td["count"] else 0
            lines.append(
                f"| {ctype} | {td['count']} | {td['loc']:,} "
                f"| {td['debt_score']:,.0f} | {td['hours']:.1f} | {avg_debt:,.0f} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    # Estimated remediation by category
    lines.append("## Estimated Remediation by Category")
    lines.append("")
    lines.append("| Category | Description | Est. Hours |")
    lines.append("|----------|-------------|------------|")

    # Break down by complexity rating
    cat_hours = {"Very High": 0, "High": 0, "Medium": 0, "Low": 0}
    cat_desc = {
        "Very High": "Major refactoring or rewrite required",
        "High": "Significant refactoring needed",
        "Medium": "Moderate cleanup and simplification",
        "Low": "Minor improvements and code hygiene",
    }
    for comp in components:
        cx = comp.get("cyclomatic_complexity", 0)
        rating = _complexity_rating(cx)
        cat_hours[rating] += comp.get("remediation_hours", 0)

    for cat in ["Very High", "High", "Medium", "Low"]:
        lines.append(f"| {cat} Complexity | {cat_desc[cat]} | {cat_hours[cat]:.1f} |")
    lines.append(f"| **Total** | **All categories** | **{total_debt_hours:.1f}** |")
    lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    lines.append("")
    if severity_counts["Very High"] > 0:
        lines.append(
            f"1. **Immediate Attention:** {severity_counts['Very High']} component(s) with "
            "Very High complexity require prioritized refactoring or decomposition."
        )
    if severity_counts["High"] > 0:
        lines.append(
            f"2. **Near-Term:** {severity_counts['High']} component(s) with High complexity "
            "should be scheduled for refactoring in the next sprint cycle."
        )
    if top10:
        top_name = top10[0].get("name", "unknown")
        lines.append(
            f"3. **Highest Priority:** `{top_name}` has the highest debt score and "
            "should be the first target for remediation."
        )
    lines.append(
        f"4. **Total Effort:** Plan for approximately {total_debt_hours:.0f} hours of "
        "remediation work across all components."
    )
    lines.append("")

    if not components:
        lines.clear()
        lines.append(f"# Technical Debt Report: {app['name']}")
        lines.append("")
        lines.append(f"**Application ID:** {app_id}")
        lines.append(f"**Generated:** {_now_iso()}")
        lines.append(f"**Classification:** {CUI_BANNER}")
        lines.append("")
        lines.append("*No components analyzed for this application.*")
        lines.append("")

    content = "\n".join(lines)
    filepath = out_dir / "tech_debt_report.md"
    _write_doc(filepath, content)
    return str(filepath)


# ---------------------------------------------------------------------------
# 6. Full Documentation (Orchestrator)
# ---------------------------------------------------------------------------

def generate_full_documentation(app_id, output_dir, db_path=None):
    """Orchestrate generation of all documentation types.

    Creates the output directory, calls all individual generators, and
    produces an index.md linking to every generated document.

    Args:
        app_id: The legacy application ID.
        output_dir: Directory to write all output files.
        db_path: Optional database path override.

    Returns:
        dict with keys:
            - files: dict mapping doc type to file path
            - index: path to the generated index.md
            - app_id: the application ID
            - generated_at: ISO timestamp
    """
    out_dir = _ensure_dir(output_dir)

    # Verify app exists before generating anything
    conn = _get_db(db_path)
    try:
        app = _get_app(conn, app_id)
    finally:
        conn.close()

    files = {}
    errors = {}

    # Generate each document type
    generators = [
        ("api", "API Documentation", generate_api_docs),
        ("data_dictionary", "Data Dictionary", generate_data_dictionary),
        ("components", "Component Documentation", generate_component_docs),
        ("dependencies", "Dependency Map", generate_dependency_map),
        ("tech_debt", "Tech Debt Report", generate_tech_debt_report),
    ]

    for key, label, func in generators:
        try:
            filepath = func(app_id, str(out_dir), db_path)
            files[key] = filepath
        except Exception as e:
            errors[key] = str(e)

    # Generate index.md
    lines = []
    lines.append(f"# Documentation Index: {app.get('name', app_id)}")
    lines.append("")
    lines.append(f"**Application ID:** {app_id}")
    lines.append(f"**Generated:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Generated Documents")
    lines.append("")

    for key, label, _ in generators:
        if key in files:
            filename = Path(files[key]).name
            lines.append(f"- [{label}]({filename})")
        elif key in errors:
            lines.append(f"- {label} -- **FAILED:** {errors[key]}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Application Summary")
    lines.append("")
    lines.append(f"- **Name:** {app.get('name', 'N/A')}")
    lines.append(f"- **Language:** {app.get('primary_language', 'N/A')} {app.get('language_version', '')}")
    lines.append(f"- **Framework:** {app.get('framework', 'N/A')} {app.get('framework_version', '')}")
    lines.append(f"- **Total LOC:** {app.get('loc_total', 0):,}")
    lines.append(f"- **Files:** {app.get('file_count', 0):,}")
    lines.append(f"- **Complexity:** {app.get('complexity_score', 0):.1f}")
    lines.append(f"- **Maintainability:** {app.get('maintainability_index', 0):.1f}")
    lines.append(f"- **Tech Debt (est):** {app.get('tech_debt_hours', 0):.1f} hours")
    lines.append("")

    if errors:
        lines.append("## Errors")
        lines.append("")
        for key, err in errors.items():
            lines.append(f"- **{key}:** {err}")
        lines.append("")

    content = "\n".join(lines)
    index_path = out_dir / "index.md"
    _write_doc(index_path, content)

    result = {
        "files": files,
        "index": str(index_path),
        "app_id": app_id,
        "generated_at": _now_iso(),
        "document_count": len(files),
        "error_count": len(errors),
    }
    if errors:
        result["errors"] = errors

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DOC_TYPES = {
    "api": ("API Documentation", generate_api_docs),
    "data-dictionary": ("Data Dictionary", generate_data_dictionary),
    "components": ("Component Documentation", generate_component_docs),
    "dependencies": ("Dependency Map", generate_dependency_map),
    "tech-debt": ("Tech Debt Report", generate_tech_debt_report),
    "all": ("Full Documentation", generate_full_documentation),
}


def main():
    """CLI entry point for the documentation generator."""
    parser = argparse.ArgumentParser(
        description="Generate documentation from legacy code analysis data.",
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s --app-id APP-001 --output-dir ./docs --type all
              %(prog)s --app-id APP-001 --output-dir ./docs --type api
              %(prog)s --app-id APP-001 --output-dir ./docs --type tech-debt --json
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--app-id",
        required=True,
        help="Legacy application ID (from legacy_applications table).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write generated documentation files.",
    )
    parser.add_argument(
        "--type",
        choices=list(DOC_TYPES.keys()),
        default="all",
        help="Type of documentation to generate (default: all).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output result as JSON (file paths and summary).",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Override path to icdev.db database.",
    )

    args = parser.parse_args()

    try:
        if args.type == "all":
            result = generate_full_documentation(
                args.app_id, args.output_dir, args.db_path
            )
            if args.json_output:
                print(json.dumps(result, indent=2))
            else:
                print(f"Documentation generated for application: {args.app_id}")
                print(f"Output directory: {args.output_dir}")
                print(f"Documents generated: {result['document_count']}")
                if result.get("error_count", 0):
                    print(f"Errors: {result['error_count']}")
                print(f"Index: {result['index']}")
                for key, path in result.get("files", {}).items():
                    print(f"  {key}: {path}")
                if result.get("errors"):
                    print("\nErrors:")
                    for key, err in result["errors"].items():
                        print(f"  {key}: {err}")
        else:
            label, func = DOC_TYPES[args.type]
            filepath = func(args.app_id, args.output_dir, args.db_path)
            if args.json_output:
                result = {
                    "type": args.type,
                    "label": label,
                    "file": filepath,
                    "app_id": args.app_id,
                    "generated_at": _now_iso(),
                }
                print(json.dumps(result, indent=2))
            else:
                print(f"{label} generated: {filepath}")

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
