"""ICDEV Studio — API Blueprint.

Self-contained Flask Blueprint for all Studio endpoints.
Registration in app.py is a single line:
    app.register_blueprint(studio_api)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.studio.workflow_editor import (  # noqa: E402
    create_workflow,
    delete_workflow,
    get_tool_catalog,
    get_workflow,
    list_builtin_templates,
    list_workflows,
    update_workflow,
    workflow_to_composer_format,
)

studio_api = Blueprint("studio_api", __name__, url_prefix="/api/studio")


# ── Workflow CRUD ──────────────────────────────────────────

@studio_api.route("/workflows", methods=["GET"])
def api_list_workflows():
    shared = request.args.get("shared") == "1"
    return jsonify({"workflows": list_workflows(shared_only=shared)})


@studio_api.route("/workflows/<workflow_id>", methods=["GET"])
def api_get_workflow(workflow_id: str):
    wf = get_workflow(workflow_id)
    if not wf:
        return jsonify({"error": "Workflow not found"}), 404
    return jsonify(wf)


@studio_api.route("/workflows", methods=["POST"])
def api_create_workflow():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    template_yaml = data.get("template_yaml", "").strip()
    if not name or not template_yaml:
        return jsonify({"error": "name and template_yaml are required"}), 400
    result = create_workflow(
        name=name,
        template_yaml=template_yaml,
        description=data.get("description", ""),
        category=data.get("category", "custom"),
    )
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result), 201


@studio_api.route("/workflows/<workflow_id>", methods=["PATCH"])
def api_update_workflow(workflow_id: str):
    data = request.get_json(silent=True) or {}
    result = update_workflow(
        workflow_id,
        name=data.get("name"),
        description=data.get("description"),
        template_yaml=data.get("template_yaml"),
        category=data.get("category"),
        shared=data.get("shared"),
    )
    if result.get("status") == "error":
        code = 404 if "not found" in result.get("error", "") else 400
        return jsonify(result), code
    return jsonify(result)


@studio_api.route("/workflows/<workflow_id>", methods=["DELETE"])
def api_delete_workflow(workflow_id: str):
    result = delete_workflow(workflow_id)
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


@studio_api.route("/workflows/<workflow_id>/composer", methods=["GET"])
def api_workflow_composer_format(workflow_id: str):
    """Return workflow in format compatible with workflow_composer.py."""
    data = workflow_to_composer_format(workflow_id)
    if not data:
        return jsonify({"error": "Workflow not found or invalid YAML"}), 404
    return jsonify(data)


# ── Tool Catalog ───────────────────────────────────────────

@studio_api.route("/tools/catalog", methods=["GET"])
def api_tool_catalog():
    return jsonify(get_tool_catalog())


# ── Built-in Templates ─────────────────────────────────────

@studio_api.route("/templates", methods=["GET"])
def api_builtin_templates():
    return jsonify({"templates": list_builtin_templates()})


# ── Marketplace (read-only wrapper) ───────────────────────

@studio_api.route("/marketplace/assets", methods=["GET"])
def api_marketplace_assets():
    """List marketplace assets for storefront UI."""
    try:
        from tools.marketplace.catalog_manager import list_assets
        query = request.args.get("q", "")
        category = request.args.get("category", "")
        assets = list_assets()
        # Client-side filtering if params provided
        if query:
            q_lower = query.lower()
            assets = [a for a in assets
                      if q_lower in a.get("name", "").lower()
                      or q_lower in a.get("description", "").lower()]
        if category:
            assets = [a for a in assets
                      if a.get("category", "").lower() == category.lower()]
        return jsonify({"assets": assets, "total": len(assets)})
    except ImportError:
        return jsonify({"assets": [], "total": 0,
                        "note": "Marketplace module not available"})


@studio_api.route("/marketplace/categories", methods=["GET"])
def api_marketplace_categories():
    """Return distinct asset categories."""
    try:
        from tools.marketplace.catalog_manager import list_assets
        assets = list_assets()
        cats = sorted({a.get("category", "uncategorized") for a in assets})
        return jsonify({"categories": cats})
    except ImportError:
        return jsonify({"categories": []})


@studio_api.route("/marketplace/assets/<asset_id>", methods=["GET"])
def api_marketplace_asset_detail(asset_id: str):
    """Get detailed info for a single asset."""
    try:
        from tools.marketplace.catalog_manager import get_asset
        asset = get_asset(asset_id)
        if not asset:
            return jsonify({"error": "Asset not found"}), 404
        return jsonify(asset)
    except ImportError:
        return jsonify({"error": "Marketplace module not available"}), 503


@studio_api.route("/marketplace/assets/<asset_id>/install", methods=["POST"])
def api_marketplace_install(asset_id: str):
    """Install a marketplace asset."""
    try:
        from tools.marketplace.install_manager import install_asset
        result = install_asset(asset_id)
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "Marketplace module not available"}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
