#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Marketplace Federation Sync — Synchronize tenant-local and central catalogs.

Handles the federated model where each tenant manages their own catalog
with optional sharing to a central vetted registry.

Sync operations:
    - Promote: Push approved assets from tenant-local to central_vetted
    - Pull: Download central_vetted assets to tenant-local installations
    - Propagate: Sync ratings and scan results across catalogs
    - Status: Check sync state across all tenants

Usage:
    # Sync approved assets to central registry
    python tools/marketplace/federation_sync.py --promote \\
        --tenant-id "tenant-abc" --json

    # Pull central assets available for a tenant
    python tools/marketplace/federation_sync.py --pull \\
        --tenant-id "tenant-abc" --consumer-il IL5 --json

    # Propagate ratings from tenants to central
    python tools/marketplace/federation_sync.py --propagate-ratings --json

    # Check sync status
    python tools/marketplace/federation_sync.py --status --json
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Graceful imports
try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

# IL hierarchy for compatibility filtering
IL_HIERARCHY = {"IL2": 0, "IL4": 1, "IL5": 2, "IL6": 3}


def _get_db(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _gen_id(prefix="sync"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(event_type, actor, action, details=None):
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type, actor=actor,
                action=action, details=details, db_path=DB_PATH,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Sync operations
# ---------------------------------------------------------------------------

def promote_approved(tenant_id, db_path=None):
    """Promote all approved tenant-local assets to central_vetted.

    An asset is eligible for promotion when:
    - catalog_tier = 'tenant_local'
    - status = 'published'
    - Has at least one approved review for the current version
    """
    conn = _get_db(db_path)
    try:
        # Find assets with approved reviews
        eligible = conn.execute(
            """SELECT DISTINCT a.id, a.name, a.current_version, a.slug
               FROM marketplace_assets a
               JOIN marketplace_versions v ON v.asset_id = a.id AND v.version = a.current_version
               JOIN marketplace_reviews r ON r.asset_id = a.id AND r.version_id = v.id
               WHERE a.publisher_tenant_id = ?
                 AND a.catalog_tier = 'tenant_local'
                 AND a.status = 'published'
                 AND r.decision = 'approved'""",
            (tenant_id,),
        ).fetchall()

        promoted = []
        for asset in eligible:
            conn.execute(
                "UPDATE marketplace_assets SET catalog_tier = 'central_vetted', updated_at = ? WHERE id = ?",
                (_now(), asset["id"]),
            )
            promoted.append({
                "asset_id": asset["id"],
                "name": asset["name"],
                "version": asset["current_version"],
                "slug": asset["slug"],
            })

        conn.commit()

        if promoted:
            _audit(
                event_type="marketplace_federation_sync",
                actor="federation-sync",
                action=f"Promoted {len(promoted)} assets from tenant {tenant_id} to central",
                details={"tenant_id": tenant_id, "promoted": promoted},
            )

        return {
            "action": "promote",
            "tenant_id": tenant_id,
            "promoted_count": len(promoted),
            "promoted": promoted,
        }
    finally:
        conn.close()


def pull_available(tenant_id, consumer_il, db_path=None):
    """List central_vetted assets available for a tenant to install.

    Filters by IL compatibility: consumer IL must be >= asset IL.
    Excludes assets already installed by this tenant.
    """
    consumer_rank = IL_HIERARCHY.get(consumer_il, -1)
    compatible_ils = [il for il, rank in IL_HIERARCHY.items() if rank <= consumer_rank]

    if not compatible_ils:
        return {"action": "pull", "available": [], "total": 0}

    conn = _get_db(db_path)
    try:
        placeholders = ",".join("?" for _ in compatible_ils)
        available = conn.execute(
            f"""SELECT a.id, a.slug, a.name, a.asset_type, a.description,
                       a.current_version, a.impact_level, a.classification,
                       a.avg_rating, a.rating_count, a.install_count,
                       a.publisher_org, a.tags
                FROM marketplace_assets a
                WHERE a.catalog_tier = 'central_vetted'
                  AND a.status = 'published'
                  AND a.impact_level IN ({placeholders})
                  AND a.id NOT IN (
                      SELECT asset_id FROM marketplace_installations
                      WHERE tenant_id = ? AND status = 'active'
                  )
                ORDER BY a.avg_rating DESC, a.install_count DESC""",
            (*compatible_ils, tenant_id),
        ).fetchall()

        assets = []
        for row in available:
            a = dict(row)
            if a.get("tags"):
                try:
                    a["tags"] = json.loads(a["tags"])
                except (json.JSONDecodeError, TypeError):
                    pass
            assets.append(a)

        return {
            "action": "pull",
            "tenant_id": tenant_id,
            "consumer_il": consumer_il,
            "available": assets,
            "total": len(assets),
        }
    finally:
        conn.close()


def propagate_ratings(db_path=None):
    """Recompute average ratings for all assets from all tenant ratings."""
    conn = _get_db(db_path)
    try:
        # Recompute averages
        aggregates = conn.execute(
            """SELECT asset_id, AVG(rating) as avg_rating, COUNT(*) as cnt
               FROM marketplace_ratings
               GROUP BY asset_id"""
        ).fetchall()

        updated = 0
        for agg in aggregates:
            conn.execute(
                "UPDATE marketplace_assets SET avg_rating = ?, rating_count = ?, updated_at = ? WHERE id = ?",
                (round(agg["avg_rating"], 2), agg["cnt"], _now(), agg["asset_id"]),
            )
            updated += 1

        conn.commit()

        return {
            "action": "propagate_ratings",
            "assets_updated": updated,
        }
    finally:
        conn.close()


def get_sync_status(db_path=None):
    """Get federation sync status across all tenants."""
    conn = _get_db(db_path)
    try:
        # Count assets by tier
        tier_counts = conn.execute(
            """SELECT catalog_tier, status, COUNT(*) as cnt
               FROM marketplace_assets
               GROUP BY catalog_tier, status"""
        ).fetchall()

        # Count pending reviews
        pending_reviews = conn.execute(
            "SELECT COUNT(*) as cnt FROM marketplace_reviews WHERE decision = 'pending'"
        ).fetchone()["cnt"]

        # Count tenants with published assets
        active_tenants = conn.execute(
            """SELECT COUNT(DISTINCT publisher_tenant_id) as cnt
               FROM marketplace_assets
               WHERE status = 'published'"""
        ).fetchone()["cnt"]

        # Total installations
        total_installs = conn.execute(
            "SELECT COUNT(*) as cnt FROM marketplace_installations WHERE status = 'active'"
        ).fetchone()["cnt"]

        # Eligible for promotion (published + approved review, still tenant_local)
        eligible_promotion = conn.execute(
            """SELECT COUNT(DISTINCT a.id) as cnt
               FROM marketplace_assets a
               JOIN marketplace_versions v ON v.asset_id = a.id AND v.version = a.current_version
               JOIN marketplace_reviews r ON r.asset_id = a.id AND r.version_id = v.id
               WHERE a.catalog_tier = 'tenant_local'
                 AND a.status = 'published'
                 AND r.decision = 'approved'"""
        ).fetchone()["cnt"]

        tiers = {}
        for row in tier_counts:
            tier = row["catalog_tier"]
            if tier not in tiers:
                tiers[tier] = {}
            tiers[tier][row["status"]] = row["cnt"]

        return {
            "sync_status": {
                "catalog_tiers": tiers,
                "pending_reviews": pending_reviews,
                "active_tenants": active_tenants,
                "total_active_installations": total_installs,
                "eligible_for_promotion": eligible_promotion,
            },
            "checked_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ICDEV Marketplace Federation Sync")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--promote", action="store_true", help="Promote approved assets to central")
    group.add_argument("--pull", action="store_true", help="List available central assets")
    group.add_argument("--propagate-ratings", action="store_true", help="Recompute ratings")
    group.add_argument("--status", action="store_true", help="Get sync status")

    parser.add_argument("--tenant-id", help="Tenant ID")
    parser.add_argument("--consumer-il", choices=sorted(IL_HIERARCHY.keys()))

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.promote:
            if not args.tenant_id:
                parser.error("--promote requires --tenant-id")
            result = promote_approved(args.tenant_id, db_path)
        elif args.pull:
            if not all([args.tenant_id, args.consumer_il]):
                parser.error("--pull requires --tenant-id, --consumer-il")
            result = pull_available(args.tenant_id, args.consumer_il, db_path)
        elif args.propagate_ratings:
            result = propagate_ratings(db_path)
        elif args.status:
            result = get_sync_status(db_path)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            for k, v in result.items():
                print(f"  {k}: {v}")

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
