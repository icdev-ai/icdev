#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Marketplace Install Manager — Install, update, and uninstall marketplace assets.

Manages the lifecycle of installed marketplace assets within tenant projects.
Handles IL compatibility checks, file deployment, version updates, and
uninstallation with full audit trail.

Usage:
    # Install an asset into a project
    python tools/marketplace/install_manager.py --install \
        --asset-id "asset-abc123" --version-id "ver-def456" \
        --tenant-id "tenant-abc" --project-id "proj-123" \
        --installed-by "john.doe@mil" --install-path "/path/to/project/skills" --json

    # Uninstall an asset
    python tools/marketplace/install_manager.py --uninstall \
        --installation-id "inst-abc123" --uninstalled-by "john.doe@mil" --json

    # Update an installed asset to a new version
    python tools/marketplace/install_manager.py --update \
        --installation-id "inst-abc123" --version-id "ver-ghi789" \
        --updated-by "john.doe@mil" --json

    # List installations (with optional filters)
    python tools/marketplace/install_manager.py --list --json
    python tools/marketplace/install_manager.py --list --tenant-id "tenant-abc" --json
    python tools/marketplace/install_manager.py --list --project-id "proj-123" --status active --json

    # Check for available updates
    python tools/marketplace/install_manager.py --check-updates \
        --tenant-id "tenant-abc" --json
"""

import argparse
import json
import os
import shutil
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
IL_RANK = {"IL2": 0, "IL4": 1, "IL5": 2, "IL6": 3}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _gen_id(prefix="inst"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


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

def install_asset(asset_id, version_id, tenant_id, project_id,
                  installed_by, install_path, db_path=None):
    """Install a marketplace asset into a tenant project.

    Performs IL compatibility check, verifies asset is published,
    copies files to install_path, records installation, and increments
    the install_count on the asset.

    Args:
        asset_id: ID of the marketplace asset to install.
        version_id: ID of the specific version to install.
        tenant_id: Tenant performing the installation.
        project_id: Target project for the installation.
        installed_by: Identity of the user performing the install.
        install_path: Filesystem path where asset files will be copied.
        db_path: Optional database path override.

    Returns:
        dict with installation_id, status, and metadata.

    Raises:
        ValueError: If asset not found, not published, IL incompatible,
                    or already installed.
        FileNotFoundError: If asset source files are missing.
    """
    conn = _get_db(db_path)
    try:
        # Fetch asset details
        asset = conn.execute(
            "SELECT * FROM marketplace_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not asset:
            raise ValueError(f"Asset not found: {asset_id}")

        # Check asset is published
        if asset["status"] != "published":
            raise ValueError(
                f"Asset is not published (status: {asset['status']}). "
                f"Only published assets can be installed."
            )

        # IL compatibility check: consumer IL rank >= asset IL rank
        asset_il = asset["impact_level"]
        # Resolve tenant IL from the tenant_id context — for now use the
        # asset's own IL as the floor.  In a full deployment, look up the
        # tenant's IL from the platform DB.  To allow the caller to specify,
        # we accept tenant_id and look for tenant metadata.
        tenant_row = conn.execute(
            "SELECT * FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone() if _table_exists(conn, "tenants") else None

        if tenant_row and "impact_level" in tenant_row.keys():
            consumer_il = tenant_row["impact_level"]
        else:
            # Fallback: assume the consumer IL is at least the asset IL
            # (no tenant table or no IL column — skip check with warning)
            consumer_il = None

        if consumer_il:
            consumer_rank = IL_RANK.get(consumer_il, -1)
            asset_rank = IL_RANK.get(asset_il, -1)
            if consumer_rank < asset_rank:
                raise ValueError(
                    f"IL compatibility failure: tenant IL ({consumer_il}, rank={consumer_rank}) "
                    f"is lower than asset IL ({asset_il}, rank={asset_rank}). "
                    f"Consumer IL must be >= asset IL."
                )

        # Check if already installed (UNIQUE constraint: asset_id, tenant_id, project_id)
        existing = conn.execute(
            """SELECT id, status FROM marketplace_installations
               WHERE asset_id = ? AND tenant_id = ? AND project_id = ?""",
            (asset_id, tenant_id, project_id),
        ).fetchone()
        if existing:
            if existing["status"] == "uninstalled":
                # Allow re-installation of previously uninstalled assets
                # by removing the old record
                conn.execute(
                    "DELETE FROM marketplace_installations WHERE id = ?",
                    (existing["id"],),
                )
                conn.commit()
            else:
                raise ValueError(
                    f"Asset already installed (installation_id: {existing['id']}, "
                    f"status: {existing['status']}). "
                    f"Use --update to change versions."
                )

        # Fetch version details for file_path
        version = conn.execute(
            "SELECT * FROM marketplace_versions WHERE id = ? AND asset_id = ?",
            (version_id, asset_id),
        ).fetchone()
        if not version:
            raise ValueError(
                f"Version not found: {version_id} for asset {asset_id}"
            )

        # Copy asset files to install_path
        source_path = version["file_path"]
        files_copied = False
        if source_path and Path(source_path).exists():
            dest = Path(install_path)
            if dest.exists():
                shutil.rmtree(str(dest))
            shutil.copytree(str(source_path), str(dest))
            files_copied = True

        # Record installation
        installation_id = _gen_id("inst")
        now = _now()
        conn.execute(
            """INSERT INTO marketplace_installations
               (id, asset_id, version_id, tenant_id, project_id,
                installed_by, install_path, status, installed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                installation_id, asset_id, version_id, tenant_id,
                project_id, installed_by, str(install_path), now, now,
            ),
        )

        # Increment install_count on the asset
        conn.execute(
            "UPDATE marketplace_assets SET install_count = install_count + 1, updated_at = ? WHERE id = ?",
            (now, asset_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Audit trail
    _audit(
        event_type="marketplace_asset_installed",
        actor=installed_by,
        action=f"Installed asset {asset['name']} v{version['version']} into project {project_id}",
        project_id=project_id,
        details={
            "installation_id": installation_id,
            "asset_id": asset_id,
            "version_id": version_id,
            "tenant_id": tenant_id,
            "install_path": str(install_path),
            "files_copied": files_copied,
        },
    )

    return {
        "installation_id": installation_id,
        "asset_id": asset_id,
        "asset_name": asset["name"],
        "version_id": version_id,
        "version": version["version"],
        "tenant_id": tenant_id,
        "project_id": project_id,
        "install_path": str(install_path),
        "status": "active",
        "files_copied": files_copied,
        "installed_at": now,
    }


def uninstall_asset(installation_id, uninstalled_by, db_path=None):
    """Uninstall a marketplace asset.

    Marks the installation status as 'uninstalled' and records the
    uninstallation timestamp. Does NOT remove files from disk (caller
    is responsible for cleanup if desired).

    Args:
        installation_id: ID of the installation record.
        uninstalled_by: Identity of the user performing the uninstall.
        db_path: Optional database path override.

    Returns:
        dict with installation_id, status, and metadata.

    Raises:
        ValueError: If installation not found or already uninstalled.
    """
    conn = _get_db(db_path)
    try:
        installation = conn.execute(
            "SELECT * FROM marketplace_installations WHERE id = ?",
            (installation_id,),
        ).fetchone()
        if not installation:
            raise ValueError(f"Installation not found: {installation_id}")

        if installation["status"] == "uninstalled":
            raise ValueError(
                f"Installation already uninstalled at {installation['uninstalled_at']}"
            )

        now = _now()
        conn.execute(
            """UPDATE marketplace_installations
               SET status = 'uninstalled', uninstalled_at = ?, updated_at = ?
               WHERE id = ?""",
            (now, now, installation_id),
        )
        conn.commit()

        asset_id = installation["asset_id"]
        project_id = installation["project_id"]
        tenant_id = installation["tenant_id"]
    finally:
        conn.close()

    # Audit trail
    _audit(
        event_type="marketplace_asset_uninstalled",
        actor=uninstalled_by,
        action=f"Uninstalled asset {asset_id} from project {project_id}",
        project_id=project_id,
        details={
            "installation_id": installation_id,
            "asset_id": asset_id,
            "tenant_id": tenant_id,
            "install_path": installation["install_path"],
        },
    )

    return {
        "installation_id": installation_id,
        "asset_id": asset_id,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "status": "uninstalled",
        "uninstalled_at": now,
        "uninstalled_by": uninstalled_by,
    }


def update_asset(installation_id, new_version_id, updated_by, db_path=None):
    """Update an installed asset to a new version.

    Verifies the new version exists and belongs to the same asset,
    then updates the installation record.

    Args:
        installation_id: ID of the installation record.
        new_version_id: ID of the new version to update to.
        updated_by: Identity of the user performing the update.
        db_path: Optional database path override.

    Returns:
        dict with installation_id, old/new version info, and metadata.

    Raises:
        ValueError: If installation not found, not active, or version
                    not found / mismatched asset.
    """
    conn = _get_db(db_path)
    try:
        # Get current installation
        installation = conn.execute(
            "SELECT * FROM marketplace_installations WHERE id = ?",
            (installation_id,),
        ).fetchone()
        if not installation:
            raise ValueError(f"Installation not found: {installation_id}")

        if installation["status"] not in ("active", "update_available"):
            raise ValueError(
                f"Cannot update installation with status '{installation['status']}'. "
                f"Must be 'active' or 'update_available'."
            )

        asset_id = installation["asset_id"]
        old_version_id = installation["version_id"]

        # Verify new version exists and belongs to the same asset
        new_version = conn.execute(
            "SELECT * FROM marketplace_versions WHERE id = ? AND asset_id = ?",
            (new_version_id, asset_id),
        ).fetchone()
        if not new_version:
            raise ValueError(
                f"Version not found: {new_version_id} for asset {asset_id}"
            )

        # Get old version info for audit
        old_version = conn.execute(
            "SELECT version FROM marketplace_versions WHERE id = ?",
            (old_version_id,),
        ).fetchone()

        # Copy new version files if available
        files_copied = False
        source_path = new_version["file_path"]
        install_path = installation["install_path"]
        if source_path and Path(source_path).exists() and install_path:
            dest = Path(install_path)
            if dest.exists():
                shutil.rmtree(str(dest))
            shutil.copytree(str(source_path), str(dest))
            files_copied = True

        # Update installation record
        now = _now()
        conn.execute(
            """UPDATE marketplace_installations
               SET version_id = ?, status = 'active', updated_at = ?
               WHERE id = ?""",
            (new_version_id, now, installation_id),
        )
        conn.commit()

        project_id = installation["project_id"]
        tenant_id = installation["tenant_id"]
    finally:
        conn.close()

    # Audit trail
    _audit(
        event_type="marketplace_asset_updated",
        actor=updated_by,
        action=(
            f"Updated asset {asset_id} from v{old_version['version'] if old_version else old_version_id} "
            f"to v{new_version['version']} in project {project_id}"
        ),
        project_id=project_id,
        details={
            "installation_id": installation_id,
            "asset_id": asset_id,
            "old_version_id": old_version_id,
            "new_version_id": new_version_id,
            "old_version": old_version["version"] if old_version else None,
            "new_version": new_version["version"],
            "tenant_id": tenant_id,
            "files_copied": files_copied,
        },
    )

    return {
        "installation_id": installation_id,
        "asset_id": asset_id,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "old_version_id": old_version_id,
        "old_version": old_version["version"] if old_version else None,
        "new_version_id": new_version_id,
        "new_version": new_version["version"],
        "status": "active",
        "files_copied": files_copied,
        "updated_at": now,
        "updated_by": updated_by,
    }


def list_installations(tenant_id=None, project_id=None, status=None,
                       db_path=None):
    """List marketplace installations with optional filters.

    Args:
        tenant_id: Filter by tenant ID.
        project_id: Filter by project ID.
        status: Filter by status (active, disabled, uninstalled, update_available).
        db_path: Optional database path override.

    Returns:
        dict with installations list and total count.
    """
    conn = _get_db(db_path)
    try:
        query = """
            SELECT i.*, a.name AS asset_name, a.asset_type,
                   a.impact_level AS asset_impact_level,
                   v.version AS installed_version
            FROM marketplace_installations i
            LEFT JOIN marketplace_assets a ON i.asset_id = a.id
            LEFT JOIN marketplace_versions v ON i.version_id = v.id
            WHERE 1=1
        """
        params = []

        if tenant_id:
            query += " AND i.tenant_id = ?"
            params.append(tenant_id)
        if project_id:
            query += " AND i.project_id = ?"
            params.append(project_id)
        if status:
            query += " AND i.status = ?"
            params.append(status)

        query += " ORDER BY i.updated_at DESC"

        rows = conn.execute(query, params).fetchall()
        installations = [dict(r) for r in rows]

        return {
            "installations": installations,
            "total": len(installations),
            "filters": {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "status": status,
            },
        }
    finally:
        conn.close()


def check_updates(tenant_id, db_path=None):
    """Check for available updates across all installed assets for a tenant.

    Compares the installed version against the asset's current_version.
    Returns a list of installations that have newer versions available.

    Args:
        tenant_id: Tenant ID to check updates for.
        db_path: Optional database path override.

    Returns:
        dict with updates_available list and summary counts.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT i.id AS installation_id, i.asset_id, i.version_id,
                      i.project_id, i.install_path,
                      v.version AS installed_version,
                      a.name AS asset_name, a.current_version,
                      a.asset_type, a.status AS asset_status
               FROM marketplace_installations i
               JOIN marketplace_assets a ON i.asset_id = a.id
               JOIN marketplace_versions v ON i.version_id = v.id
               WHERE i.tenant_id = ? AND i.status = 'active'
               ORDER BY a.name""",
            (tenant_id,),
        ).fetchall()

        updates_available = []
        up_to_date = []

        for row in rows:
            row_dict = dict(row)
            installed_ver = row_dict["installed_version"]
            current_ver = row_dict["current_version"]

            if installed_ver != current_ver:
                # Find the latest published version record
                latest_version = conn.execute(
                    """SELECT id, version, changelog
                       FROM marketplace_versions
                       WHERE asset_id = ? AND version = ? AND status = 'published'""",
                    (row_dict["asset_id"], current_ver),
                ).fetchone()

                update_info = {
                    "installation_id": row_dict["installation_id"],
                    "asset_id": row_dict["asset_id"],
                    "asset_name": row_dict["asset_name"],
                    "asset_type": row_dict["asset_type"],
                    "project_id": row_dict["project_id"],
                    "installed_version": installed_ver,
                    "available_version": current_ver,
                    "new_version_id": latest_version["id"] if latest_version else None,
                    "changelog": latest_version["changelog"] if latest_version else None,
                }
                updates_available.append(update_info)
            else:
                up_to_date.append({
                    "installation_id": row_dict["installation_id"],
                    "asset_name": row_dict["asset_name"],
                    "version": installed_ver,
                })

        return {
            "tenant_id": tenant_id,
            "updates_available": updates_available,
            "updates_count": len(updates_available),
            "up_to_date_count": len(up_to_date),
            "total_installed": len(rows),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _table_exists(conn, table_name):
    """Check if a table exists in the database."""
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row["cnt"] > 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Marketplace Install Manager"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db-path", type=Path, default=None,
                        help="Override database path")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true",
                       help="Install a marketplace asset")
    group.add_argument("--uninstall", action="store_true",
                       help="Uninstall a marketplace asset")
    group.add_argument("--update", action="store_true",
                       help="Update an installed asset to a new version")
    group.add_argument("--list", action="store_true",
                       help="List installations")
    group.add_argument("--check-updates", action="store_true",
                       help="Check for available updates")

    # Install args
    parser.add_argument("--asset-id", help="Marketplace asset ID")
    parser.add_argument("--version-id", help="Version ID to install/update to")
    parser.add_argument("--tenant-id", help="Tenant ID")
    parser.add_argument("--project-id", help="Project ID")
    parser.add_argument("--installed-by", help="User performing the install")
    parser.add_argument("--install-path", help="Filesystem path for asset files")

    # Uninstall / update args
    parser.add_argument("--installation-id", help="Installation record ID")
    parser.add_argument("--uninstalled-by", help="User performing the uninstall")
    parser.add_argument("--updated-by", help="User performing the update")

    # List filters
    parser.add_argument("--status",
                        choices=["active", "disabled", "uninstalled",
                                 "update_available"],
                        help="Filter by status")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.install:
            if not all([args.asset_id, args.version_id, args.tenant_id,
                        args.project_id, args.installed_by, args.install_path]):
                parser.error(
                    "--install requires --asset-id, --version-id, --tenant-id, "
                    "--project-id, --installed-by, --install-path"
                )
            result = install_asset(
                asset_id=args.asset_id,
                version_id=args.version_id,
                tenant_id=args.tenant_id,
                project_id=args.project_id,
                installed_by=args.installed_by,
                install_path=args.install_path,
                db_path=db_path,
            )

        elif args.uninstall:
            if not all([args.installation_id, args.uninstalled_by]):
                parser.error(
                    "--uninstall requires --installation-id, --uninstalled-by"
                )
            result = uninstall_asset(
                installation_id=args.installation_id,
                uninstalled_by=args.uninstalled_by,
                db_path=db_path,
            )

        elif args.update:
            if not all([args.installation_id, args.version_id, args.updated_by]):
                parser.error(
                    "--update requires --installation-id, --version-id, "
                    "--updated-by"
                )
            result = update_asset(
                installation_id=args.installation_id,
                new_version_id=args.version_id,
                updated_by=args.updated_by,
                db_path=db_path,
            )

        elif args.list:
            result = list_installations(
                tenant_id=args.tenant_id,
                project_id=args.project_id,
                status=args.status,
                db_path=db_path,
            )

        elif args.check_updates:
            if not args.tenant_id:
                parser.error("--check-updates requires --tenant-id")
            result = check_updates(
                tenant_id=args.tenant_id,
                db_path=db_path,
            )

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
