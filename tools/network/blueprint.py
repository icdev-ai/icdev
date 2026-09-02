# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Network Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /network/ inside the ICDEV dashboard.
Uses ICDEV's auth system, separate network_canvas.db, and feature flag
ICDEV_NETWORK_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.network.blueprint import create_network_blueprint
    bp = create_network_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/network")

Route implementations live in ``tools/network/routes/*`` route-group modules,
each exposing a ``register_<group>_routes(bp)`` function. This module is a thin
assembler that builds the single shared blueprint and registers every group on
it, so the public URL surface is identical to the pre-split monolith. See the
cvx-net-01 refactor for the split rationale.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.network")

# ── Paths ──────────────────────────────────────────────────────────────────────
_NETWORK_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _NETWORK_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"


def create_network_blueprint():
    """Create and return the Network Design Canvas Blueprint.

    Returns None if ICDEV_NETWORK_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_NETWORK_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Network Canvas disabled (ICDEV_NETWORK_ENABLED=%s)", enabled)
        return None

    # Initialize DB
    try:
        from tools.network.db.init_db import init_db

        init_db()
    except Exception as exc:
        logger.warning("Network DB init failed: %s", exc)

    # Register canvas event bus subscribers (reactive NDC listener)
    try:
        from tools.network.bus_subscriber import register as _register_bus

        _register_bus()
    except Exception as exc:
        logger.warning("Network Canvas bus subscriber registration failed: %s", exc)

    bp = Blueprint(
        "network_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Load config ────────────────────────────────────────────────────────
    try:
        import yaml

        _config_path = _ICDEV_ROOT / "args" / "network_canvas_config.yaml"
        if _config_path.exists():
            with open(_config_path, encoding="utf-8") as f:
                NC_CONFIG = yaml.safe_load(f) or {}
        else:
            NC_CONFIG = {}
    except Exception:
        NC_CONFIG = {}

    # ── Register route groups (cvx-net-01 monolith split) ───────────────────
    # Each module registers a cohesive slice of the NDC URL surface onto the
    # single shared blueprint. Order is not load-bearing: every rule has a
    # unique endpoint, so URL matching is registration-order-independent.
    from tools.network.routes.pages import register_pages_routes
    from tools.network.routes.topology import register_topology_routes
    from tools.network.routes.import_io import register_import_io_routes
    from tools.network.routes.peering_inventory import register_peering_inventory_routes
    from tools.network.routes.collect import register_collect_routes
    from tools.network.routes.crud import register_crud_routes
    from tools.network.routes.topology_ops import register_topology_ops_routes
    from tools.network.routes.ai import register_ai_routes
    from tools.network.routes.analytics import register_analytics_routes
    from tools.network.routes.collab_capture import register_collab_capture_routes
    from tools.network.routes.twin_migration import register_twin_migration_routes
    from tools.network.routes.misc import register_misc_routes

    register_pages_routes(bp, NC_CONFIG)
    register_topology_routes(bp)
    register_import_io_routes(bp)
    register_peering_inventory_routes(bp)
    register_collect_routes(bp)
    register_crud_routes(bp)
    register_topology_ops_routes(bp)
    register_ai_routes(bp)
    register_analytics_routes(bp)
    register_collab_capture_routes(bp)
    register_twin_migration_routes(bp, NC_CONFIG)
    register_misc_routes(bp)

    # ── Register pre-existing extracted route groups ────────────────────────
    from tools.network.routes.projects import register_projects_routes
    from tools.network.routes.governance import register_governance_routes
    from tools.network.routes.stencils import register_stencil_routes
    from tools.network.routes.analysis import register_analysis_routes
    from tools.network.routes.intelligence import register_intelligence_routes
    from tools.network.routes.ingestion import register_ingestion_routes
    from tools.network.routes.pvm import register_pvm_routes
    from tools.network.routes.pna import register_pna_routes
    from tools.network.routes.discovery import register_discovery_routes

    register_projects_routes(bp)
    register_governance_routes(bp)
    register_stencil_routes(bp)
    register_analysis_routes(bp)
    register_intelligence_routes(bp)
    register_ingestion_routes(bp)
    register_pvm_routes(bp)
    register_pna_routes(bp)
    # rmf-disc-02: /discovery + the five /api/discovery/* endpoints the
    # page has always called and that were defined nowhere.
    register_discovery_routes(bp)

    # ── Done ───────────────────────────────────────────────────────────────
    logger.info("Network Design Canvas Blueprint created (%d routes)", len(bp.deferred_functions))
    return bp
