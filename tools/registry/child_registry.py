#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Enhanced Child App Registry for ICDEV Evolution Engine.

Provides CRUD operations over the child_app_registry and
child_capabilities tables. Tracks child app lifecycle, capabilities,
and enables the evolution engine to query child state.

Tables:
- child_app_registry: Core child app metadata (Phase 19, existing)
- child_capabilities: Per-child capability tracking (Phase 36, new)

Usage:
    python tools/registry/child_registry.py --register --name "my-app" \\
        --parent-project "proj-123" --project-path .tmp/my-app --json
    python tools/registry/child_registry.py --list --json
    python tools/registry/child_registry.py --get --child-id "child-abc" --json
    python tools/registry/child_registry.py --add-capability --child-id "child-abc" \\
        --capability-name "sast_scanning" --version "1.0.0" --json
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


class ChildRegistry:
    """Enhanced child app registry with capability tracking.

    Provides CRUD for child_app_registry and child_capabilities tables.
    Supports the evolution engine's need to track child state, capabilities,
    and learned behaviors.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH

    # -----------------------------------------------------------------
    # Database helpers
    # -----------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {self.db_path}\n"
                "Run: python tools/db/init_icdev_db.py"
            )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _generate_id(self, prefix: str = "child") -> str:
        """Generate a unique ID with prefix."""
        now = datetime.now(timezone.utc).isoformat()
        raw = f"{prefix}-{now}-{id(self)}"
        return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        """Ensure child_capabilities table exists (child_app_registry
        is created by init_icdev_db.py).
        """
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS child_capabilities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                child_id TEXT NOT NULL,
                capability_name TEXT NOT NULL,
                version TEXT DEFAULT '1.0.0',
                status TEXT DEFAULT 'active'
                    CHECK(status IN ('active', 'disabled', 'deprecated',
                                     'staging', 'evaluating')),
                source TEXT DEFAULT 'parent'
                    CHECK(source IN ('parent', 'learned', 'marketplace',
                                     'evolved', 'manual')),
                learned_at TEXT DEFAULT (datetime('now')),
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(child_id, capability_name)
            );
            CREATE INDEX IF NOT EXISTS idx_child_capabilities_child
                ON child_capabilities(child_id);
            CREATE INDEX IF NOT EXISTS idx_child_capabilities_status
                ON child_capabilities(status);
            CREATE INDEX IF NOT EXISTS idx_child_capabilities_source
                ON child_capabilities(source);
        """)
        conn.commit()

    def _log_audit_event(
        self, conn: sqlite3.Connection, project_id: str,
        action: str, details: Dict,
    ) -> None:
        """Log an audit event (append-only, D6)."""
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details,
                    affected_files, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    "child_registry",
                    "icdev-evolution-engine",
                    action,
                    json.dumps(details),
                    json.dumps([]),
                    "CUI",
                ),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: Could not log audit event: {e}", file=sys.stderr)

    # -----------------------------------------------------------------
    # Child App CRUD
    # -----------------------------------------------------------------

    def register_child(
        self,
        name: str,
        parent_project_id: str,
        project_path: str,
        child_type: str = "microservice",
        target_cloud: str = "aws",
        compliance_required: bool = True,
        blueprint_json: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a new child application.

        Args:
            name: Child app name.
            parent_project_id: Parent project ID.
            project_path: Filesystem path to child app.
            child_type: Application type (microservice, api, cli, etc.).
            target_cloud: Target cloud provider.
            compliance_required: Whether compliance frameworks apply.
            blueprint_json: Optional blueprint configuration JSON.

        Returns:
            Dict with registration details.
        """
        conn = self._get_connection()
        try:
            self._ensure_tables(conn)
            child_id = self._generate_id("child")
            now = datetime.now(timezone.utc).isoformat()

            conn.execute(
                """INSERT INTO child_app_registry
                   (id, parent_project_id, child_name, child_type,
                    project_path, target_cloud, compliance_required,
                    blueprint_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    child_id, parent_project_id, name, child_type,
                    project_path, target_cloud, compliance_required,
                    blueprint_json or "{}",
                    "registered", now, now,
                ),
            )
            conn.commit()

            self._log_audit_event(conn, parent_project_id, "child_registered", {
                "child_id": child_id,
                "child_name": name,
                "child_type": child_type,
            })

            return {
                "child_id": child_id,
                "name": name,
                "parent_project_id": parent_project_id,
                "project_path": project_path,
                "child_type": child_type,
                "target_cloud": target_cloud,
                "compliance_required": compliance_required,
                "status": "registered",
                "created_at": now,
            }
        finally:
            conn.close()

    def update_child_status(
        self, child_id: str, status: str, notes: str = "",
    ) -> Dict[str, Any]:
        """Update child app status.

        Args:
            child_id: Child app ID.
            status: New status (registered, generating, active,
                     degraded, stopped, decommissioned).
            notes: Optional status notes.

        Returns:
            Dict with updated status.
        """
        valid_statuses = (
            "registered", "generating", "active", "degraded",
            "stopped", "decommissioned",
        )
        if status not in valid_statuses:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of: "
                f"{', '.join(valid_statuses)}"
            )

        conn = self._get_connection()
        try:
            now = datetime.now(timezone.utc).isoformat()
            result = conn.execute(
                """UPDATE child_app_registry
                   SET status = ?, updated_at = ?
                   WHERE id = ?""",
                (status, now, child_id),
            )
            conn.commit()

            if result.rowcount == 0:
                raise ValueError(f"Child app '{child_id}' not found.")

            # Get parent_project_id for audit
            row = conn.execute(
                "SELECT parent_project_id FROM child_app_registry WHERE id = ?",
                (child_id,),
            ).fetchone()
            parent_id = row["parent_project_id"] if row else ""

            self._log_audit_event(conn, parent_id, "child_status_updated", {
                "child_id": child_id,
                "new_status": status,
                "notes": notes,
            })

            return {
                "child_id": child_id,
                "status": status,
                "updated_at": now,
            }
        finally:
            conn.close()

    def get_child(self, child_id: str) -> Dict[str, Any]:
        """Get child app details including capabilities.

        Args:
            child_id: Child app ID.

        Returns:
            Dict with child app details and capabilities.
        """
        conn = self._get_connection()
        try:
            self._ensure_tables(conn)

            row = conn.execute(
                "SELECT * FROM child_app_registry WHERE id = ?",
                (child_id,),
            ).fetchone()

            if not row:
                raise ValueError(f"Child app '{child_id}' not found.")

            child = dict(row)

            # Get capabilities
            caps = conn.execute(
                """SELECT capability_name, version, status, source,
                          learned_at, metadata
                   FROM child_capabilities
                   WHERE child_id = ?
                   ORDER BY capability_name""",
                (child_id,),
            ).fetchall()

            child["capabilities"] = [dict(c) for c in caps]
            child["capability_count"] = len(caps)

            return child
        finally:
            conn.close()

    def list_children(
        self,
        parent_project_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List child apps with optional filtering.

        Args:
            parent_project_id: Filter by parent project.
            status: Filter by status.

        Returns:
            Dict with list of child apps.
        """
        conn = self._get_connection()
        try:
            self._ensure_tables(conn)

            query = "SELECT * FROM child_app_registry WHERE 1=1"
            params: List[str] = []

            if parent_project_id:
                query += " AND parent_project_id = ?"
                params.append(parent_project_id)
            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY created_at DESC"

            rows = conn.execute(query, params).fetchall()
            children = []
            for row in rows:
                child = dict(row)
                # Get capability count
                cap_count = conn.execute(
                    """SELECT COUNT(*) as cnt FROM child_capabilities
                       WHERE child_id = ?""",
                    (child["id"],),
                ).fetchone()
                child["capability_count"] = (
                    cap_count["cnt"] if cap_count else 0
                )
                children.append(child)

            return {
                "children": children,
                "total": len(children),
            }
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # Capability management
    # -----------------------------------------------------------------

    def add_capability(
        self,
        child_id: str,
        capability_name: str,
        version: str = "1.0.0",
        source: str = "parent",
        metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Add or update a capability for a child app.

        Args:
            child_id: Child app ID.
            capability_name: Name of the capability (e.g., 'sast_scanning').
            version: Capability version.
            source: How the capability was acquired
                    (parent, learned, marketplace, evolved, manual).
            metadata: Optional metadata dict.

        Returns:
            Dict with capability details.
        """
        conn = self._get_connection()
        try:
            self._ensure_tables(conn)
            now = datetime.now(timezone.utc).isoformat()

            conn.execute(
                """INSERT OR REPLACE INTO child_capabilities
                   (child_id, capability_name, version, status, source,
                    learned_at, metadata, updated_at)
                   VALUES (?, ?, ?, 'active', ?, ?, ?, ?)""",
                (
                    child_id, capability_name, version, source,
                    now, json.dumps(metadata or {}), now,
                ),
            )
            conn.commit()

            # Audit
            row = conn.execute(
                "SELECT parent_project_id FROM child_app_registry WHERE id = ?",
                (child_id,),
            ).fetchone()
            parent_id = row["parent_project_id"] if row else ""

            self._log_audit_event(conn, parent_id, "capability_added", {
                "child_id": child_id,
                "capability_name": capability_name,
                "version": version,
                "source": source,
            })

            return {
                "child_id": child_id,
                "capability_name": capability_name,
                "version": version,
                "source": source,
                "status": "active",
                "learned_at": now,
            }
        finally:
            conn.close()

    def remove_capability(
        self, child_id: str, capability_name: str,
    ) -> Dict[str, Any]:
        """Remove (disable) a capability from a child app.

        Does not delete — sets status to 'disabled' for audit trail.

        Args:
            child_id: Child app ID.
            capability_name: Capability to disable.

        Returns:
            Dict with removal confirmation.
        """
        conn = self._get_connection()
        try:
            self._ensure_tables(conn)
            now = datetime.now(timezone.utc).isoformat()

            result = conn.execute(
                """UPDATE child_capabilities
                   SET status = 'disabled', updated_at = ?
                   WHERE child_id = ? AND capability_name = ?""",
                (now, child_id, capability_name),
            )
            conn.commit()

            if result.rowcount == 0:
                raise ValueError(
                    f"Capability '{capability_name}' not found for "
                    f"child '{child_id}'."
                )

            return {
                "child_id": child_id,
                "capability_name": capability_name,
                "status": "disabled",
                "updated_at": now,
            }
        finally:
            conn.close()

    def get_capabilities(
        self,
        child_id: str,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get all capabilities for a child app.

        Args:
            child_id: Child app ID.
            status: Optional filter by status.

        Returns:
            Dict with capabilities list.
        """
        conn = self._get_connection()
        try:
            self._ensure_tables(conn)

            query = """SELECT capability_name, version, status, source,
                              learned_at, metadata
                       FROM child_capabilities
                       WHERE child_id = ?"""
            params: List[str] = [child_id]

            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY capability_name"

            rows = conn.execute(query, params).fetchall()
            capabilities = [dict(r) for r in rows]

            return {
                "child_id": child_id,
                "capabilities": capabilities,
                "total": len(capabilities),
            }
        finally:
            conn.close()


# =====================================================================
# CLI
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Child App Registry — manage child apps and capabilities"
    )

    # Actions
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--register", action="store_true", help="Register a new child app")
    action.add_argument("--list", action="store_true", help="List child apps")
    action.add_argument("--get", action="store_true", help="Get child app details")
    action.add_argument("--update-status", type=str, help="Update child status")
    action.add_argument("--add-capability", action="store_true", help="Add capability to child")
    action.add_argument("--remove-capability", action="store_true", help="Remove capability from child")
    action.add_argument("--get-capabilities", action="store_true", help="List capabilities for child")

    # Common args
    parser.add_argument("--child-id", help="Child app ID")
    parser.add_argument("--name", help="Child app name")
    parser.add_argument("--parent-project", help="Parent project ID")
    parser.add_argument("--project-path", help="Child app filesystem path")
    parser.add_argument("--child-type", default="microservice", help="App type")
    parser.add_argument("--target-cloud", default="aws", help="Target cloud")
    parser.add_argument("--compliance", action="store_true", default=True, help="Compliance required")
    parser.add_argument("--status-filter", help="Filter by status")

    # Capability args
    parser.add_argument("--capability-name", help="Capability name")
    parser.add_argument("--version", default="1.0.0", help="Capability version")
    parser.add_argument("--source", default="parent", help="Capability source")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, help="Database path override")

    args = parser.parse_args()

    registry = ChildRegistry(db_path=args.db_path)

    try:
        if args.register:
            if not args.name or not args.parent_project:
                parser.error("--register requires --name and --parent-project")
            result = registry.register_child(
                name=args.name,
                parent_project_id=args.parent_project,
                project_path=args.project_path or "",
                child_type=args.child_type,
                target_cloud=args.target_cloud,
                compliance_required=args.compliance,
            )

        elif args.list:
            result = registry.list_children(
                parent_project_id=args.parent_project,
                status=args.status_filter,
            )

        elif args.get:
            if not args.child_id:
                parser.error("--get requires --child-id")
            result = registry.get_child(args.child_id)

        elif args.update_status:
            if not args.child_id:
                parser.error("--update-status requires --child-id")
            result = registry.update_child_status(
                child_id=args.child_id,
                status=args.update_status,
            )

        elif args.add_capability:
            if not args.child_id or not args.capability_name:
                parser.error(
                    "--add-capability requires --child-id and --capability-name"
                )
            result = registry.add_capability(
                child_id=args.child_id,
                capability_name=args.capability_name,
                version=args.version,
                source=args.source,
            )

        elif args.remove_capability:
            if not args.child_id or not args.capability_name:
                parser.error(
                    "--remove-capability requires --child-id and --capability-name"
                )
            result = registry.remove_capability(
                child_id=args.child_id,
                capability_name=args.capability_name,
            )

        elif args.get_capabilities:
            if not args.child_id:
                parser.error("--get-capabilities requires --child-id")
            result = registry.get_capabilities(
                child_id=args.child_id,
                status=args.status_filter,
            )

        else:
            parser.print_help()
            sys.exit(1)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(json.dumps(result, indent=2, default=str))

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
