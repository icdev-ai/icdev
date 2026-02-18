#!/usr/bin/env python3
# CUI // SP-CTI
"""Marketplace MCP server exposing GOTCHA asset registry tools.

Tools:
    publish_asset    - Publish an asset through the 7-gate pipeline
    install_asset    - Install a marketplace asset to a project
    uninstall_asset  - Uninstall an asset
    search_assets    - Search the marketplace catalog
    list_assets      - List assets with filters
    get_asset        - Get full asset details
    review_asset     - Complete a review (approve/reject/conditional)
    list_pending     - List pending reviews
    check_compat     - Check IL/version/dependency compatibility
    sync_status      - Get federation sync status
    asset_scan       - Run security scanning on an asset

Resources:
    marketplace://catalog         - Full asset catalog
    marketplace://pending-reviews - Pending review queue

Runs as an MCP server over stdio with Content-Length framing.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer  # noqa: E402

# Graceful imports of marketplace modules
try:
    from tools.marketplace.catalog_manager import (
        get_asset,
        list_assets,
    )
    from tools.marketplace.publish_pipeline import publish_asset as _publish_asset
    from tools.marketplace.install_manager import (
        install_asset as _install_asset,
        uninstall_asset as _uninstall_asset,
    )
    from tools.marketplace.search_engine import search_assets as _search_assets
    from tools.marketplace.review_queue import (
        complete_review, list_pending as _list_pending,
    )
    from tools.marketplace.compatibility_checker import full_compatibility_check
    from tools.marketplace.asset_scanner import run_full_scan
    from tools.marketplace.federation_sync import get_sync_status
    _MODULES_LOADED = True
except ImportError as e:
    _MODULES_LOADED = False
    _IMPORT_ERROR = str(e)

# Audit logger
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    audit_log_event = None


def _audit(event_type, actor, action, project_id=None, details=None):
    if audit_log_event:
        try:
            audit_log_event(
                event_type=event_type, actor=actor, action=action,
                project_id=project_id, details=details, db_path=DB_PATH,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_publish_asset(args: dict) -> dict:
    """Publish a GOTCHA asset through the 7-gate security pipeline."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    asset_path = args.get("asset_path")
    asset_type = args.get("asset_type")
    tenant_id = args.get("tenant_id")
    publisher_user = args.get("publisher_user", "mcp-user")

    if not all([asset_path, asset_type, tenant_id]):
        raise ValueError("'asset_path', 'asset_type', and 'tenant_id' are required")

    return _publish_asset(
        asset_path=asset_path,
        asset_type=asset_type,
        tenant_id=tenant_id,
        publisher_user=publisher_user,
        publisher_org=args.get("publisher_org"),
        target_tier=args.get("target_tier", "tenant_local"),
        asset_id=args.get("asset_id"),
        new_version=args.get("new_version"),
        changelog=args.get("changelog"),
        signing_key_path=args.get("signing_key"),
    )


def handle_install_asset(args: dict) -> dict:
    """Install a marketplace asset to a project."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    asset_id = args.get("asset_id")
    tenant_id = args.get("tenant_id")
    if not all([asset_id, tenant_id]):
        raise ValueError("'asset_id' and 'tenant_id' are required")

    return _install_asset(
        asset_id=asset_id,
        version_id=args.get("version_id"),
        tenant_id=tenant_id,
        project_id=args.get("project_id"),
        installed_by=args.get("installed_by", "mcp-user"),
        install_path=args.get("install_path"),
    )


def handle_uninstall_asset(args: dict) -> dict:
    """Uninstall a marketplace asset."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    installation_id = args.get("installation_id")
    if not installation_id:
        raise ValueError("'installation_id' is required")

    return _uninstall_asset(
        installation_id=installation_id,
        uninstalled_by=args.get("uninstalled_by", "mcp-user"),
    )


def handle_search_assets(args: dict) -> dict:
    """Search the marketplace catalog using hybrid keyword + semantic search."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    query = args.get("query")
    if not query:
        raise ValueError("'query' is required")

    return _search_assets(
        query=query,
        asset_type=args.get("asset_type"),
        impact_level=args.get("impact_level"),
        catalog_tier=args.get("catalog_tier"),
        tenant_id=args.get("tenant_id"),
        limit=args.get("limit", 20),
    )


def handle_list_assets(args: dict) -> dict:
    """List marketplace assets with optional filters."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    return list_assets(
        asset_type=args.get("asset_type"),
        tenant_id=args.get("tenant_id"),
        catalog_tier=args.get("catalog_tier"),
        status=args.get("status"),
        impact_level=args.get("impact_level"),
        limit=args.get("limit", 50),
        offset=args.get("offset", 0),
    )


def handle_get_asset(args: dict) -> dict:
    """Get full asset details including versions and scan results."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    slug = args.get("slug")
    asset_id = args.get("asset_id")
    if not (slug or asset_id):
        raise ValueError("Either 'slug' or 'asset_id' is required")

    result = get_asset(slug=slug, asset_id=asset_id)
    if not result:
        raise ValueError(f"Asset not found: {slug or asset_id}")
    return result


def handle_review_asset(args: dict) -> dict:
    """Complete a review decision on a marketplace asset."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    review_id = args.get("review_id")
    reviewer_id = args.get("reviewer_id")
    decision = args.get("decision")
    rationale = args.get("rationale")

    if not all([review_id, reviewer_id, decision, rationale]):
        raise ValueError("'review_id', 'reviewer_id', 'decision', 'rationale' are required")

    return complete_review(
        review_id=review_id,
        reviewer_id=reviewer_id,
        decision=decision,
        rationale=rationale,
        conditions=args.get("conditions"),
    )


def handle_list_pending(args: dict) -> dict:
    """List pending review requests."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    return _list_pending(reviewer_id=args.get("reviewer_id"))


def handle_check_compat(args: dict) -> dict:
    """Check IL, version, and dependency compatibility for an asset."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    asset_id = args.get("asset_id")
    if not asset_id:
        raise ValueError("'asset_id' is required")

    return full_compatibility_check(
        asset_id=asset_id,
        consumer_il=args.get("consumer_il"),
        tenant_id=args.get("tenant_id"),
        platform_version=args.get("platform_version"),
    )


def handle_sync_status(args: dict) -> dict:
    """Get federation sync status across all tenants."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    return get_sync_status()


def handle_asset_scan(args: dict) -> dict:
    """Run security scanning pipeline on an asset."""
    if not _MODULES_LOADED:
        raise RuntimeError(f"Marketplace modules not loaded: {_IMPORT_ERROR}")

    asset_id = args.get("asset_id")
    version_id = args.get("version_id")
    asset_path = args.get("asset_path")

    if not all([asset_id, version_id, asset_path]):
        raise ValueError("'asset_id', 'version_id', 'asset_path' are required")

    gates = args.get("gates")
    if gates and isinstance(gates, str):
        gates = gates.split(",")

    return run_full_scan(
        asset_id=asset_id,
        version_id=version_id,
        asset_path=asset_path,
        gates=gates,
        expected_classification=args.get("classification"),
    )


# ---------------------------------------------------------------------------
# Resource handlers
# ---------------------------------------------------------------------------

def handle_catalog_resource(uri: str) -> dict:
    """Return the full marketplace catalog."""
    if not _MODULES_LOADED:
        return {"error": _IMPORT_ERROR}
    return list_assets(status="published", limit=100)


def handle_pending_resource(uri: str) -> dict:
    """Return pending review queue."""
    if not _MODULES_LOADED:
        return {"error": _IMPORT_ERROR}
    return _list_pending()


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    server = MCPServer(name="icdev-marketplace", version="1.0.0")

    # --- Tools ---
    server.register_tool(
        "publish_asset",
        "Publish a GOTCHA asset (skill/goal/hardprompt/context/args/compliance) through 7-gate security pipeline",
        {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string", "description": "Path to asset directory"},
                "asset_type": {"type": "string", "enum": ["skill", "goal", "hardprompt", "context", "args", "compliance"]},
                "tenant_id": {"type": "string", "description": "Publisher tenant ID"},
                "publisher_user": {"type": "string", "description": "Publisher identity"},
                "publisher_org": {"type": "string", "description": "Publisher organization"},
                "target_tier": {"type": "string", "enum": ["tenant_local", "central_vetted"], "default": "tenant_local"},
                "asset_id": {"type": "string", "description": "Existing asset ID (for new version)"},
                "new_version": {"type": "string", "description": "Version for update"},
                "changelog": {"type": "string"},
                "signing_key": {"type": "string", "description": "Path to RSA private key"},
            },
            "required": ["asset_path", "asset_type", "tenant_id"],
        },
        handle_publish_asset,
    )

    server.register_tool(
        "install_asset",
        "Install a marketplace asset to a tenant/project",
        {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "version_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "project_id": {"type": "string"},
                "installed_by": {"type": "string"},
                "install_path": {"type": "string"},
            },
            "required": ["asset_id", "tenant_id"],
        },
        handle_install_asset,
    )

    server.register_tool(
        "uninstall_asset",
        "Uninstall a marketplace asset",
        {
            "type": "object",
            "properties": {
                "installation_id": {"type": "string"},
                "uninstalled_by": {"type": "string"},
            },
            "required": ["installation_id"],
        },
        handle_uninstall_asset,
    )

    server.register_tool(
        "search_assets",
        "Search marketplace using hybrid keyword + semantic search",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "asset_type": {"type": "string", "enum": ["skill", "goal", "hardprompt", "context", "args", "compliance"]},
                "impact_level": {"type": "string", "enum": ["IL2", "IL4", "IL5", "IL6"]},
                "catalog_tier": {"type": "string", "enum": ["tenant_local", "central_vetted"]},
                "tenant_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        handle_search_assets,
    )

    server.register_tool(
        "list_assets",
        "List marketplace assets with optional filters",
        {
            "type": "object",
            "properties": {
                "asset_type": {"type": "string"},
                "tenant_id": {"type": "string"},
                "catalog_tier": {"type": "string"},
                "status": {"type": "string"},
                "impact_level": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
        },
        handle_list_assets,
    )

    server.register_tool(
        "get_asset",
        "Get full asset details including versions and scan results",
        {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Asset slug (publisher/name)"},
                "asset_id": {"type": "string"},
            },
        },
        handle_get_asset,
    )

    server.register_tool(
        "review_asset",
        "Complete review decision (approve/reject/conditional) for cross-tenant sharing",
        {
            "type": "object",
            "properties": {
                "review_id": {"type": "string"},
                "reviewer_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["approved", "rejected", "conditional"]},
                "rationale": {"type": "string"},
                "conditions": {"type": "string"},
            },
            "required": ["review_id", "reviewer_id", "decision", "rationale"],
        },
        handle_review_asset,
    )

    server.register_tool(
        "list_pending",
        "List pending review requests for marketplace assets",
        {
            "type": "object",
            "properties": {
                "reviewer_id": {"type": "string"},
            },
        },
        handle_list_pending,
    )

    server.register_tool(
        "check_compat",
        "Check IL, version, and dependency compatibility for an asset",
        {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "consumer_il": {"type": "string", "enum": ["IL2", "IL4", "IL5", "IL6"]},
                "tenant_id": {"type": "string"},
                "platform_version": {"type": "string"},
            },
            "required": ["asset_id"],
        },
        handle_check_compat,
    )

    server.register_tool(
        "sync_status",
        "Get federation sync status across all tenants",
        {"type": "object", "properties": {}},
        handle_sync_status,
    )

    server.register_tool(
        "asset_scan",
        "Run 7-gate security scanning pipeline on a marketplace asset",
        {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "version_id": {"type": "string"},
                "asset_path": {"type": "string"},
                "gates": {"type": "string", "description": "Comma-separated gate names"},
                "classification": {"type": "string"},
            },
            "required": ["asset_id", "version_id", "asset_path"],
        },
        handle_asset_scan,
    )

    # --- Resources ---
    server.register_resource(
        "marketplace://catalog",
        "Marketplace Catalog",
        "Published marketplace assets",
        handle_catalog_resource,
    )

    server.register_resource(
        "marketplace://pending-reviews",
        "Pending Reviews",
        "Marketplace assets awaiting review",
        handle_pending_resource,
    )

    return server


def main():
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
