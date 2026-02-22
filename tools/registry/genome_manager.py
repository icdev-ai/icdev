#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Capability Genome Manager -- versioned capability genome with semver + SHA-256.

ADR D209: Genome is versioned with semver + content hash. Children can pin to a
genome version; upgrades are explicit.

ADR D6: Append-only audit -- rollback creates a new version pointing to target
content (never deletes).

ADR D215: Genome inheritance at birth is snapshot-based -- child gets current
genome; does not auto-upgrade.

The Capability Genome is the DNA of the ICDEV ecosystem. It captures the set of
core capabilities (tools, goals, args, context, hardprompts), compliance configs,
security gate definitions, self-healing patterns, knowledge base patterns, and
default configurations that every child application inherits at birth.

Usage:
    python tools/registry/genome_manager.py --get --json
    python tools/registry/genome_manager.py --get --version "1.2.0" --json
    python tools/registry/genome_manager.py --create --genome-data '{"capabilities":{}}' --json
    python tools/registry/genome_manager.py --diff --v1 "1.0.0" --v2 "1.1.0" --json
    python tools/registry/genome_manager.py --rollback --target-version "1.0.0" --json
    python tools/registry/genome_manager.py --history --json
    python tools/registry/genome_manager.py --verify --json
    python tools/registry/genome_manager.py --verify --version-id "gv-abc12345" --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1


# =========================================================================
# CONSTANTS
# =========================================================================
GENOME_VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS genome_versions (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    genome_data TEXT NOT NULL,
    change_type TEXT NOT NULL DEFAULT 'minor'
        CHECK(change_type IN ('major', 'minor', 'patch')),
    change_summary TEXT,
    parent_version TEXT,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

INITIAL_VERSION = "0.1.0"


# =========================================================================
# HELPERS
# =========================================================================
def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix="gv"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _content_hash(data: dict) -> str:
    """Compute SHA-256 hash of canonical JSON representation."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_semver(version_str: str) -> tuple:
    """Parse semver string to (major, minor, patch) tuple."""
    parts = version_str.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid semver: {version_str}")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _increment_semver(version_str: str, change_type: str) -> str:
    """Increment semver based on change type."""
    major, minor, patch = _parse_semver(version_str)
    if change_type == "major":
        return f"{major + 1}.0.0"
    elif change_type == "minor":
        return f"{major}.{minor + 1}.0"
    else:  # patch
        return f"{major}.{minor}.{patch + 1}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (append-only, D6)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="genome-manager",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="icdev-genome",
            )
        except Exception:
            pass


# =========================================================================
# GENOME MANAGER
# =========================================================================
class GenomeManager:
    """Versioned capability genome with semver + SHA-256 content hash (D209).

    The genome tracks all core capabilities that define the ICDEV ecosystem.
    Each version is immutable (append-only, D6). Rollback creates a new version
    with the content of the target version.
    """

    def __init__(self, db_path=None):
        """Initialize GenomeManager.

        Args:
            db_path: Path to SQLite database. Defaults to data/icdev.db.
        """
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._ensure_tables()

    def _get_conn(self):
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self):
        """Create genome_versions table if it does not exist."""
        try:
            conn = self._get_conn()
            conn.executescript(GENOME_VERSIONS_DDL)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: Table creation failed: {e}", file=sys.stderr)

    def create_version(
        self, genome_data: dict, created_by: str = "system", change_type: str = None,
        change_summary: str = None
    ) -> Optional[dict]:
        """Create a new genome version with semver + SHA-256 content hash.

        Auto-increments semver based on change_type. If change_type is not
        provided, it defaults to the value in genome_data or 'minor'.

        Args:
            genome_data: Dict containing genome capabilities and metadata.
            created_by: Identity of the creator.
            change_type: One of 'major', 'minor', 'patch'. Overrides genome_data.
            change_summary: Human-readable summary of changes.

        Returns:
            Dict with the new version record, or None on failure.
        """
        if change_type is None:
            change_type = genome_data.pop("change_type", "minor")
        if change_type not in ("major", "minor", "patch"):
            change_type = "minor"

        if change_summary is None:
            change_summary = genome_data.pop("change_summary", None)

        content_hash = _content_hash(genome_data)

        conn = self._get_conn()
        try:
            # Get latest version for auto-increment
            row = conn.execute(
                "SELECT version FROM genome_versions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

            if row:
                parent_version = row["version"]
                new_version = _increment_semver(parent_version, change_type)
            else:
                parent_version = None
                new_version = INITIAL_VERSION

            version_id = _generate_id("gv")

            conn.execute(
                """INSERT INTO genome_versions
                   (id, version, content_hash, genome_data, change_type,
                    change_summary, parent_version, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    new_version,
                    content_hash,
                    json.dumps(genome_data, default=str),
                    change_type,
                    change_summary,
                    parent_version,
                    created_by,
                    _now(),
                ),
            )
            conn.commit()

            result = {
                "id": version_id,
                "version": new_version,
                "content_hash": content_hash,
                "change_type": change_type,
                "change_summary": change_summary,
                "parent_version": parent_version,
                "created_by": created_by,
                "created_at": _now(),
            }

            _audit(
                "genome.version.created",
                f"Genome version {new_version} created",
                {"version_id": version_id, "version": new_version, "change_type": change_type},
            )
            return result

        except sqlite3.IntegrityError as e:
            return {"error": f"Version conflict: {e}"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()

    def get_version(
        self, version_id: str = None, version: str = None
    ) -> Optional[dict]:
        """Get a specific genome version or the latest.

        Args:
            version_id: The unique ID (e.g. gv-abc12345).
            version: The semver string (e.g. 1.2.0).

        Returns:
            Dict with version record, or None if not found.
        """
        conn = self._get_conn()
        try:
            if version_id:
                row = conn.execute(
                    "SELECT * FROM genome_versions WHERE id = ?", (version_id,)
                ).fetchone()
            elif version:
                row = conn.execute(
                    "SELECT * FROM genome_versions WHERE version = ?", (version,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM genome_versions ORDER BY created_at DESC LIMIT 1"
                ).fetchone()

            if row:
                result = dict(row)
                # Parse genome_data from JSON string
                try:
                    result["genome_data"] = json.loads(result["genome_data"])
                except (json.JSONDecodeError, TypeError):
                    pass
                return result
            return None
        finally:
            conn.close()

    def get_current(self) -> Optional[dict]:
        """Get the latest genome version.

        Returns:
            Dict with the latest version record, or None if no versions exist.
        """
        return self.get_version()

    def diff(self, v1: str, v2: str) -> dict:
        """Compare two genome versions.

        Args:
            v1: First version semver string (e.g. '1.0.0').
            v2: Second version semver string (e.g. '1.1.0').

        Returns:
            Dict with added, removed, and modified capabilities.
        """
        ver1 = self.get_version(version=v1)
        ver2 = self.get_version(version=v2)

        if not ver1:
            return {"error": f"Version {v1} not found"}
        if not ver2:
            return {"error": f"Version {v2} not found"}

        data1 = ver1.get("genome_data", {})
        data2 = ver2.get("genome_data", {})

        if isinstance(data1, str):
            try:
                data1 = json.loads(data1)
            except json.JSONDecodeError:
                data1 = {}
        if isinstance(data2, str):
            try:
                data2 = json.loads(data2)
            except json.JSONDecodeError:
                data2 = {}

        # Extract capability keys for comparison
        caps1 = data1.get("capabilities", {})
        caps2 = data2.get("capabilities", {})

        keys1 = set(caps1.keys()) if isinstance(caps1, dict) else set()
        keys2 = set(caps2.keys()) if isinstance(caps2, dict) else set()

        added = sorted(keys2 - keys1)
        removed = sorted(keys1 - keys2)
        common = keys1 & keys2

        modified = []
        for key in sorted(common):
            val1 = json.dumps(caps1[key], sort_keys=True)
            val2 = json.dumps(caps2[key], sort_keys=True)
            if val1 != val2:
                modified.append({
                    "capability": key,
                    "v1_hash": hashlib.sha256(val1.encode()).hexdigest()[:12],
                    "v2_hash": hashlib.sha256(val2.encode()).hexdigest()[:12],
                })

        # Also diff top-level keys beyond capabilities
        top_keys1 = set(data1.keys())
        top_keys2 = set(data2.keys())
        config_added = sorted(top_keys2 - top_keys1 - {"capabilities"})
        config_removed = sorted(top_keys1 - top_keys2 - {"capabilities"})

        result = {
            "v1": v1,
            "v2": v2,
            "v1_hash": ver1.get("content_hash", ""),
            "v2_hash": ver2.get("content_hash", ""),
            "capabilities_added": added,
            "capabilities_removed": removed,
            "capabilities_modified": modified,
            "config_sections_added": config_added,
            "config_sections_removed": config_removed,
            "total_changes": len(added) + len(removed) + len(modified),
        }

        _audit(
            "genome.diff",
            f"Diff {v1} vs {v2}: {result['total_changes']} changes",
            {"v1": v1, "v2": v2, "total_changes": result["total_changes"]},
        )
        return result

    def rollback(
        self, target_version: str, rolled_back_by: str = "system"
    ) -> Optional[dict]:
        """Rollback genome to a previous version.

        Creates a new version entry with the content of the target version
        (append-only, D6 -- never deletes or overwrites).

        Args:
            target_version: The semver string to rollback to.
            rolled_back_by: Identity of the person/system performing rollback.

        Returns:
            Dict with the newly created rollback version, or error dict.
        """
        target = self.get_version(version=target_version)
        if not target:
            return {"error": f"Target version {target_version} not found"}

        genome_data = target.get("genome_data", {})
        if isinstance(genome_data, str):
            try:
                genome_data = json.loads(genome_data)
            except json.JSONDecodeError:
                genome_data = {}

        result = self.create_version(
            genome_data=genome_data,
            created_by=rolled_back_by,
            change_type="patch",
            change_summary=f"Rollback to version {target_version}",
        )

        if result and "error" not in result:
            _audit(
                "genome.rollback",
                f"Rolled back to {target_version}, new version: {result.get('version')}",
                {
                    "target_version": target_version,
                    "new_version": result.get("version"),
                    "rolled_back_by": rolled_back_by,
                },
            )

        return result

    def get_history(self, limit: int = 20) -> list:
        """Return genome version history.

        Args:
            limit: Maximum number of versions to return. Default 20.

        Returns:
            List of version dicts ordered by creation time (newest first).
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, version, content_hash, change_type,
                          change_summary, parent_version, created_by, created_at
                   FROM genome_versions
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def verify_integrity(self, version_id: str = None) -> dict:
        """Verify genome version integrity by recomputing SHA-256.

        Args:
            version_id: Specific version ID to verify. If None, verifies latest.

        Returns:
            Dict with stored_hash, computed_hash, and integrity_ok boolean.
        """
        if version_id:
            record = self.get_version(version_id=version_id)
        else:
            record = self.get_current()

        if not record:
            return {"error": "No genome version found", "integrity_ok": False}

        genome_data = record.get("genome_data", {})
        if isinstance(genome_data, str):
            try:
                genome_data = json.loads(genome_data)
            except json.JSONDecodeError:
                return {
                    "error": "Cannot parse genome data",
                    "integrity_ok": False,
                    "version": record.get("version"),
                }

        stored_hash = record.get("content_hash", "")
        computed_hash = _content_hash(genome_data)
        integrity_ok = stored_hash == computed_hash

        result = {
            "version": record.get("version"),
            "version_id": record.get("id"),
            "stored_hash": stored_hash,
            "computed_hash": computed_hash,
            "integrity_ok": integrity_ok,
            "verified_at": _now(),
        }

        if not integrity_ok:
            _audit(
                "genome.integrity.failed",
                f"Integrity check FAILED for version {record.get('version')}",
                result,
            )
        else:
            _audit(
                "genome.integrity.passed",
                f"Integrity check passed for version {record.get('version')}",
                {"version": record.get("version"), "version_id": record.get("id")},
            )

        return result


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Capability Genome Manager -- versioned genome with semver + SHA-256"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--get", action="store_true", help="Get genome version (latest or specific)")
    group.add_argument("--create", action="store_true", help="Create a new genome version")
    group.add_argument("--diff", action="store_true", help="Diff two genome versions")
    group.add_argument("--rollback", action="store_true", help="Rollback to a previous version")
    group.add_argument("--history", action="store_true", help="Show version history")
    group.add_argument("--verify", action="store_true", help="Verify integrity of a version")

    parser.add_argument("--version-id", help="Specific version ID (for --get, --verify)")
    parser.add_argument("--version", help="Specific semver string (for --get)")
    parser.add_argument("--genome-data", help="JSON string of genome data (for --create)")
    parser.add_argument("--change-type", choices=["major", "minor", "patch"],
                        help="Semver change type (for --create)")
    parser.add_argument("--change-summary", help="Change description (for --create)")
    parser.add_argument("--created-by", default="system", help="Creator identity (for --create)")
    parser.add_argument("--v1", help="First version for diff")
    parser.add_argument("--v2", help="Second version for diff")
    parser.add_argument("--target-version", help="Target version for rollback")
    parser.add_argument("--rolled-back-by", default="system", help="Rollback identity")
    parser.add_argument("--limit", type=int, default=20, help="History limit")

    args = parser.parse_args()

    try:
        manager = GenomeManager(db_path=args.db_path)

        if args.get:
            result = manager.get_version(
                version_id=args.version_id, version=args.version
            )
            if result is None:
                result = {"error": "No genome version found"}

        elif args.create:
            if not args.genome_data:
                parser.error("--create requires --genome-data (JSON string)")
            try:
                genome_data = json.loads(args.genome_data)
            except json.JSONDecodeError as e:
                result = {"error": f"Invalid JSON in --genome-data: {e}"}
                if args.json:
                    print(json.dumps(result, indent=2))
                else:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                sys.exit(1)
            result = manager.create_version(
                genome_data=genome_data,
                created_by=args.created_by,
                change_type=args.change_type,
                change_summary=args.change_summary,
            )

        elif args.diff:
            if not args.v1 or not args.v2:
                parser.error("--diff requires --v1 and --v2")
            result = manager.diff(args.v1, args.v2)

        elif args.rollback:
            if not args.target_version:
                parser.error("--rollback requires --target-version")
            result = manager.rollback(
                target_version=args.target_version,
                rolled_back_by=args.rolled_back_by,
            )

        elif args.history:
            result = manager.get_history(limit=args.limit)

        elif args.verify:
            result = manager.verify_integrity(version_id=args.version_id)

        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable output
            if args.get and result and "error" not in result:
                print("Genome Version")
                print("=" * 50)
                print(f"  Version:      {result.get('version', 'N/A')}")
                print(f"  ID:           {result.get('id', 'N/A')}")
                print(f"  Content Hash: {result.get('content_hash', 'N/A')}")
                print(f"  Change Type:  {result.get('change_type', 'N/A')}")
                print(f"  Summary:      {result.get('change_summary', 'N/A')}")
                print(f"  Parent:       {result.get('parent_version', 'N/A')}")
                print(f"  Created By:   {result.get('created_by', 'N/A')}")
                print(f"  Created At:   {result.get('created_at', 'N/A')}")
            elif args.create and result and "error" not in result:
                print(f"Created genome version {result.get('version')} "
                      f"(ID: {result.get('id')})")
                print(f"  Hash: {result.get('content_hash')}")
            elif args.diff and "error" not in result:
                print(f"Genome Diff: {result.get('v1')} -> {result.get('v2')}")
                print("=" * 50)
                print(f"  Added:    {len(result.get('capabilities_added', []))}")
                print(f"  Removed:  {len(result.get('capabilities_removed', []))}")
                print(f"  Modified: {len(result.get('capabilities_modified', []))}")
                for cap in result.get("capabilities_added", []):
                    print(f"    + {cap}")
                for cap in result.get("capabilities_removed", []):
                    print(f"    - {cap}")
                for mod in result.get("capabilities_modified", []):
                    print(f"    ~ {mod['capability']}")
            elif args.history:
                if isinstance(result, list):
                    print("Genome Version History")
                    print("=" * 70)
                    for entry in result:
                        print(f"  {entry.get('version', '?'):10s}  "
                              f"{entry.get('change_type', '?'):6s}  "
                              f"{entry.get('created_at', '?'):22s}  "
                              f"{entry.get('change_summary', '') or ''}")
                else:
                    print(json.dumps(result, indent=2, default=str))
            elif args.verify:
                ok = result.get("integrity_ok", False)
                print(f"Integrity: {'PASS' if ok else 'FAIL'}")
                print(f"  Version:       {result.get('version', 'N/A')}")
                print(f"  Stored Hash:   {result.get('stored_hash', 'N/A')}")
                print(f"  Computed Hash: {result.get('computed_hash', 'N/A')}")
            elif args.rollback and result and "error" not in result:
                print(f"Rolled back to {args.target_version}, "
                      f"new version: {result.get('version')}")
            else:
                print(json.dumps(result, indent=2, default=str))

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
