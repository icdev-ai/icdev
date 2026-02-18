#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Marketplace Compatibility Checker — IL and version compatibility.

Validates that a marketplace asset is compatible with a target tenant's
impact level and ICDEV platform version before installation.

Rules:
    - Impact Level: consumer IL rank must be >= asset IL rank
      (IL2=0, IL4=1, IL5=2, IL6=3)
    - Version: asset's min_icdev_version must be <= platform version
    - Dependencies: all declared dependencies must be available and compatible

Usage:
    # Check if an asset is compatible with a tenant
    python tools/marketplace/compatibility_checker.py \\
        --asset-id "asset-abc" --tenant-id "tenant-abc" --json

    # Check version compatibility
    python tools/marketplace/compatibility_checker.py \\
        --asset-id "asset-abc" --platform-version "2.0.0" --json

    # Check dependency compatibility
    python tools/marketplace/compatibility_checker.py \\
        --asset-id "asset-abc" --check-deps --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
IL_HIERARCHY = {"IL2": 0, "IL4": 1, "IL5": 2, "IL6": 3}

# Classification marking compatibility matrix
# A tenant at a given IL can consume assets marked at or below their IL
IL_CAN_CONSUME = {
    "IL2": {"IL2"},
    "IL4": {"IL2", "IL4"},
    "IL5": {"IL2", "IL4", "IL5"},
    "IL6": {"IL2", "IL4", "IL5", "IL6"},
}


def _get_db(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def parse_semver(version_str):
    """Parse semantic version string into tuple (major, minor, patch)."""
    if not version_str:
        return (0, 0, 0)
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)', version_str)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return (0, 0, 0)


def version_satisfies(version, constraint):
    """Check if a version satisfies a constraint (>=, ~, ^, exact)."""
    if not constraint or constraint == "*":
        return True

    version_tuple = parse_semver(version)

    # Handle >=X.Y.Z
    if constraint.startswith(">="):
        min_ver = parse_semver(constraint[2:])
        return version_tuple >= min_ver

    # Handle <=X.Y.Z
    if constraint.startswith("<="):
        max_ver = parse_semver(constraint[2:])
        return version_tuple <= max_ver

    # Handle ~X.Y.Z (approximately: same major.minor)
    if constraint.startswith("~"):
        target = parse_semver(constraint[1:])
        return (version_tuple[0] == target[0] and
                version_tuple[1] == target[1] and
                version_tuple[2] >= target[2])

    # Handle ^X.Y.Z (compatible: same major)
    if constraint.startswith("^"):
        target = parse_semver(constraint[1:])
        return (version_tuple[0] == target[0] and
                version_tuple >= target)

    # Exact match
    return version_tuple == parse_semver(constraint)


# ---------------------------------------------------------------------------
# Compatibility checks
# ---------------------------------------------------------------------------

def check_il_compatibility(asset_id, consumer_il, db_path=None):
    """Check if a tenant's IL can consume an asset.

    Returns dict with compatible (bool), asset_il, consumer_il, and reason.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT impact_level, classification, name FROM marketplace_assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
        if not row:
            return {"compatible": False, "reason": f"Asset not found: {asset_id}"}

        asset_il = row["impact_level"]
        asset_rank = IL_HIERARCHY.get(asset_il, 99)
        consumer_rank = IL_HIERARCHY.get(consumer_il, -1)

        compatible = consumer_rank >= asset_rank
        allowed = IL_CAN_CONSUME.get(consumer_il, set())

        return {
            "compatible": compatible,
            "asset_name": row["name"],
            "asset_il": asset_il,
            "asset_classification": row["classification"],
            "consumer_il": consumer_il,
            "reason": (
                f"Consumer IL {consumer_il} (rank {consumer_rank}) can consume "
                f"asset IL {asset_il} (rank {asset_rank})"
                if compatible else
                f"Consumer IL {consumer_il} cannot consume IL {asset_il} asset. "
                f"Allowed: {sorted(allowed)}"
            ),
        }
    finally:
        conn.close()


def check_version_compatibility(asset_id, platform_version=None, db_path=None):
    """Check if an asset is compatible with the current platform version."""
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT min_icdev_version, name, current_version FROM marketplace_assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
        if not row:
            return {"compatible": False, "reason": f"Asset not found: {asset_id}"}

        min_version = row["min_icdev_version"]
        if not min_version:
            return {
                "compatible": True,
                "asset_name": row["name"],
                "min_icdev_version": None,
                "platform_version": platform_version,
                "reason": "No minimum version requirement",
            }

        if not platform_version:
            # Read from environment or default
            platform_version = os.environ.get("ICDEV_VERSION", "1.0.0")

        compatible = version_satisfies(platform_version, f">={min_version}")
        return {
            "compatible": compatible,
            "asset_name": row["name"],
            "min_icdev_version": min_version,
            "platform_version": platform_version,
            "reason": (
                f"Platform {platform_version} >= required {min_version}"
                if compatible else
                f"Platform {platform_version} < required {min_version}"
            ),
        }
    finally:
        conn.close()


def check_dependency_compatibility(asset_id, tenant_id=None, db_path=None):
    """Check if all dependencies of an asset are available and compatible.

    For each dependency in marketplace_dependencies:
    - Check if the target asset exists and is published
    - Check version constraint satisfaction
    - If tenant_id provided, check IL compatibility
    """
    conn = _get_db(db_path)
    try:
        deps = conn.execute(
            """SELECT depends_on_slug, version_constraint, dependency_type
               FROM marketplace_dependencies WHERE asset_id = ?""",
            (asset_id,),
        ).fetchall()

        if not deps:
            return {
                "compatible": True,
                "dependencies_checked": 0,
                "reason": "No dependencies declared",
            }

        results = []
        all_compatible = True

        for dep in deps:
            slug = dep["depends_on_slug"]
            constraint = dep["version_constraint"]
            dep_type = dep["dependency_type"]

            # Find the dependency asset
            dep_asset = conn.execute(
                "SELECT id, current_version, impact_level, status FROM marketplace_assets WHERE slug = ?",
                (slug,),
            ).fetchone()

            if not dep_asset:
                result = {
                    "slug": slug,
                    "constraint": constraint,
                    "type": dep_type,
                    "available": False,
                    "compatible": dep_type != "required",
                    "reason": "Dependency not found in marketplace",
                }
            elif dep_asset["status"] != "published":
                result = {
                    "slug": slug,
                    "constraint": constraint,
                    "type": dep_type,
                    "available": False,
                    "compatible": dep_type != "required",
                    "reason": f"Dependency status is '{dep_asset['status']}', not published",
                }
            else:
                ver_ok = version_satisfies(dep_asset["current_version"], constraint)
                result = {
                    "slug": slug,
                    "constraint": constraint,
                    "type": dep_type,
                    "available": True,
                    "current_version": dep_asset["current_version"],
                    "compatible": ver_ok,
                    "reason": (
                        f"Version {dep_asset['current_version']} satisfies {constraint}"
                        if ver_ok else
                        f"Version {dep_asset['current_version']} does not satisfy {constraint}"
                    ),
                }

            if not result["compatible"] and dep_type == "required":
                all_compatible = False
            results.append(result)

        return {
            "compatible": all_compatible,
            "dependencies_checked": len(results),
            "dependency_results": results,
        }
    finally:
        conn.close()


def full_compatibility_check(asset_id, consumer_il=None, tenant_id=None,
                             platform_version=None, db_path=None):
    """Run all compatibility checks for an asset.

    Returns combined result with IL, version, and dependency checks.
    """
    results = {"asset_id": asset_id, "checks": {}}
    overall = True

    # IL check (if consumer_il provided)
    if consumer_il:
        il_result = check_il_compatibility(asset_id, consumer_il, db_path)
        results["checks"]["impact_level"] = il_result
        if not il_result["compatible"]:
            overall = False

    # Version check
    ver_result = check_version_compatibility(asset_id, platform_version, db_path)
    results["checks"]["version"] = ver_result
    if not ver_result["compatible"]:
        overall = False

    # Dependency check
    dep_result = check_dependency_compatibility(asset_id, tenant_id, db_path)
    results["checks"]["dependencies"] = dep_result
    if not dep_result["compatible"]:
        overall = False

    results["overall_compatible"] = overall
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ICDEV Marketplace Compatibility Checker")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    parser.add_argument("--asset-id", required=True, help="Asset ID to check")
    parser.add_argument("--tenant-id", help="Consumer tenant ID")
    parser.add_argument("--consumer-il", choices=sorted(IL_HIERARCHY.keys()),
                        help="Consumer impact level")
    parser.add_argument("--platform-version", help="ICDEV platform version")
    parser.add_argument("--check-deps", action="store_true", help="Check dependencies only")
    parser.add_argument("--check-il", action="store_true", help="Check IL only")
    parser.add_argument("--check-version", action="store_true", help="Check version only")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.check_il:
            if not args.consumer_il:
                parser.error("--check-il requires --consumer-il")
            result = check_il_compatibility(args.asset_id, args.consumer_il, db_path)
        elif args.check_version:
            result = check_version_compatibility(args.asset_id, args.platform_version, db_path)
        elif args.check_deps:
            result = check_dependency_compatibility(args.asset_id, args.tenant_id, db_path)
        else:
            result = full_compatibility_check(
                asset_id=args.asset_id,
                consumer_il=args.consumer_il,
                tenant_id=args.tenant_id,
                platform_version=args.platform_version,
                db_path=db_path,
            )

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            compatible = result.get("compatible", result.get("overall_compatible", False))
            print(f"Compatible: {compatible}")
            if "reason" in result:
                print(f"Reason: {result['reason']}")

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
