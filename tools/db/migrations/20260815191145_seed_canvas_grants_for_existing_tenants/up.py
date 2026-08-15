#!/usr/bin/env python3
# CUI // SP-CTI
"""Backfill canvas access grants for tenants created before G-02.

REPLACES tools/db/migrations/168_seed_canvas_grants.py, which never ran once.

That file was a bare ``NNN_name.py`` sitting in the migrations directory.
``MigrationRunner.discover_migrations`` accepts exactly two shapes — a flat
``<version>_name.sql`` file, or a ``<version>_name/`` DIRECTORY containing
``up.sql`` or ``up.py``. A bare ``.py`` matches neither: it fails the
``suffix == ".sql"`` test and then the ``is_dir()`` test, and is skipped with no
error. So it looked like migration 168, was documented as one, and had never
executed. CLAUDE.md counts 17 entries in that state.

It was also documented as a MANUAL command
(docs/features/phase-security-remediation-dod-ic.md), which is how it stayed
unnoticed: a backfill nobody is told to run is a backfill that does not happen.
Its whole purpose is to catch up tenants that predate canvas access control, so
running automatically is what it should always have done.

SAFE TO RUN AND RE-RUN. ``canvas_access.grant_access`` is an upsert
(``ON CONFLICT (tenant_id, principal_type, principal_id, canvas_name) DO
UPDATE``), so re-applying re-asserts the same defaults rather than duplicating
rows. On a single-tenant install — no rows in ``tenants`` — this is a no-op.

NEVER FAILS THE MIGRATION. A grant backfill must not block a schema upgrade: the
platform database may be absent, on another host, or empty. Every failure path
logs and returns.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PLATFORM_DB = _REPO_ROOT / "data" / "platform.db"


def _active_tenants() -> list[dict]:
    """Active tenants from platform.db, or [] when there is nothing to read."""
    if not _PLATFORM_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(_PLATFORM_DB))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, slug FROM tenants WHERE status = 'active' "
                "ORDER BY created_at"
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001 — a missing/!old platform db is not an error
        return []


def up(conn) -> None:  # noqa: ARG001 — the icdev conn is unused; grants use their own
    """Seed default canvas grants for every pre-existing active tenant."""
    try:
        from tools.logging.icdev_logger import get_logger

        logger = get_logger("migration.seed_canvas_grants")
    except Exception:  # noqa: BLE001
        import logging

        logger = logging.getLogger("migration.seed_canvas_grants")

    tenants = _active_tenants()
    if not tenants:
        logger.info(
            "seed_canvas_grants: no active tenants — nothing to backfill "
            "(expected on a single-tenant install)")
        return

    try:
        from tools.security.canvas_access import seed_tenant_defaults
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed_canvas_grants: canvas_access unavailable: %s", exc)
        return

    seeded = failed = 0
    for t in tenants:
        try:
            seed_tenant_defaults(tenant_id=t["id"], granted_by="migration_seed")
            seeded += 1
        except Exception as exc:  # noqa: BLE001 — one bad tenant must not stop the rest
            failed += 1
            logger.warning("seed_canvas_grants: tenant %s failed: %s",
                           t.get("slug") or t["id"], exc)

    logger.info("seed_canvas_grants: %d tenant(s) seeded, %d failed",
                seeded, failed)
