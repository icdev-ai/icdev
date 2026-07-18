# CUI // SP-CTI
"""Constants for the Unified System Graph."""

# Node types and their display colors — matches awareness KG + adds new types
NODE_TYPES = {
    "skill":           {"color": "#42a5f5", "label": "Skill"},
    "tool":            {"color": "#26c6da", "label": "Tool"},
    "reflex":          {"color": "#ef5350", "label": "Reflex"},
    "route":           {"color": "#66bb6a", "label": "Route"},
    "canvas_module":   {"color": "#66bb6a", "label": "Canvas Module"},
    "goal":            {"color": "#ab47bc", "label": "Goal"},
    "mcp_server":      {"color": "#ff7043", "label": "MCP Server"},
    "db_table":        {"color": "#8d6e63", "label": "DB Table"},
    "migration":       {"color": "#78909c", "label": "Migration"},
    "kanban_task":     {"color": "#ffa726", "label": "Kanban Task"},
    "agent":           {"color": "#ec407a", "label": "Agent"},
    "blueprint":       {"color": "#29b6f6", "label": "Blueprint"},
    "other":           {"color": "#78909c", "label": "Other"},
}

# Edge types
EDGE_TYPES = {
    "depends_on":    {"color": "#ef5350", "label": "Depends On"},
    "calls":         {"color": "#42a5f5", "label": "Calls"},
    "registers":     {"color": "#ff7043", "label": "Registers"},
    "implements":    {"color": "#66bb6a", "label": "Implements"},
    "migrates":      {"color": "#8d6e63", "label": "Migrates"},
    "triggers":      {"color": "#ffa726", "label": "Triggers"},
    "related":       {"color": "#78909c", "label": "Related"},
}

# Cluster palette — assigned to detected communities
CLUSTER_COLORS = [
    "#1565c0", "#6a1b9a", "#00695c", "#b71c1c",
    "#e65100", "#1b5e20", "#4a148c", "#006064",
    "#37474f", "#4e342e", "#880e4f", "#0d47a1",
]

# How many hops to traverse for graph layout (spring layout k-factor)
LAYOUT_K = 2.0
LAYOUT_ITERATIONS = 50

# Maximum nodes to return without filtering (above this, cluster summary mode)
MAX_FULL_NODES = 5000

# Graph sources federated
GRAPH_SOURCES = [
    "awareness_kg",    # kg_nodes / kg_edges
    "canvas_kg",       # canvas_kg_nodes / canvas_kg_edges
    "kanban_deps",     # kanban_tasks + kanban_task_deps
    "goals",           # goals/ directory scan
    "migrations",      # tools/db/migrations/ dependency chain
    "codebase",        # routes, blueprints, db_tables, agents from tools/
    "ndc",             # Network Design Canvas — canvas node + tables/route/SDC edges
]

# Ontology class mappings for KG enrichment
SYSTEM_GRAPH_ONTOLOGY_MAP: dict[str, str] = {
    "skill":  "core:Concept",
    "tool":   "core:Resource",
    "reflex": "core:Concept",
    "route":  "core:Resource",
}
