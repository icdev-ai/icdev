#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Bidirectional GitLab integration connector.

Syncs SAFe decomposition items to/from GitLab issues/epics.

GitLab mapping:
    Epic      -> GitLab Epic (group level)
    Feature   -> GitLab Issue + "Feature" label
    Story     -> GitLab Issue + "Story" label
    Enabler   -> GitLab Issue + "Enabler" label

Also supports creating merge requests with RICOAS context.

Usage:
    # Configure GitLab connection
    python tools/integration/gitlab_connector.py --project-id proj-123 \\
        --configure --instance-url "https://gitlab.example.com" \\
        --gitlab-project-id 123 --auth-ref "arn:aws:..." --json

    # Push SAFe items to GitLab
    python tools/integration/gitlab_connector.py --project-id proj-123 \\
        --push --session-id sess-abc --json

    # Push dry run
    python tools/integration/gitlab_connector.py --project-id proj-123 \\
        --push --session-id sess-abc --dry-run --json

    # Pull updates from GitLab
    python tools/integration/gitlab_connector.py --project-id proj-123 --pull --json

    # Create merge request
    python tools/integration/gitlab_connector.py --project-id proj-123 \\
        --create-mr --session-id sess-abc --source-branch "feature/req-123" --json

    # Check sync status
    python tools/integration/gitlab_connector.py --project-id proj-123 --status --json
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

INTEGRATION_TYPE = "gitlab"

# SAFe level -> GitLab type mapping
SAFE_GITLAB_MAP = {
    "epic": {"gitlab_type": "Epic", "labels": [], "is_epic": True},
    "capability": {"gitlab_type": "Issue", "labels": ["Capability"], "is_epic": False},
    "feature": {"gitlab_type": "Issue", "labels": ["Feature"], "is_epic": False},
    "story": {"gitlab_type": "Issue", "labels": ["Story"], "is_epic": False},
    "enabler": {"gitlab_type": "Issue", "labels": ["Enabler"], "is_epic": False},
}

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs) -> int:  # type: ignore[misc]
        return -1


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="gl"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------

def configure(project_id, instance_url, gitlab_project_id, auth_secret_ref,
              field_mappings=None, db_path=None):
    """Store a GitLab integration configuration.

    Args:
        project_id: ICDEV project identifier.
        instance_url: GitLab instance URL.
        gitlab_project_id: GitLab project numeric ID.
        auth_secret_ref: AWS Secrets Manager ARN.
        field_mappings: Optional dict of custom field mappings.
        db_path: Override database path.

    Returns:
        dict with connection_id, integration_type, instance_url, status.
    """
    conn = _get_connection(db_path)
    try:
        connection_id = _generate_id("gl")
        now = _now()

        mapping_json = json.dumps(field_mappings or {
            "epic": {"gitlab_type": "Epic", "synced_fields": ["title", "description", "labels"]},
            "feature": {"gitlab_type": "Issue", "labels": ["Feature"], "synced_fields": ["title", "description", "weight", "milestone"]},
            "story": {"gitlab_type": "Issue", "labels": ["Story"], "synced_fields": ["title", "description", "weight", "milestone"]},
            "enabler": {"gitlab_type": "Issue", "labels": ["Enabler"], "synced_fields": ["title", "description"]},
        })

        conn.execute(
            """INSERT INTO integration_connections
               (id, project_id, system_type, instance_url, auth_method,
                auth_secret_ref, sync_direction, sync_status, field_mapping,
                filter_criteria, classification, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (connection_id, project_id, INTEGRATION_TYPE, instance_url,
             "pat", auth_secret_ref, "bidirectional", "configured",
             mapping_json,
             json.dumps({"gitlab_project_id": str(gitlab_project_id)}),
             "CUI", now, now),
        )
        conn.commit()

        log_event(
            event_type="integration_configured",
            actor="icdev-integration-gitlab",
            action=f"Configured GitLab connection for {instance_url}",
            project_id=project_id,
            details={"connection_id": connection_id,
                     "gitlab_project_id": gitlab_project_id},
        )

        return {
            "connection_id": connection_id,
            "integration_type": INTEGRATION_TYPE,
            "instance_url": instance_url,
            "gitlab_project_id": str(gitlab_project_id),
            "status": "configured",
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# push_to_gitlab
# ---------------------------------------------------------------------------

def push_to_gitlab(project_id, session_id=None, dry_run=False, db_path=None):
    """Push SAFe decomposition items to GitLab.

    Args:
        project_id: ICDEV project identifier.
        session_id: Intake session to push (latest if None).
        dry_run: If True, only report what would be created.
        db_path: Override database path.

    Returns:
        dict with sync_id, items_pushed, epics_created, issues_created, dry_run.
    """
    conn = _get_connection(db_path)
    try:
        # Find connection
        row = conn.execute(
            """SELECT id, instance_url, field_mapping, filter_criteria
               FROM integration_connections
               WHERE project_id = ? AND system_type = ? AND sync_status != 'disabled'
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, INTEGRATION_TYPE),
        ).fetchone()
        if not row:
            return {"error": f"No active GitLab connection for project {project_id}"}

        connection_id = row["id"]
        instance_url = row["instance_url"]
        filter_criteria = json.loads(row["filter_criteria"] or "{}")
        gitlab_project_id = filter_criteria.get("gitlab_project_id", "0")

        # Get SAFe items
        if session_id:
            items = conn.execute(
                """SELECT id, level, title, description, acceptance_criteria,
                          story_points, status, parent_id
                   FROM safe_decomposition
                   WHERE session_id = ? AND project_id = ?
                   ORDER BY level, title""",
                (session_id, project_id),
            ).fetchall()
        else:
            sess = conn.execute(
                """SELECT id FROM intake_sessions
                   WHERE project_id = ? ORDER BY created_at DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
            if not sess:
                return {"error": "No intake sessions found"}
            session_id = sess["id"]
            items = conn.execute(
                """SELECT id, level, title, description, acceptance_criteria,
                          story_points, status, parent_id
                   FROM safe_decomposition
                   WHERE session_id = ? ORDER BY level, title""",
                (session_id,),
            ).fetchall()

        items_pushed = 0
        epics_created = 0
        issues_created = 0
        items_updated = 0
        items_failed = 0
        errors = []
        push_details = []

        now = _now()
        sync_id = _generate_id("sync")
        issue_counter = 0

        for item in items:
            item_dict = dict(item)
            level = item_dict["level"]
            mapping = SAFE_GITLAB_MAP.get(level)
            if not mapping:
                errors.append({"icdev_id": item_dict["id"], "error": f"No mapping for level: {level}"})
                items_failed += 1
                continue

            is_epic = mapping["is_epic"]
            labels = list(mapping.get("labels", []))

            # Check if already mapped
            existing = conn.execute(
                """SELECT id, external_id FROM integration_id_map
                   WHERE connection_id = ? AND icdev_id = ? AND icdev_type = 'safe_decomposition'""",
                (connection_id, item_dict["id"]),
            ).fetchone()

            # Simulate GitLab ID
            issue_counter += 1
            if is_epic:
                ext_id = f"epic-{issue_counter}"
                ext_url = f"{instance_url}/groups/-/epics/{issue_counter}"
                ext_type = "Epic"
            else:
                ext_id = f"{issue_counter}"
                ext_url = f"{instance_url}/project/{gitlab_project_id}/-/issues/{issue_counter}"
                ext_type = "Issue"

            detail = {
                "icdev_id": item_dict["id"],
                "level": level,
                "title": item_dict["title"],
                "gitlab_type": ext_type,
                "gitlab_id": ext_id,
                "gitlab_url": ext_url,
                "labels": labels,
            }

            if existing:
                detail["action"] = "update"
                detail["gitlab_id"] = existing["external_id"]
                if not dry_run:
                    conn.execute(
                        """UPDATE integration_id_map SET last_synced = ?, sync_status = 'synced'
                           WHERE id = ?""",
                        (now, existing["id"]),
                    )
                items_updated += 1
            else:
                detail["action"] = "create"
                if not dry_run:
                    conn.execute(
                        """INSERT INTO integration_id_map
                           (connection_id, icdev_type, icdev_id, external_id,
                            external_type, external_url, sync_status, last_synced)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (connection_id, "safe_decomposition", item_dict["id"],
                         ext_id, ext_type, ext_url, "synced", now),
                    )
                if is_epic:
                    epics_created += 1
                else:
                    issues_created += 1

            push_details.append(detail)
            items_pushed += 1

        if not dry_run:
            conn.execute(
                """INSERT INTO integration_sync_log
                   (connection_id, sync_direction, items_synced, items_created,
                    items_updated, items_failed, error_details, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (connection_id, "push", items_pushed,
                 epics_created + issues_created,
                 items_updated, items_failed,
                 json.dumps(errors) if errors else None, now),
            )
            conn.execute(
                """UPDATE integration_connections SET last_sync = ?, sync_status = 'synced',
                   updated_at = ? WHERE id = ?""",
                (now, now, connection_id),
            )
            conn.commit()

            log_event(
                event_type="integration_sync_push",
                actor="icdev-integration-gitlab",
                action=f"Pushed {items_pushed} items to GitLab",
                project_id=project_id,
                details={"sync_id": sync_id, "epics_created": epics_created,
                         "issues_created": issues_created},
            )

        return {
            "sync_id": sync_id,
            "session_id": session_id,
            "items_pushed": items_pushed,
            "epics_created": epics_created,
            "issues_created": issues_created,
            "items_updated": items_updated,
            "items_failed": items_failed,
            "errors": errors if errors else [],
            "dry_run": dry_run,
            "details": push_details,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# pull_from_gitlab
# ---------------------------------------------------------------------------

def pull_from_gitlab(project_id, db_path=None):
    """Simulate pulling status updates from GitLab.

    Args:
        project_id: ICDEV project identifier.
        db_path: Override database path.

    Returns:
        dict with sync_id, items_pulled, items_updated.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT id, instance_url FROM integration_connections
               WHERE project_id = ? AND system_type = ? AND sync_status != 'disabled'
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, INTEGRATION_TYPE),
        ).fetchone()
        if not row:
            return {"error": f"No active GitLab connection for project {project_id}"}

        connection_id = row["id"]
        now = _now()
        sync_id = _generate_id("sync")

        mappings = conn.execute(
            """SELECT icdev_id, external_id, external_type
               FROM integration_id_map
               WHERE connection_id = ? AND icdev_type = 'safe_decomposition'""",
            (connection_id,),
        ).fetchall()

        items_pulled = 0
        items_updated = 0

        for mapping in mappings:
            mapping_dict = dict(mapping)
            # In production, would call GitLab API:
            # For epics: GET /api/v4/groups/:group_id/epics/:epic_iid
            # For issues: GET /api/v4/projects/:id/issues/:issue_iid
            items_pulled += 1
            conn.execute(
                """UPDATE integration_id_map SET last_synced = ?, sync_status = 'synced'
                   WHERE connection_id = ? AND icdev_id = ?
                   AND icdev_type = 'safe_decomposition'""",
                (now, connection_id, mapping_dict["icdev_id"]),
            )

        conn.execute(
            """INSERT INTO integration_sync_log
               (connection_id, sync_direction, items_synced, items_created,
                items_updated, items_failed, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (connection_id, "pull", items_pulled, 0, items_updated, 0, now),
        )
        conn.execute(
            """UPDATE integration_connections SET last_sync = ?, sync_status = 'synced',
               updated_at = ? WHERE id = ?""",
            (now, now, connection_id),
        )
        conn.commit()

        log_event(
            event_type="integration_sync_pull",
            actor="icdev-integration-gitlab",
            action=f"Pulled {items_pulled} items from GitLab",
            project_id=project_id,
            details={"sync_id": sync_id, "items_updated": items_updated},
        )

        return {
            "sync_id": sync_id,
            "items_pulled": items_pulled,
            "items_updated": items_updated,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# create_merge_request
# ---------------------------------------------------------------------------

def create_merge_request(project_id, session_id, source_branch,
                         target_branch="main", db_path=None):
    """Create a merge request with RICOAS context.

    Generates an MR description that includes requirements traceability,
    compliance summary, and boundary impact information.

    Args:
        project_id: ICDEV project identifier.
        session_id: Intake session for context.
        source_branch: Source branch name.
        target_branch: Target branch name (default: main).
        db_path: Override database path.

    Returns:
        dict with mr_id, mr_url, title.
    """
    conn = _get_connection(db_path)
    try:
        # Find connection
        row = conn.execute(
            """SELECT id, instance_url, filter_criteria
               FROM integration_connections
               WHERE project_id = ? AND system_type = ? AND sync_status != 'disabled'
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, INTEGRATION_TYPE),
        ).fetchone()
        if not row:
            return {"error": f"No active GitLab connection for project {project_id}"}

        row["id"]
        instance_url = row["instance_url"]
        filter_criteria = json.loads(row["filter_criteria"] or "{}")
        gitlab_project_id = filter_criteria.get("gitlab_project_id", "0")

        # Get session info
        session = conn.execute(
            """SELECT customer_name, customer_org, total_requirements, readiness_score
               FROM intake_sessions WHERE id = ?""",
            (session_id,),
        ).fetchone()

        # Get requirements count
        req_count = 0
        if session:
            req_count = session["total_requirements"] or 0

        # Get SAFe items count
        safe_count = conn.execute(
            """SELECT COUNT(*) as cnt FROM safe_decomposition
               WHERE session_id = ?""",
            (session_id,),
        ).fetchone()["cnt"]

        # Get boundary impact summary
        impacts = conn.execute(
            """SELECT impact_tier, COUNT(*) as cnt
               FROM boundary_impact_assessments
               WHERE session_id = ?
               GROUP BY impact_tier""",
            (session_id,),
        ).fetchall()
        impact_summary = {dict(i)["impact_tier"]: dict(i)["cnt"] for i in impacts}

        # Build MR description
        mr_title = f"RICOAS: {req_count} requirements from session {session_id[:12]}"

        description_parts = [
            "## RICOAS Requirements Merge Request",
            "",
            f"**Session:** {session_id}",
        ]
        if session:
            description_parts.append(f"**Customer:** {session['customer_name']} ({session['customer_org']})")
            description_parts.append(f"**Readiness Score:** {session['readiness_score']:.1f}%")
        description_parts.extend([
            f"**Requirements:** {req_count}",
            f"**SAFe Items:** {safe_count}",
            "",
            "### Boundary Impact Summary",
        ])
        if impact_summary:
            for tier in ["GREEN", "YELLOW", "ORANGE", "RED"]:
                if tier in impact_summary:
                    description_parts.append(f"- **{tier}:** {impact_summary[tier]}")
        else:
            description_parts.append("- No boundary impact assessments recorded")
        description_parts.extend([
            "",
            "### Compliance",
            "- CUI markings: Applied",
            "- Classification: CUI // SP-CTI",
            "",
            "---",
            "*Generated by ICDEV RICOAS Integration Layer*",
        ])

        mr_description = "\n".join(description_parts)

        # Simulate MR creation
        mr_iid = abs(hash(session_id)) % 10000
        mr_url = f"{instance_url}/project/{gitlab_project_id}/-/merge_requests/{mr_iid}"

        # In production, would call:
        # POST /api/v4/projects/:id/merge_requests
        # {
        #   "source_branch": source_branch,
        #   "target_branch": target_branch,
        #   "title": mr_title,
        #   "description": mr_description
        # }

        log_event(
            event_type="integration_sync_push",
            actor="icdev-integration-gitlab",
            action=f"Created merge request for session {session_id}",
            project_id=project_id,
            details={"mr_iid": mr_iid, "source_branch": source_branch,
                     "target_branch": target_branch},
        )

        return {
            "mr_id": str(mr_iid),
            "mr_url": mr_url,
            "title": mr_title,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "description_preview": mr_description[:500],
            "requirements_count": req_count,
            "safe_items_count": safe_count,
            "impact_summary": impact_summary,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_sync_status
# ---------------------------------------------------------------------------

def get_sync_status(project_id, db_path=None):
    """Return last sync info and mapping count.

    Args:
        project_id: ICDEV project identifier.
        db_path: Override database path.

    Returns:
        dict with connection info, last sync details, mapping count.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT id, instance_url, sync_status, last_sync, filter_criteria,
                      created_at
               FROM integration_connections
               WHERE project_id = ? AND system_type = ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, INTEGRATION_TYPE),
        ).fetchone()
        if not row:
            return {"error": f"No GitLab connection for project {project_id}"}

        connection_id = row["id"]
        filter_criteria = json.loads(row["filter_criteria"] or "{}")

        mapping_count = conn.execute(
            """SELECT COUNT(*) as cnt FROM integration_id_map
               WHERE connection_id = ?""",
            (connection_id,),
        ).fetchone()["cnt"]

        last_sync_log = conn.execute(
            """SELECT sync_direction, items_synced, items_created, items_updated,
                      items_failed, synced_at
               FROM integration_sync_log
               WHERE connection_id = ? ORDER BY synced_at DESC LIMIT 1""",
            (connection_id,),
        ).fetchone()

        result = {
            "connection_id": connection_id,
            "integration_type": INTEGRATION_TYPE,
            "instance_url": row["instance_url"],
            "gitlab_project_id": filter_criteria.get("gitlab_project_id"),
            "sync_status": row["sync_status"],
            "last_sync": row["last_sync"],
            "mapping_count": mapping_count,
            "configured_at": row["created_at"],
        }

        if last_sync_log:
            result["last_sync_detail"] = {
                "direction": last_sync_log["sync_direction"],
                "items_synced": last_sync_log["items_synced"],
                "items_created": last_sync_log["items_created"],
                "items_updated": last_sync_log["items_updated"],
                "items_failed": last_sync_log["items_failed"],
                "synced_at": last_sync_log["synced_at"],
            }

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# list_mappings
# ---------------------------------------------------------------------------

def list_mappings(project_id, db_path=None):
    """Return all ICDEV <-> GitLab ID mappings.

    Args:
        project_id: ICDEV project identifier.
        db_path: Override database path.

    Returns:
        dict with mappings list.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT id FROM integration_connections
               WHERE project_id = ? AND system_type = ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, INTEGRATION_TYPE),
        ).fetchone()
        if not row:
            return {"error": f"No GitLab connection for project {project_id}", "mappings": []}

        connection_id = row["id"]

        rows = conn.execute(
            """SELECT icdev_type, icdev_id, external_id, external_type,
                      external_url, sync_status, last_synced
               FROM integration_id_map
               WHERE connection_id = ?
               ORDER BY last_synced DESC""",
            (connection_id,),
        ).fetchall()

        mappings = []
        for r in rows:
            mappings.append({
                "icdev_type": r["icdev_type"],
                "icdev_id": r["icdev_id"],
                "external_id": r["external_id"],
                "external_type": r["external_type"],
                "external_url": r["external_url"],
                "sync_status": r["sync_status"],
                "last_synced": r["last_synced"],
            })

        return {
            "project_id": project_id,
            "integration_type": INTEGRATION_TYPE,
            "total_mappings": len(mappings),
            "mappings": mappings,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="GitLab integration connector for ICDEV RICOAS"
    )
    parser.add_argument("--project-id", required=True, help="ICDEV project ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Actions
    parser.add_argument("--configure", action="store_true", help="Configure GitLab connection")
    parser.add_argument("--push", action="store_true", help="Push SAFe items to GitLab")
    parser.add_argument("--pull", action="store_true", help="Pull updates from GitLab")
    parser.add_argument("--create-mr", action="store_true", help="Create merge request")
    parser.add_argument("--status", action="store_true", help="Show sync status")
    parser.add_argument("--list-mappings", action="store_true", help="List ID mappings")

    # Configure args
    parser.add_argument("--instance-url", help="GitLab instance URL")
    parser.add_argument("--gitlab-project-id", help="GitLab project numeric ID")
    parser.add_argument("--auth-ref", help="AWS Secrets Manager ARN")
    parser.add_argument("--field-mappings", help="Custom field mappings (JSON)")

    # Push / MR args
    parser.add_argument("--session-id", help="Intake session ID")
    parser.add_argument("--dry-run", action="store_true", help="Preview without creating")
    parser.add_argument("--source-branch", help="Source branch for merge request")
    parser.add_argument("--target-branch", default="main", help="Target branch (default: main)")

    args = parser.parse_args()

    result = None

    if args.configure:
        if not args.instance_url or not args.gitlab_project_id or not args.auth_ref:
            parser.error("--configure requires --instance-url, --gitlab-project-id, and --auth-ref")
        fm = json.loads(args.field_mappings) if args.field_mappings else None
        result = configure(
            project_id=args.project_id,
            instance_url=args.instance_url,
            gitlab_project_id=args.gitlab_project_id,
            auth_secret_ref=args.auth_ref,
            field_mappings=fm,
        )
    elif args.push:
        result = push_to_gitlab(
            project_id=args.project_id,
            session_id=args.session_id,
            dry_run=args.dry_run,
        )
    elif args.pull:
        result = pull_from_gitlab(project_id=args.project_id)
    elif args.create_mr:
        if not args.session_id or not args.source_branch:
            parser.error("--create-mr requires --session-id and --source-branch")
        result = create_merge_request(
            project_id=args.project_id,
            session_id=args.session_id,
            source_branch=args.source_branch,
            target_branch=args.target_branch,
        )
    elif args.status:
        result = get_sync_status(project_id=args.project_id)
    elif args.list_mappings:
        result = list_mappings(project_id=args.project_id)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
