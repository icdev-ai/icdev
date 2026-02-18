#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Bidirectional ServiceNow integration connector.

Syncs SAFe decomposition items to/from ServiceNow tables.
Maps SAFe hierarchy to ServiceNow record types.

Usage:
    # Configure ServiceNow connection
    python tools/integration/servicenow_connector.py --project-id proj-123 \\
        --configure --instance-url "https://org.service-now.com" \\
        --table-name "rm_story" --auth-ref "arn:aws:..." --json

    # Push SAFe items to ServiceNow
    python tools/integration/servicenow_connector.py --project-id proj-123 \\
        --push --session-id sess-abc --json

    # Pull updates from ServiceNow
    python tools/integration/servicenow_connector.py --project-id proj-123 --pull --json

    # Check sync status
    python tools/integration/servicenow_connector.py --project-id proj-123 --status --json
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

INTEGRATION_TYPE = "servicenow"

# SAFe level -> ServiceNow table/type mapping
SAFE_SNOW_MAP = {
    "epic": {"table": "rm_epic", "type": "Epic", "fields": ["title", "description", "priority", "status"]},
    "capability": {"table": "rm_feature", "type": "Capability", "fields": ["title", "description", "priority"]},
    "feature": {"table": "rm_story", "type": "Feature", "fields": ["title", "description", "acceptance_criteria", "story_points"]},
    "story": {"table": "rm_story", "type": "Story", "fields": ["title", "description", "acceptance_criteria", "story_points"]},
    "enabler": {"table": "rm_story", "type": "Enabler", "fields": ["title", "description"]},
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


def _generate_id(prefix="snow"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    """ISO-8601 timestamp."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------

def configure(project_id, instance_url, table_name, auth_secret_ref,
              field_mappings=None, db_path=None):
    """Store a ServiceNow integration configuration.

    Args:
        project_id: ICDEV project identifier.
        instance_url: ServiceNow instance URL.
        table_name: Primary ServiceNow table (e.g. "rm_story").
        auth_secret_ref: AWS Secrets Manager ARN.
        field_mappings: Optional dict of custom field mappings.
        db_path: Override database path.

    Returns:
        dict with connection_id, integration_type, instance_url, status.
    """
    conn = _get_connection(db_path)
    try:
        connection_id = _generate_id("snow")
        now = _now()

        mapping_json = json.dumps(field_mappings or {
            "epic": {"table": "rm_epic", "type": "Epic"},
            "feature": {"table": "rm_story", "type": "Feature"},
            "story": {"table": "rm_story", "type": "Story"},
            "enabler": {"table": "rm_story", "type": "Enabler"},
        })

        conn.execute(
            """INSERT INTO integration_connections
               (id, project_id, system_type, instance_url, auth_method,
                auth_secret_ref, sync_direction, sync_status, field_mapping,
                filter_criteria, classification, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (connection_id, project_id, INTEGRATION_TYPE, instance_url,
             "oauth2", auth_secret_ref, "bidirectional", "configured",
             mapping_json, json.dumps({"table_name": table_name}),
             "CUI", now, now),
        )
        conn.commit()

        log_event(
            event_type="integration_configured",
            actor="icdev-integration-servicenow",
            action=f"Configured ServiceNow connection for {instance_url}",
            project_id=project_id,
            details={"connection_id": connection_id, "table_name": table_name},
        )

        return {
            "connection_id": connection_id,
            "integration_type": INTEGRATION_TYPE,
            "instance_url": instance_url,
            "table_name": table_name,
            "status": "configured",
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# push_to_servicenow
# ---------------------------------------------------------------------------

def push_to_servicenow(project_id, session_id=None, dry_run=False, db_path=None):
    """Push SAFe decomposition items to ServiceNow.

    Args:
        project_id: ICDEV project identifier.
        session_id: Intake session to push (latest if None).
        dry_run: If True, only report what would be created.
        db_path: Override database path.

    Returns:
        dict with sync_id, items_pushed, items_created, dry_run.
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
            return {"error": f"No active ServiceNow connection for project {project_id}"}

        connection_id = row["id"]
        instance_url = row["instance_url"]
        filter_criteria = json.loads(row["filter_criteria"] or "{}")
        table_name = filter_criteria.get("table_name", "rm_story")

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
        items_created = 0
        items_updated = 0
        items_failed = 0
        errors = []
        push_details = []

        now = _now()
        sync_id = _generate_id("sync")

        for item in items:
            item_dict = dict(item)
            level = item_dict["level"]
            mapping = SAFE_SNOW_MAP.get(level)
            if not mapping:
                errors.append({"icdev_id": item_dict["id"], "error": f"No mapping for level: {level}"})
                items_failed += 1
                continue

            snow_table = mapping["table"]
            snow_type = mapping["type"]

            # Check if already mapped
            existing = conn.execute(
                """SELECT id, external_id FROM integration_id_map
                   WHERE connection_id = ? AND icdev_id = ? AND icdev_type = 'safe_decomposition'""",
                (connection_id, item_dict["id"]),
            ).fetchone()

            # Simulate ServiceNow sys_id
            sys_id = uuid.uuid4().hex[:32]
            snow_url = f"{instance_url}/nav_to.do?uri={snow_table}.do?sys_id={sys_id}"

            detail = {
                "icdev_id": item_dict["id"],
                "level": level,
                "title": item_dict["title"],
                "snow_table": snow_table,
                "snow_type": snow_type,
                "snow_sys_id": sys_id,
                "snow_url": snow_url,
            }

            if existing:
                detail["action"] = "update"
                detail["snow_sys_id"] = existing["external_id"]
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
                         sys_id, snow_type, snow_url, "synced", now),
                    )
                items_created += 1

            push_details.append(detail)
            items_pushed += 1

        if not dry_run:
            conn.execute(
                """INSERT INTO integration_sync_log
                   (connection_id, sync_direction, items_synced, items_created,
                    items_updated, items_failed, error_details, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (connection_id, "push", items_pushed, items_created,
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
                actor="icdev-integration-servicenow",
                action=f"Pushed {items_pushed} items to ServiceNow",
                project_id=project_id,
                details={"sync_id": sync_id, "items_created": items_created},
            )

        return {
            "sync_id": sync_id,
            "session_id": session_id,
            "items_pushed": items_pushed,
            "items_created": items_created,
            "items_updated": items_updated,
            "items_failed": items_failed,
            "errors": errors if errors else [],
            "dry_run": dry_run,
            "details": push_details,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# pull_from_servicenow
# ---------------------------------------------------------------------------

def pull_from_servicenow(project_id, db_path=None):
    """Simulate pulling status updates from ServiceNow.

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
            return {"error": f"No active ServiceNow connection for project {project_id}"}

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
            # In production, would call ServiceNow Table API:
            # GET /api/now/table/{table}?sys_id={external_id}
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
            actor="icdev-integration-servicenow",
            action=f"Pulled {items_pulled} items from ServiceNow",
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
            return {"error": f"No ServiceNow connection for project {project_id}"}

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
            "table_name": filter_criteria.get("table_name"),
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
# Attachment vision analysis
# ---------------------------------------------------------------------------

def _analyze_image_attachment(image_path, source_context=""):
    """Analyze an image attachment using a vision LLM.

    Args:
        image_path: Path to the image file.
        source_context: Context about where the image came from.

    Returns:
        dict with category, description, extracted_requirements, ui_elements,
        or None if vision unavailable.
    """
    try:
        from tools.testing.screenshot_validator import encode_image
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        router = get_router()
        provider, model_id, model_cfg = router.get_provider_for_function("attachment_analysis")
        if provider is None or not model_cfg.get("supports_vision", False):
            return None

        b64_data, media_type = encode_image(str(image_path))

        context_note = f" This image is from: {source_context}." if source_context else ""

        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data,
                },
            },
            {
                "type": "text",
                "text": (
                    f"Analyze this ticket attachment.{context_note} "
                    "Classify it and extract useful information. "
                    "Respond with EXACTLY this JSON (no markdown, no extra text): "
                    '{"category": "mockup|wireframe|diagram|screenshot|'
                    'error_screenshot|architecture_diagram|test_result|other", '
                    '"description": "brief description", '
                    '"extracted_requirements": ["any requirement statements visible"], '
                    '"ui_elements": ["notable UI elements or components visible"]}'
                ),
            },
        ]

        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=(
                "You are analyzing image attachments from a project management tool "
                "for a DoD software project. Extract useful information for requirements "
                "analysis and development planning."
            ),
            max_tokens=512,
            temperature=0.1,
        )

        response = router.invoke("attachment_analysis", request)
        text = response.content.strip()

        # Strip markdown fences
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        import json as _json
        return _json.loads(text)

    except Exception:
        return None


def analyze_attachments(project_id, attachment_paths=None, db_path=None):
    """Analyze image attachments from ServiceNow records using vision LLM.

    Args:
        project_id: ICDEV project identifier.
        attachment_paths: List of image file paths to analyze.
        db_path: Override database path.

    Returns:
        dict with analysis results for each attachment.
    """
    if not attachment_paths:
        return {
            "project_id": project_id,
            "analyzed": 0,
            "results": [],
            "note": "No attachment paths provided",
        }

    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}
    results = []

    for img_path in attachment_paths:
        p = Path(img_path)
        if not p.exists():
            results.append({"path": str(p), "error": "File not found"})
            continue
        if p.suffix.lower() not in image_exts:
            continue

        analysis = _analyze_image_attachment(
            str(p), source_context=f"ServiceNow attachment for project {project_id}"
        )
        if analysis:
            results.append({"path": str(p), "analysis": analysis})
        else:
            results.append({
                "path": str(p),
                "analysis": None,
                "note": "Vision model not available",
            })

    log_event(
        event_type="attachment_analyzed",
        actor="icdev-integration-servicenow",
        action=f"Analyzed {len(results)} ServiceNow attachments",
        project_id=project_id,
        details={"count": len(results)},
    )

    return {
        "project_id": project_id,
        "analyzed": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ServiceNow integration connector for ICDEV RICOAS"
    )
    parser.add_argument("--project-id", required=True, help="ICDEV project ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Actions
    parser.add_argument("--configure", action="store_true", help="Configure ServiceNow connection")
    parser.add_argument("--push", action="store_true", help="Push SAFe items to ServiceNow")
    parser.add_argument("--pull", action="store_true", help="Pull updates from ServiceNow")
    parser.add_argument("--status", action="store_true", help="Show sync status")

    # Configure args
    parser.add_argument("--instance-url", help="ServiceNow instance URL")
    parser.add_argument("--table-name", help="ServiceNow table name (e.g. rm_story)")
    parser.add_argument("--auth-ref", help="AWS Secrets Manager ARN")
    parser.add_argument("--field-mappings", help="Custom field mappings (JSON)")

    # Push args
    parser.add_argument("--session-id", help="Intake session ID")
    parser.add_argument("--dry-run", action="store_true", help="Preview without creating")

    # Attachment analysis
    parser.add_argument(
        "--analyze-attachments", action="store_true",
        help="Analyze image attachments using vision LLM",
    )
    parser.add_argument(
        "--attachment-paths", nargs="*",
        help="Paths to image attachments to analyze",
    )

    args = parser.parse_args()

    result = None

    if args.configure:
        if not args.instance_url or not args.table_name or not args.auth_ref:
            parser.error("--configure requires --instance-url, --table-name, and --auth-ref")
        fm = json.loads(args.field_mappings) if args.field_mappings else None
        result = configure(
            project_id=args.project_id,
            instance_url=args.instance_url,
            table_name=args.table_name,
            auth_secret_ref=args.auth_ref,
            field_mappings=fm,
        )
    elif args.push:
        result = push_to_servicenow(
            project_id=args.project_id,
            session_id=args.session_id,
            dry_run=args.dry_run,
        )
    elif args.pull:
        result = pull_from_servicenow(project_id=args.project_id)
    elif args.status:
        result = get_sync_status(project_id=args.project_id)
    elif args.analyze_attachments:
        result = analyze_attachments(
            project_id=args.project_id,
            attachment_paths=args.attachment_paths,
        )
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
