# CUI // SP-CTI
"""Demo tenant seeder — provisions a fully-seeded demo tenant.

Usage:
    icdev demo seed --tenant demo-acme
    icdev demo seed --tenant demo-acme --canvases cyber,network,infra

Programmatic:
    from icdev.tools.demo.seeder import seed_demo_tenant
    result = seed_demo_tenant("demo-acme")
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger
from tools.saas.tenant_manager import create_tenant
from tools.saas.platform_db import get_platform_connection
from icdev.tools.showcase.synthetic_data_engine import SyntheticDataEngine

logger = get_logger(__name__)

DEFAULT_CANVASES: tuple[str, ...] = (
    "cyber", "network", "infra", "requirements", "documents", "knowledge"
)
_DEMO_IL = "IL4"
_DEMO_TIER_CREATE = "professional"   # IL4 + professional auto-approves → provision_tenant called internally
_DEMO_TIER_FINAL = "enterprise"      # upgrade after provisioning for full feature access
_DEMO_RECORDS_PER_CANVAS = 50


def seed_demo_tenant(
    tenant_slug: str,
    canvases: list[str] | None = None,
) -> dict[str, Any]:
    """Provision a demo tenant with synthetic data and ICDEV_DEMO_MODE settings.

    Steps:
      1. Creates tenant (IL4/professional) — provision_tenant() is called
         automatically by create_tenant() for auto-approved tiers.
      2. Upgrades tier to enterprise + writes ICDEV_DEMO_MODE=true and
         banner settings into the tenant's settings JSON column.
      3. Seeds synthetic records for each requested canvas domain.

    Args:
        tenant_slug: URL-safe slug for the demo tenant (e.g. "demo-acme").
        canvases:    Canvas domain list to seed.  Defaults to 6 standard
                     domains: cyber, network, infra, requirements, documents,
                     knowledge.

    Returns:
        dict with keys: tenant_id, slug, canvases_seeded, canvases, demo_mode.
    """
    canvases = list(canvases) if canvases is not None else list(DEFAULT_CANVASES)

    name = " ".join(p.title() for p in tenant_slug.replace("_", "-").split("-"))
    admin_email = f"demo@{tenant_slug}.icdev.example"

    logger.info("seed_demo_tenant: creating tenant slug=%s", tenant_slug)
    create_result = create_tenant(
        name=name,
        impact_level=_DEMO_IL,
        tier=_DEMO_TIER_CREATE,
        admin_email=admin_email,
        admin_name="Demo Admin",
    )
    tenant_id: str = create_result["tenant"]["id"]
    logger.info("seed_demo_tenant: tenant created id=%s", tenant_id)

    _apply_demo_settings(tenant_id, tenant_slug)

    engine = SyntheticDataEngine(seed=42)
    seeded: list[str] = []
    for canvas in canvases:
        try:
            records = engine.generate(canvas, records=_DEMO_RECORDS_PER_CANVAS)
            _seed_canvas_records(tenant_id, canvas, records)
            seeded.append(canvas)
            logger.info(
                "seed_demo_tenant: seeded %d records into canvas=%s", len(records), canvas
            )
        except Exception as exc:
            logger.warning(
                "seed_demo_tenant: canvas=%s seed failed (skipped): %s", canvas, exc
            )

    logger.info(
        "seed_demo_tenant: complete — tenant=%s canvases=%s", tenant_id, seeded
    )
    return {
        "tenant_id": tenant_id,
        "slug": tenant_slug,
        "canvases_seeded": len(seeded),
        "canvases": seeded,
        "demo_mode": True,
    }


def is_demo_mode(tenant_settings: dict[str, Any] | None = None) -> bool:
    """Return True if the current context is in ICDEV_DEMO_MODE.

    Checks (in order):
      1. ``ICDEV_DEMO_MODE`` environment variable ("1", "true", "yes").
      2. ``ICDEV_DEMO_MODE`` key in *tenant_settings* dict.
    """
    import os

    env_val = os.environ.get("ICDEV_DEMO_MODE", "").lower()
    if env_val in ("1", "true", "yes"):
        return True
    if tenant_settings and tenant_settings.get("ICDEV_DEMO_MODE"):
        return True
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_demo_settings(tenant_id: str, tenant_slug: str) -> None:
    """Upgrade tenant tier to enterprise and write ICDEV_DEMO_MODE into settings."""
    demo_meta: dict[str, Any] = {
        "ICDEV_DEMO_MODE": True,
        "billing_status": "demo",
        "banner_mode": "custom",
        "banner_text": "Demo Mode — Read Only",
        "banner_background_color": "#1e3a5f",
        "banner_text_color": "#a8d8ea",
        "demo_tenant": tenant_slug,
    }
    conn = get_platform_connection()
    try:
        row = conn.execute(
            "SELECT settings FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone()
        existing: dict[str, Any] = json.loads(row[0] or "{}") if row and row[0] else {}
        existing.update(demo_meta)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            """UPDATE tenants
               SET tier = ?, billing_status = 'active', settings = ?, updated_at = ?
               WHERE id = ?""",
            (_DEMO_TIER_FINAL, json.dumps(existing), now, tenant_id),
        )
        conn.commit()
        logger.info(
            "_apply_demo_settings: demo settings applied for tenant=%s", tenant_id
        )
    finally:
        conn.close()


def _seed_canvas_records(
    tenant_id: str, canvas: str, records: list[dict[str, Any]]
) -> None:
    """Persist synthetic records to canvas_demo_data in the platform DB."""
    if not records:
        return
    conn = get_platform_connection()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS canvas_demo_data (
                id          TEXT NOT NULL,
                tenant_id   TEXT NOT NULL,
                canvas      TEXT NOT NULL,
                payload     TEXT NOT NULL,
                seeded_at   TEXT NOT NULL,
                PRIMARY KEY (id, tenant_id, canvas)
            )"""
        )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.executemany(
            """INSERT OR REPLACE INTO canvas_demo_data
               (id, tenant_id, canvas, payload, seeded_at)
               VALUES (?, ?, ?, ?, ?)""",
            [(r["id"], tenant_id, canvas, json.dumps(r), now) for r in records],
        )
        conn.commit()
    finally:
        conn.close()
