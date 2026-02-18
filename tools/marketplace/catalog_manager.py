#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Marketplace Catalog Manager — CRUD for assets and versions.

Manages the lifecycle of marketplace assets (skills, goals, hardprompts,
context, args, compliance extensions) within the federated GOTCHA registry.

Supports tenant-local catalogs and cross-tenant sharing to the central
vetted registry.

Usage:
    # Register a new asset
    python tools/marketplace/catalog_manager.py --register \\
        --name "custom-stig-checker" --asset-type skill \\
        --description "Agency-specific STIG checker" \\
        --version "1.0.0" --impact-level IL4 \\
        --classification "CUI // SP-CTI" \\
        --tenant-id "tenant-abc" --json

    # List assets (optionally filtered)
    python tools/marketplace/catalog_manager.py --list --json
    python tools/marketplace/catalog_manager.py --list --asset-type skill --json
    python tools/marketplace/catalog_manager.py --list --tenant-id "tenant-abc" --json
    python tools/marketplace/catalog_manager.py --list --catalog-tier central_vetted --json

    # Get asset details
    python tools/marketplace/catalog_manager.py --get --slug "tenant-abc/custom-stig-checker" --json

    # Register a new version
    python tools/marketplace/catalog_manager.py --add-version \\
        --asset-id "asset-uuid" --version "1.1.0" \\
        --changelog "Added Oracle DB support" \\
        --file-path "/path/to/asset.tar.gz" --json

    # Update asset status
    python tools/marketplace/catalog_manager.py --update-status \\
        --asset-id "asset-uuid" --status published --json

    # Deprecate an asset
    python tools/marketplace/catalog_manager.py --deprecate \\
        --asset-id "asset-uuid" --replacement-slug "better-stig-checker" --json

    # List versions for an asset
    python tools/marketplace/catalog_manager.py --versions \\
        --asset-id "asset-uuid" --json
"""

import argparse
import hashlib
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

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_ASSET_TYPES = {"skill", "goal", "hardprompt", "context", "args", "compliance"}
VALID_IMPACT_LEVELS = {"IL2", "IL4", "IL5", "IL6"}
VALID_STATUSES = {"draft", "scanning", "review", "published", "deprecated", "revoked"}
VALID_CATALOG_TIERS = {"tenant_local", "central_vetted"}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _gen_id(prefix="mkt"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    """ISO-8601 timestamp."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(file_path):
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_dir(dir_path):
    """Compute SHA-256 hex digest of a directory (sorted file hashes)."""
    h = hashlib.sha256()
    dir_path = Path(dir_path)
    for fpath in sorted(dir_path.rglob("*")):
        if fpath.is_file():
            h.update(str(fpath.relative_to(dir_path)).encode())
            with open(fpath, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
    return h.hexdigest()


def _audit(event_type, actor, action, project_id=None, details=None):
    """Write an audit trail entry."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                project_id=project_id,
                details=details,
                db_path=DB_PATH,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def register_asset(name, asset_type, description, version, impact_level,
                   classification="CUI // SP-CTI", tenant_id=None,
                   publisher_org=None, publisher_user=None,
                   license_id="USG-INTERNAL", tags=None,
                   compliance_controls=None, supported_languages=None,
                   dependencies=None, db_path=None):
    """Register a new marketplace asset.

    Returns dict with asset_id, slug, and metadata.
    """
    if asset_type not in VALID_ASSET_TYPES:
        raise ValueError(f"Invalid asset_type: {asset_type}. Must be one of {VALID_ASSET_TYPES}")
    if impact_level not in VALID_IMPACT_LEVELS:
        raise ValueError(f"Invalid impact_level: {impact_level}. Must be one of {VALID_IMPACT_LEVELS}")

    asset_id = _gen_id("asset")
    # Slug format: tenant-slug/asset-name or just asset-name for central
    slug_prefix = tenant_id[:12] if tenant_id else "central"
    slug = f"{slug_prefix}/{name}"

    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO marketplace_assets
               (id, slug, name, asset_type, description, current_version,
                classification, impact_level, publisher_tenant_id,
                publisher_org, publisher_user, catalog_tier, status,
                license, tags, compliance_controls, supported_languages)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'tenant_local', 'draft',
                       ?, ?, ?, ?)""",
            (
                asset_id, slug, name, asset_type, description, version,
                classification, impact_level, tenant_id,
                publisher_org, publisher_user,
                license_id,
                json.dumps(tags) if tags else None,
                json.dumps(compliance_controls) if compliance_controls else None,
                json.dumps(supported_languages) if supported_languages else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _audit(
        event_type="marketplace_asset_published",
        actor=publisher_user or "marketplace-catalog",
        action=f"Registered asset '{name}' v{version} ({asset_type})",
        details={"asset_id": asset_id, "slug": slug, "impact_level": impact_level},
    )

    return {
        "asset_id": asset_id,
        "slug": slug,
        "name": name,
        "asset_type": asset_type,
        "version": version,
        "impact_level": impact_level,
        "catalog_tier": "tenant_local",
        "status": "draft",
    }


def add_version(asset_id, version, changelog=None, file_path=None,
                published_by=None, metadata=None, db_path=None):
    """Add a new version to an existing asset.

    Computes SHA-256 hash of the file/directory at file_path.
    Returns dict with version_id and hash.
    """
    version_id = _gen_id("ver")

    # Compute hash
    sha256_hash = ""
    file_size = 0
    if file_path:
        p = Path(file_path)
        if p.is_dir():
            sha256_hash = _sha256_dir(p)
            file_size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        elif p.is_file():
            sha256_hash = _sha256_file(str(p))
            file_size = p.stat().st_size
        else:
            raise FileNotFoundError(f"Asset path not found: {file_path}")

    conn = _get_db(db_path)
    try:
        # Insert version record
        conn.execute(
            """INSERT INTO marketplace_versions
               (id, asset_id, version, changelog, sha256_hash,
                file_path, file_size_bytes, metadata, published_by, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')""",
            (
                version_id, asset_id, version, changelog,
                sha256_hash, str(file_path) if file_path else None,
                file_size,
                json.dumps(metadata) if metadata else None,
                published_by,
            ),
        )

        # Update current_version on asset
        conn.execute(
            "UPDATE marketplace_assets SET current_version = ?, updated_at = ? WHERE id = ?",
            (version, _now(), asset_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "version_id": version_id,
        "asset_id": asset_id,
        "version": version,
        "sha256_hash": sha256_hash,
        "file_size_bytes": file_size,
        "status": "draft",
    }


def update_status(asset_id, status, db_path=None):
    """Update asset status (draft -> scanning -> review -> published)."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Must be one of {VALID_STATUSES}")

    conn = _get_db(db_path)
    try:
        conn.execute(
            "UPDATE marketplace_assets SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), asset_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {"asset_id": asset_id, "status": status}


def promote_to_central(asset_id, db_path=None):
    """Promote an asset from tenant_local to central_vetted catalog."""
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM marketplace_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Asset not found: {asset_id}")
        if row["status"] != "published":
            raise ValueError(f"Asset must be 'published' to promote. Current: {row['status']}")

        conn.execute(
            "UPDATE marketplace_assets SET catalog_tier = 'central_vetted', updated_at = ? WHERE id = ?",
            (_now(), asset_id),
        )
        conn.commit()
    finally:
        conn.close()

    _audit(
        event_type="marketplace_federation_sync",
        actor="marketplace-catalog",
        action=f"Promoted asset {asset_id} to central_vetted catalog",
        details={"asset_id": asset_id},
    )
    return {"asset_id": asset_id, "catalog_tier": "central_vetted"}


def deprecate_asset(asset_id, replacement_slug=None, db_path=None):
    """Mark an asset as deprecated with optional replacement."""
    conn = _get_db(db_path)
    try:
        conn.execute(
            """UPDATE marketplace_assets
               SET deprecated = 1, status = 'deprecated',
                   replacement_slug = ?, updated_at = ?
               WHERE id = ?""",
            (replacement_slug, _now(), asset_id),
        )
        conn.commit()
    finally:
        conn.close()

    _audit(
        event_type="marketplace_asset_deprecated",
        actor="marketplace-catalog",
        action=f"Deprecated asset {asset_id}",
        details={"asset_id": asset_id, "replacement_slug": replacement_slug},
    )
    return {"asset_id": asset_id, "status": "deprecated", "replacement_slug": replacement_slug}


def get_asset(slug=None, asset_id=None, db_path=None):
    """Get full asset details by slug or ID."""
    conn = _get_db(db_path)
    try:
        if slug:
            row = conn.execute(
                "SELECT * FROM marketplace_assets WHERE slug = ?", (slug,)
            ).fetchone()
        elif asset_id:
            row = conn.execute(
                "SELECT * FROM marketplace_assets WHERE id = ?", (asset_id,)
            ).fetchone()
        else:
            raise ValueError("Either 'slug' or 'asset_id' is required")

        if not row:
            return None

        asset = dict(row)
        # Parse JSON fields
        for field in ("tags", "compliance_controls", "supported_languages"):
            if asset.get(field):
                try:
                    asset[field] = json.loads(asset[field])
                except (json.JSONDecodeError, TypeError):
                    pass

        # Get versions
        versions = conn.execute(
            "SELECT * FROM marketplace_versions WHERE asset_id = ? ORDER BY created_at DESC",
            (asset["id"],),
        ).fetchall()
        asset["versions"] = [dict(v) for v in versions]

        # Get latest scan results
        scans = conn.execute(
            """SELECT * FROM marketplace_scan_results
               WHERE asset_id = ?
               ORDER BY scanned_at DESC""",
            (asset["id"],),
        ).fetchall()
        asset["scan_results"] = [dict(s) for s in scans]

        return asset
    finally:
        conn.close()


def list_assets(asset_type=None, tenant_id=None, catalog_tier=None,
                status=None, impact_level=None, limit=50, offset=0,
                db_path=None):
    """List assets with optional filters."""
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM marketplace_assets WHERE 1=1"
        params = []

        if asset_type:
            query += " AND asset_type = ?"
            params.append(asset_type)
        if tenant_id:
            query += " AND publisher_tenant_id = ?"
            params.append(tenant_id)
        if catalog_tier:
            query += " AND catalog_tier = ?"
            params.append(catalog_tier)
        if status:
            query += " AND status = ?"
            params.append(status)
        if impact_level:
            query += " AND impact_level = ?"
            params.append(impact_level)

        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        assets = []
        for row in rows:
            a = dict(row)
            for field in ("tags", "compliance_controls", "supported_languages"):
                if a.get(field):
                    try:
                        a[field] = json.loads(a[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            assets.append(a)

        # Get total count
        count_query = "SELECT COUNT(*) as cnt FROM marketplace_assets WHERE 1=1"
        count_params = []
        if asset_type:
            count_query += " AND asset_type = ?"
            count_params.append(asset_type)
        if tenant_id:
            count_query += " AND publisher_tenant_id = ?"
            count_params.append(tenant_id)
        if catalog_tier:
            count_query += " AND catalog_tier = ?"
            count_params.append(catalog_tier)
        if status:
            count_query += " AND status = ?"
            count_params.append(status)

        total = conn.execute(count_query, count_params).fetchone()["cnt"]

        return {"assets": assets, "total": total, "limit": limit, "offset": offset}
    finally:
        conn.close()


def list_versions(asset_id, db_path=None):
    """List all versions for an asset."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM marketplace_versions WHERE asset_id = ? ORDER BY created_at DESC",
            (asset_id,),
        ).fetchall()
        return {"versions": [dict(r) for r in rows], "total": len(rows)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ICDEV Marketplace Catalog Manager")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db-path", type=Path, default=None)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", action="store_true", help="Register a new asset")
    group.add_argument("--list", action="store_true", help="List assets")
    group.add_argument("--get", action="store_true", help="Get asset details")
    group.add_argument("--add-version", action="store_true", help="Add a new version")
    group.add_argument("--update-status", action="store_true", help="Update asset status")
    group.add_argument("--deprecate", action="store_true", help="Deprecate an asset")
    group.add_argument("--promote", action="store_true", help="Promote to central registry")
    group.add_argument("--versions", action="store_true", help="List versions")

    # Register args
    parser.add_argument("--name", help="Asset name")
    parser.add_argument("--asset-type", choices=sorted(VALID_ASSET_TYPES))
    parser.add_argument("--description", help="Asset description")
    parser.add_argument("--version", help="Semantic version")
    parser.add_argument("--impact-level", choices=sorted(VALID_IMPACT_LEVELS))
    parser.add_argument("--classification", default="CUI // SP-CTI")
    parser.add_argument("--tenant-id", help="Publisher tenant ID")
    parser.add_argument("--publisher-org", help="Publisher organization")
    parser.add_argument("--publisher-user", help="Publisher user identifier")
    parser.add_argument("--license", default="USG-INTERNAL")
    parser.add_argument("--tags", help="Comma-separated tags")
    parser.add_argument("--compliance-controls", help="Comma-separated NIST control IDs")

    # Get/update args
    parser.add_argument("--slug", help="Asset slug (publisher/name)")
    parser.add_argument("--asset-id", help="Asset UUID")
    parser.add_argument("--status", choices=sorted(VALID_STATUSES))

    # Version args
    parser.add_argument("--changelog", help="Version changelog")
    parser.add_argument("--file-path", help="Path to asset files")

    # List filters
    parser.add_argument("--catalog-tier", choices=sorted(VALID_CATALOG_TIERS))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)

    # Deprecate args
    parser.add_argument("--replacement-slug", help="Replacement asset slug")

    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.register:
            if not all([args.name, args.asset_type, args.description, args.version, args.impact_level]):
                parser.error("--register requires --name, --asset-type, --description, --version, --impact-level")
            tags = args.tags.split(",") if args.tags else None
            controls = args.compliance_controls.split(",") if args.compliance_controls else None
            result = register_asset(
                name=args.name, asset_type=args.asset_type,
                description=args.description, version=args.version,
                impact_level=args.impact_level, classification=args.classification,
                tenant_id=args.tenant_id, publisher_org=args.publisher_org,
                publisher_user=args.publisher_user, license_id=args.license,
                tags=tags, compliance_controls=controls, db_path=db_path,
            )
        elif args.list:
            result = list_assets(
                asset_type=args.asset_type, tenant_id=args.tenant_id,
                catalog_tier=args.catalog_tier, status=args.status,
                impact_level=args.impact_level, limit=args.limit,
                offset=args.offset, db_path=db_path,
            )
        elif args.get:
            if not (args.slug or args.asset_id):
                parser.error("--get requires --slug or --asset-id")
            result = get_asset(slug=args.slug, asset_id=args.asset_id, db_path=db_path)
            if not result:
                result = {"error": "Asset not found"}
        elif args.add_version:
            if not all([args.asset_id, args.version]):
                parser.error("--add-version requires --asset-id, --version")
            result = add_version(
                asset_id=args.asset_id, version=args.version,
                changelog=args.changelog, file_path=args.file_path,
                published_by=args.publisher_user, db_path=db_path,
            )
        elif args.update_status:
            if not all([args.asset_id, args.status]):
                parser.error("--update-status requires --asset-id, --status")
            result = update_status(asset_id=args.asset_id, status=args.status, db_path=db_path)
        elif args.deprecate:
            if not args.asset_id:
                parser.error("--deprecate requires --asset-id")
            result = deprecate_asset(
                asset_id=args.asset_id, replacement_slug=args.replacement_slug, db_path=db_path,
            )
        elif args.promote:
            if not args.asset_id:
                parser.error("--promote requires --asset-id")
            result = promote_to_central(asset_id=args.asset_id, db_path=db_path)
        elif args.versions:
            if not args.asset_id:
                parser.error("--versions requires --asset-id")
            result = list_versions(asset_id=args.asset_id, db_path=db_path)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, dict):
                for k, v in result.items():
                    print(f"  {k}: {v}")
            else:
                print(result)

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
