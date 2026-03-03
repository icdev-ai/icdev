#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Diagrams API Blueprint — serves Mermaid diagram catalog and definitions.

Endpoints:
    GET /api/diagrams/       — List all diagrams (filtered by ?role= query param)
    GET /api/diagrams/<id>   — Get full diagram definition with Mermaid source

ADR: D-M2 (dual storage — inline templates + API endpoint)
"""

import sys
from pathlib import Path

# Ensure parent packages importable when run via Flask
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Blueprint, jsonify, request as flask_request

from tools.dashboard.diagram_definitions import get_catalog_for_role, get_diagram

diagrams_api = Blueprint("diagrams_api", __name__, url_prefix="/api/diagrams")


@diagrams_api.route("/", methods=["GET"])
def list_diagrams():
    """Return diagram catalog, optionally filtered by role.

    Query params:
        role (str): Filter to diagrams visible for this role (pm, developer, isso, co).
        category (str): Filter by category (workflows, compliance, security, architecture).
    """
    role = flask_request.args.get("role", None)
    category = flask_request.args.get("category", None)

    catalog = get_catalog_for_role(role)

    if category:
        catalog = [d for d in catalog if d.get("category") == category]

    return jsonify({"diagrams": catalog, "total": len(catalog)})


@diagrams_api.route("/<diagram_id>", methods=["GET"])
def get_diagram_detail(diagram_id):
    """Return full diagram definition including Mermaid source."""
    diagram = get_diagram(diagram_id)
    if not diagram:
        return jsonify({"error": f"Diagram '{diagram_id}' not found"}), 404

    return jsonify({
        "id": diagram_id,
        "title": diagram["title"],
        "description": diagram["description"],
        "category": diagram.get("category", "general"),
        "roles": diagram.get("roles", []),
        "mermaid": diagram["mermaid"],
    })
