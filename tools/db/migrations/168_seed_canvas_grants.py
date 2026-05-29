#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 168 — Seed canvas_access_grants for existing tenants.

Seeds default role-level canvas grants for all active tenants in platform.db.
New tenants are auto-seeded on creation via tenant_manager.py; this script
backfills grants for tenants created before G-02 (canvas access control) was
implemented.

Usage:
    python tools/db/migrations/168_seed_canvas_grants.py [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.logging.icdev_logger import get_logger
from tools.security.canvas_access import seed_tenant_defaults

logger = get_logger("migration.168_seed_canvas_grants")

_PLATFORM_DB = Path(__file__).resolve().parents[3] / "data" / "platform.db"


def _get_active_tenants() -> list[dict]:
    if not _PLATFORM_DB.exists():
        logger.warning("platform.db not found at %s", _PLATFORM_DB)
        return []
    conn = sqlite3.connect(str(_PLATFORM_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, slug, name FROM tenants WHERE status='active' ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def run(dry_run: bool = False) -> dict:
    tenants = _get_active_tenants()
    results = []
    for t in tenants:
        if dry_run:
            results.append({"tenant_id": t["id"], "slug": t["slug"], "status": "dry_run"})
            continue
        try:
            seed_tenant_defaults(tenant_id=t["id"], granted_by="migration_168")
            results.append({"tenant_id": t["id"], "slug": t["slug"], "status": "seeded"})
            logger.info("Seeded canvas grants for tenant %s (%s)", t["slug"], t["id"])
        except Exception as exc:
            results.append({"tenant_id": t["id"], "slug": t["slug"], "status": "error", "error": str(exc)})
            logger.error("Failed to seed tenant %s: %s", t["slug"], exc)

    seeded = sum(1 for r in results if r["status"] == "seeded")
    errors = sum(1 for r in results if r["status"] == "error")
    return {
        "migration": "168_seed_canvas_grants",
        "dry_run": dry_run,
        "total_tenants": len(tenants),
        "seeded": seeded,
        "errors": errors,
        "results": results,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Migration 168: Seed canvas grants for existing tenants")
    p.add_argument("--dry-run", action="store_true", help="Show what would be seeded without writing")
    p.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    args = p.parse_args()

    result = run(dry_run=args.dry_run)

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        print(f"Migration 168: {result['seeded']} tenants seeded, {result['errors']} errors")
        for r in result["results"]:
            print(f"  [{r['status']}] {r['slug']} ({r['tenant_id']})")


if __name__ == "__main__":
    main()
