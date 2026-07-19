#!/usr/bin/env python3
# CUI // SP-CTI
"""Ontology MCP server — tools for ontology federation and external standard mappings.

Each tool is registered via MCPServer.register_tool() and is also callable
from the unified MCP gateway via tool_registry.py.

Usage:
    python tools/mcp/ontology_server.py          # Start MCP server (stdio)
    python tools/mcp/ontology_server.py --help   # Show help
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.mcp.base_server import MCPServer  # noqa: E402


def handle_ontology_build(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Build the ontology federation — merge domain ontologies and compute subclass closure."""
    db_path = arguments.get("db_path")
    try:
        from tools.ontology.federation import build_federation

        result = build_federation(db_path=db_path)
        return {
            "classification": "CUI // SP-CTI",
            "success": True,
            "class_count": result.get("classes_total", 0),
            "property_count": result.get("properties_total", 0),
            "alignment_count": result.get("alignments_total", 0),
            "closure_count": result.get("closure_pairs", 0),
            "canonical_groups": result.get("canonical_groups", 0),
        }
    except ImportError as e:
        return {"error": f"Ontology federation not available: {e}", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}


def handle_ontology_query(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Keyword/pattern search over the ontology graph (heuristic term matching, not SPARQL)."""
    query = arguments.get("query", "")
    db_path = arguments.get("db_path")
    if not query:
        return {"error": "query is required", "results": []}

    try:
        from tools.ontology.federation import query_federation

        results = query_federation(query_text=query, db_path=db_path)
        return {
            "classification": "CUI // SP-CTI",
            "query": query,
            "engine": "heuristic_keyword_matcher",
            "sparql": False,
            "results": results.get("results", []),
            "results_count": results.get("results_count", 0),
            "patterns_detected": results.get("patterns_detected", {}),
        }
    except ImportError as e:
        return {"error": f"Ontology federation not available: {e}", "results": []}
    except Exception as e:
        return {"error": str(e), "results": []}


def handle_ontology_list_domains(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List all registered ontology domains and their class counts."""
    db_path = arguments.get("db_path")
    try:
        from tools.ontology.federation import list_domains

        result = list_domains(db_path=db_path)
        return {
            "classification": "CUI // SP-CTI",
            "domains": result.get("domains", []),
        }
    except ImportError as e:
        return {"error": f"Ontology federation not available: {e}", "domains": []}
    except Exception as e:
        return {"error": str(e), "domains": []}


def handle_ontology_export_mappings(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Export ICDEV ontology mappings to an external standard (STIX, OSCAL, GeoSPARQL, DCAT)."""
    export_format = arguments.get("format", "stix")
    icdev_class = arguments.get("icdev_class")
    try:
        from tools.ontology.external_mappings import export_to_stix, export_to_oscal, export_to_geosparql, load_mappings

        if export_format == "stix":
            result = export_to_stix(icdev_class=icdev_class)
        elif export_format == "oscal":
            result = export_to_oscal(icdev_class=icdev_class)
        elif export_format == "geosparql":
            result = export_to_geosparql(icdev_class=icdev_class)
        elif export_format == "all":
            result = load_mappings()
        else:
            return {"error": f"Unsupported format: {export_format}", "format": export_format}

        return {
            "classification": "CUI // SP-CTI",
            "format": export_format,
            "mappings": result,
        }
    except ImportError as e:
        return {"error": f"External mappings not available: {e}", "mappings": {}}
    except Exception as e:
        return {"error": str(e), "mappings": {}}


# ---------------------------------------------------------------------------
# MCPServer — register all ontology tools
# ---------------------------------------------------------------------------


def build_server() -> MCPServer:
    """Build and return a configured Ontology MCP server."""
    server = MCPServer(name="icdev-ontology", version="1.0.0")

    server.register_tool(
        name="ontology_build",
        description="Build the ontology federation — merge domain ontologies into ICDEV Core, resolve equivalent classes, and pre-compute subclass closure.",
        input_schema={
            "type": "object",
            "properties": {
                "db_path": {"type": "string", "description": "Optional path to ICDEV database"},
            },
        },
        handler=handle_ontology_build,
    )

    server.register_tool(
        name="ontology_query",
        description="Keyword/pattern search over the unified ontology graph. Heuristic term matching over a small set of hard-coded query shapes — NOT a SPARQL evaluator.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language query (keyword/pattern matched, not SPARQL)"},
                "db_path": {"type": "string", "description": "Optional path to ICDEV database"},
            },
            "required": ["query"],
        },
        handler=handle_ontology_query,
    )

    server.register_tool(
        name="ontology_list_domains",
        description="List all registered ontology domains and their class counts.",
        input_schema={
            "type": "object",
            "properties": {
                "db_path": {"type": "string", "description": "Optional path to ICDEV database"},
            },
        },
        handler=handle_ontology_list_domains,
    )

    server.register_tool(
        name="ontology_export_mappings",
        description="Export ICDEV ontology mappings to external standards (STIX 2.1, OSCAL, GeoSPARQL, DCAT).",
        input_schema={
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["stix", "oscal", "geosparql", "all"],
                    "description": "External standard to export mappings to",
                    "default": "stix",
                },
                "icdev_class": {"type": "string", "description": "Optional specific ICDEV class to filter mappings"},
            },
        },
        handler=handle_ontology_export_mappings,
    )

    return server


def main():
    """Build and run the Ontology MCP server over stdio."""
    build_server().run()


if __name__ == "__main__":
    main()
